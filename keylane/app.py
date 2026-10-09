"""Resident owner process: one engine, launcher overlay, manager, tray."""

from __future__ import annotations

import ctypes
import logging
import os
import resource
import signal
import sys
import time
from pathlib import Path

log = logging.getLogger("keylane")

PKG = Path(__file__).resolve().parent
QML_DIR = PKG / "ui" / "qml"


def harden() -> None:
    """No core dumps, no ptrace by same-uid processes, private file mode."""
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_DUMPABLE = 4
        libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except OSError:
        pass
    os.umask(0o077)


def _layer_shell_paths() -> tuple[str | None, str | None]:
    """Find org.kde.layershell: system install, then repository vendor dir."""
    candidates = [
        (Path("/usr/lib/qt6/qml"), Path("/usr/lib")),
        (PKG.parent / "vendor/layershellqt/lib/qt6/qml", PKG.parent / "vendor/layershellqt/lib"),
    ]
    extra = os.environ.get("KEYLANE_LAYERSHELL_DIR")
    if extra:
        candidates.insert(0, (Path(extra) / "qt6/qml", Path(extra)))
    for qml, lib in candidates:
        if (qml / "org/kde/layershell/qmldir").exists():
            so = lib / "libLayerShellQtInterface.so.6"
            if so.exists():
                try:
                    # RTLD_LOCAL on purpose: RTLD_GLOBAL would export Qt's libb2 blake2b_* symbols and
                    # override the copies bundled in argon2-cffi, crashing the KDF.
                    ctypes.CDLL(str(so), mode=ctypes.RTLD_LOCAL)
                except OSError:
                    continue
            return str(qml), str(lib)
    return None, None


def setup_logging(level: str) -> None:
    logging.basicConfig(level=logging.DEBUG if level == "debug" else logging.INFO,
                        format="keylane[%(name)s] %(levelname)s %(message)s", stream=sys.stderr)


