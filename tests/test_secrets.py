"""T-18: synthetic secrets never reach logs, control responses, errors or persisted state."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from keylane.core import config, errors, generator, ipc, kdbx, storage
from keylane.core.config import Settings
from keylane.core.kdbx import CustomField, EntryData, VaultDocument, make_composite
from keylane.core.service import InlineWorker, Session, VaultService

MASTER = "Synthetic-Master-3f9d7c"
NEW_MASTER = "Synthetic-Master2-a81e44"
PASSWORD = "Synthetic-Secret-Pa55-8e1f"
PASSWORD2 = "Synthetic-Secret2-Pa55-0b7d"
USERNAME = "synthetic-user-91c2"
NOTES = "synthetic-notes-77ab: recovery codes 1234"
CUSTOM = "synthetic-custom-pin-5550"
SECRETS = (MASTER, NEW_MASTER, PASSWORD, PASSWORD2, USERNAME, NOTES, CUSTOM)


def leaks(text: str | bytes, allow: tuple[str, ...] = ()) -> list[str]:
    secrets = [s for s in SECRETS if s not in allow]
    if isinstance(text, bytes):
        found = []
        for s in secrets:
            for enc in ("utf-8", "utf-16-le", "utf-16-be"):
                if s.encode(enc) in text:
                    found.append(f"{s} ({enc})")
        return found
    return [s for s in secrets if s in text]


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(kdbx, "tune_argon2_iterations", lambda *a, **k: 2)


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    home = tmp_path / "home"
    paths = {"HOME": home, "XDG_CONFIG_HOME": home / "config", "XDG_STATE_HOME": home / "state",
             "XDG_RUNTIME_DIR": tmp_path / "run", "KEYLANE_RUNTIME_DIR": tmp_path / "run" / "kl"}
    for k, p in paths.items():
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        monkeypatch.setenv(k, str(p))
    assert config.config_home() == home / "config" / "keylane"
    assert config.state_home() == home / "state" / "keylane"
    return home


def run(fn, *args, **kw):
    box = {}

    def cb(*a):
        if len(a) == 1:
            box["res"], box["err"] = None, a[0]
        else:
            box["res"], box["err"] = a
    fn(*args, cb, **kw)
    return box.get("res"), box.get("err")


def scan_tree(root: Path) -> dict[str, list[str]]:
    found = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            hits = leaks(p.name) + leaks(p.read_bytes())
            if hits:
                found[str(p)] = hits
    return found


def test_full_session_never_leaks(xdg, tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    vault = tmp_path / "sync" / "vault.kdbx"
    vault.parent.mkdir()
    settings = Settings(vault_path=str(vault), backup_min_interval_minutes=0, log_level="debug")
    config.save(settings)
    svc = VaultService(settings, post=lambda fn: fn(), worker=InlineWorker())
    observed: list = []  # every value/error surfaced to callers that is supposed to be non-secret

    _, err = run(svc.create_vault, vault, MASTER, None, "Synthetic")
    assert err is None
    svc.reconfigure(settings)

    _, err = run(svc.unlock, NEW_MASTER)
    assert isinstance(err, errors.WrongCredentials)
    observed.append(err)
    _, err = run(svc.unlock, MASTER)
    assert err is None and svc.session == Session.UNLOCKED

    data = EntryData(None, title="Synthetic Bank", username=USERNAME, password=PASSWORD, url="https://bank.example",
                     notes=NOTES, fields=[CustomField("PIN", CUSTOM, True)])
    observed.append(repr(data))
    eid, err = run(svc.create_entry, data)
    assert err is None

    # Invalid input errors never echo the offending values.
    bad = EntryData(None, title="X", fields=[CustomField(CUSTOM, "v", False), CustomField(CUSTOM, "w", False)])
    _, err = run(svc.create_entry, bad)
    assert isinstance(err, errors.InvalidInput)
    observed.append(err)

    # Reads (secrets returned to the explicit caller only).
    full, err = run(svc.read_entry, eid)
    assert full.password == PASSWORD
    observed.append(repr(full))
    value, err = run(svc.get_field, eid, "Password")
    assert value == PASSWORD

    full.password = PASSWORD2
    _, err = run(svc.update_entry, full)
    assert err is None
    hist, err = run(svc.history, eid)
    # The history list is a UI summary that shows usernames by design, never secret values.
    assert leaks(repr(hist), allow=(USERNAME,)) == []

    # Failed save (disk full) and its error.
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    _, err = run(svc.create_entry, EntryData(None, title="Draft", password=PASSWORD, notes=NOTES))
    assert isinstance(err, errors.DiskFull)
    observed.append(err)
    monkeypatch.setattr(os, "replace", real_replace)

    # External (sync) change merged, then a true conflict.
    time.sleep(0.06)
    other = VaultDocument.open_bytes(vault.read_bytes(), make_composite(MASTER, None))
    e = other.read_entry(eid)
    e.notes = NOTES + " (phone)"
    other.update_entry(e)
    pre_conflict = other.to_bytes()
    full, _ = run(svc.read_entry, eid)
    full.notes = NOTES + " (laptop)"
    run(svc.update_entry, full)
    vault.write_bytes(pre_conflict)
    svc.poll_external()
    assert svc.conflicts, "expected a synthetic conflict"
    observed.append(repr(svc.conflicts))
    _, err = run(svc.get_field, eid, "Password")
    observed.append(err)
    _, err = run(svc.resolve_conflict, svc.conflicts[0].id, "local")
    assert err is None

    # Trash, restore, credentials change, generator.
    run(svc.recycle, eid)
    run(svc.restore, eid)
    _, err = run(svc.change_credentials, "wrong-current", None, NEW_MASTER, None)
    observed.append(err)
    _, err = run(svc.change_credentials, MASTER, None, NEW_MASTER, None)
    assert err is None
    observed.append(repr(generator.generate(generator.PasswordSpec())))
    observed.append(repr(make_composite(MASTER, None)))
    observed.append(repr(svc.snapshot.format))

    svc.lock("test")
    _, err = run(svc.get_field, eid, "Password")
    observed.append(err)
    svc.quit()

    # ---- assertions
    log_text = caplog.text + "\n".join(
        str(r.args) + (r.exc_text or "") for r in caplog.records)
    assert caplog.records, "expected some diagnostic logging"
    assert leaks(log_text) == []
    for item in observed:
        text = item if isinstance(item, str) else f"{item!s} {item!r} {getattr(item, 'to_dict', dict)()}"
        assert leaks(text) == [], item
    assert scan_tree(xdg) == {}, "secrets in persisted config/state"
    assert leaks(vault.read_bytes()) == []  # vault itself only holds ciphertext
    assert leaks((xdg / "config" / "keylane" / "config.toml").read_text()) == []
    state = settings.vault_state_dir()
    assert (state / "baseline.kdbx").exists() and svc.backups()


def test_error_messages_are_fixed_strings():
    for cls in [c for c in vars(errors).values() if isinstance(c, type) and issubclass(c, errors.KeylaneError)]:
        e = cls()
        assert e.to_dict()["message"] == cls.message
    e = errors.classify_os_error(OSError(28, f"No space left: {PASSWORD}", f"/x/{PASSWORD}"))
    assert leaks(json.dumps(e.to_dict())) == [] and leaks(str(e)) == []


def test_unlock_failures_do_not_leak(xdg, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    vault = tmp_path / "v.kdbx"
    vault.write_bytes(b"\x03\xd9\xa2\x9a\x67\xfb\x4b\xb5" + PASSWORD.encode() * 4)
    for s in (Settings(vault_path=str(vault)), Settings(vault_path=str(vault), keyfile_path=str(tmp_path / f"{CUSTOM}.key"))):
        svc = VaultService(s, post=lambda fn: fn(), worker=InlineWorker())
        _, err = run(svc.unlock, MASTER)
        assert isinstance(err, errors.KeylaneError)
        assert leaks(f"{err} {err!r} {err.to_dict()}") == []
        svc.quit()
    assert leaks(caplog.text) == []


@pytest.fixture
def control(xdg):
    """A live control server wired like the daemon: status comes from the real Backend.status code."""
    bridge = pytest.importorskip("keylane.ui.bridge")

    def make(svc, settings):
        fake = SimpleNamespace(service=svc, _settings=settings)

        def handle(req):
            if req["cmd"] == "ping":
                return {"ok": True}
            if req["cmd"] == "status":
                return {"ok": True, "status": bridge.Backend.status(fake)}
            return {"ok": True}

        server = ipc.Server(handle)
        server.start()
        return server
    servers = []
    yield lambda svc, s: servers.append(make(svc, s)) or servers[-1]
    for s in servers:
        s.stop()


def test_control_responses_never_contain_secrets(xdg, tmp_path, control, caplog):
    caplog.set_level(logging.DEBUG)
    vault = tmp_path / "v.kdbx"
    doc = VaultDocument.create(make_composite(MASTER, None), name="C", kdf_target_ms=1)
    doc.create_entry(EntryData(None, title="Ctl", username=USERNAME, password=PASSWORD, notes=NOTES))
    vault.write_bytes(doc.to_bytes())
    settings = Settings(vault_id="c0ffee", vault_path=str(vault))
    svc = VaultService(settings, post=lambda fn: fn(), worker=InlineWorker())
    assert run(svc.unlock, MASTER)[1] is None
    server = control(svc, settings)
    assert server.path.parent == Path(os.environ["KEYLANE_RUNTIME_DIR"])

    responses = [
        ipc.request("status"),
        ipc.request("search", {"query": "ctl"}),
        ipc.request("new", {"title": "anything"}),
    ]
    for payload in ({"cmd": "show", "args": {"query": PASSWORD}},
                    {"cmd": "search", "args": {"password": PASSWORD}},
                    {"cmd": "search", "args": {"query": NOTES * 20}}):
        responses.append(json.loads(_send(server.path, json.dumps(payload).encode() + b"\n")))
    responses.append(json.loads(_send(server.path, PASSWORD.encode() * 300)))
    status = responses[0]["status"]
    assert status["session"] == "unlocked" and status["vault_configured"] is True
    for r in responses:
        assert leaks(json.dumps(r)) == [], r
    assert leaks(caplog.text) == []
    svc.quit()
    assert scan_tree(xdg) == {}


def _send(path: Path, data: bytes) -> bytes:
    import socket

    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(3)
    try:
        c.connect(str(path))
        c.sendall(data)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = c.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf
    finally:
        c.close()


def test_config_never_persists_secrets(xdg):
    s = Settings(vault_path="/sync/vault.kdbx", keyfile_path="/keys/k.keyx")
    s.generator = generator.PasswordSpec(length=30)
    config.save(s)
    text = (config.config_home() / "config.toml").read_text()
    assert leaks(text) == []
    assert set(__import__("tomllib").loads(text)) == {"vault", "security", "backups", "appearance", "diagnostics", "generator"}
    loaded = config.load()
    assert loaded.vault_path == "/sync/vault.kdbx" and loaded.generator.length == 30
    assert not hasattr(loaded, "password")


def test_backups_and_baseline_are_encrypted(xdg, tmp_path):
    vault = tmp_path / "v.kdbx"
    doc = VaultDocument.create(make_composite(MASTER, None), name="B", kdf_target_ms=1)
    doc.create_entry(EntryData(None, title="B1", username=USERNAME, password=PASSWORD, notes=NOTES))
    vault.write_bytes(doc.to_bytes())
    settings = Settings(vault_id="b0b0", vault_path=str(vault), backup_min_interval_minutes=0)
    svc = VaultService(settings, post=lambda fn: fn(), worker=InlineWorker())
    assert run(svc.unlock, MASTER)[1] is None
    for i in range(3):
        assert run(svc.create_entry, EntryData(None, title=f"N{i}", password=PASSWORD2, notes=NOTES))[1] is None
    svc.quit()
    files = [p for p in settings.vault_state_dir().rglob("*") if p.is_file()]
    assert len(files) >= 2
    for p in files:
        assert stat_mode(p) == 0o600
        data = p.read_bytes()
        assert data[:4] == b"\x03\xd9\xa2\x9a"  # KDBX signature: ciphertext, never decrypted content
        assert leaks(data) == []
    assert storage.BackupStore(settings.vault_state_dir() / "backups").list()


def stat_mode(p: Path) -> int:
    return p.stat().st_mode & 0o777
