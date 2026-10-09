"""Crash-safe file storage primitives for vault files.

* ``read_stable`` returns a snapshot whose stat identity did not change while it
  was being read (sync clients may write in place or replace the file).
* ``atomic_replace`` writes to a sibling temporary file, fsyncs it, renames it
  over the target and fsyncs the directory, so the destination is always either
  the old or the new complete file.
* ``BackupStore`` keeps a bounded set of *encrypted* vault snapshots (the raw
  KDBX bytes, never decrypted content).
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from . import errors


@dataclass(frozen=True)
class FileIdentity:
    """Version identity of a vault file as seen on disk."""

    dev: int
    ino: int
    size: int
    mtime_ns: int
    sha256: str

    @property
    def short(self) -> str:
        return self.sha256[:12]


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    identity: FileIdentity


def _identity(st: os.stat_result, data: bytes) -> FileIdentity:
    return FileIdentity(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())


def stat_key(path: Path) -> tuple | None:
    """Cheap change detector (no read). ``None`` if the file is missing."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return ("error",)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def read_stable(path: Path, *, attempts: int = 6, settle: float = 0.15, max_bytes: int = 512 * 1024 * 1024) -> Snapshot:
    """Read ``path`` until two consecutive stats agree around the read.

    Raises VaultMissing / LocationUnavailable / ExternalChangeRace.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            st1 = os.stat(path)
            if not stat.S_ISREG(st1.st_mode):
                raise errors.NotAVault(detail="not-a-regular-file")
            if st1.st_size > max_bytes:
                raise errors.UnsupportedFormat(detail="file-too-large")
            with open(path, "rb") as f:
                data = f.read()
                st_fd = os.fstat(f.fileno())
            st2 = os.stat(path)
        except FileNotFoundError:
            parent = Path(path).parent
            if not parent.exists():
                raise errors.LocationUnavailable(detail="vault-dir-missing") from None
            raise errors.VaultMissing() from None
        except PermissionError:
            raise errors.PermissionDenied("Permission denied while reading the vault.", detail="read") from None
        except OSError as exc:  # EIO, ENOTCONN on FUSE sync mounts...
            last_exc = exc
            time.sleep(settle)
            continue
        same = (
            (st1.st_ino, st1.st_size, st1.st_mtime_ns)
            == (st2.st_ino, st2.st_size, st2.st_mtime_ns)
            == (st_fd.st_ino, len(data), st_fd.st_mtime_ns)
        )
        if same:
            # Very recent writes may still be in flight from a non-atomic writer.
            age = time.time_ns() - st2.st_mtime_ns
            if age < 50_000_000 and i < attempts - 1:
                time.sleep(settle)
                continue
            return Snapshot(data, _identity(st2, data))
        time.sleep(settle)
    if last_exc is not None:
        raise errors.LocationUnavailable(detail=errno.errorcode.get(getattr(last_exc, "errno", 0) or 0, "io")) from None
    raise errors.ExternalChangeRace("The vault file kept changing while being read. Try again in a moment.")


def current_identity(path: Path) -> FileIdentity | None:
    """Hash the file as it is now (used immediately before committing a save)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
            st = os.fstat(f.fileno())
    except FileNotFoundError:
        return None
    return _identity(st, data)


def fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some FUSE/network filesystems do not support directory fsync
    finally:
        os.close(fd)


def atomic_replace(path: Path, data: bytes, *, mode: int | None = None, fault=None) -> FileIdentity:
    """Atomically replace ``path`` with ``data``.

    ``fault`` is a test hook: a callable receiving a stage name
    ("write", "fsync", "rename") that may raise to simulate failures.
    The original file is never truncated or modified in place.
    """
    path = Path(path)
    directory = path.parent
    if not directory.is_dir():
        raise errors.LocationUnavailable(detail="vault-dir-missing")
    if mode is None:
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            mode = 0o600
    tmp = directory / f".{path.name}.keylane-{os.getpid()}-{time.monotonic_ns()}.tmp"
    fd = None
    try:
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
            if fault:
                fault("write")
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            if fault:
                fault("fsync")
            os.fsync(fd)
            os.close(fd)
            fd = None
            # Verify what landed on disk before it can replace the last good copy.
            with open(tmp, "rb") as f:
                if hashlib.sha256(f.read()).digest() != hashlib.sha256(data).digest():
                    raise errors.SaveFailed(detail="verify-mismatch")
            if fault:
                fault("rename")
            os.replace(tmp, path)
        except OSError as exc:
            raise errors.classify_os_error(exc) from None
        fsync_dir(directory)
        st = os.stat(path)
        return _identity(st, data)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_private(path: Path, data: bytes) -> None:
    """Atomically write a private (0600) state file, creating 0700 parents."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_replace(path, data, mode=0o600)


class BackupStore:
    """Bounded store of encrypted vault snapshots.

    Snapshots are the raw KDBX bytes that were about to be replaced, so they
    remain protected by whichever credentials were valid when they were written.
    """

    def __init__(self, directory: Path, keep: int = 20, min_interval_s: float = 300.0):
        self.directory = Path(directory)
        self.keep = max(1, int(keep))
        self.min_interval_s = min_interval_s

    def _ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def list(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(p for p in self.directory.glob("*.kdbx") if p.is_file())

    def add(self, data: bytes, *, reason: str, force: bool = False) -> Path | None:
        """Store ``data`` unless an identical or very recent own-save backup exists."""
        if not data:
            return None
        self._ensure()
        digest = hashlib.sha256(data).hexdigest()[:16]
        existing = self.list()
        if any(digest in p.name for p in existing):
            return None
        if not force and existing:
            newest = existing[-1]
            if time.time() - newest.stat().st_mtime < self.min_interval_s:
                return None
        # Names sort chronologically (prune relies on it), also within one second.
        now_ns = time.time_ns()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now_ns // 10**9))
        safe_reason = "".join(c for c in reason if c.isalnum() or c == "-")[:24] or "save"
        target = self.directory / f"{stamp}-{now_ns % 10**9:09d}-{safe_reason}-{digest}.kdbx"
        try:
            write_private(target, data)
        except errors.KeylaneError:
            return None  # a failed backup must not block saving; caller logs category
        self.prune()
        return target

    def prune(self) -> None:
        items = self.list()
        for old in items[: max(0, len(items) - self.keep)]:
            try:
                old.unlink()
            except OSError:
                pass
