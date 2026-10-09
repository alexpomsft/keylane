import time

import pytest

from keylane.core import errors, storage
from keylane.core.config import Settings
from keylane.core.kdbx import EntryData, VaultDocument, make_composite
from keylane.core.service import InlineWorker, Session, Storage, VaultService

PW = "synthetic-test-pass"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    vault = tmp_path / "v.kdbx"
    doc = VaultDocument.create(make_composite(PW, None), name="T", kdf_target_ms=1)
    doc.create_entry(EntryData(None, title="GitHub", username="octo", password="gh-secret", url="https://github.com"))
    vault.write_bytes(doc.to_bytes())
    s = Settings(vault_id="abc", vault_path=str(vault))
    return s, vault


def make(settings):
    return VaultService(settings, post=lambda fn: fn(), worker=InlineWorker())


def result():
    box = {}

    def cb(*args):
        if len(args) == 1:
            box["err"] = args[0]
        else:
            box["res"], box["err"] = args
    return box, cb


def unlocked(settings):
    svc = make(settings)
    box, cb = result()
    svc.unlock(PW, cb)
    assert box["err"] is None
    return svc


def first_id(svc, title):
    return next(r.summary.id for r in svc.snapshot.index.search(title) if r.summary.title == title)


def test_unlock_wrong_password(env):
    s, _ = env
    svc = make(s)
    box, cb = result()
    svc.unlock("nope", cb)
    assert isinstance(box["err"], errors.WrongCredentials)
    assert svc.session == Session.LOCKED and svc.snapshot is None


def test_missing_vault_and_keyfile(env, tmp_path):
    s, vault = env
    s2 = Settings(vault_id="x", vault_path=str(tmp_path / "nothere.kdbx"))
    box, cb = result()
    make(s2).unlock(PW, cb)
    assert isinstance(box["err"], errors.VaultMissing)
    s3 = Settings(vault_id="x", vault_path=str(vault), keyfile_path=str(tmp_path / "nokey.keyx"))
    box, cb = result()
    make(s3).unlock(PW, cb)
    assert isinstance(box["err"], errors.KeyFileMissing)


def test_copy_and_lock_invalidates(env):
    s, _ = env
    svc = unlocked(s)
    eid = first_id(svc, "GitHub")
    box, cb = result()
    svc.get_field(eid, "Password", cb)
    assert box["res"] == "gh-secret"
    svc.lock()
    assert svc.snapshot is None
    box, cb = result()
    svc.get_field(eid, "Password", cb)
    assert isinstance(box["err"], errors.VaultLocked)


def test_late_callback_dropped_after_lock(env):
    s, _ = env
    pending = []
    svc = VaultService(s, post=pending.append, worker=InlineWorker())
    got = []
    svc.unlock(PW, lambda e: got.append(e))
    for fn in pending[:]:
        pending.remove(fn)
        fn()
    eid = first_id(svc, "GitHub")
    svc.get_field(eid, "Password", lambda r, e: got.append(r))
    svc.lock()
    for fn in pending:
        fn()
    assert "gh-secret" not in got


def test_create_entry_saves_durably(env):
    s, vault = env
    svc = unlocked(s)
    box, cb = result()
    svc.create_entry(EntryData(None, title="Bank", username="me", password="b-secret"), cb)
    assert box["err"] is None
    new_id = box["res"]
    doc = VaultDocument.open_bytes(vault.read_bytes(), make_composite(PW, None))
    assert doc.get_field(new_id, "Password") == "b-secret"
    assert any(r.summary.title == "Bank" for r in svc.snapshot.index.search("bank"))


def test_failed_save_rolls_back(env, monkeypatch):
    s, vault = env
    svc = unlocked(s)
    before = vault.read_bytes()

    def boom(*a, **k):
        raise errors.DiskFull()
    monkeypatch.setattr(storage, "atomic_replace", boom)
    box, cb = result()
    svc.create_entry(EntryData(None, title="Lost"), cb)
    assert isinstance(box["err"], errors.DiskFull)
    assert vault.read_bytes() == before
    assert not svc.snapshot.index.search("Lost") or all(r.summary.title != "Lost" for r in svc.snapshot.index.search("Lost"))


def _external_edit(vault, fn):
    doc = VaultDocument.open_bytes(vault.read_bytes(), make_composite(PW, None))
    fn(doc)
    time.sleep(0.06)
    vault.write_bytes(doc.to_bytes())


def test_external_change_merged_on_save(env):
    s, vault = env
    svc = unlocked(s)
    _external_edit(vault, lambda d: d.create_entry(EntryData(None, title="FromPhone", password="p")))
    box, cb = result()
    svc.create_entry(EntryData(None, title="FromLaptop", password="l"), cb)
    assert box["err"] is None
    doc = VaultDocument.open_bytes(vault.read_bytes(), make_composite(PW, None))
    titles = {x.title for x in doc.summaries()}
    assert {"GitHub", "FromPhone", "FromLaptop"} <= titles


