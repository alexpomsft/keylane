"""StatusNotifier tray item."""

from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class Tray:
    def __init__(self, backend, icon: QIcon, *, open_manager, quick_search, create, settings):
        self.backend = backend
        self.icon = QSystemTrayIcon(icon)
        self.icon.setToolTip("Keylane")
        menu = QMenu()
        self.menu = menu
        menu.addAction("Open manager", open_manager)
        menu.addAction("Quick search", quick_search)
        menu.addAction("Create entry", create)
        menu.addSeparator()
        self.lock_action: QAction = menu.addAction("Lock", self._toggle_lock)
        menu.addAction("Settings", settings)
        menu.addSeparator()
        menu.addAction("Quit", backend.quit)
        self.icon.setContextMenu(menu)
        self._quick_search = quick_search
        self.icon.activated.connect(lambda reason: open_manager() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        backend.sessionChanged.connect(self._sync)
        backend.storageChanged.connect(self._sync)
        self._sync()

    def _toggle_lock(self):
        if self.backend.unlocked:
            self.backend.lock("tray")
        else:
            self._quick_search()

    def _sync(self):
        unlocked = self.backend.unlocked
        self.lock_action.setText("Lock" if unlocked else "Unlock…")
        state = "unlocked" if unlocked else ("no vault" if self.backend.session == "no-vault" else "locked")
        extra = ""
        if self.backend.conflictCount:
            extra = f" · {self.backend.conflictCount} conflict(s)"
        self.icon.setToolTip(f"Keylane — {state}{extra}")

    def show(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.icon.show()

    def hide(self):
        self.icon.hide()
