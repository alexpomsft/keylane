"""T-19: control socket validation, permissions, duplicate instances and live round trips."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading

import pytest

from keylane.core import ipc
from keylane.core.ipc import COMMANDS, MAX_REQUEST, validate_request


def raw(obj) -> bytes:
    return json.dumps(obj).encode()


# ---------------------------------------------------------------- validate_request


@pytest.mark.parametrize("cmd", sorted(COMMANDS))
def test_known_commands_accepted(cmd):
    assert validate_request(raw({"cmd": cmd})) == {"cmd": cmd, "args": {}, "t": None}


def test_args_and_timestamp_pass_through():
    req = validate_request(raw({"cmd": "search", "args": {"query": "git"}, "t": 123456789}))
    assert req == {"cmd": "search", "args": {"query": "git"}, "t": 123456789}
    req = validate_request(raw({"cmd": "new", "args": {"title": "x" * 512}}))
    assert req["args"]["title"] == "x" * 512


@pytest.mark.parametrize("payload", [
    {"cmd": "rm -rf"}, {"cmd": "get-password"}, {"cmd": "export"}, {"cmd": ""}, {"cmd": None}, {"cmd": 1},
    {"cmd": ["ping"]}, {"command": "ping"}, {}, [], ["ping"], "ping", 42, None,
])
def test_unknown_or_missing_commands_rejected(payload):
    with pytest.raises(ValueError, match="unknown command"):
        validate_request(raw(payload))


@pytest.mark.parametrize("args", [
    [], "query", 1, None,
    {"query": 1}, {"query": None}, {"query": ["a"]}, {"title": {"x": 1}}, {"title": True},
    {"query": "x" * 513},
    {"password": "p"}, {"entry": "x"}, {"query": "ok", "field": "Password"},
])
def test_bad_args_rejected(args):
    with pytest.raises(ValueError, match="bad args"):
        validate_request(raw({"cmd": "search", "args": args}))


@pytest.mark.parametrize("data", [
    b"", b"{", b"not json", b"{'cmd': 'ping'}", b"\xff\xfe\x00", b'{"cmd": "ping"',
])
def test_malformed_json_rejected(data):
    with pytest.raises(ValueError, match="malformed request"):
        validate_request(data)


def test_oversized_rejected_before_parsing():
    big = raw({"cmd": "search", "args": {"query": "x" * 500}, "pad": "y" * MAX_REQUEST})
    with pytest.raises(ValueError, match="too large"):
        validate_request(big)
    with pytest.raises(ValueError, match="too large"):
        validate_request(b"\xff" * (MAX_REQUEST + 1))  # size is checked before decoding
    exact = raw({"cmd": "ping", "pad": "z" * (MAX_REQUEST - 30)})
    exact = exact + b" " * (MAX_REQUEST - len(exact))
    assert len(exact) == MAX_REQUEST
    assert validate_request(exact)["cmd"] == "ping"


@pytest.mark.parametrize("t", [True, False, 0, -5, 2**63, 2**80, 1.5, "123", None, [1], {"t": 1}])
def test_timestamp_only_positive_int(t):
    assert validate_request(raw({"cmd": "ping", "t": t}))["t"] is None


def test_unapproved_top_level_fields_are_dropped():
    req = validate_request(raw({"cmd": "status", "args": {}, "t": 7, "password": "p", "debug": True, "__class__": "x"}))
    assert set(req) == {"cmd", "args", "t"}
    assert req == {"cmd": "status", "args": {}, "t": 7}


def test_validated_args_are_fresh_copies():
    req = validate_request(raw({"cmd": "search", "args": {"query": "a"}}))
    assert type(req["args"]) is dict and type(req["args"]["query"]) is str


# ---------------------------------------------------------------- paths & permissions


def test_socket_path_respects_runtime_override(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYLANE_RUNTIME_DIR", str(tmp_path / "rt"))
    assert ipc.socket_path() == tmp_path / "rt" / "control.sock"
    monkeypatch.delenv("KEYLANE_RUNTIME_DIR")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert ipc.socket_path() == tmp_path / "keylane" / "control.sock"


@pytest.fixture
def sock_dir(tmp_path):
    d = tmp_path / "r"
    return d


@pytest.fixture
def server_factory(sock_dir):
    servers = []

    def make(handle=None, path=None):
        s = ipc.Server(handle or (lambda req: {"ok": True, "echo": req["cmd"]}), path=path or sock_dir / "c.sock")
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.stop()


def test_server_creates_private_dir_and_socket(server_factory, sock_dir):
    s = server_factory()
    s.start()
    assert stat.S_IMODE(sock_dir.stat().st_mode) == 0o700
    st = os.lstat(s.path)
    assert stat.S_ISSOCK(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_uid == os.getuid()


def test_existing_world_readable_dir_is_tightened(server_factory, sock_dir):
    sock_dir.mkdir(mode=0o755)
    os.chmod(sock_dir, 0o755)
    server_factory().start()
    assert stat.S_IMODE(sock_dir.stat().st_mode) == 0o700


def test_symlinked_runtime_dir_refused(server_factory, tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(PermissionError):
        server_factory(path=link / "c.sock").start()


def test_duplicate_instance_detected(server_factory):
    first = server_factory()
    first.start()
    second = server_factory()
    with pytest.raises(RuntimeError, match="already running"):
        second.start()
    assert ipc.ping(first.path)  # the running instance is unaffected


def test_stale_socket_is_replaced(server_factory, sock_dir):
    sock_dir.mkdir(mode=0o700)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_dir / "c.sock"))
    stale.close()  # file remains, nobody listening
    assert not ipc.ping(sock_dir / "c.sock")
    s = server_factory()
    s.start()
    assert ipc.ping(s.path)


# ---------------------------------------------------------------- live round trips


def send_raw(path, data: bytes, timeout=3.0) -> bytes:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
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


def test_round_trip_and_handler_receives_validated_request(server_factory):
    seen = []

    def handle(req):
        seen.append(req)
        return {"ok": True}

    s = server_factory(handle)
    s.start()
    assert ipc.request("search", {"query": "mail"}, path=s.path) == {"ok": True}
    assert seen[0]["cmd"] == "search" and seen[0]["args"] == {"query": "mail"}
    assert isinstance(seen[0]["t"], int) and seen[0]["t"] > 0
    assert ipc.ping(s.path)


@pytest.mark.parametrize("data, error", [
    (b"garbage\n", "malformed request"),
    (raw({"cmd": "dump-vault"}) + b"\n", "unknown command"),
    (raw({"cmd": {"nested": 1}}) + b"\n", "unknown command"),
    (raw({"cmd": "search", "args": {"password": "x"}}) + b"\n", "bad args"),
    (b"x" * (MAX_REQUEST + 600), "request too large"),
])
def test_invalid_requests_answered_safely_and_server_survives(server_factory, data, error):
    calls = []
    s = server_factory(lambda req: calls.append(req) or {"ok": True})
    s.start()
    resp = json.loads(send_raw(s.path, data))
    assert resp == {"ok": False, "error": error}
    assert calls == []
    assert ipc.request("status", path=s.path) == {"ok": True}


def test_handler_crash_does_not_kill_server(server_factory):
    def handle(req):
        if req["cmd"] == "new":
            raise RuntimeError("boom")
        return {"ok": True}

    s = server_factory(handle)
    s.start()
    assert ipc.request("new", path=s.path) == {}  # connection closed without a response
    assert ipc.ping(s.path)


def test_silent_client_times_out_without_blocking_others(server_factory):
    s = server_factory()
    s.start()
    idle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    idle.connect(str(s.path))
    try:
        result = {}
        t = threading.Thread(target=lambda: result.update(r=ipc.request("ping", path=s.path, timeout=3.0)))
        t.start()
        t.join(5)
        assert result.get("r", {}).get("ok") is True
    finally:
        idle.close()


def test_rate_limit(server_factory):
    s = server_factory()
    s.start()
    replies = [ipc.request("status", path=s.path) for _ in range(ipc._RATE[0] + 5)]
    assert {"ok": False, "error": "rate limited"} in replies
    assert sum(1 for r in replies if r.get("ok")) == ipc._RATE[0]


def test_stop_removes_socket(server_factory):
    s = server_factory()
    s.start()
    s.stop()
    assert not s.path.exists()
    assert not ipc.ping(s.path)
    with pytest.raises(OSError):
        ipc.request("ping", path=s.path, timeout=0.5)
