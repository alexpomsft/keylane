"""Wayland clipboard ownership via wl-copy.

Secrets are passed to ``wl-copy`` on stdin (never argv/env). ``--foreground``
keeps the process alive exactly as long as it owns the selection, which gives
us a reliable ownership signal: when the lifetime expires we clear only if our
process is still alive (i.e. nobody copied something else meanwhile).

The primary selection is never touched.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import errors

log = logging.getLogger("keylane.clipboard")

# Known clipboard-history tools and whether they are known to honour the
# x-kde-passwordManagerHint / CLIPBOARD_STATE=sensitive convention.
_HISTORY_TOOLS = {
    "omarchy-clipboard": True,  # /usr/share/omarchy/shell/plugins/clipboard/capture.sh
    "cliphist": False,  # only if wrapped by a hint-aware filter
    "clipman": False,
    "copyq": True,
    "clipse": False,
    "gpaste": True,
    "klipper": True,
    "wl-clip-persist": False,
}


@dataclass(frozen=True)
class HistoryStatus:
    confirmed: bool
    detail: str


def history_status(proc_root: Path = Path("/proc")) -> HistoryStatus:
    """Best-effort check of running clipboard history tools for this user."""
    uid = os.getuid()
    found: dict[str, bool] = {}
    try:
        pids = [p for p in proc_root.iterdir() if p.name.isdigit()]
    except OSError:
        return HistoryStatus(False, "Could not inspect running clipboard tools.")
    for p in pids:
        try:
            if p.stat().st_uid != uid:
                continue
            argv = (p / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        cmd = b" ".join(argv).decode("utf-8", "replace")
        if "omarchy" in cmd and "clipboard" in cmd and "capture" in cmd:
            found["omarchy-clipboard"] = True
            continue
        exe = os.path.basename(argv[0].decode("utf-8", "replace")) if argv and argv[0] else ""
        if exe == "wl-paste" and "--watch" in cmd and "cliphist" not in cmd:
            found.setdefault("wl-paste watcher", False)
            continue
        for tool, honours in _HISTORY_TOOLS.items():
            if exe == tool or (tool == "cliphist" and "wl-paste" in exe and "cliphist" in cmd):
                found.setdefault(tool, honours)
    if "omarchy-clipboard" in found:
        found.pop("cliphist", None)  # Omarchy's capture wrapper filters sensitive copies before cliphist
    unsafe = sorted(t for t, ok in found.items() if not ok)
    if unsafe:
        return HistoryStatus(False, f"Clipboard history ({', '.join(unsafe)}) may record copied secrets.")
    if found:
        return HistoryStatus(True, "Clipboard history honours the password-manager hint.")
    return HistoryStatus(True, "No clipboard history tool detected.")


class ClipboardOwner:
    """Owns at most one sensitive clipboard value at a time.

    ``schedule(seconds, fn)`` must run ``fn`` later on the owner's thread and
    return a cancel callable. Defaults to threading.Timer.
    """

    def __init__(self, *, seconds: int = 20, schedule: Callable[[float, Callable[[], None]], Callable[[], None]] | None = None,
                 wl_copy: str | None = None):
        self.seconds = seconds
        self._schedule = schedule or _thread_schedule
        self._wl_copy = wl_copy or shutil.which("wl-copy")
        self._proc: subprocess.Popen | None = None
        self._cancel: Callable[[], None] | None = None
        self._token = 0
        self._lock = threading.Lock()
        self.on_cleared: Callable[[str], None] | None = None  # reason

    @property
    def available(self) -> bool:
        return bool(self._wl_copy) and bool(os.environ.get("WAYLAND_DISPLAY"))

    def owns(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def copy(self, value: str) -> int:
        """Place ``value`` on the clipboard. Returns a token. Raises ClipboardFailed."""
        if value == "":
            raise errors.EmptyField()
        if not self.available:
            raise errors.ClipboardFailed(detail="wl-copy-unavailable")
        with self._lock:
            self._release_locked(clear=False)
            env = dict(os.environ)
            env["CLIPBOARD_STATE"] = "sensitive"
            try:
                proc = subprocess.Popen(
                    [self._wl_copy, "--foreground", "--sensitive", "--type", "text/plain;charset=utf-8"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, close_fds=True, start_new_session=True,
                )
                proc.stdin.write(value.encode("utf-8"))
                proc.stdin.close()
            except OSError as exc:
                raise errors.ClipboardFailed(detail=type(exc).__name__) from None
            finally:
                del value
            try:
                rc = proc.wait(timeout=0.15)
            except subprocess.TimeoutExpired:
                rc = None  # still running: we own the selection
            if rc is not None:
                raise errors.ClipboardFailed(detail=f"wl-copy-exit-{rc}")
            self._proc = proc
            self._token += 1
            token = self._token
            self._cancel = self._schedule(self.seconds, lambda: self._expire(token))
            return token

    def _expire(self, token: int) -> None:
        with self._lock:
            if token != self._token:
                return
            owned = self.owns()
            self._release_locked(clear=True)
        if self.on_cleared:
            self.on_cleared("expired" if owned else "replaced")

    def _release_locked(self, *, clear: bool) -> None:
        if self._cancel:
            self._cancel()
            self._cancel = None
        p, self._proc = self._proc, None
        if p is None:
            return
        if p.poll() is None:
            # Still the owner. Terminating the source makes the selection empty;
            # an explicit --clear also covers compositors that keep a copy.
            p.terminate()
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill()
            if clear and self._wl_copy:
                try:
                    subprocess.run([self._wl_copy, "--clear"], timeout=2, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def clear(self, reason: str = "lock") -> None:
        """Clear now if we still own the selection (lock, quit)."""
        with self._lock:
            owned = self.owns()
            self._token += 1
            self._release_locked(clear=True)
        if owned and self.on_cleared:
            self.on_cleared(reason)


def _thread_schedule(seconds: float, fn: Callable[[], None]) -> Callable[[], None]:
    t = threading.Timer(seconds, fn)
    t.daemon = True
    t.start()
    return t.cancel
