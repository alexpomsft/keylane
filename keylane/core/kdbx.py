"""KDBX engine wrapper.

pykeepass is used for the binary container (KDBX 3.1 / 4.x parsing, ciphers,
KDFs, inner random stream). Everything above that is done directly on the
decrypted lxml tree so that:

* unknown elements, custom data, attachments, TOTP fields etc. are carried
  through untouched (we only modify the elements an operation targets);
* field names are never interpolated into XPath expressions;
* KeePass deletion records (``DeletedObjects``) and history are maintained
  consistently for reconciliation with other clients.

Secrets in the tree are plain Python ``str`` objects while the vault is
unlocked; see docs/SECURITY.md for the memory-handling limits this implies.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import os
import struct
import time
import uuid as uuidlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from . import errors

# --------------------------------------------------------------------------
# pykeepass integration: precomputed composite keys
# --------------------------------------------------------------------------


class CompositeKey:
    """SHA-256 composite of the unlock factors (password hash + key-file hash).

    Holding this instead of the typed password lets Keylane re-open the vault
    after external sync changes (whose KDF salt may differ) without retaining
    the password text. It is still secret-equivalent and dropped on lock.
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: bytes):
        self._raw = bytearray(raw)

    @property
    def raw(self) -> bytes:
        return bytes(self._raw)

    def wipe(self) -> None:
        for i in range(len(self._raw)):
            self._raw[i] = 0

    def __repr__(self) -> str:  # never print key material
        return "CompositeKey(<redacted>)"


_patched = False


def _patch_pykeepass() -> None:
    global _patched
    if _patched:
        return
    from pykeepass.kdbx_parsing import common, kdbx3, kdbx4

    original = common.compute_key_composite

    def compute_key_composite(password=None, keyfile=None):
        if isinstance(password, CompositeKey):
            return password.raw
        return original(password=password, keyfile=keyfile)

    common.compute_key_composite = compute_key_composite
    kdbx3.compute_key_composite = compute_key_composite
    kdbx4.compute_key_composite = compute_key_composite
    kdbx3.aes_kdf = aes_kdf
    kdbx4.aes_kdf = aes_kdf
    _patched = True


def aes_kdf(key: bytes, rounds: int, key_composite: bytes) -> bytes:
    """AES-KDF, identical to pykeepass' per-round loop but computed in bulk.

    Iterating AES-ECB on a block equals AES-CBC over zero blocks with that
    block as IV, so millions of rounds (KeePassXC's AES-KDF default) run in
    native code instead of one Python call per round.
    """
    from Cryptodome.Cipher import AES

    chunk = 1 << 16
    out = b""
    for half in (key_composite[:16], key_composite[16:32]):
        cipher = AES.new(key, AES.MODE_CBC, iv=half)
        block = half
        left = rounds
        while left > 0:
            n = min(left, chunk)
            block = cipher.encrypt(bytes(16 * n))[-16:]
            left -= n
        out += block
    return hashlib.sha256(out).digest()


