"""The resident vault owner.

All access to the decrypted document happens on one worker thread, so
mutations, saves and reconciliation are naturally serialized. The UI thread
only ever holds immutable, non-secret summaries (the search index) plus
short-lived secret values it explicitly requested.

Every job carries the *lock epoch* at submission time. Locking increments the
epoch; results from older epochs are discarded (and their secrets dropped)
instead of being delivered, so a late callback can never repopulate state.
"""

from __future__ import annotations

import copy
import enum
import gc
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import errors, merge, storage
from .config import Settings
from .kdbx import CompositeKey, EntryData, VaultDocument, make_composite
from .search import SearchIndex, UsageTracker

log = logging.getLogger("keylane.vault")


def _settle_gc() -> None:
    """Collect now (off the typing path) and freeze the survivors.

    A 10k-entry vault keeps a few hundred thousand objects alive; without this
    every full collection rescans them, stalling the GUI thread for ~20 ms at
    arbitrary keystrokes. Unfreezing first lets the previous generation of
    frozen objects (e.g. the replaced snapshot) be collected normally.
    """
    gc.unfreeze()
    gc.collect()
    gc.freeze()


class Session(str, enum.Enum):
    NO_VAULT = "no-vault"
    LOCKED = "locked"
    UNLOCKING = "unlocking"
    UNLOCKED = "unlocked"
    QUITTING = "quitting"


class Storage(str, enum.Enum):
    IDLE = "idle"
    SAVING = "saving"
    RECONCILING = "reconciling"
    CONFLICT = "conflict"
    ERROR = "error"


@dataclass(frozen=True)
class ConflictInfo:
    """Non-secret description of a pending conflict."""

    id: str
    kind: str
    reason: str
    title: str
    fields: tuple[str, ...]
    entry_id: str | None


@dataclass
class Snapshot:
    """Everything the UI thread needs after a load/save. Non-secret."""

    index: SearchIndex
    groups: list
    trash: list
    format: Any
    conflicts: list[ConflictInfo] = field(default_factory=list)


class _Race(Exception):
    pass


class Worker:
    """Single background thread executing jobs in order."""

    def __init__(self, name: str = "keylane-vault"):
        self._q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._run, name=name, daemon=True)
        self._t.start()

    def submit(self, fn: Callable[[], Any], done: Callable[[Any, BaseException | None], None]) -> None:
        self._q.put((fn, done))

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, done = item
            try:
                res, err = fn(), None
            except BaseException as exc:  # delivered to the caller, never logged with content
                res, err = None, exc
            try:
                done(res, err)
            except Exception:
                log.exception("result delivery failed")

    def stop(self) -> None:
        self._q.put(None)


class InlineWorker:
    """Synchronous worker for tests."""

    def submit(self, fn, done):
        try:
            res, err = fn(), None
        except BaseException as exc:
            res, err = None, exc
        done(res, err)

    def stop(self):
        pass


def _as_error(exc: BaseException) -> errors.KeylaneError:
    if isinstance(exc, errors.KeylaneError):
        return exc
    if isinstance(exc, OSError):
        return errors.classify_os_error(exc)
    log.error("unexpected internal error category=%s", type(exc).__name__)
    return errors.KeylaneError(detail=type(exc).__name__)


