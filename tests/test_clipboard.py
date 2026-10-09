import stat
import time

import pytest

from keylane.core import errors
from keylane.core.clipboard import ClipboardOwner, history_status

FAKE = """#!/bin/sh
out="$FAKE_CLIP_DIR"
if [ "$1" = "--clear" ]; then echo cleared >> "$out/log"; exit 0; fi
echo "args:$*" >> "$out/log"
echo "state:$CLIPBOARD_STATE" >> "$out/log"
cat > "$out/value"
[ -n "$FAKE_FAIL" ] && exit 3
exec sleep 30
"""


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = tmp_path / "wl-copy"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_CLIP_DIR", str(tmp_path))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    return tmp_path, str(exe)


class ManualClock:
    def __init__(self):
        self.jobs = []

    def __call__(self, seconds, fn):
        job = [seconds, fn, True]
        self.jobs.append(job)
        return lambda: job.__setitem__(2, False)

    def fire(self):
        for j in list(self.jobs):
            if j[2]:
                j[2] = False
                j[1]()


def test_copy_uses_stdin_and_sensitive(fake):
    d, exe = fake
    clock = ManualClock()
    c = ClipboardOwner(seconds=20, schedule=clock, wl_copy=exe)
    c.copy("s3cret value")
    time.sleep(0.1)
    assert (d / "value").read_text() == "s3cret value"
    log = (d / "log").read_text()
    assert "--sensitive" in log and "--foreground" in log and "s3cret" not in log
    assert "--primary" not in log and "state:sensitive" in log
    assert clock.jobs[0][0] == 20
    assert c.owns()
    cleared = []
    c.on_cleared = cleared.append
    clock.fire()
    assert not c.owns()
    assert cleared == ["expired"]
    assert "cleared" in (d / "log").read_text()


def test_not_cleared_when_replaced(fake):
    d, exe = fake
    clock = ManualClock()
    c = ClipboardOwner(schedule=clock, wl_copy=exe)
    c.copy("x")
    c._proc.terminate()  # someone else took the selection: wl-copy exits
    c._proc.wait()
    cleared = []
    c.on_cleared = cleared.append
    clock.fire()
    assert cleared == ["replaced"]
    assert "cleared" not in (d / "log").read_text()


def test_second_copy_replaces_timer(fake):
    d, exe = fake
    clock = ManualClock()
    c = ClipboardOwner(schedule=clock, wl_copy=exe)
    c.copy("a")
    first = c._proc
    c.copy("b")
    assert first.poll() is not None
    assert sum(1 for j in clock.jobs if j[2]) == 1
    c.clear()


def test_empty_and_failure(fake, monkeypatch):
    d, exe = fake
    c = ClipboardOwner(schedule=ManualClock(), wl_copy=exe)
    with pytest.raises(errors.EmptyField):
        c.copy("")
    monkeypatch.setenv("FAKE_FAIL", "1")
    with pytest.raises(errors.ClipboardFailed):
        c.copy("x")
    assert not c.owns()


def test_history_status_runs():
    st = history_status()
    assert isinstance(st.confirmed, bool) and st.detail