def run() -> int:
    t_start = time.perf_counter()
    harden()
    from .core import config, ipc

    settings = config.load()
    setup_logging(settings.log_level)
    for w in settings.warnings:
        log.warning("config: %s", w)

    qml_import, ls_lib = _layer_shell_paths()
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")

    from PySide6.QtCore import Q_ARG, QCoreApplication, QMetaObject, QObject, Qt, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication

    if ls_lib:
        QCoreApplication.addLibraryPath(str(Path(ls_lib) / "qt6/plugins"))
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Keylane")
    app.setOrganizationName("Keylane")
    app.setDesktopFileName("keylane")
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(PKG / "assets" / "keylane.svg"))
    app.setWindowIcon(icon)

    from .ui.bridge import Backend, Poster
    from .ui import bridge as bridge_mod
    from .ui.tray import Tray
    from .desktop import hyprland
    from .desktop.session_watch import SessionWatch

    poster = Poster()
    backend = Backend(settings, poster)

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda ws: [log.warning("qml: %s", w.toString()) for w in ws])

    def qcall(obj, method: str, *args) -> None:
        """Invoke a JS function declared on a QML root object."""
        QMetaObject.invokeMethod(obj, method, Qt.DirectConnection, *[Q_ARG("QVariant", a) for a in args])
    bridge_mod.set_engine(engine)
    if qml_import:
        engine.addImportPath(qml_import)
    engine.addImportPath(str(QML_DIR))
    from .ui.theme_qt import ThemeObject

    theme_obj = ThemeObject(backend)
    engine.rootContext().setContextProperty("kl", backend)
    engine.rootContext().setContextProperty("th", theme_obj)
    engine.rootContext().setContextProperty("layerShellAvailable", bool(qml_import))
    LAUNCHER_QML = "Launcher.qml" if qml_import else "LauncherFallback.qml"

    class Windows(QObject):
        def __init__(self):
            super().__init__()
            self.launcher = None
            self.manager = None
            self.toast = None
            self.prev_window: str | None = None
            self.shown_at = 0.0
            self.tracer = None
            self.pending_trace: tuple[int | None, int] | None = None

        def load(self, name: str):
            before = len(engine.rootObjects())
            engine.load(QUrl.fromLocalFile(str(QML_DIR / name)))
            objs = engine.rootObjects()
            if len(objs) <= before:
                raise RuntimeError(f"failed to load {name} (see QML warnings above)")
            return objs[-1]

        def show_launcher(self, mode: str = "search", text: str = "") -> None:
            t0 = time.perf_counter()
            if self.launcher is None:
                self.load_launcher()
            if self.tracer is not None and self.pending_trace and not self.launcher.property("visible"):
                self.tracer.opened(*self.pending_trace)
            self.pending_trace = None
            if not self.launcher.property("visible"):
                self.prev_window = hyprland.active_window()
            qcall(self.launcher, "open", mode, text)
            self.shown_at = t0
            log.debug("launcher open requested in %.1f ms", (time.perf_counter() - t0) * 1000)

        def load_launcher(self) -> None:
            self.launcher = self.load(LAUNCHER_QML)
            if os.environ.get("KEYLANE_TRACE") and qml_import:
                from .ui.trace import LauncherTracer

                card = self.launcher.findChild(QObject, "card")
                query = self.launcher.findChild(QObject, "query")
                if card is not None and query is not None:
                    self.tracer = LauncherTracer(self.launcher, card, query)
                    log.info("latency tracing enabled")

        def hide_launcher(self) -> None:
            if self.launcher is not None and self.launcher.property("visible"):
                qcall(self.launcher, "close_")
                prev = self.prev_window
                self.prev_window = None
                if prev:
                    QTimer.singleShot(30, lambda: self._restore(prev))

        def _restore(self, prev: str) -> None:
            if hyprland.active_window() != prev:
                hyprland.focus_window(prev)

        def show_manager(self, page: str = "entries", entry_id: str = "") -> None:
            if self.launcher is not None and self.launcher.property("visible"):
                qcall(self.launcher, "close_")
                self.prev_window = None
            if self.manager is not None and os.environ.get("KEYLANE_QML_DEV"):
                # Developer hot-reload: rebuild the window from fresh QML.
                self.manager.close()
                self.manager.deleteLater()
                self.manager = None
                engine.clearComponentCache()
            if self.manager is None:
                self.manager = self.load("Manager.qml")
            qcall(self.manager, "present", page, entry_id)

        def show_toast(self, label: str, seconds: int) -> None:
            if self.toast is None and qml_import:
                self.toast = self.load("Toast.qml")
            if self.toast is not None:
                qcall(self.toast, "flash", f"{label} copied", f"Clears in {seconds}s")

    win = Windows()
    backend.launcherRequested.connect(win.show_launcher)
    backend.launcherHideRequested.connect(win.hide_launcher)
    backend.managerRequested.connect(win.show_manager)
    backend.copied.connect(win.show_toast)

    tray = Tray(backend, icon,
                open_manager=lambda: win.show_manager("entries", ""),
                quick_search=lambda: win.show_launcher("search", ""),
                create=lambda: win.show_launcher("new", ""),
                settings=lambda: win.show_manager("settings", ""))

    watch = SessionWatch(lock_on_desktop_lock=settings.lock_on_desktop_lock, lock_on_sleep=settings.lock_on_sleep)
    watch.lockRequested.connect(lambda reason: backend.lock(reason))
    backend.session_gaps = watch.coverage_gaps
    for gap in watch.coverage_gaps():
        log.warning("lock coverage gap: %s", gap)

    def apply_settings():
        watch.lock_on_desktop_lock = backend._settings.lock_on_desktop_lock
        watch.lock_on_sleep = backend._settings.lock_on_sleep
        if watch.lock_on_sleep:
            watch.acquire_inhibitor()
        else:
            watch.release_inhibitor()
        backend.settingsChanged.emit()  # coverage gaps depend on these settings
    backend.settingsApplied.connect(apply_settings)

    class IpcBridge(QObject):
        command = Signal(str, str)

        @Slot(str, str)
        def dispatch(self, cmd: str, text: str) -> None:
            if cmd == "search":
                win.show_launcher("search", text)
            elif cmd == "new":
                win.show_launcher("new", text)
            elif cmd == "manager":
                win.show_manager("entries", "")
            elif cmd == "settings":
                win.show_manager("settings", "")
            elif cmd == "unlock":
                win.show_launcher("search", "")
            elif cmd == "lock":
                backend.lock("cli")
            elif cmd == "quit":
                backend.quit()

    ipcb = IpcBridge()
    ipcb.command.connect(ipcb.dispatch, Qt.QueuedConnection)

    def handle(req: dict) -> dict:
        cmd = req["cmd"]
        if cmd == "ping":
            return {"ok": True}
        if cmd == "status":
            return {"ok": True, "status": backend.status()}
        args = req["args"]
        if win.tracer is not None and cmd in ("search", "new"):
            win.pending_trace = (req.get("t"), time.monotonic_ns())
        ipcb.command.emit(cmd, args.get("query") or args.get("title") or "")
        return {"ok": True}

    server = ipc.Server(handle)
    try:
        server.start()
    except RuntimeError:
        log.error("another Keylane instance is already running")
        return 1

    def do_quit():
        backend.lock("quit")
        app.quit()
    backend.quitRequested.connect(do_quit)

    # SIGTERM/SIGINT/SIGHUP (logout, systemd stop, terminal closed): lock first.
    def on_signal(_sig, _frm):
        QTimer.singleShot(0, do_quit)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    sig_timer = QTimer(interval=250)
    sig_timer.timeout.connect(lambda: None)  # lets Python handle signals while Qt loops
    sig_timer.start()

    app.aboutToQuit.connect(lambda: (backend.shutdown(), watch.shutdown(), server.stop(), tray.hide()))

    # Pre-create the launcher so the first shortcut is as fast as later ones.
    if not qml_import:
        log.warning("layer-shell-qt not found: launcher falls back to a regular window")
    win.load_launcher()
    tray.show()

    initial = os.environ.pop("KEYLANE_INITIAL", "")
    if initial:
        try:
            req = ipc.validate_request(initial.encode())
            QTimer.singleShot(0, lambda: ipcb.dispatch(req["cmd"], req["args"].get("query") or req["args"].get("title") or ""))
        except ValueError:
            pass
    log.info("ready in %.0f ms (layer-shell: %s, session sources: %s)", (time.perf_counter() - t_start) * 1000,
             "yes" if qml_import else "no", ",".join(watch.sources) or "none")
    rc = app.exec()
    # Tear the QML engine down while its context objects (kl, th) are still alive;
    # otherwise every binding re-evaluates against null during interpreter shutdown.
    import shiboken6

    try:
        engine.warnings.disconnect()
    except (RuntimeError, TypeError):
        pass
    win.launcher = win.manager = win.toast = None
    shiboken6.delete(engine)
    return rc