def keyfile_composite(data: bytes) -> bytes:
    """KeePass key-file hash for XML v1/v2, 32-byte raw, 64-hex, or hashed files."""
    if not data:
        raise errors.KeyFileInvalid(detail="empty")
    stripped = data.strip()
    if stripped.startswith(b"<"):
        try:
            tree = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
        except etree.XMLSyntaxError:
            tree = None
        if tree is not None and tree.tag == "KeyFile":
            version = (tree.findtext("Meta/Version") or "").strip()
            data_el = tree.find("Key/Data")
            if data_el is None or data_el.text is None:
                raise errors.KeyFileInvalid(detail="xml-no-data")
            if version.startswith("1."):
                try:
                    return base64.b64decode(data_el.text.strip(), validate=True)
                except Exception:
                    raise errors.KeyFileInvalid(detail="xml-v1-data") from None
            if version.startswith("2."):
                try:
                    raw = bytes.fromhex("".join(data_el.text.split()))
                except ValueError:
                    raise errors.KeyFileInvalid(detail="xml-v2-data") from None
                expected = data_el.get("Hash")
                if expected and hashlib.sha256(raw).digest()[:4].hex().upper() != expected.replace(" ", "").upper():
                    raise errors.KeyFileInvalid(detail="xml-v2-hash")
                return raw
            raise errors.KeyFileInvalid(detail="xml-version")
    if len(data) == 32:
        return data
    if len(data) == 64:
        try:
            return bytes.fromhex(data.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            pass
    return hashlib.sha256(data).digest()


def make_composite(password: str | None, keyfile: Path | None) -> CompositeKey:
    """Derive the composite key. Raises KeyFileMissing / KeyFileInvalid."""
    parts = b""
    if password is not None:
        parts += hashlib.sha256(password.encode("utf-8")).digest()
    if keyfile is not None:
        try:
            with open(keyfile, "rb") as f:
                kdata = f.read(16 * 1024 * 1024 + 1)
        except FileNotFoundError:
            raise errors.KeyFileMissing() from None
        except OSError:
            raise errors.KeyFileMissing(detail="unreadable") from None
        if len(kdata) > 16 * 1024 * 1024:
            raise errors.KeyFileInvalid(detail="too-large")
        parts += keyfile_composite(kdata)
    if password is None and keyfile is None:
        raise errors.InvalidInput("A password or key file is required.")
    return CompositeKey(hashlib.sha256(parts).digest())


# --------------------------------------------------------------------------
# Small XML helpers
# --------------------------------------------------------------------------

STANDARD_FIELDS = ("Title", "UserName", "Password", "URL", "Notes")
NULL_UUID = base64.b64encode(b"\0" * 16).decode()


def b64_to_hex(text: str | None) -> str:
    try:
        return binascii.a2b_base64(text or "").hex()
    except Exception:
        return ""


def hex_to_b64(hexid: str) -> str:
    return base64.b64encode(bytes.fromhex(hexid)).decode()


def new_uuid_b64() -> str:
    return base64.b64encode(uuidlib.uuid4().bytes).decode()


def _child(el, tag):
    return el.find(tag)


def _text(el, tag, default=""):
    c = el.find(tag)
    if c is None or c.text is None:
        return default
    return c.text


def _set_text(el, tag, value: str, *, before: tuple[str, ...] = ()):
    c = el.find(tag)
    if c is None:
        c = etree.Element(tag)
        anchor = None
        for b in before:
            anchor = el.find(b)
            if anchor is not None:
                break
        if anchor is not None:
            anchor.addprevious(c)
        else:
            el.append(c)
    c.text = value
    return c


def get_string(entry, key: str) -> tuple[str, bool] | None:
    for s in entry.findall("String"):
        if s.findtext("Key") == key:
            v = s.find("Value")
            if v is None:
                return "", False
            return (v.text or ""), v.get("Protected") == "True"
    return None


def set_string(entry, key: str, value: str, protected: bool | None = None) -> None:
    for s in entry.findall("String"):
        if s.findtext("Key") == key:
            v = s.find("Value")
            if v is None:
                v = etree.SubElement(s, "Value")
            v.text = value
            if protected is not None:
                if protected:
                    v.set("Protected", "True")
                elif "Protected" in v.attrib:
                    del v.attrib["Protected"]
            return
    s = etree.Element("String")
    etree.SubElement(s, "Key").text = key
    v = etree.SubElement(s, "Value")
    v.text = value
    if protected:
        v.set("Protected", "True")
    # Keep strings grouped together after the last existing String.
    existing = entry.findall("String")
    if existing:
        existing[-1].addnext(s)
    else:
        anchor = entry.find("Binary") or entry.find("AutoType") or entry.find("History")
        if anchor is not None:
            anchor.addprevious(s)
        else:
            entry.append(s)


def remove_string(entry, key: str) -> None:
    for s in entry.findall("String"):
        if s.findtext("Key") == key:
            entry.remove(s)


# --------------------------------------------------------------------------
# Data transfer objects (plain values; produced on the vault thread)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EntrySummary:
    """Non-secret summary used for search/listing."""

    id: str
    title: str
    username: str
    url: str
    tags: tuple[str, ...]
    group_id: str
    group_path: str
    in_trash: bool
    modified: float  # unix seconds (informational)
    has_password: bool
    has_otp: bool
    attachment_count: int
    origin_path: str = ""  # for trashed items: where Restore will put it


@dataclass
class CustomField:
    key: str
    value: str
    protected: bool


@dataclass
class EntryData:
    """Full editable entry. Contains secrets: never log or persist."""

    id: str | None
    title: str = ""
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    fields: list[CustomField] = field(default_factory=list)
    group_id: str | None = None
    expires: bool = False
    expiry: float | None = None
    attachments: list[tuple[str, int]] = field(default_factory=list)  # (name, size) read-only
    created: float | None = None
    modified: float | None = None
    history_count: int = 0

    def __repr__(self) -> str:
        return f"EntryData(id={self.id!r}, <redacted>)"


@dataclass(frozen=True)
class GroupInfo:
    id: str
    name: str
    parent_id: str | None
    path: str
    depth: int
    entry_count: int
    is_trash: bool
    in_trash: bool
    notes: str = ""


@dataclass(frozen=True)
class HistoryItem:
    index: int
    modified: float
    title: str
    username: str
    changed_fields: tuple[str, ...]


@dataclass(frozen=True)
class FormatInfo:
    version: str
    cipher: str
    kdf: str
    kdf_detail: str
    compression: bool
    entry_count: int
    group_count: int


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------

KDF_NAMES = {
    bytes.fromhex("ef636ddf8c29444b91f7a9a403e30a0c"): "Argon2d",
    bytes.fromhex("9e298b1956db4773b23dfc3ec6f0a1e6"): "Argon2id",
    bytes.fromhex("c9d9f39a628a4460bf740d08c18a4fea"): "AES-KDF",
}
ARGON2ID_UUID = bytes.fromhex("9e298b1956db4773b23dfc3ec6f0a1e6")


def _classify_parse_error(exc: BaseException) -> errors.KeylaneError:
    from pykeepass.exceptions import CredentialsError, HeaderChecksumError, PayloadChecksumError

    if isinstance(exc, CredentialsError):
        return errors.WrongCredentials()
    if isinstance(exc, HeaderChecksumError):
        if "Not a KeePass" in str(exc):
            return errors.NotAVault()
        return errors.CorruptVault(detail="header")
    if isinstance(exc, PayloadChecksumError):
        return errors.CorruptVault(detail="payload")
    text = type(exc).__name__ + ":" + str(exc)[:200]
    if "Unsupported key derivation" in text or "SwitchError" in text or "MappingError" in text:
        return errors.UnsupportedFormat(detail="cipher-or-kdf")
    if "StreamError" in text or "stream read less" in text or "Terminated" in text:
        return errors.CorruptVault(detail="truncated")
    return errors.CorruptVault(detail=type(exc).__name__)


def _check_signature(data: bytes) -> None:
    if len(data) < 12:
        raise errors.CorruptVault(detail="too-short")
    sig1, sig2 = struct.unpack("<II", data[:8])
    if sig1 != 0x9AA2D903:
        raise errors.NotAVault()
    if sig2 == 0xB54BFB65:
        raise errors.UnsupportedFormat("KeePass 1.x (.kdb) databases are not supported. Convert it to KDBX first.", detail="kdb1")
    if sig2 not in (0xB54BFB67, 0xB54BFB66):
        raise errors.NotAVault()
    minor, major = struct.unpack("<HH", data[8:12])
    if major not in (3, 4) or (major == 3 and minor not in (0, 1)) or (major == 4 and minor > 1):
        raise errors.UnsupportedFormat(f"KDBX version {major}.{minor} is not supported.", detail=f"kdbx{major}.{minor}")


class VaultDocument:
    """One decrypted vault. Not thread-safe: owned by the vault worker thread."""

    def __init__(self, kp, composite: CompositeKey, transformed_key: bytes):
        self.kp = kp
        self.composite = composite
        self.transformed_key = transformed_key
        self._credentials_changed = False
        self._index_uuid: dict[str, etree._Element] | None = None

    # ---- open / create / save -------------------------------------------

    @classmethod
    def open_bytes(cls, data: bytes, composite: CompositeKey, *, transformed_key: bytes | None = None) -> "VaultDocument":
        _patch_pykeepass()
        from pykeepass import PyKeePass

        _check_signature(data)
        try:
            kp = PyKeePass(io.BytesIO(data), password=composite, transformed_key=transformed_key)
        except errors.KeylaneError:
            raise
        except Exception as exc:  # pykeepass/construct raise many types
            raise _classify_parse_error(exc) from None
        try:
            tk = kp.transformed_key
        except Exception:
            tk = transformed_key
        doc = cls(kp, composite, tk)
        doc._validate_tree()
        return doc

    @classmethod
    def create(cls, composite: CompositeKey, *, name: str = "Keylane", kdf_target_ms: int = 1000) -> "VaultDocument":
        """New KDBX 4.0 vault: AES-256 + Argon2id (64 MiB, tuned iterations)."""
        _patch_pykeepass()
        import pykeepass
        from pykeepass import PyKeePass

        blank = Path(pykeepass.__file__).parent / "blank_database.kdbx"
        kp = PyKeePass(str(blank), password="password")
        kp._password = composite
        kp._keyfile = None
        kp.filename = None
        kdf = kp.kdbx.header.value.dynamic_header.kdf_parameters.data.dict
        kdf["$UUID"].value = ARGON2ID_UUID
        kdf["M"].value = 64 * 1024 * 1024
        kdf["P"].value = 2
        kdf["I"].value = tune_argon2_iterations(kdf_target_ms, memory_kib=64 * 1024, parallelism=2)
        root = kp.tree.getroot()
        now = _now_text(kp)
        for g in root.iter("Group"):
            _set_text(g, "UUID", new_uuid_b64())
            g.find("Name").text = name
            times_el = g.find("Times")
            for t in (list(times_el) if times_el is not None else []):
                if t.tag.endswith("Time") and t.tag != "ExpiryTime":
                    t.text = now
        meta = root.find("Meta")
        _set_text(meta, "DatabaseName", name)
        _set_text(meta, "Generator", "Keylane")
        cd = meta.find("CustomData")
        if cd is not None:
            for item in list(cd):
                if item.findtext("Key") == "_LAST_MODIFIED":
                    cd.remove(item)
        for tag in ("DatabaseNameChanged", "SettingsChanged", "MasterKeyChanged", "RecycleBinChanged"):
            if meta.find(tag) is not None:
                meta.find(tag).text = now
        doc = cls(kp, composite, b"")
        doc._credentials_changed = True
        return doc

    def to_bytes(self) -> bytes:
        """Serialize. With unchanged credentials the KDF is not re-run."""
        from pykeepass.kdbx_parsing import KDBX
        from pykeepass.kdbx_parsing.common import rotate_seeds

        self.kp._password = self.composite
        self.kp._keyfile = None
        self._prune_all_history()
        tk = None if self._credentials_changed else self.transformed_key

        def build() -> bytes:
            buf = io.BytesIO()
            KDBX.build_stream(self.kp.kdbx, buf, password=self.composite, keyfile=None, transformed_key=tk, decrypt=True)
            return buf.getvalue()

        try:
            rotate_seeds(self.kp.kdbx, rotate_kdf_salt=tk is None)
            out = build()
            if self.version[0] == 3:
                # pykeepass leaves Meta/HeaderHash stale after rotating seeds,
                # which KeePassXC rejects. Header bytes only depend on header
                # fields, so a second build reproduces them exactly.
                digest = base64.b64encode(hashlib.sha256(out[: _kdbx3_header_len(out)]).digest()).decode()
                _set_text(self.meta, "HeaderHash", digest, before=("DatabaseName",))
                out = build()
        except errors.KeylaneError:
            raise
        except Exception as exc:
            raise errors.SaveFailed(detail=f"serialize:{type(exc).__name__}") from None
        return out

    def verify_bytes(self, data: bytes) -> bytes:
        """Re-open serialized bytes; returns the transformed key for them."""
        check = VaultDocument.open_bytes(data, self.composite, transformed_key=None if self._credentials_changed else self.transformed_key)
        return check.transformed_key

    def mark_saved(self, new_transformed_key: bytes) -> None:
        self.transformed_key = new_transformed_key
        self._credentials_changed = False

    def change_credentials(self, composite: CompositeKey) -> None:
        self.composite = composite
        self._credentials_changed = True
        meta = self.meta
        if meta.find("MasterKeyChanged") is not None:
            meta.find("MasterKeyChanged").text = _now_text(self.kp)

    def wipe(self) -> None:
        """Drop references to decrypted state (best effort; see SECURITY.md)."""
        try:
            root = self.kp.tree.getroot()
            for v in root.iter("Value"):
                v.text = None
            root.clear()
        except Exception:
            pass
        self.kp = None
        self.transformed_key = b""
        self._index_uuid = None

    def _validate_tree(self) -> None:
        root = self.kp.tree.getroot()
        if root is None or root.tag != "KeePassFile" or root.find("Meta") is None or root.find("Root/Group") is None:
            raise errors.CorruptVault(detail="xml-structure")

    # ---- format info -----------------------------------------------------

    @property
    def version(self) -> tuple[int, int]:
        return self.kp.version

    def format_info(self) -> FormatInfo:
        major, minor = self.version
        cipher = {"aes256": "AES-256", "chacha20": "ChaCha20", "twofish": "Twofish"}.get(self.kp.encryption_algorithm, self.kp.encryption_algorithm)
        dh = self.kp.kdbx.header.value.dynamic_header
        if major >= 4:
            kdf = dh.kdf_parameters.data.dict
            uid = kdf["$UUID"].value
            name = KDF_NAMES.get(uid, "unknown")
            if name.startswith("Argon2"):
                detail = f"{kdf['M'].value // (1024 * 1024)} MiB, {kdf['I'].value} iterations, {kdf['P'].value} lanes"
            else:
                detail = f"{kdf['R'].value} rounds"
        else:
            name = "AES-KDF"
            detail = f"{dh.transform_rounds.data} rounds"
        try:
            comp = bool(dh.compression_flags.data.compression)
        except Exception:
            comp = False
        return FormatInfo(f"KDBX {major}.{minor}", cipher, name, detail, comp, len(self._entries(include_trash=True)), sum(1 for _ in self.root_group.iter("Group")))

    # ---- tree access -------------------------------------------------------

    @property
    def root(self):
        return self.kp.tree.getroot()

    @property
    def meta(self):
        return self.root.find("Meta")

    @property
    def root_group(self):
        return self.root.find("Root/Group")

    def invalidate(self) -> None:
        self._index_uuid = None

    def _index(self) -> dict[str, etree._Element]:
        if self._index_uuid is None:
            idx = {}
            for el in self.root_group.iter("Entry", "Group"):
                if el.getparent() is not None and el.getparent().tag == "History":
                    continue
                u = el.findtext("UUID")
                if u:
                    idx[b64_to_hex(u)] = el
            self._index_uuid = idx
        return self._index_uuid

    def find(self, hexid: str, tag: str | None = None):
        el = self._index().get(hexid)
        if el is None or (tag and el.tag != tag):
            return None
        return el

    def entry_el(self, hexid: str):
        el = self.find(hexid, "Entry")
        if el is None:
            raise errors.StaleEntry()
        return el

    def group_el(self, hexid: str):
        el = self.find(hexid, "Group")
        if el is None:
            raise errors.StaleEntry("That group no longer exists.")
        return el

    def recycle_bin_el(self, create: bool = False):
        meta = self.meta
        enabled = (meta.findtext("RecycleBinEnabled") or "True") == "True"
        rb = meta.findtext("RecycleBinUUID") or NULL_UUID
        if rb != NULL_UUID:
            el = self.find(b64_to_hex(rb), "Group")
            if el is not None:
                return el
        if not create:
            return None
        if not enabled:
            return None
        g = self._new_group_el("Recycle Bin", icon="43")
        _set_text(g, "EnableAutoType", "false")
        _set_text(g, "EnableSearching", "false")
        self.root_group.append(g)
        _set_text(meta, "RecycleBinUUID", g.findtext("UUID"))
        if meta.find("RecycleBinChanged") is not None:
            meta.find("RecycleBinChanged").text = _now_text(self.kp)
        self.invalidate()
        return g

    def _in_trash(self, el) -> bool:
        rb = self.recycle_bin_el()
        if rb is None:
            return False
        p = el
        while p is not None:
            if p is rb:
                return True
            p = p.getparent()
        return False

    def _entries(self, include_trash: bool = False):
        rb = self.recycle_bin_el()
        out = []
        for e in self.root_group.iter("Entry"):
            parent = e.getparent()
            if parent.tag == "History":
                continue
            if not include_trash and rb is not None and self._ancestor(rb, e):
                continue
            out.append(e)
        return out

    @staticmethod
    def _ancestor(anc, el) -> bool:
        p = el.getparent()
        while p is not None:
            if p is anc:
                return True
            p = p.getparent()
        return False

    def group_path(self, group_el) -> str:
        names = []
        g = group_el
        while g is not None and g.tag == "Group":
            if g is self.root_group:
                break
            names.append(g.findtext("Name") or "")
            g = g.getparent()
        return " / ".join(reversed(names))

    # ---- summaries ---------------------------------------------------------

    def _time(self, el, tag) -> float | None:
        t = el.find("Times")
        if t is None:
            return None
        txt = t.findtext(tag)
        if not txt:
            return None
        try:
            return self.kp._decode_time(txt).timestamp()
        except Exception:
            return None

    _OTP_KEYS = frozenset(("otp", "TOTP Seed", "TimeOtp-Secret-Base32"))

    def summary(self, e) -> EntrySummary:
        g = e.getparent()
        return self._summary(e, b64_to_hex(g.findtext("UUID")), self.group_path(g), self._in_trash(e))

    def _summary(self, e, group_id: str, group_path: str, in_trash: bool) -> EntrySummary:
        title = username = url = ""
        has_password = has_otp = False
        for s in e.iterchildren("String"):
            k = s.findtext("Key")
            if k == "Title":
                title = s.findtext("Value") or ""
            elif k == "UserName":
                username = s.findtext("Value") or ""
            elif k == "URL":
                url = s.findtext("Value") or ""
            elif k == "Password":
                has_password = bool(s.findtext("Value"))
            elif k in self._OTP_KEYS:
                has_otp = True
        tags = tuple(t.strip() for t in (e.findtext("Tags") or "").replace(",", ";").split(";") if t.strip())
        origin = ""
        if in_trash:
            pp = e.findtext("PreviousParentGroup")
            cand = self.find(b64_to_hex(pp), "Group") if pp else None
            origin = self.group_path(cand) if cand is not None and not self._in_trash(cand) else ""
            origin = origin or "top level"
        return EntrySummary(
            id=b64_to_hex(e.findtext("UUID")),
            title=title,
            username=username,
            url=url,
            tags=tags,
            group_id=group_id,
            group_path=group_path,
            in_trash=in_trash,
            modified=self._time(e, "LastModificationTime") or 0.0,
            has_password=has_password,
            has_otp=has_otp,
            attachment_count=len(e.findall("Binary")),
            origin_path=origin,
        )

    def summaries(self, include_trash: bool = False) -> list[EntrySummary]:
        """One walk over the group tree; group paths and trash state are computed once per group."""
        rb = self.recycle_bin_el()
        out: list[EntrySummary] = []
        stack = [(self.root_group, "", False)]
        while stack:
            g, path, in_trash = stack.pop()
            if in_trash and not include_trash:
                continue
            gid = b64_to_hex(g.findtext("UUID"))
            for e in g.iterchildren("Entry"):
                out.append(self._summary(e, gid, path, in_trash))
            for c in reversed(list(g.iterchildren("Group"))):
                name = c.findtext("Name") or ""
                stack.append((c, f"{path} / {name}" if path else name, in_trash or (rb is not None and c is rb)))
        return out

    def groups(self) -> list[GroupInfo]:
        rb = self.recycle_bin_el()
        out = []

        def walk(g, depth, parent_id, in_trash):
            is_trash = rb is not None and g is rb
            gid = b64_to_hex(g.findtext("UUID"))
            out.append(GroupInfo(gid, g.findtext("Name") or "", parent_id, self.group_path(g), depth,
                                 len(g.findall("Entry")), is_trash, in_trash, g.findtext("Notes") or ""))
            for c in g.findall("Group"):
                walk(c, depth + 1, gid, in_trash or is_trash)

        walk(self.root_group, 0, None, False)
        return out

    # ---- entry read --------------------------------------------------------

    def read_entry(self, hexid: str) -> EntryData:
        e = self.entry_el(hexid)
        return self._entry_data(e)

    def _entry_data(self, e, parent_b64: str | None = None) -> EntryData:
        def s(key):
            r = get_string(e, key)
            return r[0] if r else ""

        fields = []
        for st in e.findall("String"):
            k = st.findtext("Key") or ""
            if k in STANDARD_FIELDS:
                continue
            v = st.find("Value")
            fields.append(CustomField(k, (v.text or "") if v is not None else "", v is not None and v.get("Protected") == "True"))
        pool = self.binaries()
        atts = []
        for b in e.findall("Binary"):
            ref = b.find("Value")
            size = 0
            if ref is not None and ref.get("Ref") is not None:
                try:
                    size = len(pool[int(ref.get("Ref"))])
                except (ValueError, IndexError):
                    size = 0
            atts.append((b.findtext("Key") or "", size))
        times = e.find("Times")
        expires = times is not None and (times.findtext("Expires") or "False") == "True"
        hist = e.find("History")
        tags = [t.strip() for t in (e.findtext("Tags") or "").replace(",", ";").split(";") if t.strip()]
        parent = e.getparent()
        if parent is not None and parent.tag == "Group":
            parent_b64 = parent.findtext("UUID")
        group_id = b64_to_hex(parent_b64) if parent_b64 else None
        return EntryData(
            id=b64_to_hex(e.findtext("UUID")),
            title=s("Title"), username=s("UserName"), password=s("Password"), url=s("URL"), notes=s("Notes"),
            tags=tags, fields=fields,
            group_id=group_id,
            expires=expires, expiry=self._time(e, "ExpiryTime") if expires else None,
            attachments=atts,
            created=self._time(e, "CreationTime"), modified=self._time(e, "LastModificationTime"),
            history_count=len(hist) if hist is not None else 0,
        )

    def get_field(self, hexid: str, key: str) -> str:
        e = self.entry_el(hexid)
        r = get_string(e, key)
        if r is None or r[0] == "":
            raise errors.EmptyField()
        return r[0]

    # ---- mutation helpers --------------------------------------------------

    def _protect_default(self, key: str) -> bool:
        tag = {"Title": "ProtectTitle", "UserName": "ProtectUserName", "Password": "ProtectPassword", "URL": "ProtectURL", "Notes": "ProtectNotes"}[key]
        return (self.meta.findtext(f"MemoryProtection/{tag}") or ("True" if key == "Password" else "False")) == "True"

    def _times_el(self, now: str):
        t = etree.Element("Times")
        for tag in ("CreationTime", "LastModificationTime", "LastAccessTime", "ExpiryTime"):
            etree.SubElement(t, tag).text = now
        etree.SubElement(t, "Expires").text = "False"
        etree.SubElement(t, "UsageCount").text = "0"
        etree.SubElement(t, "LocationChanged").text = now
        return t

    def _new_group_el(self, name: str, icon: str = "48"):
        now = _now_text(self.kp)
        g = etree.Element("Group")
        etree.SubElement(g, "UUID").text = new_uuid_b64()
        etree.SubElement(g, "Name").text = name
        etree.SubElement(g, "Notes").text = ""
        etree.SubElement(g, "IconID").text = icon
        g.append(self._times_el(now))
        etree.SubElement(g, "IsExpanded").text = "True"
        etree.SubElement(g, "DefaultAutoTypeSequence")
        etree.SubElement(g, "EnableAutoType").text = "null"
        etree.SubElement(g, "EnableSearching").text = "null"
        etree.SubElement(g, "LastTopVisibleEntry").text = NULL_UUID
        return g

    def _touch(self, el, *, modified=True, location=False) -> None:
        now = _now_text(self.kp)
        t = el.find("Times")
        if t is None:
            t = self._times_el(now)
            el.insert(min(3, len(el)), t)
        if modified:
            _set_text(t, "LastModificationTime", now)
        _set_text(t, "LastAccessTime", now)
        if location:
            _set_text(t, "LocationChanged", now)

    def _snapshot_history(self, e) -> None:
        archive = copy.deepcopy(e)
        h = archive.find("History")
        if h is not None:
            archive.remove(h)
        hist = e.find("History")
        if hist is None:
            hist = etree.SubElement(e, "History")
        hist.append(archive)

    def _prune_history(self, e) -> None:
        hist = e.find("History")
        if hist is None:
            return
        try:
            max_items = int(self.meta.findtext("HistoryMaxItems") or "10")
        except ValueError:
            max_items = 10
        try:
            max_size = int(self.meta.findtext("HistoryMaxSize") or "-1")
        except ValueError:
            max_size = -1
        items = list(hist)
        if max_items >= 0:
            while len(items) > max_items:
                hist.remove(items.pop(0))
        if max_size >= 0:
            def size(x):
                return len(etree.tostring(x))
            while items and sum(size(x) for x in items) > max_size:
                hist.remove(items.pop(0))

    def _prune_all_history(self) -> None:
        for e in self._entries(include_trash=True):
            self._prune_history(e)

    def _record_deletion(self, uuid_b64: str) -> None:
        root = self.root.find("Root")
        dobj = root.find("DeletedObjects")
        if dobj is None:
            dobj = etree.SubElement(root, "DeletedObjects")
        d = etree.SubElement(dobj, "DeletedObject")
        etree.SubElement(d, "UUID").text = uuid_b64
        etree.SubElement(d, "DeletionTime").text = _now_text(self.kp)

    def _set_previous_parent(self, el, parent) -> None:
        if self.version >= (4, 0):
            pp = el.find("PreviousParentGroup")
            if pp is None:
                pp = etree.Element("PreviousParentGroup")
                times = el.find("Times")
                (times.addnext(pp) if times is not None else el.append(pp))
            pp.text = parent.findtext("UUID")

    def _move(self, el, dest) -> None:
        old = el.getparent()
        if old is dest:
            return
        if el.tag == "Group":
            p = dest
            while p is not None:
                if p is el:
                    raise errors.InvalidInput("A group cannot be moved into itself.")
                p = p.getparent()
            dest.append(el)
        else:
            # Entries go before subgroups for tidy XML.
            groups = dest.findall("Group")
            if groups:
                groups[0].addprevious(el)
            else:
                dest.append(el)
        self._set_previous_parent(el, old)
        self._touch(el, modified=False, location=True)
        self.invalidate()

    # ---- entry mutations ---------------------------------------------------

    def create_entry(self, data: EntryData) -> str:
        if not data.title.strip():
            raise errors.InvalidInput("An entry needs a title.")
        group = self.group_el(data.group_id) if data.group_id else self.root_group
        if self._in_trash(group):
            raise errors.InvalidInput("Entries cannot be created in the trash.")
        now = _now_text(self.kp)
        e = etree.Element("Entry")
        etree.SubElement(e, "UUID").text = new_uuid_b64()
        etree.SubElement(e, "IconID").text = "0"
        etree.SubElement(e, "ForegroundColor")
        etree.SubElement(e, "BackgroundColor")
        etree.SubElement(e, "OverrideURL")
        etree.SubElement(e, "Tags")
        e.append(self._times_el(now))
        for key in STANDARD_FIELDS:
            etree.SubElement(etree.SubElement(e, "String"), "Key").text = key
        for s in e.findall("String"):
            v = etree.SubElement(s, "Value")
            v.text = ""
            if self._protect_default(s.findtext("Key")):
                v.set("Protected", "True")
        at = etree.SubElement(e, "AutoType")
        etree.SubElement(at, "Enabled").text = "True"
        etree.SubElement(at, "DataTransferObfuscation").text = "0"
        self._apply_fields(e, data)
        groups = group.findall("Group")
        (groups[0].addprevious(e) if groups else group.append(e))
        hexid = b64_to_hex(e.findtext("UUID"))
        if self._index_uuid is not None:
            self._index_uuid[hexid] = e  # a new element: extend the index instead of rebuilding it
        return hexid

    def _apply_fields(self, e, data: EntryData) -> bool:
        """Write standard/custom fields, tags and expiry. Returns True if anything changed."""
        changed = False
        for key, value in (("Title", data.title), ("UserName", data.username), ("Password", data.password), ("URL", data.url), ("Notes", data.notes)):
            cur = get_string(e, key)
            if cur is None or cur[0] != value:
                set_string(e, key, value, None if cur is not None else self._protect_default(key))
                changed = True
        wanted = {}
        for f in data.fields:
            k = f.key.strip()
            if not k:
                raise errors.InvalidInput("Custom field names cannot be empty.")
            if k in STANDARD_FIELDS:
                raise errors.InvalidInput(f"“{k}” is a reserved field name.")
            if k in wanted:
                raise errors.InvalidInput("Custom field names must be unique.")
            wanted[k] = f
        for st in list(e.findall("String")):
            k = st.findtext("Key") or ""
            if k not in STANDARD_FIELDS and k not in wanted:
                e.remove(st)
                changed = True
        for k, f in wanted.items():
            cur = get_string(e, k)
            if cur is None or cur[0] != f.value or cur[1] != f.protected:
                set_string(e, k, f.value, f.protected)
                changed = True
        tags = ";".join(t.strip() for t in data.tags if t.strip())
        if (e.findtext("Tags") or "") != tags:
            _set_text(e, "Tags", tags, before=("Times",))
            changed = True
        times = e.find("Times")
        cur_exp = (times.findtext("Expires") or "False") == "True"
        if cur_exp != data.expires:
            _set_text(times, "Expires", "True" if data.expires else "False")
            changed = True
        if data.expires and data.expiry is not None:
            txt = self.kp._encode_time(datetime.fromtimestamp(data.expiry, timezone.utc))
            if times.findtext("ExpiryTime") != txt:
                _set_text(times, "ExpiryTime", txt)
                changed = True
        return changed

    def update_entry(self, data: EntryData, *, expected_modified: float | None = None) -> bool:
        e = self.entry_el(data.id)
        if expected_modified is not None:
            cur = self._time(e, "LastModificationTime")
            if cur is not None and abs(cur - expected_modified) > 0.5:
                raise errors.StaleEntry("This entry changed (possibly from sync) while you were editing. Review the latest version before saving.")
        snapshot = copy.deepcopy(e)
        self._snapshot_history(e)
        changed = self._apply_fields(e, data)
        dest = self.group_el(data.group_id) if data.group_id else e.getparent()
        if dest is not e.getparent():
            self._move(e, dest)
            changed = True
        if not changed:
            # Nothing changed: undo history snapshot so a no-op does not rewrite storage.
            e.getparent().replace(e, snapshot)
            self.invalidate()
            return False
        self._touch(e)
        self._prune_history(e)
        return True

    def move_entry(self, hexid: str, group_id: str) -> None:
        self._move(self.entry_el(hexid), self.group_el(group_id))

    def recycle(self, hexid: str) -> str:
        """Move entry/group to trash; returns 'trashed' or 'deleted' if trash disabled."""
        el = self.find(hexid)
        if el is None:
            raise errors.StaleEntry()
        if el is self.root_group:
            raise errors.InvalidInput("The root group cannot be deleted.")
        rb = self.recycle_bin_el(create=True)
        if rb is None:
            raise errors.InvalidInput("The recycle bin is disabled for this vault; use permanent delete.")
        if el is rb or self._in_trash(el):
            raise errors.InvalidInput("That item is already in the trash.")
        self._move(el, rb)
        return "trashed"

    def restore(self, hexid: str) -> str:
        el = self.find(hexid)
        if el is None:
            raise errors.StaleEntry()
        if not self._in_trash(el):
            raise errors.InvalidInput("That item is not in the trash.")
        target = None
        pp = el.findtext("PreviousParentGroup")
        if pp:
            cand = self.find(b64_to_hex(pp), "Group")
            if cand is not None and not self._in_trash(cand) and cand is not el:
                target = cand
        if target is None:
            target = self.root_group
        self._move(el, target)
        return b64_to_hex(target.findtext("UUID"))

    def delete_permanently(self, hexid: str) -> int:
        el = self.find(hexid)
        if el is None:
            raise errors.StaleEntry()
        if el is self.root_group or el is self.recycle_bin_el():
            raise errors.InvalidInput("That group cannot be deleted permanently.")
        if not self._in_trash(el):
            raise errors.InvalidInput("Only items in the trash can be deleted permanently.")
        count = 0
        for sub in list(el.iter("Entry", "Group")):
            if sub.getparent() is not None and sub.getparent().tag == "History":
                continue
            self._record_deletion(sub.findtext("UUID"))
            count += 1
        el.getparent().remove(el)
        self._gc_binaries()
        self.invalidate()
        return count

    def empty_trash(self) -> int:
        rb = self.recycle_bin_el()
        if rb is None:
            return 0
        n = 0
        for child in list(rb):
            if child.tag in ("Entry", "Group"):
                n += self.delete_permanently(b64_to_hex(child.findtext("UUID")))
        return n

    # ---- history -----------------------------------------------------------

    def history(self, hexid: str) -> list[HistoryItem]:
        e = self.entry_el(hexid)
        hist = e.find("History")
        out = []
        if hist is None:
            return out
        items = list(hist)
        for i, h in enumerate(items):
            nxt = items[i + 1] if i + 1 < len(items) else e
            changed = _diff_fields(h, nxt)
            out.append(HistoryItem(i, self._time(h, "LastModificationTime") or 0.0,
                                   (get_string(h, "Title") or ("", False))[0],
                                   (get_string(h, "UserName") or ("", False))[0], tuple(changed)))
        return list(reversed(out))

    def read_history(self, hexid: str, index: int) -> EntryData:
        e = self.entry_el(hexid)
        hist = e.find("History")
        if hist is None or not (0 <= index < len(hist)):
            raise errors.StaleEntry("That history version no longer exists.")
        data = self._entry_data(hist[index])
        data.id = hexid
        return data

    def restore_history(self, hexid: str, index: int) -> None:
        """Make a historical version current; the replaced version goes to history."""
        e = self.entry_el(hexid)
        hist = e.find("History")
        if hist is None or not (0 <= index < len(hist)):
            raise errors.StaleEntry("That history version no longer exists.")
        old = copy.deepcopy(hist[index])
        self._snapshot_history(e)
        keep = {"UUID", "Times", "History", "PreviousParentGroup"}
        for c in list(e):
            if c.tag not in keep:
                e.remove(c)
        insert_at = 1
        for c in old:
            if c.tag in keep:
                continue
            e.insert(insert_at, c)
            insert_at += 1
        self._touch(e)
        self._prune_history(e)
        self.invalidate()

    # ---- groups --------------------------------------------------------------

    def create_group(self, name: str, parent_id: str | None) -> str:
        name = name.strip()
        if not name:
            raise errors.InvalidInput("A group needs a name.")
        parent = self.group_el(parent_id) if parent_id else self.root_group
        if self._in_trash(parent):
            raise errors.InvalidInput("Groups cannot be created in the trash.")
        g = self._new_group_el(name)
        parent.append(g)
        self.invalidate()
        return b64_to_hex(g.findtext("UUID"))

    def rename_group(self, hexid: str, name: str, notes: str | None = None) -> None:
        name = name.strip()
        if not name:
            raise errors.InvalidInput("A group needs a name.")
        g = self.group_el(hexid)
        _set_text(g, "Name", name)
        if notes is not None:
            _set_text(g, "Notes", notes)
        self._touch(g)

    def move_group(self, hexid: str, parent_id: str) -> None:
        g = self.group_el(hexid)
        if g is self.root_group:
            raise errors.InvalidInput("The root group cannot be moved.")
        self._move(g, self.group_el(parent_id))

    # ---- binaries / merge plumbing ----------------------------------------

    def binaries(self) -> list[bytes]:
        return self.kp.binaries

    def _binary_flags(self) -> dict[bytes, bool]:
        if self.version >= (4, 0):
            return {a.data[1:]: a.data[:1] == b"\x01" for a in self.kp.payload.inner_header.binary}
        return {}

    def set_binaries(self, pool: list[bytes]) -> None:
        from construct import Container, ListContainer

        if self.version >= (4, 0):
            flags = self._binary_flags()
            self.kp.payload.inner_header.binary = ListContainer(
                Container(type="binary", data=(b"\x01" if flags.get(d, True) else b"\x00") + d) for d in pool)
        else:
            meta = self.meta
            bins = meta.find("Binaries")
            if bins is None:
                if not pool:
                    return
                bins = etree.SubElement(meta, "Binaries")
            for c in list(bins):
                bins.remove(c)
            for i, d in enumerate(pool):
                b = etree.SubElement(bins, "Binary", ID=str(i), Compressed="False")
                b.text = base64.b64encode(d).decode()

    def _gc_binaries(self) -> None:
        """Drop pool items no longer referenced anywhere (incl. history)."""
        pool = self.binaries()
        if not pool:
            return
        used = set()
        refs = []
        for b in self.root.iter("Binary"):
            v = b.find("Value")
            if v is not None and v.get("Ref") is not None:
                try:
                    used.add(int(v.get("Ref")))
                    refs.append(v)
                except ValueError:
                    pass
        if len(used) == len(pool):
            return
        remap = {}
        new_pool = []
        for i, d in enumerate(pool):
            if i in used:
                remap[i] = len(new_pool)
                new_pool.append(d)
        for v in refs:
            v.set("Ref", str(remap[int(v.get("Ref"))]))
        self.set_binaries(new_pool)

    def adopt(self, new_root, pool: list[bytes]) -> None:
        """Replace decrypted content with a merge result."""
        tree = self.kp.tree
        tree._setroot(new_root)
        self.set_binaries(pool)
        self.invalidate()

    def clone_root(self):
        return copy.deepcopy(self.root)


# --------------------------------------------------------------------------


def _kdbx3_header_len(data: bytes) -> int:
    pos = 12
    while pos + 3 <= len(data):
        fid = data[pos]
        (length,) = struct.unpack("<H", data[pos + 1 : pos + 3])
        pos += 3 + length
        if fid == 0:
            return pos
    raise errors.SaveFailed(detail="kdbx3-header")


def _now_text(kp) -> str:
    return kp._encode_time(datetime.now(timezone.utc).replace(microsecond=0))


def _diff_fields(a, b) -> list[str]:
    names = {"UserName": "Username", "URL": "URL", "Password": "Password", "Title": "Title", "Notes": "Notes"}
    keys = {s.findtext("Key") for s in a.findall("String")} | {s.findtext("Key") for s in b.findall("String")}
    out = []
    for k in sorted(k for k in keys if k):
        ra, rb = get_string(a, k), get_string(b, k)
        if (ra[0] if ra else None) != (rb[0] if rb else None):
            out.append(names.get(k, k))
    if (a.findtext("Tags") or "") != (b.findtext("Tags") or ""):
        out.append("Tags")
    if [x.findtext("Key") for x in a.findall("Binary")] != [x.findtext("Key") for x in b.findall("Binary")]:
        out.append("Attachments")
    return out


def tune_argon2_iterations(target_ms: int, *, memory_kib: int, parallelism: int) -> int:
    """Benchmark Argon2id on this machine; never return fewer than 2 iterations."""
    import argon2

    salt = os.urandom(32)
    t0 = time.perf_counter()
    argon2.low_level.hash_secret_raw(b"keylane-benchmark", salt, time_cost=2, memory_cost=memory_kib,
                                     parallelism=parallelism, hash_len=32, type=argon2.low_level.Type.ID)
    per_iter = max((time.perf_counter() - t0) / 2, 1e-4)
    return max(2, min(100, int((target_ms / 1000.0) / per_iter)))
