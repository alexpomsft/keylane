"""Opt-in end-to-end latency tracing (``KEYLANE_TRACE=1``).

Measures what the user sees, not just algorithm time:

* ``open``: from the CLI's request timestamp (CLOCK_MONOTONIC, shared by all
  processes) to the swap of the first launcher frame in which the card is
  visible and the search field holds keyboard focus.
* ``key``: from a key press arriving at the launcher to the swap of the first
  frame rendered after the resulting search/model update.

The swap is when the frame is handed to the compositor; on-screen presentation
adds at most one refresh interval. Only timings are logged, never text.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QEvent, QObject, Qt

log = logging.getLogger("keylane.trace")


_gc_t = [0]


def _gc_cb(phase, info):
    if phase == "start":
        _gc_t[0] = time.monotonic_ns()
    elif (ms := (time.monotonic_ns() - _gc_t[0]) / 1e6) > 2:
        log.info("trace gc gen=%d ms=%.2f thread=%s", info["generation"], ms, threading.current_thread().name)


class LauncherTracer(QObject):
    def __init__(self, window, card, query):
        super().__init__(window)
        self._w, self._card, self._query = window, card, query
        self._lock = threading.Lock()
        self._open_t: int | None = None   # pending open start (ns)
        self._open_recv: int | None = None
        self._key_t: int | None = None    # pending key start (ns)
        self._armed: list[tuple[str, int, int | None]] = []
        import gc
        gc.callbacks.append(_gc_cb)
        window.installEventFilter(self)
        window.afterAnimating.connect(self._after_animating)
        window.frameSwapped.connect(self._swapped, Qt.DirectConnection)

    def opened(self, client_t: int | None, recv_t: int) -> None:
        self._open_t, self._open_recv = (client_t or recv_t), recv_t
        self._key_t = None  # a key that hid the launcher never produced a frame

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress and self._key_t is None:
            self._key_t = time.monotonic_ns()
        return False

    def _after_animating(self) -> None:
        # GUI thread, after events are processed and before the frame is synced.
        armed = []
        if self._open_t is not None and self._w.isVisible() and self._card.property("opacity") > 0 \
                and self._query.property("activeFocus"):
            armed.append(("open", self._open_t, self._open_recv))
            self._open_t = None
        if self._key_t is not None:
            armed.append(("key", self._key_t, None))
            self._key_t = None
        if armed:
            with self._lock:
                self._armed.extend(armed)

    def _swapped(self) -> None:
        # Render thread: only compute and log.
        now = time.monotonic_ns()
        with self._lock:
            armed, self._armed = self._armed, []
        for kind, t0, recv in armed:
            if kind == "open":
                log.info("trace open_ms=%.2f ipc_to_frame_ms=%.2f", (now - t0) / 1e6, (now - recv) / 1e6)
            else:
                log.info("trace key_ms=%.2f", (now - t0) / 1e6)
