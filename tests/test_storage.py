"""T-05 / T-22: atomic durable saves, fault injection, backups, missing paths, save & copy ordering."""

from __future__ import annotations

import errno
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from keylane.core import errors, kdbx, storage
from keylane.core.config import Settings
from keylane.core.kdbx import EntryData, VaultDocument, make_composite
from keylane.core.service import InlineWorker, VaultService

PW = "synthetic-storage-pass"
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def leftovers(directory: Path) -> list[str]:
    return [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]


def old_mtime(path: Path) -> None:
    t = time.time() - 60
    os.utime(path, (t, t))


@pytest.fixture
def target(tmp_path):
    p = tmp_path / "vault.kdbx"
    p.write_bytes(b"ORIGINAL-CONTENT")
    os.chmod(p, 0o640)
    return p


# ---------------------------------------------------------------- atomic_replace


def test_atomic_replace_success_keeps_mode_and_cleans_up(target):
    ident = storage.atomic_replace(target, b"NEW-CONTENT")
    assert target.read_bytes() == b"NEW-CONTENT"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert ident == storage.current_identity(target)
    assert leftovers(target.parent) == []


def test_new_file_defaults_to_owner_only(tmp_path):
    p = tmp_path / "new.kdbx"
    storage.atomic_replace(p, b"x")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_atomic_replace_writes_tmp_fsyncs_then_renames(target, monkeypatch):
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def replace(src, dst):
        events.append(("replace", Path(src).parent == Path(dst).parent, Path(src).name.endswith(".tmp")))
        assert Path(dst).read_bytes() == b"ORIGINAL-CONTENT"  # original untouched until the rename
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    storage.atomic_replace(target, b"NEW")
    assert events[0] == "fsync"  # file data fsynced before rename
    assert events[1] == ("replace", True, True)  # sibling temp file renamed over target
    assert events[2:] == ["fsync"]  # directory fsynced after rename


@pytest.mark.parametrize("stage", ["write", "fsync", "rename"])
@pytest.mark.parametrize("err, expected", [
    (errno.ENOSPC, errors.DiskFull),
    (errno.EACCES, errors.PermissionDenied),
    (errno.EROFS, errors.ReadOnlyLocation),
    (errno.EIO, errors.LocationUnavailable),
])
def test_fault_hook_preserves_original(target, stage, err, expected):
    def fault(s):
        if s == stage:
            raise OSError(err, os.strerror(err))

    with pytest.raises(expected):
        storage.atomic_replace(target, b"NEW-CONTENT", fault=fault)
    assert target.read_bytes() == b"ORIGINAL-CONTENT"
    assert leftovers(target.parent) == []


def test_partial_write_then_enospc(target, monkeypatch):
    real_write = os.write
    calls = []

    def write(fd, data):
        calls.append(len(data))
        if len(calls) == 1:
            return real_write(fd, bytes(data[:3]))  # short write
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "write", write)
    with pytest.raises(errors.DiskFull):
        storage.atomic_replace(target, b"NEW-CONTENT-THAT-DOES-NOT-FIT")
    assert target.read_bytes() == b"ORIGINAL-CONTENT"
    assert leftovers(target.parent) == []


def test_short_writes_are_completed(target, monkeypatch):
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, bytes(data[:2])))
    storage.atomic_replace(target, b"0123456789")
    assert target.read_bytes() == b"0123456789"


def test_fsync_and_rename_errors_via_os(target, monkeypatch):
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, "I/O error")))
    with pytest.raises(errors.LocationUnavailable):
        storage.atomic_replace(target, b"NEW")
    monkeypatch.undo()
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError(errno.EACCES, "denied")))
    with pytest.raises(errors.PermissionDenied):
        storage.atomic_replace(target, b"NEW")
    assert target.read_bytes() == b"ORIGINAL-CONTENT"
    assert leftovers(target.parent) == []


