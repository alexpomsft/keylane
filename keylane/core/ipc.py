"""Local control socket.

One JSON object per line, request <= 4 KiB, same-UID peers only (SO_PEERCRED),
socket directory 0700. The protocol only carries commands and non-secret
status; there is no command that returns vault content.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable

MAX_REQUEST = 4096
COMMANDS = {"search", "new", "manager", "lock", "unlock", "status", "quit", "settings", "ping"}
_RATE = (20, 1.0)  # at most 20 requests per second


def socket_path() -> Path:
    from .paths import runtime_dir

    return runtime_dir() / "control.sock"


def _ensure_dir(d: Path) -> None:
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = d.lstat()
    if not os.path.isdir(d) or os.path.islink(d) or st.st_uid != os.getuid():
        raise PermissionError(f"unsafe runtime directory {d}")
    if st.st_mode & 0o077:
        os.chmod(d, 0o700)


def validate_request(raw: bytes) -> dict:
    if len(raw) > MAX_REQUEST:
        raise ValueError("request too large")
    try:
        req = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("malformed request") from None
    if not isinstance(req, dict) or not isinstance(req.get("cmd"), str) or req["cmd"] not in COMMANDS:
        raise ValueError("unknown command")
    args = req.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("bad args")
    clean = {}
    for k, v in args.items():
        if k not in ("query", "title") or not isinstance(v, str) or len(v) > 512:
            raise ValueError("bad args")
        clean[k] = v
    t = req.get("t")
    # Optional client CLOCK_MONOTONIC timestamp (ns), used only for latency tracing.
    t = t if isinstance(t, int) and not isinstance(t, bool) and 0 < t < 2**63 else None
    return {"cmd": req["cmd"], "args": clean, "t": t}


class Server:
    """Accepts commands on a background thread; ``handle`` runs on that thread
    and must itself marshal to the UI thread. It returns a JSON-able dict."""

    def __init__(self, handle: Callable[[dict], dict], path: Path | None = None):
        self.path = path or socket_path()
        self._handle = handle
        self._sock: socket.socket | None = None
        self._stop = False
        self._hits: list[float] = []

    def start(self) -> None:
        _ensure_dir(self.path.parent)
        if self.path.exists() or self.path.is_symlink():
            if ping(self.path):
                raise RuntimeError("Keylane is already running")
            self.path.unlink()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            s.bind(str(self.path))
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        s.listen(8)
        self._sock = s
        threading.Thread(target=self._loop, name="keylane-ipc", daemon=True).start()

    def stop(self) -> None:
        self._stop = True
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        self._hits = [t for t in self._hits if now - t < _RATE[1]]
        if len(self._hits) >= _RATE[0]:
            return False
        self._hits.append(now)
        return True

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                try:
                    self._serve(conn)
                except Exception:
                    pass

    def _serve(self, conn: socket.socket) -> None:
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
        if uid != os.getuid():
            return
        conn.settimeout(1.0)
        buf = b""
        while b"\n" not in buf and len(buf) <= MAX_REQUEST:
            chunk = conn.recv(1024)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        try:
            if not self._rate_ok():
                raise ValueError("rate limited")
            req = validate_request(line)
            resp = self._handle(req)
        except ValueError as exc:
            resp = {"ok": False, "error": str(exc)}
        conn.sendall(json.dumps(resp).encode() + b"\n")


def request(cmd: str, args: dict | None = None, *, path: Path | None = None, timeout: float = 3.0) -> dict:
    path = path or socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall(json.dumps({"cmd": cmd, "args": args or {}, "t": time.monotonic_ns()}).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf or b"{}")


def ping(path: Path | None = None) -> bool:
    try:
        return bool(request("ping", path=path, timeout=0.5).get("ok"))
    except (OSError, ValueError):
        return False
