"""Typed, non-secret error categories.

Every error that can reach the UI, logs or the control socket is one of these.
Messages are fixed, human-readable strings that never include vault contents,
search queries, passwords or raw parser input.
"""

from __future__ import annotations


class KeylaneError(Exception):
    """Base class. ``code`` is a stable machine-readable category."""

    code = "error"
    message = "Something went wrong."

    def __init__(self, message: str | None = None, *, detail: str | None = None):
        super().__init__(message or self.message)
        self.user_message = message or self.message
        # ``detail`` must also be non-secret (e.g. an errno name or file role).
        self.detail = detail

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.user_message}
        if self.detail:
            d["detail"] = self.detail
        return d


class VaultNotConfigured(KeylaneError):
    code = "no-vault"
    message = "No vault is configured yet."


class VaultMissing(KeylaneError):
    code = "vault-missing"
    message = "The vault file could not be found. Check that the sync folder is available."


class KeyFileMissing(KeylaneError):
    code = "keyfile-missing"
    message = "The key file could not be found or read."


class KeyFileInvalid(KeylaneError):
    code = "keyfile-invalid"
    message = "The key file is not in a supported KeePass key-file format."


class WrongCredentials(KeylaneError):
    code = "wrong-credentials"
    message = "Wrong password or key file."


class CorruptVault(KeylaneError):
    code = "corrupt"
    message = "The vault file is damaged or incomplete. It was not modified."


class NotAVault(KeylaneError):
    code = "not-kdbx"
    message = "This file is not a KeePass database."


class UnsupportedFormat(KeylaneError):
    code = "unsupported-format"
    message = "This KeePass database version or feature is not supported."


class UnsupportedFactor(KeylaneError):
    code = "unsupported-factor"
    message = "This vault requires an unlock factor Keylane does not support (for example a hardware key)."


class VaultLocked(KeylaneError):
    code = "locked"
    message = "The vault is locked."


class StaleEntry(KeylaneError):
    code = "stale"
    message = "That entry no longer exists or has changed. Nothing was copied."


class EmptyField(KeylaneError):
    code = "empty-field"
    message = "That field is empty. Nothing was copied."


class ConflictPending(KeylaneError):
    code = "conflict"
    message = "This change conflicts with another copy of the vault and needs your decision before it can be saved."


class SaveFailed(KeylaneError):
    code = "save-failed"
    message = "The vault could not be saved. Your change is kept here so you can retry."


class ReadOnlyLocation(SaveFailed):
    code = "read-only"
    message = "The vault location is read-only. Your change was not saved."


class PermissionDenied(SaveFailed):
    code = "permission-denied"
    message = "Permission denied while saving the vault. Your change was not saved."


class DiskFull(SaveFailed):
    code = "disk-full"
    message = "The disk is full. Your change was not saved."


class LocationUnavailable(SaveFailed):
    code = "location-unavailable"
    message = "The vault folder is unavailable (is the sync folder mounted?). Your change was not saved."


class ExternalChangeRace(SaveFailed):
    code = "external-race"
    message = "The vault kept changing on disk while saving. Your change is kept; try again in a moment."


class InvalidInput(KeylaneError):
    code = "invalid"
    message = "That value is not valid."


class ClipboardFailed(KeylaneError):
    code = "clipboard-failed"
    message = "Copy failed. Nothing was placed on the clipboard."


class Cancelled(KeylaneError):
    code = "cancelled"
    message = "Cancelled."


def classify_os_error(exc: OSError, *, role: str = "vault") -> KeylaneError:
    """Map an OSError raised while writing to a typed, non-secret error."""
    import errno

    e = exc.errno
    if e in (errno.ENOSPC, errno.EDQUOT):
        return DiskFull(detail=role)
    if e == errno.EROFS:
        return ReadOnlyLocation(detail=role)
    if e in (errno.EACCES, errno.EPERM):
        return PermissionDenied(detail=role)
    if e in (errno.ENOENT, errno.ENOTDIR, errno.ENODEV, errno.ENXIO, errno.EIO, errno.ESTALE, errno.ENOTCONN):
        return LocationUnavailable(detail=role)
    return SaveFailed(detail=f"{role}:{errno.errorcode.get(e, 'unknown')}")