class VaultService:
    """Owns vault lifecycle. Public methods are called on the UI thread.

    ``post(fn)`` must schedule ``fn`` on the UI thread.
    """

    def __init__(self, settings: Settings, *, post: Callable[[Callable[[], None]], None], worker=None):
        self.settings = settings
        self._post = post
        self._worker = worker or Worker()
        self._epoch = 0
        # UI-thread state
        self.session = Session.LOCKED if settings.vault else Session.NO_VAULT
        self.storage = Storage.IDLE
        self.snapshot: Snapshot | None = None
        self.usage = UsageTracker()
        self.last_error: errors.KeylaneError | None = None
        self.external_change_while_locked = False
        self.listeners: list[Callable[[str], None]] = []
        self._last_stat = storage.stat_key(settings.vault) if settings.vault else None
        # worker-thread state
        self._doc: VaultDocument | None = None
        self._base: merge.Side | None = None
        self._disk: storage.FileIdentity | None = None
        self._pending: merge.MergeResult | None = None
        self._pending_base: merge.Side | None = None

    # ------------------------------------------------------------------ utils

    def _emit(self, what: str) -> None:
        for cb in list(self.listeners):
            try:
                cb(what)
            except Exception:
                log.exception("listener failed")

    def _submit(self, fn: Callable[[], Any], cb: Callable[[Any, errors.KeylaneError | None], None] | None,
                *, require_unlocked: bool = True) -> None:
        epoch = self._epoch
        if require_unlocked and self.session != Session.UNLOCKED:
            if cb:
                cb(None, errors.VaultLocked())
            return

        def job():
            if epoch != self._epoch:
                raise errors.VaultLocked()
            return fn()

        def done(res, err):
            def deliver():
                if epoch != self._epoch:
                    return  # stale: drop result (may contain secrets)
                if cb:
                    cb(res, _as_error(err) if err else None)
            self._post(deliver)

        self._worker.submit(job, done)

    def _check_epoch(self, epoch: int) -> None:
        if epoch != self._epoch:
            raise errors.VaultLocked()

    @property
    def path(self) -> Path | None:
        return self.settings.vault

    @property
    def conflicts(self) -> list[ConflictInfo]:
        return self.snapshot.conflicts if self.snapshot else []

    def conflicted_entry_ids(self) -> set[str]:
        return {c.entry_id for c in self.conflicts if c.entry_id}

    # ------------------------------------------------------------- lifecycle

    def unlock(self, password: str | None, cb: Callable[[errors.KeylaneError | None], None], *, keyfile: Path | None = None) -> None:
        if self.session == Session.UNLOCKED:
            cb(None)
            return
        if self.session == Session.UNLOCKING:
            cb(errors.InvalidInput("Already unlocking."))
            return
        if self.session == Session.QUITTING:
            cb(errors.VaultLocked())
            return
        if not self.path:
            cb(errors.VaultNotConfigured())
            return
        kf = keyfile or self.settings.keyfile
        path = self.path
        epoch = self._epoch
        self.session = Session.UNLOCKING
        self._emit("session")
        box = [password]
        del password

        def job():
            try:
                pw = box.pop()
                composite = make_composite(pw, kf)
                del pw
            except errors.KeylaneError:
                raise
            snap = storage.read_stable(path)
            doc = VaultDocument.open_bytes(snap.data, composite)
            self._check_epoch(epoch)
            self._doc = doc
            self._disk = snap.identity
            self._base = self._side()
            self._pending = None
            self._recover_from_baseline(composite, snap, epoch)
            return self._make_snapshot()

        def done(res, err):
            box.clear()

            def deliver():
                if epoch != self._epoch:
                    return
                if err:
                    self._worker.submit(self._wipe_job, lambda *_: None)
                    self.session = Session.LOCKED
                    e = _as_error(err)
                    self.last_error = e
                    self._emit("session")
                    cb(e)
                    return
                self.session = Session.UNLOCKED
                self.external_change_while_locked = False
                self._last_stat = storage.stat_key(path)
                self._apply_snapshot(res)
                self._emit("session")
                cb(None)
            self._post(deliver)

        self._worker.submit(job, done)

    def lock(self, reason: str = "manual") -> None:
        """Synchronously invalidate all UI-visible state; wipe happens on the worker."""
        if self.session in (Session.NO_VAULT,):
            return
        self._epoch += 1
        was = self.session
        if self.session != Session.QUITTING:
            self.session = Session.LOCKED
        self.snapshot = None
        self.usage.clear()
        self.storage = Storage.IDLE
        self._worker.submit(self._wipe_job, lambda *_: None)
        if was != Session.LOCKED:
            log.info("vault locked reason=%s", reason)
        self._emit("session")
        self._emit("index")

    def _wipe_job(self) -> None:
        doc, self._doc = self._doc, None
        if doc is not None:
            doc.wipe()
            doc.composite.wipe()
        self._base = None
        self._pending = None
        self._pending_base = None
        gc.unfreeze()  # frozen objects are never collected; release them on lock
        gc.collect()

    def quit(self) -> None:
        self.lock("quit")
        self.session = Session.QUITTING
        done = threading.Event()
        self._worker.submit(lambda: None, lambda *_: done.set())
        done.wait(5)
        self._worker.stop()

    def reconfigure(self, settings: Settings) -> None:
        if settings.vault_path != self.settings.vault_path or settings.keyfile_path != self.settings.keyfile_path:
            self.lock("vault-changed")
            self.session = Session.LOCKED if settings.vault else Session.NO_VAULT
        self.settings = settings
        self._last_stat = storage.stat_key(settings.vault) if settings.vault else None
        self._emit("session")

    def create_vault(self, path: Path, password: str | None, keyfile: Path | None, name: str,
                     cb: Callable[[errors.KeylaneError | None], None]) -> None:
        path = Path(path).expanduser()
        epoch = self._epoch
        box = [password]
        del password

        def job():
            if path.exists():
                raise errors.InvalidInput("A file already exists at that location. Choose another name or open it instead.")
            if not path.parent.is_dir():
                raise errors.LocationUnavailable(detail="vault-dir-missing")
            composite = make_composite(box.pop(), keyfile)
            doc = VaultDocument.create(composite, name=name or path.stem)
            data = doc.to_bytes()
            doc.mark_saved(doc.verify_bytes(data))
            storage.atomic_replace(path, data, mode=0o600)
            doc.wipe()
            composite.wipe()

        def done(res, err):
            box.clear()
            def deliver():
                if epoch == self._epoch:
                    cb(_as_error(err) if err else None)
            self._post(deliver)

        self._worker.submit(job, done)

    # ------------------------------------------------------ worker internals

    def _side(self) -> merge.Side:
        return merge.Side(self._doc.clone_root(), list(self._doc.binaries()))

    def _baseline_path(self) -> Path:
        return self.settings.vault_state_dir() / "baseline.kdbx"

    def _backups(self) -> storage.BackupStore:
        return storage.BackupStore(self.settings.vault_state_dir() / "backups", keep=self.settings.backup_keep,
                                   min_interval_s=self.settings.backup_min_interval_minutes * 60)

    def _write_baseline(self, data: bytes) -> None:
        try:
            storage.write_private(self._baseline_path(), data)
        except errors.KeylaneError as exc:
            log.warning("baseline not written category=%s", exc.code)

    def _recover_from_baseline(self, composite: CompositeKey, snap: storage.Snapshot, epoch: int) -> None:
        """At unlock: reconcile writes that may never have reached the sync service."""
        bpath = self._baseline_path()
        try:
            bdata = bpath.read_bytes()
        except FileNotFoundError:
            self._write_baseline(snap.data)
            return
        except OSError:
            return
        import hashlib

        if hashlib.sha256(bdata).hexdigest() == snap.identity.sha256:
            return
        try:
            bdoc = VaultDocument.open_bytes(bdata, composite)
        except errors.KeylaneError as exc:
            # Different credentials or damaged baseline: keep it as a recovery backup.
            self._backups().add(bdata, reason="baseline-unreadable", force=True)
            log.info("baseline unavailable category=%s", exc.code)
            self._write_baseline(snap.data)
            return
        self._check_epoch(epoch)
        local = merge.Side(bdoc.clone_root(), list(bdoc.binaries()))
        bdoc.wipe()
        remote = self._side()
        if merge.descends_from(local, remote):
            self._write_baseline(snap.data)
            return
        log.info("vault on disk lacks earlier local writes; reconciling")
        result = merge.merge(None, local, remote)
        self._integrate(result, remote_base=remote, epoch=epoch, remote_data=snap.data)

    def _integrate(self, result: merge.MergeResult, *, remote_base: merge.Side | None, epoch: int, remote_data: bytes | None) -> None:
        """Adopt a merge result: block on conflicts, otherwise save if needed."""
        self._doc.adopt(result.root, result.binaries)
        if result.conflicts:
            self._pending = result
            # Keep the pre-conflict base so later merges still see both sides as changed.
            self._pending_base = self._pending_base or self._base
            return
        self._pending = None
        self._pending_base = None
        if result.changed_remote:
            if remote_data is not None:
                self._backups().add(remote_data, reason="before-merge", force=True)
            self._write(epoch, reason="merge")
        else:
            self._base = self._side()
            if remote_data is not None:
                self._write_baseline(remote_data)

    def _reconcile_if_needed(self, epoch: int) -> bool:
        """Merge an external change into the document. Returns True if one was found."""
        cur = storage.current_identity(self.path)
        if cur is None:
            raise errors.VaultMissing()
        if self._disk is not None and cur.sha256 == self._disk.sha256:
            return False
        snap = storage.read_stable(self.path)
        try:
            rdoc = VaultDocument.open_bytes(snap.data, self._doc.composite)
        except errors.CorruptVault:
            # Possibly an incomplete sync write; retry once after settling.
            time.sleep(0.5)
            snap = storage.read_stable(self.path)
            rdoc = VaultDocument.open_bytes(snap.data, self._doc.composite)
        self._check_epoch(epoch)
        remote = merge.Side(rdoc.clone_root(), list(rdoc.binaries()))
        rdoc.wipe()
        local = self._side()
        base = self._pending_base or self._base
        if base is not None and merge.descends_from(base, remote):
            result = merge.merge(base, local, remote)
        else:
            result = merge.merge(None, local, remote)
        self._disk = snap.identity
        log.info("external change reconciled conflicts=%d", len(result.conflicts))
        self._integrate(result, remote_base=remote, epoch=epoch, remote_data=snap.data)
        return True

    def _write(self, epoch: int, *, reason: str) -> None:
        doc = self._doc
        data = doc.to_bytes()
        tk = doc.verify_bytes(data)
        self._check_epoch(epoch)
        path = self.path
        try:
            with open(path, "rb") as f:
                current = f.read()
        except FileNotFoundError:
            current = None
        except OSError as exc:
            raise errors.classify_os_error(exc) from None
        import hashlib

        if current is not None and self._disk is not None and hashlib.sha256(current).hexdigest() != self._disk.sha256:
            raise _Race()
        if current:
            external = self._disk is None
            self._backups().add(current, reason=reason, force=external)
        ident = storage.atomic_replace(path, data)
        doc.mark_saved(tk)
        self._disk = ident
        self._base = self._side()
        self._write_baseline(data)

    def _mutate(self, epoch: int, fn: Callable[[VaultDocument], Any], *, reason: str = "save"):
        if self._pending is not None:
            raise errors.ConflictPending()
        for _attempt in range(3):
            self._reconcile_if_needed(epoch)
            if self._pending is not None:
                raise errors.ConflictPending()
            snapshot_root = self._doc.clone_root()
            snapshot_pool = list(self._doc.binaries())
            try:
                result = fn(self._doc)
                if result is _NOOP:
                    return None
                if self._pending is not None:
                    return result  # conflicts created: nothing is written until resolved
                self._write(epoch, reason=reason)
                return result
            except _Race:
                self._doc.adopt(snapshot_root, snapshot_pool)
                time.sleep(0.2)
                continue
            except BaseException:
                self._doc.adopt(snapshot_root, snapshot_pool)
                raise
        raise errors.ExternalChangeRace()

    def _make_snapshot(self) -> Snapshot:
        doc = self._doc
        summaries = doc.summaries(include_trash=True)
        prev = self.snapshot.index if self.snapshot is not None else None
        index = SearchIndex([s for s in summaries if not s.in_trash], previous=prev)
        trash = [s for s in summaries if s.in_trash]
        conflicts = []
        if self._pending is not None:
            for c in self._pending.conflicts:
                eid = None
                if c.kind == "entry" and c.uuid:
                    from .kdbx import b64_to_hex
                    eid = b64_to_hex(c.uuid)
                conflicts.append(ConflictInfo(c.id, c.kind, c.reason, c.title, tuple(c.fields), eid))
        snap = Snapshot(index=index, groups=doc.groups(), trash=trash, format=doc.format_info(), conflicts=conflicts)
        _settle_gc()
        return snap

    def _apply_snapshot(self, snap: Snapshot) -> None:
        self.snapshot = snap
        self.storage = Storage.CONFLICT if snap.conflicts else Storage.IDLE
        self._emit("index")
        self._emit("storage")

    # ------------------------------------------------------------ operations

    def _mutation(self, fn: Callable[[VaultDocument], Any], cb, *, reason="save") -> None:
        epoch = self._epoch
        self.storage = Storage.SAVING
        self._emit("storage")

        def job():
            try:
                res = self._mutate(epoch, fn, reason=reason)
            finally:
                snap = self._make_snapshot() if self._doc is not None and epoch == self._epoch else None
            return res, snap

        def done(value, err):
            def deliver():
                if epoch != self._epoch:
                    return
                snap = value[1] if value else None
                if snap is not None:
                    self._apply_snapshot(snap)
                else:
                    self.storage = Storage.IDLE
                    self._emit("storage")
                e = _as_error(err) if err else None
                if e is not None:
                    self.last_error = e
                    if isinstance(e, errors.ConflictPending):
                        self.refresh()
                    else:
                        self.storage = Storage.ERROR if self.storage != Storage.CONFLICT else self.storage
                        self._emit("storage")
                if cb:
                    cb(value[0] if value else None, e)
            self._post(deliver)

        if self.session != Session.UNLOCKED:
            self.storage = Storage.IDLE
            if cb:
                cb(None, errors.VaultLocked())
            return
        self._worker.submit(job, done)

    def refresh(self, cb=None) -> None:
        """Reload snapshot (e.g. after a conflict was detected)."""
        self._submit(self._make_snapshot, lambda snap, err: (self._apply_snapshot(snap) if snap else None, cb and cb(err)))

    def create_entry(self, data: EntryData, cb) -> None:
        self._mutation(lambda doc: doc.create_entry(data), cb, reason="create")

    def update_entry(self, data: EntryData, cb, *, expected_modified: float | None = None) -> None:
        def fn(doc):
            changed = doc.update_entry(data, expected_modified=expected_modified)
            return data.id if changed else _NOOP
        self._mutation(fn, cb, reason="edit")

    def move_entry(self, entry_id: str, group_id: str, cb) -> None:
        self._mutation(lambda doc: doc.move_entry(entry_id, group_id), cb, reason="move")

    def recycle(self, item_id: str, cb) -> None:
        self._mutation(lambda doc: doc.recycle(item_id), cb, reason="trash")

    def restore(self, item_id: str, cb) -> None:
        self._mutation(lambda doc: doc.restore(item_id), cb, reason="restore")

    def delete_permanently(self, item_id: str, cb) -> None:
        self._mutation(lambda doc: doc.delete_permanently(item_id), cb, reason="delete")

    def empty_trash(self, cb) -> None:
        self._mutation(lambda doc: doc.empty_trash(), cb, reason="empty-trash")

    def create_group(self, name: str, parent_id: str | None, cb) -> None:
        self._mutation(lambda doc: doc.create_group(name, parent_id), cb, reason="group")

    def rename_group(self, group_id: str, name: str, cb, notes: str | None = None) -> None:
        self._mutation(lambda doc: doc.rename_group(group_id, name, notes), cb, reason="group")

    def move_group(self, group_id: str, parent_id: str, cb) -> None:
        self._mutation(lambda doc: doc.move_group(group_id, parent_id), cb, reason="group")

    def restore_history(self, entry_id: str, index: int, cb) -> None:
        self._mutation(lambda doc: doc.restore_history(entry_id, index), cb, reason="history")

    def change_credentials(self, current_password: str | None, current_keyfile: Path | None,
                           new_password: str | None, new_keyfile: Path | None, cb) -> None:
        box = [current_password, new_password]
        del current_password, new_password

        def fn(doc):
            cur = make_composite(box[0], current_keyfile)
            if cur.raw != doc.composite.raw:
                cur.wipe()
                raise errors.WrongCredentials("The current password or key file is not correct.")
            cur.wipe()
            new = make_composite(box[1], new_keyfile)
            box.clear()
            doc.change_credentials(new)
            return True
        self._mutation(fn, cb, reason="credentials")

    def read_entry(self, entry_id: str, cb) -> None:
        self._submit(lambda: self._doc.read_entry(entry_id), cb)

    def get_field(self, entry_id: str, field_key: str, cb, *, allow_conflicted: bool = False) -> None:
        if not allow_conflicted and entry_id in self.conflicted_entry_ids():
            cb(None, errors.ConflictPending("This entry has a sync conflict. Choose a version before copying."))
            return
        self._submit(lambda: self._doc.get_field(entry_id, field_key), cb)

    def history(self, entry_id: str, cb) -> None:
        self._submit(lambda: self._doc.history(entry_id), cb)

    def read_history(self, entry_id: str, index: int, cb) -> None:
        self._submit(lambda: self._doc.read_history(entry_id, index), cb)

    def backups(self) -> list[Path]:
        return self._backups().list()

    # ---------------------------------------------------------- reconciliation

    def poll_external(self) -> None:
        """Cheap stat check, called periodically and on watcher events."""
        if not self.path:
            return
        key = storage.stat_key(self.path)
        if key == self._last_stat:
            return
        self._last_stat = key
        if self.session in (Session.LOCKED, Session.UNLOCKING):
            self.external_change_while_locked = True
            self._emit("external")
            return
        if self.session != Session.UNLOCKED:
            return
        epoch = self._epoch
        self.storage = Storage.RECONCILING
        self._emit("storage")

        def job():
            try:
                self._reconcile_if_needed(epoch)
            finally:
                snap = self._make_snapshot() if self._doc is not None and epoch == self._epoch else None
            return snap

        def done(snap, err):
            def deliver():
                if epoch != self._epoch:
                    return
                if snap is not None:
                    self._apply_snapshot(snap)
                if err is not None:
                    e = _as_error(err)
                    self.last_error = e
                    self.storage = Storage.ERROR
                    self._emit("storage")
                    self._emit("error")
            self._post(deliver)

        self._worker.submit(job, done)

    def merge_file(self, other: Path, password: str | None, keyfile: Path | None, cb) -> None:
        """Merge another KDBX (e.g. a sync conflict copy). Never modifies ``other``."""
        box = [password]
        del password
        epoch = self._epoch

        def fn(doc):
            if Path(other).resolve() == Path(self.path).resolve():
                raise errors.InvalidInput("That is the active vault itself.")
            snap = storage.read_stable(Path(other))
            pw = box.pop() if box else None
            composite = make_composite(pw, keyfile) if (pw is not None or keyfile) else doc.composite
            try:
                odoc = VaultDocument.open_bytes(snap.data, composite)
            finally:
                if composite is not doc.composite:
                    composite.wipe()
            remote = merge.Side(odoc.clone_root(), list(odoc.binaries()))
            odoc.wipe()
            local = self._side()
            base = self._base
            if base is not None and merge.descends_from(base, remote):
                result = merge.merge(base, local, remote)
            else:
                result = merge.merge(None, local, remote)
            doc.adopt(result.root, result.binaries)
            if result.conflicts:
                self._pending = result
                self._pending_base = self._base
                return {"conflicts": len(result.conflicts), "changed": result.changed_local}
            if not result.changed_local:
                return _NOOP
            return {"conflicts": 0, "changed": True}

        def cb2(res, err):
            box.clear()
            cb(res if res is not None else {"conflicts": 0, "changed": False}, err)

        self._mutation(fn, cb2, reason="merge-file")
        _ = epoch

    def conflict_detail(self, conflict_id: str, cb) -> None:
        def fn():
            if self._pending is None:
                raise errors.StaleEntry("That conflict is no longer pending.")
            c = next((c for c in self._pending.conflicts if c.id == conflict_id), None)
            if c is None:
                raise errors.StaleEntry("That conflict is no longer pending.")
            return {
                "local": self._element_data(c.local, c.local_parent) if c.local is not None else None,
                "remote": self._element_data(c.remote, c.remote_parent) if c.remote is not None else None,
                "kind": c.kind, "reason": c.reason, "fields": list(c.fields), "title": c.title,
            }
        self._submit(fn, cb)

    def _element_data(self, el, parent_b64: str | None = None):
        if el.tag == "Entry":
            return self._doc._entry_data(el, parent_b64)
        if el.tag == "Group":
            return {"name": el.findtext("Name") or "", "notes": el.findtext("Notes") or ""}
        return {"text": "(database setting)"}

    def resolve_conflict(self, conflict_id: str, choice: str, cb, *, custom: EntryData | None = None) -> None:
        epoch = self._epoch

        def job():
            if self._pending is None:
                raise errors.StaleEntry("That conflict is no longer pending.")
            c = next((c for c in self._pending.conflicts if c.id == conflict_id), None)
            if c is None:
                raise errors.StaleEntry("That conflict is no longer pending.")
            custom_el = None
            if choice == "custom":
                if custom is None or c.local is None:
                    raise errors.InvalidInput("Nothing to combine.")
                custom_el = copy.deepcopy(c.local)
                h = custom_el.find("History")
                if h is not None:
                    custom_el.remove(h)
                self._doc._apply_fields(custom_el, custom)
                self._doc._touch(custom_el)
            merge.resolve(self._pending, conflict_id, choice, custom=custom_el)
            self._doc.adopt(self._pending.root, self._pending.binaries)
            if not self._pending.conflicts:
                self._pending = None
                self._pending_base = None
                for _ in range(3):
                    try:
                        self._write(epoch, reason="resolve")
                        break
                    except _Race:
                        # Another sync change arrived: merge it on top of the resolution.
                        self._disk = storage.current_identity(self.path) and self._disk
                        self._reconcile_if_needed(epoch)
                        if self._pending is not None:
                            break
                else:
                    raise errors.ExternalChangeRace()
            return self._make_snapshot()

        def done(snap, err):
            def deliver():
                if epoch != self._epoch:
                    return
                if snap is not None:
                    self._apply_snapshot(snap)
                cb(None, _as_error(err) if err else None)
            self._post(deliver)

        if self.session != Session.UNLOCKED:
            cb(None, errors.VaultLocked())
            return
        self._worker.submit(job, done)


class _Noop:
    pass


_NOOP = _Noop()