def test_corrupted_write_is_detected_before_rename(target, monkeypatch):
    real_write = os.write

    def flipping_write(fd, data):
        data = bytearray(data)
        data[0] ^= 0xFF
        return real_write(fd, bytes(data))

    monkeypatch.setattr(os, "write", flipping_write)
    with pytest.raises(errors.SaveFailed) as exc:
        storage.atomic_replace(target, b"NEW-CONTENT")
    assert exc.value.detail == "verify-mismatch"
    assert target.read_bytes() == b"ORIGINAL-CONTENT"
    assert leftovers(target.parent) == []


def test_interruption_preserves_original(target):
    def fault(stage):
        if stage == "rename":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        storage.atomic_replace(target, b"NEW", fault=fault)
    assert target.read_bytes() == b"ORIGINAL-CONTENT"
    assert leftovers(target.parent) == []


def test_unsupported_directory_fsync_is_tolerated(target, monkeypatch):
    real_fsync = os.fsync
    calls = []

    def fsync(fd):
        calls.append(fd)
        if len(calls) > 1:  # directory fsync (FUSE / network filesystems)
            raise OSError(errno.EINVAL, "not supported")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    storage.atomic_replace(target, b"NEW")
    assert target.read_bytes() == b"NEW"


@pytest.mark.skipif(IS_ROOT, reason="root bypasses directory permissions")
def test_read_only_directory(target):
    d = target.parent
    os.chmod(d, 0o500)
    try:
        with pytest.raises(errors.PermissionDenied):
            storage.atomic_replace(target, b"NEW")
        assert target.read_bytes() == b"ORIGINAL-CONTENT"
    finally:
        os.chmod(d, 0o700)


def test_missing_directory(tmp_path):
    with pytest.raises(errors.LocationUnavailable):
        storage.atomic_replace(tmp_path / "gone" / "vault.kdbx", b"x")


def test_write_private_creates_owner_only_paths(tmp_path):
    p = tmp_path / "state" / "deep" / "baseline.kdbx"
    storage.write_private(p, b"secret-equivalent bytes")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700


# ---------------------------------------------------------------- reading


def test_read_stable_and_missing_paths(tmp_path, target):
    old_mtime(target)
    snap = storage.read_stable(target)
    assert snap.data == b"ORIGINAL-CONTENT"
    assert snap.identity == storage.current_identity(target)
    with pytest.raises(errors.VaultMissing):
        storage.read_stable(tmp_path / "missing.kdbx")
    with pytest.raises(errors.LocationUnavailable):
        storage.read_stable(tmp_path / "unmounted" / "vault.kdbx")
    with pytest.raises(errors.NotAVault):
        storage.read_stable(tmp_path)
    assert storage.stat_key(tmp_path / "missing.kdbx") is None
    assert storage.current_identity(tmp_path / "missing.kdbx") is None


@pytest.mark.skipif(IS_ROOT, reason="root bypasses file permissions")
def test_read_stable_permission_denied(target):
    old_mtime(target)
    os.chmod(target, 0)
    try:
        with pytest.raises(errors.PermissionDenied):
            storage.read_stable(target)
    finally:
        os.chmod(target, 0o600)


def test_read_stable_retries_transient_io_errors(target, monkeypatch):
    old_mtime(target)
    real_open = open
    fails = [OSError(errno.EIO, "transient")]

    def flaky_open(path, *a, **k):
        if Path(path) == target and fails:
            raise fails.pop()
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", flaky_open)
    assert storage.read_stable(target, settle=0.001).data == b"ORIGINAL-CONTENT"
    fails.extend(OSError(errno.ENOTCONN, "gone") for _ in range(3))
    with pytest.raises(errors.LocationUnavailable):
        storage.read_stable(target, attempts=3, settle=0.001)


# ---------------------------------------------------------------- backups


def test_backups_are_bounded_and_keep_newest(tmp_path):
    store = storage.BackupStore(tmp_path / "backups", keep=3, min_interval_s=0)
    for i in range(6):
        assert store.add(f"vault-version-{i}".encode(), reason="save", force=True) is not None
    kept = [p.read_bytes() for p in store.list()]
    assert kept == [b"vault-version-3", b"vault-version-4", b"vault-version-5"]
    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in store.list())


