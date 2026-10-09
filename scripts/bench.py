#!/usr/bin/env python3
"""Keylane performance benchmark (REQUIREMENTS §13, T-11).

    python scripts/bench.py [--entries 10000] [--runs 40]            # engine only, headless
    python scripts/bench.py --desktop [--entries 10000] [--runs 40]  # end-to-end, live Wayland session

Engine mode times query -> ranked, model-ready rows on a synthetic vault.

Desktop mode runs a *separate* resident instance with an isolated config/state/
runtime directory and a synthetic vault, so it never touches a real profile.
It needs `wtype`, a layer-shell compositor and an otherwise idle desktop: the
script types into the Keylane overlay only (it checks that the overlay is mapped
first). Measured with KEYLANE_TRACE=1:

* open: CLI request timestamp -> first swapped frame with the card visible
  and the search field focused (the exact command a shortcut binding runs);
* key:  key press -> first swapped frame after the result list was updated.

Unlock is a separate, intentionally expensive step and is not part of the
warm measurements. The default (production) KDF is used for the vault.
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUERIES = ["g", "gi", "git", "gith", "githu", "github", "nas", "cloud", "bank ex", "zzzq", "hetz", "arch wiki"]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return xs[k]


def summary(name: str, xs: list[float], target: float | None = None) -> str:
    if not xs:
        return f"{name:<22} no samples"
    line = (f"{name:<22} n={len(xs):<4} p50={statistics.median(xs):6.2f}  p95={pct(xs, 95):6.2f}  "
            f"p99={pct(xs, 99):6.2f}  max={max(xs):6.2f} ms")
    if target is not None:
        line += "  " + ("PASS" if max(xs) <= target else ("PASS (p95)" if pct(xs, 95) <= target else "FAIL")) + f" (≤{target:g} ms)"
    return line


def make_vault(path: Path, entries: int, fast: bool) -> None:
    cmd = [sys.executable, str(ROOT / "scripts/make-demo-vault.py"), str(path), "--entries", str(entries), "--password", "bench"]
    if fast:
        cmd.append("--fast")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


# ---------------------------------------------------------------- engine

def bench_engine(args) -> int:
    from keylane.core import kdbx
    from keylane.core.search import SearchIndex
    from keylane.ui.bridge import _row

    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "bench.kdbx"
        t = time.perf_counter()
        make_vault(vault, args.entries, fast=True)
        print(f"fixture: {args.entries} synthetic entries ({(time.perf_counter() - t):.1f}s to generate)")
        t = time.perf_counter()
        doc = kdbx.VaultDocument.open_bytes(vault.read_bytes(), kdbx.make_composite("bench", None))
        t_load = time.perf_counter() - t
        t = time.perf_counter()
        index = SearchIndex(doc.summaries())
        t_index = time.perf_counter() - t
    print(f"decrypt+parse {t_load * 1000:.0f} ms (cheap test KDF), index build {t_index * 1000:.1f} ms")
    samples: list[float] = []
    per_len: dict[int, list[float]] = {}
    for _ in range(args.runs):
        for q in QUERIES:
            for i in range(1, len(q) + 1):  # simulate typing
                s = q[:i]
                t = time.perf_counter()
                rows = [_row(r.summary, r.matched, set()) for r in index.search(s, limit=50)]
                dt = (time.perf_counter() - t) * 1000
                samples.append(dt)
                per_len.setdefault(min(len(s), 6), []).append(dt)
                del rows
    print(summary("query->rows", samples, 50))
    for n in sorted(per_len):
        print("  " + summary(f"{n}{'+' if n == 6 else ''} chars", per_len[n]))
    return 0


# ---------------------------------------------------------------- desktop

def _layer_mapped() -> bool:
    out = subprocess.run(["hyprctl", "layers"], capture_output=True, text=True).stdout
    return "namespace: keylane" in out


def _wtype(*a: str) -> None:
    if not _layer_mapped():
        raise RuntimeError("Keylane overlay is not mapped; refusing to type")
    subprocess.run(["wtype", *a], check=True)


def bench_desktop(args) -> int:
    for tool in ("wtype", "hyprctl"):
        if not shutil.which(tool):
            print(f"desktop mode needs {tool}", file=sys.stderr)
            return 2
    td = Path(tempfile.mkdtemp(prefix="keylane-bench-"))
    env = dict(os.environ, XDG_CONFIG_HOME=str(td / "config"), XDG_STATE_HOME=str(td / "state"),
               KEYLANE_RUNTIME_DIR=str(td / "run"), KEYLANE_TRACE="1")
    env.pop("KEYLANE_QML_DEV", None)
    vault = td / "bench.kdbx"
    print(f"creating {args.entries}-entry vault with the production KDF…")
    make_vault(vault, args.entries, fast=False)
    cfg = td / "config/keylane"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text(f'[vault]\npath = "{vault}"\n\n[diagnostics]\nlog_level = "info"\n')

    py = [sys.executable, "-m", "keylane"]
    daemon = subprocess.Popen([*py, "daemon"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: queue.Queue[str] = queue.Queue()
    logf = open(args.log, "w") if args.log else None

    def pump() -> None:
        for ln in daemon.stdout:
            lines.put(ln)
            if logf:
                logf.write(ln)
                logf.flush()

    threading.Thread(target=pump, daemon=True).start()

    def wait_for(pattern: str, timeout: float = 5.0) -> re.Match | None:
        end = time.monotonic() + timeout
        rx = re.compile(pattern)
        while time.monotonic() < end:
            try:
                ln = lines.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                break
            m = rx.search(ln)
            if m:
                return m
        return None

    def drain() -> None:
        time.sleep(0.15)
        while not lines.empty():
            lines.get_nowait()

    def cli(cmd: str) -> None:
        subprocess.run([*py, cmd], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)

    opens: list[float] = []
    keys: list[float] = []
    try:
        if not wait_for(r"ready in", 10):
            print("daemon did not start", file=sys.stderr)
            return 1
        t = time.perf_counter()
        cli("search")
        time.sleep(0.6)
        _wtype("bench")
        _wtype("-k", "Return")
        for _ in range(600):
            st = subprocess.run([*py, "status"], cwd=ROOT, env=env, capture_output=True, text=True).stdout
            if '"unlocked"' in st:
                break
            time.sleep(0.1)
        else:
            print("vault did not unlock", file=sys.stderr)
            return 1
        print(f"unlock (production KDF, not a launcher target): ~{(time.perf_counter() - t):.1f}s incl. typing")
        time.sleep(0.5)
        _wtype("-k", "Escape")
        time.sleep(0.4)
        skipped = 0
        for i in range(args.runs):
            drain()
            cli("search")
            m = wait_for(r"trace open_ms=([\d.]+)")
            if m:
                opens.append(float(m.group(1)))
            time.sleep(0.25)
            q = QUERIES[i % len(QUERIES)]
            try:
                for ch in q:
                    drain()
                    if logf:
                        logf.write(f"# typing {ch!r} of {q!r}\n")
                    _wtype(ch)
                    m = wait_for(r"trace key_ms=([\d.]+)", 2)
                    if m:
                        keys.append(float(m.group(1)))
                _wtype("-k", "Escape")
                time.sleep(0.1)
                _wtype("-k", "Escape")
            except RuntimeError:
                skipped += 1  # overlay lost focus (e.g. the user clicked elsewhere)
            time.sleep(0.3)
    finally:
        try:
            cli("quit")
            daemon.wait(5)
        except Exception:
            daemon.kill()
        shutil.rmtree(td, ignore_errors=True)

    print(f"\n{args.entries} entries, {args.runs} invocations, frame swap timing (+≤1 refresh to scan-out)")
    print(summary("shortcut->focused", opens, 150))
    print(summary("key->results frame", keys, 50))
    if skipped:
        print(f"({skipped} run(s) cut short because the overlay was dismissed externally)")
    return 0 if opens and keys and pct(opens, 95) <= 150 and pct(keys, 95) <= 50 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--entries", type=int, default=10000)
    ap.add_argument("--log", help="desktop mode: also write the daemon's log here")
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--desktop", action="store_true", help="end-to-end on the live Wayland session")
    args = ap.parse_args()
    return bench_desktop(args) if args.desktop else bench_engine(args)


if __name__ == "__main__":
    sys.exit(main())
