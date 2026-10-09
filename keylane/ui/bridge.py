"""Qt bridge between QML and the vault service.

Lives on the UI thread. Async results are delivered to QML through JS
callbacks (QJSValue). Secrets cross this boundary only when the user asked for
them (editor load, reveal) and are never logged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (QAbstractListModel, QByteArray, QFileSystemWatcher, QModelIndex, QObject, Property, Qt,
                            QTimer, Signal, Slot)
from PySide6.QtQml import QJSValue

from ..core import config, errors, generator
from ..core.clipboard import ClipboardOwner, history_status
from ..core.kdbx import CustomField, EntryData
from ..core.search import url_host
from ..core.service import Session, VaultService
from ..desktop import theme as theme_mod

log = logging.getLogger("keylane.ui")


def _err(e: errors.KeylaneError | None) -> dict:
    if e is None:
        return {"ok": True}
    return {"ok": False, "code": e.code, "message": e.user_message}


_ENGINE = None


def set_engine(engine) -> None:
    """The JS engine used to convert callback results into QJSValues."""
    global _ENGINE
    _ENGINE = engine


def _call(cb, *args) -> None:
    if not (isinstance(cb, QJSValue) and cb.isCallable()):
        return
    if _ENGINE is None:
        log.error("callback dropped: no engine")
        return
    res = cb.call([_ENGINE.toScriptValue(a) for a in args])
    if res.isError():
        log.error("QML callback failed: %s", res.toString())


class Poster(QObject):
    """Marshals callables from any thread onto the UI thread."""

    _sig = Signal(object)

    def __init__(self):
        super().__init__()
        self._sig.connect(self._run, Qt.QueuedConnection)

    def post(self, fn) -> None:
        self._sig.emit(fn)

    @Slot(object)
    def _run(self, fn) -> None:
        fn()


class ResultsModel(QAbstractListModel):
    ROLES = {
        Qt.UserRole + 1: b"entryId", Qt.UserRole + 2: b"title", Qt.UserRole + 3: b"username",
        Qt.UserRole + 4: b"groupPath", Qt.UserRole + 5: b"host", Qt.UserRole + 6: b"matched",
        Qt.UserRole + 7: b"conflicted", Qt.UserRole + 8: b"hasPassword", Qt.UserRole + 9: b"hasOtp",
        Qt.UserRole + 10: b"tags", Qt.UserRole + 11: b"modified", Qt.UserRole + 12: b"attachments",
    }
    countChanged = Signal()

    def __init__(self, parent=None, incremental: bool = False):
        super().__init__(parent)
        self._rows: list[tuple] = []
        self._incremental = incremental

    def roleNames(self):
        return {k: QByteArray(v) for k, v in self.ROLES.items()}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        r = self._rows[index.row()]
        i = role - Qt.UserRole - 1
        if role == Qt.DisplayRole:
            return r[1]
        return r[i] if 0 <= i < len(r) else None

    def set_rows(self, rows: list[tuple]) -> None:
        if rows == self._rows:
            return
        if not self._incremental:
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            self.countChanged.emit()
            return
        # Update in place instead of resetting so the view reuses its delegates
        # (a reset destroys and recreates every visible row on each keystroke).
        old, n_old, n_new = self._rows, len(self._rows), len(rows)
        common = min(n_old, n_new)
        if n_new < n_old:
            self.beginRemoveRows(QModelIndex(), n_new, n_old - 1)
            self._rows = old[:n_new]
            self.endRemoveRows()
        first = next((i for i in range(common) if old[i] != rows[i]), None)
        if first is not None:
            last = next(i for i in range(common - 1, -1, -1) if old[i] != rows[i])
            self._rows = rows[:common] + self._rows[common:]
            self.dataChanged.emit(self.index(first), self.index(last))
        if n_new > n_old:
            self.beginInsertRows(QModelIndex(), n_old, n_new - 1)
            self._rows = rows
            self.endInsertRows()
        self._rows = rows
        if n_new != n_old:
            self.countChanged.emit()

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._rows)

    @Slot(int, result="QVariant")
    def get(self, row: int):
        if not (0 <= row < len(self._rows)):
            return None
        return {self.ROLES[k].decode(): v for k, v in zip(sorted(self.ROLES), self._rows[row])}


def _row(s, matched: str, conflicted: set, trash: bool = False) -> tuple:
    return (s.id, s.title or "(untitled)", s.username, s.origin_path if trash else s.group_path, url_host(s.url), matched, s.id in conflicted,
            s.has_password, s.has_otp, ", ".join(s.tags), s.modified, s.attachment_count)


_REASONS = {
    "both-modified": "Changed on both devices since the last sync",
    "no-ancestry": "Changed independently on both devices",
    "move-conflict": "Moved to different groups on each device",
    "edit-vs-delete": "Edited here, deleted on the other device",
    "delete-vs-edit": "Deleted here, edited on the other device",
    "meta": "Database setting changed on both devices",
}


def reason_text(kind: str, reason: str) -> str:
    text = _REASONS.get(reason, reason.replace("-", " ").capitalize())
    if kind == "group" and reason in ("both-modified", "no-ancestry"):
        text = text.replace("Changed", "Group changed")
    return text


def entry_to_map(d: EntryData) -> dict:
    return {
        "id": d.id or "", "title": d.title, "username": d.username, "password": d.password, "url": d.url,
        "notes": d.notes, "tags": ", ".join(d.tags), "groupId": d.group_id or "",
        "fields": [{"key": f.key, "value": f.value, "protected": f.protected} for f in d.fields],
        "expires": d.expires, "expiry": (d.expiry or 0) * 1000.0,
        "attachments": [{"name": n, "size": s} for n, s in d.attachments],
        "created": (d.created or 0) * 1000.0, "modified": d.modified or 0, "historyCount": d.history_count,
    }


def map_to_entry(m: dict) -> EntryData:
    def s(k):
        v = m.get(k, "")
        return v if isinstance(v, str) else ("" if v is None else str(v))
    tags = [t.strip() for t in s("tags").replace(";", ",").split(",") if t.strip()]
    fields = []
    for f in m.get("fields") or []:
        fields.append(CustomField(str(f.get("key", "")), str(f.get("value", "")), bool(f.get("protected", False))))
    exp = m.get("expiry") or 0
    return EntryData(
        id=s("id") or None, title=s("title"), username=s("username"), password=s("password"), url=s("url"),
        notes=s("notes"), tags=tags, fields=fields, group_id=s("groupId") or None, expires=bool(m.get("expires")),
        expiry=(float(exp) / 1000.0) if exp else None,
    )


def _spec_from(m, default: generator.PasswordSpec) -> generator.PasswordSpec:
    d = default.to_dict()
    if isinstance(m, QJSValue):
        m = m.toVariant()
    for k, v in dict(m or {}).items():
        if k not in d:
            continue
        cur = d[k]
        if isinstance(cur, bool):
            d[k] = bool(v)
        elif isinstance(cur, int):
            d[k] = int(v)
        else:
            d[k] = str(v)
    return generator.PasswordSpec.from_dict(d)


class Backend(QObject):
    sessionChanged = Signal()
    themeChanged = Signal()
    storageChanged = Signal()
    indexChanged = Signal()
    settingsChanged = Signal()
    busyChanged = Signal()
    notice = Signal(str, str)  # kind (info|success|warning|error), text
    launcherRequested = Signal(str, str)  # mode (search|new), text
    launcherHideRequested = Signal()
    managerRequested = Signal(str, str)  # page, entry id
    draftHandoff = Signal("QVariant")  # create-form draft -> manager editor
    copied = Signal(str, int)  # label, seconds
    quitRequested = Signal()

    def __init__(self, settings: config.Settings, poster: Poster, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.session_gaps: Callable[[], list[str]] = list  # set by the app once session watching starts
        self._poster = poster
        self.service = VaultService(settings, post=poster.post)
        self.service.listeners.append(self._on_service)
        self.clipboard = ClipboardOwner(seconds=settings.clipboard_seconds, schedule=self._schedule)
        self.clipboard.on_cleared = lambda reason: None
        self._theme = theme_mod.load(settings.theme, settings.omarchy_theme_dir)
        self.results = ResultsModel(self, incremental=True)
        self.entries = ResultsModel(self)
        self.trash = ResultsModel(self)
        self._query = ""
        self._busy = False
        self._history = history_status()
        self._list_group = ""
        self._list_query = ""
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._theme_dirty)
        self._watcher.directoryChanged.connect(self._theme_dirty)
        self._theme_timer = QTimer(self, singleShot=True, interval=200)
        self._theme_timer.timeout.connect(self.reloadTheme)
        self._watch_theme()
        self._vault_watcher = QFileSystemWatcher(self)
        self._vault_watcher.fileChanged.connect(lambda _p: self._vault_poll.start(250))
        self._vault_watcher.directoryChanged.connect(lambda _p: self._vault_poll.start(250))
        self._vault_poll = QTimer(self, singleShot=True)
        self._vault_poll.timeout.connect(self._poll_vault)
        self._vault_tick = QTimer(self, interval=5000)
        self._vault_tick.timeout.connect(self._poll_vault)
        self._vault_tick.start()
        self._watch_vault()
        self._idle = QTimer(self, singleShot=True)
        self._idle.timeout.connect(lambda: self.lock("inactivity"))

    # ------------------------------------------------------------ plumbing

    def _schedule(self, seconds, fn):
        t = QTimer(self, singleShot=True)
        t.timeout.connect(fn)
        t.start(int(seconds * 1000))

        def cancel():
            try:
                t.stop()
                t.deleteLater()
            except RuntimeError:
                pass
        return cancel

    def _on_service(self, what: str) -> None:
        if what == "session":
            self.sessionChanged.emit()
            if self.service.session != Session.UNLOCKED:
                self.results.set_rows([])
                self.entries.set_rows([])
                self.trash.set_rows([])
        elif what == "index":
            self._refresh_models()
            self.indexChanged.emit()
        elif what in ("storage", "error"):
            self.storageChanged.emit()
            if what == "error" and self.service.last_error:
                self.notice.emit("error", self.service.last_error.user_message)
        elif what == "external":
            self.storageChanged.emit()

    def _refresh_models(self) -> None:
        self.search(self._query)
        self.listEntries(self._list_group, self._list_query)
        snap = self.service.snapshot
        conflicted = self.service.conflicted_entry_ids()
        self.trash.set_rows([_row(s, "", conflicted, trash=True) for s in sorted(snap.trash, key=lambda s: -s.modified)] if snap else [])

    def _set_busy(self, v: bool) -> None:
        if v != self._busy:
            self._busy = v
            self.busyChanged.emit()

    def _activity(self) -> None:
        mins = self._settings.inactivity_timeout_minutes
        if mins > 0 and self.service.session == Session.UNLOCKED:
            self._idle.start(mins * 60 * 1000)
        else:
            self._idle.stop()

    def _watch_theme(self) -> None:
        paths = [p for p in theme_mod.watch_paths(self._settings.omarchy_theme_dir) if os.path.exists(p)]
        old = self._watcher.files() + self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        if paths:
            self._watcher.addPaths(paths)

    def _theme_dirty(self, _path) -> None:
        self._theme_timer.start()

    def _watch_vault(self) -> None:
        old = self._vault_watcher.files() + self._vault_watcher.directories()
        if old:
            self._vault_watcher.removePaths(old)
        p = self._settings.vault
        if p:
            paths = [str(x) for x in (p, p.parent) if x.exists()]
            if paths:
                self._vault_watcher.addPaths(paths)

    def _poll_vault(self) -> None:
        self.service.poll_external()
        p = self._settings.vault
        if p and p.exists() and str(p) not in self._vault_watcher.files():
            self._vault_watcher.addPath(str(p))  # atomic replaces drop the inotify watch

    # ---------------------------------------------------------- properties

    @Property(str, notify=sessionChanged)
    def session(self):
        return self.service.session.value

    @Property(bool, notify=sessionChanged)
    def unlocked(self):
        return self.service.session == Session.UNLOCKED

    @Property("QVariantMap", notify=themeChanged)
    def theme(self):
        return self._theme

    @Property(str, notify=storageChanged)
    def storageState(self):
        return self.service.storage.value

    @Property(int, notify=storageChanged)
    def conflictCount(self):
        return len(self.service.conflicts)

    @Property(bool, notify=storageChanged)
    def externalChangeWhileLocked(self):
        return self.service.external_change_while_locked

    @Property(str, notify=storageChanged)
    def lastError(self):
        e = self.service.last_error
        return e.user_message if e else ""

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=settingsChanged)
    def vaultPath(self):
        return self._settings.vault_path

    @Property(str, notify=settingsChanged)
    def vaultName(self):
        p = self._settings.vault
        return p.stem if p else ""

    @Property(str, notify=settingsChanged)
    def keyfilePath(self):
        return self._settings.keyfile_path

    @Property(bool, notify=settingsChanged)
    def vaultExists(self):
        p = self._settings.vault
        return bool(p and p.exists())

    @Property(int, notify=settingsChanged)
    def clipboardSeconds(self):
        return self._settings.clipboard_seconds

    @Property(str, constant=True)
    def clipboardHistoryWarning(self):
        return "" if self._history.confirmed else self._history.detail

    @Property(QObject, constant=True)
    def resultsModel(self):
        return self.results

    @Property(QObject, constant=True)
    def entriesModel(self):
        return self.entries

    @Property(QObject, constant=True)
    def trashModel(self):
        return self.trash

    @Property("QVariantList", notify=indexChanged)
    def groups(self):
        snap = self.service.snapshot
        if not snap:
            return []
        return [{"id": g.id, "name": g.name, "path": g.path, "depth": g.depth, "count": g.entry_count,
                 "isTrash": g.is_trash, "inTrash": g.in_trash, "parentId": g.parent_id or "", "notes": g.notes}
                for g in snap.groups]

    @Property("QVariantList", notify=indexChanged)
    def conflicts(self):
        return [{"id": c.id, "kind": c.kind, "reason": c.reason, "reasonText": reason_text(c.kind, c.reason), "title": c.title, "fields": list(c.fields),
                 "entryId": c.entry_id or ""} for c in self.service.conflicts]

    @Property("QVariantMap", notify=indexChanged)
    def formatInfo(self):
        snap = self.service.snapshot
        if not snap:
            return {}
        f = snap.format
        return {"version": f.version, "cipher": f.cipher, "kdf": f.kdf, "kdfDetail": f.kdf_detail,
                "compression": f.compression, "entries": f.entry_count, "groups": f.group_count}

    @Property(int, notify=indexChanged)
    def entryCount(self):
        snap = self.service.snapshot
        return len(snap.index) if snap else 0

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self):
        s = self._settings
        return {
            "vaultPath": s.vault_path, "keyfilePath": s.keyfile_path, "defaultGroup": s.default_group,
            "lockOnDesktopLock": s.lock_on_desktop_lock, "lockOnSleep": s.lock_on_sleep,
            "inactivityMinutes": s.inactivity_timeout_minutes, "clipboardSeconds": s.clipboard_seconds,
            "backupKeep": s.backup_keep, "backupIntervalMinutes": s.backup_min_interval_minutes,
            "theme": s.theme, "configPath": str(config.config_home() / "config.toml"),
            "statePath": str(s.vault_state_dir()), "warnings": [f"Config: {w}" for w in s.warnings] + self.session_gaps(),
        }

    @Property("QVariantMap", notify=settingsChanged)
    def generatorSpec(self):
        return self._settings.generator.to_dict()

    # ---------------------------------------------------------- search

    @Slot(str)
    def search(self, query: str) -> None:
        self._query = query
        snap = self.service.snapshot
        if not snap:
            self.results.set_rows([])
            return
        conflicted = self.service.conflicted_entry_ids()
        res = snap.index.search(query, usage=self.service.usage.scores(), limit=50)
        self.results.set_rows([_row(r.summary, r.matched, conflicted) for r in res])

    @Slot(str, str)
    def listEntries(self, group_id: str, query: str) -> None:
        """Manager list: entries in a group (recursive) optionally filtered by query."""
        self._list_group, self._list_query = group_id, query
        snap = self.service.snapshot
        if not snap:
            self.entries.set_rows([])
            return
        conflicted = self.service.conflicted_entry_ids()
        if query.strip():
            res = snap.index.search(query, limit=500)
            items = [(r.summary, r.matched) for r in res]
        else:
            items = [(s, "") for s in snap.index.all()]
            items.sort(key=lambda x: (x[0].title.casefold(), x[0].id))
        if group_id == "@conflicts":
            items = [x for x in items if x[0].id in conflicted]
        elif group_id:
            ids = self._descendant_groups(group_id)
            items = [x for x in items if x[0].group_id in ids]
        self.entries.set_rows([_row(s, m, conflicted) for s, m in items])

    def _descendant_groups(self, gid: str) -> set[str]:
        snap = self.service.snapshot
        children: dict[str, list[str]] = {}
        for g in snap.groups:
            children.setdefault(g.parent_id or "", []).append(g.id)
        out, stack = set(), [gid]
        while stack:
            x = stack.pop()
            if x in out:
                continue
            out.add(x)
            stack.extend(children.get(x, []))
        return out

    # ---------------------------------------------------------- lifecycle

    @Slot(str, str, "QVariant")
    def unlock(self, password: str, keyfile: str, cb) -> None:
        if self.service.session == Session.UNLOCKED:
            _call(cb, {"ok": True})
            return
        self._set_busy(True)
        kf = Path(keyfile).expanduser() if keyfile else None

        def done(err):
            self._set_busy(False)
            if err is None:
                self._activity()
                if self._settings.vault and not self._settings.vault_id:
                    pass
            _call(cb, _err(err))
        self.service.unlock(password or None, done, keyfile=kf)
        del password

    @Slot()
    def lockNow(self) -> None:
        self.lock("manual")

    def lock(self, reason: str = "manual") -> None:
        self.clipboard.clear("lock")
        self.service.lock(reason)
        self._idle.stop()
        self._query = ""
        self.sessionChanged.emit()

    @Slot()
    def quit(self) -> None:
        self.quitRequested.emit()

    def shutdown(self) -> None:
        self.clipboard.clear("quit")
        self.service.quit()

    @Slot()
    def reloadTheme(self) -> None:
        self._theme = theme_mod.load(self._settings.theme, self._settings.omarchy_theme_dir)
        self._watch_theme()
        self.themeChanged.emit()

    # ---------------------------------------------------------- copying

    @Slot(str, str, "QVariant")
    def copyField(self, entry_id: str, field: str, cb) -> None:
        """Copy a stored field. Fails visibly on stale/empty/locked/conflicted."""
        self._activity()
        label = {"Password": "Password", "UserName": "Username", "URL": "URL"}.get(field, field)

        def done(value, err):
            if err is not None:
                _call(cb, _err(err))
                return
            try:
                self.clipboard.copy(value)
            except errors.KeylaneError as exc:
                _call(cb, _err(exc))
                return
            finally:
                del value
            self.service.usage.record(entry_id)
            self.copied.emit(label, self._settings.clipboard_seconds)
            _call(cb, {"ok": True, "label": label, "seconds": self._settings.clipboard_seconds})
        self.service.get_field(entry_id, field, done)

    @Slot(str, str, "QVariant")
    def copyValue(self, value: str, label: str, cb) -> None:
        """Copy a value currently shown in an editor (draft or generator)."""
        self._activity()
        try:
            self.clipboard.copy(value)
        except errors.KeylaneError as exc:
            _call(cb, _err(exc))
            return
        finally:
            del value
        self.copied.emit(label or "Value", self._settings.clipboard_seconds)
        _call(cb, {"ok": True, "label": label, "seconds": self._settings.clipboard_seconds})

    @Slot(str, int, str, "QVariant")
    def copyHistoryField(self, entry_id: str, index: int, field: str, cb) -> None:
        def done(data, err):
            if err is not None:
                _call(cb, _err(err))
                return
            v = {"Password": data.password, "UserName": data.username, "URL": data.url}.get(field, "")
            if not v:
                _call(cb, _err(errors.EmptyField()))
                return
            self.copyValue(v, field, cb)
        self.service.read_history(entry_id, index, done)

    # ---------------------------------------------------------- generator

    @Slot("QVariant", result="QVariantMap")
    def generate(self, spec_map) -> dict:
        try:
            spec = _spec_from(spec_map, self._settings.generator)
            g = generator.generate(spec)
        except errors.KeylaneError as exc:
            return {"ok": False, "message": exc.user_message}
        except (TypeError, ValueError):
            return {"ok": False, "message": "Invalid generator settings."}
        return {"ok": True, "value": g.value, "bits": round(g.entropy_bits), "description": g.description}

    @Slot("QVariant", result="QVariantMap")
    def saveGeneratorSpec(self, spec_map) -> dict:
        try:
            spec = _spec_from(spec_map, self._settings.generator)
            generator.validate(spec)
        except errors.KeylaneError as exc:
            return {"ok": False, "message": exc.user_message}
        except (TypeError, ValueError):
            return {"ok": False, "message": "Invalid generator settings."}
        self._settings.generator = spec
        return self._persist_settings()

    # ---------------------------------------------------------- entries

    def _mut_cb(self, cb, *, extra=None):
        self._set_busy(True)

        def done(res, err):
            self._set_busy(False)
            out = _err(err)
            if err is None and extra:
                out.update(extra(res))
            _call(cb, out)
        return done

    @Slot("QVariant", bool, "QVariant")
    def createEntry(self, m, copy_after: bool, cb) -> None:
        self._activity()
        try:
            data = map_to_entry(dict(m.toVariant() if isinstance(m, QJSValue) else m))
        except (TypeError, ValueError):
            _call(cb, {"ok": False, "message": "Invalid entry."})
            return
        if not data.group_id and self._settings.default_group:
            data.group_id = self._settings.default_group
        password = data.password

        def done(new_id, err):
            self._set_busy(False)
            if err is not None:
                _call(cb, _err(err))
                return
            out = {"ok": True, "id": new_id}
            if copy_after:
                # Copy only after the save is durable (atomic replace + fsync done).
                if not password:
                    out.update({"copyError": errors.EmptyField().user_message})
                else:
                    try:
                        self.clipboard.copy(password)
                        self.service.usage.record(new_id)
                        self.copied.emit("Password", self._settings.clipboard_seconds)
                        out["copied"] = True
                    except errors.KeylaneError as exc:
                        out["copyError"] = exc.user_message
            _call(cb, out)
        self._set_busy(True)
        self.service.create_entry(data, done)

    @Slot(str, "QVariant")
    def readEntry(self, entry_id: str, cb) -> None:
        self._activity()
        self.service.read_entry(entry_id, lambda d, e: _call(cb, {"ok": True, "entry": entry_to_map(d)} if e is None else _err(e)))

    @Slot("QVariant", "QVariant")
    def updateEntry(self, m, cb) -> None:
        self._activity()
        mm = dict(m.toVariant() if isinstance(m, QJSValue) else m)
        data = map_to_entry(mm)
        expected = mm.get("modified") or None
        self.service.update_entry(data, self._mut_cb(cb), expected_modified=float(expected) if expected else None)

    @Slot(str, "QVariant")
    def recycle(self, item_id: str, cb) -> None:
        self.service.recycle(item_id, self._mut_cb(cb))

    @Slot(str, "QVariant")
    def restore(self, item_id: str, cb) -> None:
        self.service.restore(item_id, self._mut_cb(cb))

    @Slot(str, "QVariant")
    def deletePermanently(self, item_id: str, cb) -> None:
        self.service.delete_permanently(item_id, self._mut_cb(cb))

    @Slot("QVariant")
    def emptyTrash(self, cb) -> None:
        self.service.empty_trash(self._mut_cb(cb, extra=lambda n: {"count": n or 0}))

    @Slot(str, str, "QVariant")
    def moveEntry(self, entry_id: str, group_id: str, cb) -> None:
        self.service.move_entry(entry_id, group_id, self._mut_cb(cb))

    @Slot(str, str, "QVariant")
    def createGroup(self, name: str, parent_id: str, cb) -> None:
        if not name.strip():
            _call(cb, {"ok": False, "message": "A group needs a name."})
            return
        self.service.create_group(name.strip(), parent_id or None, self._mut_cb(cb, extra=lambda gid: {"id": gid}))

    @Slot(str, str, "QVariant")
    def renameGroup(self, group_id: str, name: str, cb) -> None:
        if not name.strip():
            _call(cb, {"ok": False, "message": "A group needs a name."})
            return
        self.service.rename_group(group_id, name.strip(), self._mut_cb(cb))

    @Slot(str, str, "QVariant")
    def moveGroup(self, group_id: str, parent_id: str, cb) -> None:
        self.service.move_group(group_id, parent_id, self._mut_cb(cb))

    @Slot(str, "QVariant")
    def history(self, entry_id: str, cb) -> None:
        def done(items, err):
            if err is not None:
                _call(cb, _err(err))
                return
            _call(cb, {"ok": True, "items": [{"index": h.index, "modified": h.modified * 1000.0, "title": h.title,
                                               "username": h.username, "changed": ", ".join(h.changed_fields)} for h in items]})
        self.service.history(entry_id, done)

    @Slot(str, int, "QVariant")
    def readHistory(self, entry_id: str, index: int, cb) -> None:
        self.service.read_history(entry_id, index, lambda d, e: _call(cb, {"ok": True, "entry": entry_to_map(d)} if e is None else _err(e)))

    @Slot(str, int, "QVariant")
    def restoreHistory(self, entry_id: str, index: int, cb) -> None:
        self.service.restore_history(entry_id, index, self._mut_cb(cb))

    # ---------------------------------------------------------- conflicts / merge

    @Slot(str, "QVariant")
    def conflictDetail(self, cid: str, cb) -> None:
        def done(d, err):
            if err is not None:
                _call(cb, _err(err))
                return
            def side(x):
                if x is None:
                    return None
                return entry_to_map(x) if isinstance(x, EntryData) else x
            _call(cb, {"ok": True, "kind": d["kind"], "reason": d["reason"], "reasonText": reason_text(d["kind"], d["reason"]),
                       "title": d["title"], "fields": d["fields"],
                       "local": side(d["local"]), "remote": side(d["remote"])})
        self.service.conflict_detail(cid, done)

    @Slot(str, str, "QVariant", "QVariant")
    def resolveConflict(self, cid: str, choice: str, custom, cb) -> None:
        if choice not in ("local", "remote", "both", "custom"):
            _call(cb, {"ok": False, "message": "Unknown choice."})
            return
        data = None
        if choice == "custom":
            data = map_to_entry(dict(custom.toVariant() if isinstance(custom, QJSValue) else custom))
        self._set_busy(True)

        def done(_res, err):
            self._set_busy(False)
            _call(cb, _err(err))
        self.service.resolve_conflict(cid, choice, done, custom=data)

    @Slot(str, str, str, "QVariant")
    def mergeFile(self, path: str, password: str, keyfile: str, cb) -> None:
        p = Path(_local_path(path)).expanduser()
        if not p.is_file():
            _call(cb, {"ok": False, "message": "That file does not exist."})
            return
        kf = Path(_local_path(keyfile)).expanduser() if keyfile else None
        self.service.merge_file(p, password or None, kf, self._mut_cb(cb, extra=lambda r: dict(r or {})))
        del password

    @Slot(str, str, str, str, "QVariant")
    def changeCredentials(self, cur_pw: str, cur_kf: str, new_pw: str, new_kf: str, cb) -> None:
        if not new_pw and not new_kf:
            _call(cb, {"ok": False, "message": "Choose a new password, a key file, or both."})
            return
        self.service.change_credentials(cur_pw or None, Path(_local_path(cur_kf)).expanduser() if cur_kf else None,
                                        new_pw or None, Path(_local_path(new_kf)).expanduser() if new_kf else None,
                                        self._mut_cb(cb))
        if new_kf:
            self._settings.keyfile_path = _local_path(new_kf)
            self._persist_settings()
        del cur_pw, new_pw

    # ---------------------------------------------------------- vault setup / settings

    @Slot(str, str, result="QVariantMap")
    def useVault(self, path: str, keyfile: str) -> dict:
        path, keyfile = _local_path(path), _local_path(keyfile)
        if not path:
            return {"ok": False, "message": "Choose a vault file."}
        p = Path(path).expanduser()
        if not p.is_file():
            return {"ok": False, "message": "That vault file does not exist."}
        if keyfile and not Path(keyfile).expanduser().is_file():
            return {"ok": False, "message": "That key file does not exist."}
        s = self._settings
        changed = s.vault_path != str(p)
        s.vault_path, s.keyfile_path = str(p), keyfile
        if changed:
            import uuid

            s.vault_id = uuid.uuid4().hex
            s.default_group = ""
        self.service.reconfigure(s)
        self._watch_vault()
        res = self._persist_settings()
        self.sessionChanged.emit()
        return res

    @Slot(str, str, str, "QVariant")
    def createVault(self, path: str, password: str, keyfile: str, cb) -> None:
        path, keyfile = _local_path(path), _local_path(keyfile)
        if not path:
            _call(cb, {"ok": False, "message": "Choose where to save the vault."})
            return
        p = Path(path).expanduser()
        if p.suffix.lower() != ".kdbx":
            p = p.with_suffix(".kdbx")
        if not password and not keyfile:
            _call(cb, {"ok": False, "message": "Set a password, a key file, or both."})
            return
        self._set_busy(True)

        def done(err):
            self._set_busy(False)
            if err is None:
                self.useVault(str(p), keyfile)
            _call(cb, _err(err) if err else {"ok": True, "path": str(p)})
        self.service.create_vault(p, password or None, Path(keyfile).expanduser() if keyfile else None, p.stem, done)
        del password

    @Slot("QVariant", result="QVariantMap")
    def saveSettings(self, m) -> dict:
        m = dict(m.toVariant() if isinstance(m, QJSValue) else m)
        s = self._settings
        try:
            if "lockOnDesktopLock" in m:
                s.lock_on_desktop_lock = bool(m["lockOnDesktopLock"])
            if "lockOnSleep" in m:
                s.lock_on_sleep = bool(m["lockOnSleep"])
            if "inactivityMinutes" in m:
                v = int(m["inactivityMinutes"])
                if not 0 <= v <= 1440:
                    return {"ok": False, "message": "Inactivity timeout must be 0 (off) to 1440 minutes."}
                s.inactivity_timeout_minutes = v
            if "clipboardSeconds" in m:
                v = int(m["clipboardSeconds"])
                if not 5 <= v <= 600:
                    return {"ok": False, "message": "Clipboard lifetime must be between 5 and 600 seconds."}
                s.clipboard_seconds = v
                self.clipboard.seconds = v
            if "backupKeep" in m:
                v = int(m["backupKeep"])
                if not 1 <= v <= 500:
                    return {"ok": False, "message": "Keep between 1 and 500 backups."}
                s.backup_keep = v
            if "theme" in m:
                if m["theme"] not in ("auto", "omarchy", "dark", "light"):
                    return {"ok": False, "message": "Unknown theme."}
                s.theme = m["theme"]
            if "defaultGroup" in m:
                s.default_group = str(m["defaultGroup"] or "")
        except (TypeError, ValueError):
            return {"ok": False, "message": "Invalid value."}
        res = self._persist_settings()
        self.reloadTheme()
        self._activity()
        self.settingsApplied.emit()
        return res

    settingsApplied = Signal()

    def _persist_settings(self) -> dict:
        try:
            config.save(self._settings)
        except errors.KeylaneError as exc:
            self.settingsChanged.emit()
            return _err(exc)
        self.settingsChanged.emit()
        return {"ok": True}

    @Slot(result="QVariantList")
    def backups(self) -> list:
        out = []
        for p in self.service.backups():
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"name": p.name, "path": str(p), "size": st.st_size, "time": st.st_mtime * 1000.0})
        return out

    @Slot(str, result=str)
    def localPath(self, url: str) -> str:
        return _local_path(url)

    # ---------------------------------------------------------- windows

    @Slot(str)
    def openManager(self, entry_id: str = "") -> None:
        self.managerRequested.emit("entries", entry_id)

    @Slot(str)
    def openManagerPage(self, page: str) -> None:
        self.managerRequested.emit(page, "")

    @Slot("QVariant")
    def handoffDraft(self, m) -> None:
        draft = m.toVariant() if isinstance(m, QJSValue) else m
        # The manager may not exist yet: create/present it before delivering the draft.
        self.managerRequested.emit("draft", "")
        self.draftHandoff.emit(draft)

    @Slot()
    def hideLauncher(self) -> None:
        self.launcherHideRequested.emit()

    def status(self) -> dict:
        return {"running": True, "session": self.service.session.value, "storage": self.service.storage.value,
                "conflicts": len(self.service.conflicts), "vault_configured": bool(self._settings.vault_path)}


def _local_path(url: str) -> str:
    if not url:
        return ""
    if url.startswith("file://"):
        from urllib.parse import unquote, urlparse

        return unquote(urlparse(url).path)
    return url