def test_backup_dedup_interval_and_force(tmp_path):
    store = storage.BackupStore(tmp_path / "b", keep=10, min_interval_s=3600)
    assert store.add(b"", reason="save") is None
    first = store.add(b"v1", reason="save")
    assert first is not None and first.read_bytes() == b"v1"
    assert store.add(b"v1", reason="save", force=True) is None  # identical content already stored
    assert store.add(b"v2", reason="save") is None  # within min interval
    forced = store.add(b"v2", reason="before-merge", force=True)
    assert forced is not None and "before-merge" in forced.name
    assert len(store.list()) == 2
    weird = store.add(b"v3", reason="../../evil name", force=True)
    assert weird.parent == store.directory and "/" not in weird.name.replace(str(store.directory), "")


def test_failed_backup_does_not_raise(tmp_path, monkeypatch):
    store = storage.BackupStore(tmp_path / "b", keep=2, min_interval_s=0)
    monkeypatch.setattr(storage, "write_private", lambda *a, **k: (_ for _ in ()).throw(errors.DiskFull()))
    assert store.add(b"v1", reason="save", force=True) is None


# ---------------------------------------------------------------- through the service


@pytest.fixture(scope="module")
def vault_bytes():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(kdbx, "tune_argon2_iterations", lambda *a, **k: 2)
        doc = VaultDocument.create(make_composite(PW, None), name="S", kdf_target_ms=1)
    doc.create_entry(EntryData(None, title="Existing", username="u", password="existing-pw"))
    return doc.to_bytes()


@pytest.fixture
def svc_env(tmp_path, monkeypatch, vault_bytes):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    vault = tmp_path / "sync" / "v.kdbx"
    vault.parent.mkdir()
    vault.write_bytes(vault_bytes)
    old_mtime(vault)
    settings = Settings(vault_id="0123abcd", vault_path=str(vault), backup_keep=2, backup_min_interval_minutes=0)
    svc = VaultService(settings, post=lambda fn: fn(), worker=InlineWorker())
    errs = []
    svc.unlock(PW, errs.append)
    assert errs == [None]
    return svc, vault


def call(fn, *args, **kw):
    box = {}
    fn(*args, lambda res, err: box.update(res=res, err=err), **kw)
    return box["res"], box["err"]


def titles_on_disk(vault: Path) -> set[str]:
    return {s.title for s in VaultDocument.open_bytes(vault.read_bytes(), make_composite(PW, None)).summaries(include_trash=True)}


@pytest.mark.parametrize("err, expected", [(errno.ENOSPC, errors.DiskFull), (errno.EACCES, errors.PermissionDenied)])
def test_service_save_failure_keeps_valid_vault_and_retry_works(svc_env, monkeypatch, err, expected):
    svc, vault = svc_env
    before = vault.read_bytes()
    real_replace = os.replace

    def failing_replace(src, dst):
        if Path(dst) == vault:
            raise OSError(err, os.strerror(err))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    res, e = call(svc.create_entry, EntryData(None, title="Draft", password="draft-pw"))
    assert isinstance(e, expected) and res is None
    assert vault.read_bytes() == before
    assert titles_on_disk(vault) == {"Existing"}
    assert leftovers(vault.parent) == []
    assert all(r.summary.title != "Draft" for r in svc.snapshot.index.search("Draft"))  # rolled back in memory

    monkeypatch.setattr(os, "replace", real_replace)
    res, e = call(svc.create_entry, EntryData(None, title="Draft", password="draft-pw"))
    assert e is None
    assert titles_on_disk(vault) == {"Existing", "Draft"}


def test_service_missing_vault_path_on_save(svc_env):
    svc, vault = svc_env
    vault.unlink()
    _, e = call(svc.create_entry, EntryData(None, title="Nowhere"))
    assert isinstance(e, errors.VaultMissing)
    assert not vault.exists()