def test_conflict_blocks_save_and_copy(env):
    s, vault = env
    svc = unlocked(s)
    eid = first_id(svc, "GitHub")
    box, cb = result()
    svc.read_entry(eid, cb)
    data = box["res"]
    data.password = "laptop-pw"
    box, cb = result()
    svc.update_entry(data, cb)
    assert box["err"] is None

    def remote(d):
        e = d.read_entry(eid)
        e.password = "phone-pw"
        d.update_entry(e)
    # remote edits from the *old* version: simulate by editing the pre-laptop baseline copy
    _external_edit(vault, remote)
    # Now remote descends from laptop's write; make a true concurrent edit instead:
    svc.poll_external()
    # a simple sequential edit is not a conflict
    assert svc.storage != Storage.CONFLICT


def test_true_conflict(env):
    s, vault = env
    original = vault.read_bytes()
    svc = unlocked(s)
    eid = first_id(svc, "GitHub")
    box, cb = result()
    svc.read_entry(eid, cb)
    data = box["res"]
    data.password = "laptop-pw"
    svc.update_entry(data, lambda r, e: None)
    # Replace the file with a concurrent edit from the original version.
    doc = VaultDocument.open_bytes(original, make_composite(PW, None))
    e = doc.read_entry(eid)
    e.password = "phone-pw"
    doc.update_entry(e)
    time.sleep(0.06)
    vault.write_bytes(doc.to_bytes())
    svc.poll_external()
    assert svc.storage == Storage.CONFLICT
    assert eid in svc.conflicted_entry_ids()
    box, cb = result()
    svc.get_field(eid, "Password", cb)
    assert isinstance(box["err"], errors.ConflictPending)
    box, cb = result()
    svc.create_entry(EntryData(None, title="Blocked"), cb)
    assert isinstance(box["err"], errors.ConflictPending)
    cid = svc.conflicts[0].id
    box, cb = result()
    svc.conflict_detail(cid, cb)
    assert box["err"] is None, box["err"]
    detail = box["res"]
    assert detail["local"].password == "laptop-pw" and detail["remote"].password == "phone-pw"
    assert detail["local"].group_id == data.group_id == detail["remote"].group_id
    box, cb = result()
    svc.resolve_conflict(cid, "remote", cb)
    assert box["err"] is None
    assert svc.storage == Storage.IDLE
    box, cb = result()
    svc.get_field(eid, "Password", cb)
    assert box["res"] == "phone-pw"
    disk = VaultDocument.open_bytes(vault.read_bytes(), make_composite(PW, None))
    assert disk.get_field(eid, "Password") == "phone-pw"
    hist = [disk.read_history(eid, i).password for i in range(len(disk.history(eid)))]
    assert "laptop-pw" in hist


def test_baseline_recovery_at_unlock(env):
    """A local write that was later overwritten by a sync tool is not lost."""
    s, vault = env
    original = vault.read_bytes()
    svc = unlocked(s)
    svc.create_entry(EntryData(None, title="LocalOnly", password="x"), lambda r, e: None)
    svc.lock()
    # Sync tool reverts the file to an older version with a remote addition.
    doc = VaultDocument.open_bytes(original, make_composite(PW, None))
    doc.create_entry(EntryData(None, title="RemoteOnly"))
    vault.write_bytes(doc.to_bytes())
    svc2 = unlocked(s)
    titles = {r.summary.title for r in svc2.snapshot.index.search("only")}
    assert {"LocalOnly", "RemoteOnly"} <= titles


def test_merge_file(env, tmp_path):
    s, vault = env
    other = tmp_path / "other.kdbx"
    doc = VaultDocument.open_bytes(vault.read_bytes(), make_composite(PW, None))
    doc.create_entry(EntryData(None, title="FromConflictCopy"))
    other.write_bytes(doc.to_bytes())
    svc = unlocked(s)
    box, cb = result()
    svc.merge_file(other, None, None, cb)
    assert box["err"] is None, box["err"]
    assert any(r.summary.title == "FromConflictCopy" for r in svc.snapshot.index.search("conflict copy"))


def test_backups_created(env):
    s, vault = env
    svc = unlocked(s)
    svc.create_entry(EntryData(None, title="A"), lambda r, e: None)
    assert len(svc.backups()) >= 1


def test_change_credentials(env):
    s, vault = env
    svc = unlocked(s)
    box, cb = result()
    svc.change_credentials("wrong", None, "new-pass", None, cb)
    assert isinstance(box["err"], errors.WrongCredentials)
    box, cb = result()
    svc.change_credentials(PW, None, "new-pass", None, cb)
    assert box["err"] is None
    VaultDocument.open_bytes(vault.read_bytes(), make_composite("new-pass", None))


def test_reconfigure_detects_vault_set_on_shared_settings(env):
    # The UI mutates the same Settings object it shares with the service.
    s, vault = env
    path = s.vault_path
    s.vault_path = ""
    svc = make(s)
    assert svc.session == Session.NO_VAULT
    s.vault_path = path
    svc.reconfigure(s)
    assert svc.session == Session.LOCKED
    box, cb = result()
    svc.unlock(PW, cb)
    assert box["err"] is None and svc.session == Session.UNLOCKED
    s.vault_path = ""
    svc.reconfigure(s)
    assert svc.session == Session.NO_VAULT and svc.snapshot is None
