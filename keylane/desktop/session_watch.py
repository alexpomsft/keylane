"""Session events that must lock the vault: desktop lock, suspend, shutdown.

Sources (any one is sufficient):
* logind ``PrepareForSleep``/``PrepareForShutdown`` (Manager) and ``Lock``
  (Session) via QtDBus. A *delay* inhibitor gives us time to wipe plaintext
  before the system sleeps; it is released right after locking and
  re-acquired on resume.
* Hyprland ext-session-lock state, polled cheaply over the IPC socket.
  Omarchy locks through Quickshell, which does not go through logind.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface

from . import hyprland

log = logging.getLogger("keylane.session")

LOGIN1 = "org.freedesktop.login1"


class SessionWatch(QObject):
    lockRequested = Signal(str)  # reason

    def __init__(self, parent=None, *, lock_on_desktop_lock=True, lock_on_sleep=True):
        super().__init__(parent)
        self.lock_on_desktop_lock = lock_on_desktop_lock
        self.lock_on_sleep = lock_on_sleep
        self._inhibitor: subprocess.Popen | None = None
        self._bus = QDBusConnection.systemBus()
        self._was_locked = False
        self.sources: list[str] = []
        if self._bus.isConnected():
            ok = self._bus.connect(LOGIN1, "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                                   "PrepareForSleep", self, "1onPrepareForSleep(bool)")
            ok2 = self._bus.connect(LOGIN1, "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                                    "PrepareForShutdown", self, "1onPrepareForShutdown(bool)")
            spath = self._session_path()
            ok3 = bool(spath) and self._bus.connect(LOGIN1, spath, "org.freedesktop.login1.Session", "Lock", self, "1onSessionLock()")
            if ok or ok2:
                self.sources.append("logind")
            if ok3:
                self.sources.append("logind-session-lock")
        if hyprland.available():
            self._poll = QTimer(self)
            self._poll.setInterval(750)
            self._poll.timeout.connect(self._poll_hyprland)
            self._poll.start()
            self.sources.append("hyprland-session-lock")
        self.acquire_inhibitor()

    def _session_path(self) -> str:
        sid = os.environ.get("XDG_SESSION_ID")
        iface = QDBusInterface(LOGIN1, "/org/freedesktop/login1", "org.freedesktop.login1.Manager", self._bus)
        if not iface.isValid():
            return ""
        reply = iface.call("GetSession", sid) if sid else iface.call("GetSessionByPID", os.getpid())
        args = reply.arguments()
        if not args:
            return ""
        v = args[0]
        return v.path() if hasattr(v, "path") else str(v)

    # ---- inhibitor
    def coverage_gaps(self) -> list[str]:
        """User-facing descriptions of enabled lock triggers that cannot be detected."""
        gaps = []
        if self.lock_on_sleep:
            if "logind" not in self.sources:
                gaps.append("Suspend and shutdown cannot be detected (no logind on the system bus); lock manually before sleeping.")
            elif self._inhibitor is None or self._inhibitor.poll() is not None:
                gaps.append("No sleep delay inhibitor (systemd-inhibit unavailable); the vault may lock only after resume.")
        if self.lock_on_desktop_lock and not ({"logind-session-lock", "hyprland-session-lock"} & set(self.sources)):
            gaps.append("Desktop lock cannot be detected in this session; lock Keylane manually.")
        return gaps

    def acquire_inhibitor(self) -> None:
        if not self.lock_on_sleep or self._inhibitor is not None:
            return
        exe = shutil.which("systemd-inhibit")
        if not exe:
            return
        try:
            self._inhibitor = subprocess.Popen(
                [exe, "--what=sleep:shutdown", "--mode=delay", "--who=Keylane",
                 "--why=Lock the password vault before sleep", "sleep", "infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self._inhibitor = None

    def release_inhibitor(self) -> None:
        p, self._inhibitor = self._inhibitor, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(1)
            except subprocess.TimeoutExpired:
                p.kill()

    # ---- slots
    @Slot(bool)
    def onPrepareForSleep(self, starting: bool) -> None:
        if starting:
            if self.lock_on_sleep:
                self.lockRequested.emit("sleep")
            self.release_inhibitor()
        else:
            self.acquire_inhibitor()

    @Slot(bool)
    def onPrepareForShutdown(self, starting: bool) -> None:
        if starting:
            self.lockRequested.emit("shutdown")
            self.release_inhibitor()

    @Slot()
    def onSessionLock(self) -> None:
        if self.lock_on_desktop_lock:
            self.lockRequested.emit("desktop-lock")

    def _poll_hyprland(self) -> None:
        locked = hyprland.session_locked()
        if locked and not self._was_locked and self.lock_on_desktop_lock:
            self.lockRequested.emit("desktop-lock")
        if locked is not None:
            self._was_locked = locked

    def shutdown(self) -> None:
        self.release_inhibitor()