def test_service_unmounted_vault_dir_on_save(svc_env):
    svc, vault = svc_env
    vault.unlink()
    vault.parent.rmdir()
    _, e = call(svc.create_entry, EntryData(None, title="Nowhere"))
    assert isinstance(e, (errors.VaultMissing, errors.LocationUnavailable))
    assert not vault.parent.exists()


def test_service_backups_bounded_by_settings_and_restorable(svc_env):
    svc, vault = svc_env
    versions = [vault.read_bytes()]
    for i in range(4):
        _, e = call(svc.create_entry, EntryData(None, title=f"E{i}", password=f"pw{i}"))
        assert e is None
        versions.append(vault.read_bytes())
    backups = svc.backups()
    assert len(backups) == 2  # Settings.backup_keep
    assert backups[0].parent == svc.settings.vault_state_dir() / "backups"
    # Backups are the encrypted pre-save files and still open with the vault credentials.
    assert {b.read_bytes() for b in backups} <= set(versions[:-1])
    newest = VaultDocument.open_bytes(backups[-1].read_bytes(), make_composite(PW, None))
    assert {s.title for s in newest.summaries()} == {"Existing", "E0", "E1", "E2"}


def test_backups_keep_original_credentials_after_change(svc_env):
    svc, vault = svc_env
    _, e = call(svc.change_credentials, PW, None, "new-synthetic-pass", None)
    assert e is None
    VaultDocument.open_bytes(vault.read_bytes(), make_composite("new-synthetic-pass", None))
    backup = svc.backups()[-1].read_bytes()
    VaultDocument.open_bytes(backup, make_composite(PW, None))
    with pytest.raises(errors.WrongCredentials):
        VaultDocument.open_bytes(backup, make_composite("new-synthetic-pass", None))


def test_service_callback_only_after_durable_replace(svc_env, monkeypatch):
    svc, vault = svc_env
    order = []
    real = storage.atomic_replace

    def tracking(path, data, **kw):
        ident = real(path, data, **kw)
        if Path(path) == vault:
            order.append("durable")
        return ident

    monkeypatch.setattr(storage, "atomic_replace", tracking)
    svc.create_entry(EntryData(None, title="Ordered", password="o-pw"), lambda r, e: order.append(("cb", e)))
    assert order == ["durable", ("cb", None)]


# ---------------------------------------------------------------- save & copy (UI bridge, no GUI)


def _bridge_create(svc, entry: dict, copy_after: bool, clipboard, monkeypatch):
    bridge = pytest.importorskip("keylane.ui.bridge")
    out = []
    monkeypatch.setattr(bridge, "_call", lambda cb, *args: out.append(args[0]))
    fake = SimpleNamespace(
        _activity=lambda: None, _set_busy=lambda busy: None, _settings=svc.settings, service=svc,
        clipboard=clipboard, copied=SimpleNamespace(emit=lambda *a: None))
    bridge.Backend.createEntry(fake, entry, copy_after, object())
    assert len(out) == 1
    return out[0]


def test_save_and_copy_copies_only_after_durable_save(svc_env, monkeypatch):
    svc, vault = svc_env
    copied = []

    class Clip:
        def copy(self, value):
            # At copy time the entry must already be in the file on disk.
            assert "Copied" in titles_on_disk(vault)
            copied.append(value)

    out = _bridge_create(svc, {"title": "Copied", "password": "copy-me-pw"}, True, Clip(), monkeypatch)
    assert out["ok"] and out.get("copied") is True
    assert copied == ["copy-me-pw"]


def test_save_and_copy_never_copies_when_save_fails(svc_env, monkeypatch):
    svc, vault = svc_env
    before = vault.read_bytes()

    class Clip:
        def copy(self, value):
            pytest.fail("clipboard written although the save failed")

    def full(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", full)
    out = _bridge_create(svc, {"title": "NotSaved", "password": "never-copied"}, True, Clip(), monkeypatch)
    assert out == {"ok": False, "code": "disk-full", "message": errors.DiskFull.message}
    assert vault.read_bytes() == before
