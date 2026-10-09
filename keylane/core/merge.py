"""Deterministic three-way merge of decrypted KDBX XML documents.

The merge operates on the in-memory XML tree as held by pykeepass (protected
values already decrypted, ``Protected="True"`` kept on ``<Value>``).  It never
uses timestamps to decide which side wins (REQUIREMENTS R-02); timestamps are
only used as a stable display order for history and as bookkeeping values.

Design notes
------------
* Objects (entries, groups) are matched by UUID.  A *field* is the unit of the
  field-level three-way merge (each String key, each attachment key, each
  CustomData item, simple elements, location, unknown elements by tag).
* Ambiguity never results in silent loss: the merged tree keeps the local
  version (or the surviving version for edit/delete) and a :class:`Conflict`
  describes the alternatives until :func:`resolve` is called.
* Binary pools: each side's attachment Refs are first rewritten into one
  content-interned pool, so attachments compare by content.  At the end the
  pool is compacted to the binaries actually referenced by the merged tree and
  by the conflict versions (in document order), and every Ref is rewritten.
* ``Meta/Binaries`` (KDBX3 inline binary pool): if the local document has this
  element it is rebuilt from the merged pool as
  ``<Binary ID="i" Compressed="False">base64</Binary>`` so that pykeepass reads
  a consistent pool; otherwise (KDBX4) nothing is added and the caller writes
  ``MergeResult.binaries`` to the inner header.
* History: the history of an entry present on several sides is the union of
  all histories, deduplicated by semantic content and ordered by
  LastModificationTime (decoded; tie-break on canonical content).  Size limits
  are the caller's responsibility.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import uuid as uuidlib
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from lxml import etree

__all__ = [
    "Conflict",
    "MergeResult",
    "Side",
    "descends_from",
    "fingerprint",
    "merge",
    "resolve",
    "semantic_equal",
]

_Element = etree._Element
FieldKey = tuple[str, str]
BinaryKey = Callable[[str], Any]


@dataclass
class Side:
    """One input document with its binary pool (index == ``Ref``)."""

    root: _Element
    binaries: list[bytes] = field(default_factory=list)


@dataclass
class Conflict:
    """An ambiguity the user must resolve; the merged tree holds a safe placeholder."""

    id: str
    kind: str
    uuid: str | None
    reason: str
    fields: list[str]
    title: str
    local: _Element | None
    remote: _Element | None
    base: _Element | None
    local_parent: str | None
    remote_parent: str | None


@dataclass
class MergeResult:
    """Outcome of :func:`merge`."""

    root: _Element
    binaries: list[bytes]
    conflicts: list[Conflict]
    changed_local: bool
    changed_remote: bool
    notes: list[str]


# --------------------------------------------------------------------------
# constants

_MISSING = ("<missing>",)
_ZERO_UUID = "AAAAAAAAAAAAAAAAAAAAAA=="
_STD_STRINGS = ("Title", "UserName", "Password", "URL", "Notes")

_ENTRY_ORDER = (
    "UUID", "IconID", "CustomIconUUID", "ForegroundColor", "BackgroundColor",
    "OverrideURL", "QualityCheck", "Tags", "PreviousParentGroup", "Times",
    "CustomData", "String", "Binary", "AutoType", "History",
)
_ENTRY_SIMPLE = (
    "IconID", "CustomIconUUID", "ForegroundColor", "BackgroundColor",
    "OverrideURL", "Tags", "AutoType", "QualityCheck",
)
_ENTRY_IGNORED = {"UUID", "History", "PreviousParentGroup"}

_GROUP_ORDER = (
    "UUID", "Name", "Notes", "IconID", "CustomIconUUID", "Times", "IsExpanded",
    "DefaultAutoTypeSequence", "EnableAutoType", "EnableSearching",
    "LastTopVisibleEntry", "CustomData", "Tags", "PreviousParentGroup",
    "Entry", "Group",
)
_GROUP_SIMPLE = (
    "Name", "Notes", "IconID", "CustomIconUUID", "Tags", "EnableAutoType",
    "EnableSearching", "DefaultAutoTypeSequence",
)
_GROUP_IGNORED = {
    "UUID", "Times", "IsExpanded", "LastTopVisibleEntry", "PreviousParentGroup",
    "Entry", "Group",
}

_META_BOOKKEEPING_TIMES = {
    "SettingsChanged", "DatabaseNameChanged", "DatabaseDescriptionChanged",
    "DefaultUserNameChanged", "RecycleBinChanged", "EntryTemplatesGroupChanged",
    "MasterKeyChanged",
}
_META_TAKE_LOCAL = {"HeaderHash", "Generator", "LastSelectedGroup", "LastTopVisibleGroup"}


# --------------------------------------------------------------------------
# small XML helpers


def _is_el(node: Any) -> bool:
    return isinstance(node.tag, str)


def _children(el: _Element, tag: str | None = None) -> list[_Element]:
    return [c for c in el if _is_el(c) and (tag is None or c.tag == tag)]


def _uuid_of(el: _Element) -> str:
    return (el.findtext("UUID") or "").strip()


def _canon(el: _Element) -> tuple:
    """Order-preserving canonical form ignoring attribute order and layout whitespace."""
    kids = tuple(_canon(c) for c in el if _is_el(c))
    text = el.text or ""
    if kids and not text.strip():
        text = ""
    return (el.tag, tuple(sorted(el.attrib.items())), text, kids)


def _leaf_value(el: _Element) -> Any:
    """Canonical value of a simple element; empty elements equal a missing one."""
    c = _canon(el)
    if not c[1] and not c[3] and not c[2]:
        return _MISSING
    return c


def _is_protected(value: _Element | None) -> bool:
    if value is None:
        return False
    for attr in ("Protected", "ProtectInMemory"):
        if (value.get(attr) or "").strip().lower() == "true":
            return True
    return False


def _shell(el: _Element, drop: Iterable[str] = ("Entry", "Group")) -> _Element:
    """Deep copy of ``el`` without children whose tag is in ``drop``."""
    drop = set(drop)
    new = etree.Element(el.tag, dict(el.attrib))
    new.text = el.text
    for c in el:
        if _is_el(c) and c.tag in drop:
            continue
        new.append(copy.deepcopy(c))
    return new


def _rank(order: Sequence[str], tag: str) -> float:
    if tag in order:
        return float(order.index(tag))
    anchor = "History" if "History" in order else "Entry"
    return order.index(anchor) - 0.5


def _insert_ordered(parent: _Element, child: _Element, order: Sequence[str]) -> None:
    r = _rank(order, child.tag)
    for i, c in enumerate(parent):
        if _is_el(c) and _rank(order, c.tag) > r:
            parent.insert(i, child)
            return
    parent.append(child)


def _get_or_create(parent: _Element, tag: str, order: Sequence[str]) -> _Element:
    found = parent.find(tag)
    if found is None:
        found = etree.Element(tag)
        _insert_ordered(parent, found, order)
    return found


def _set_child_text(parent: _Element, tag: str, text: str) -> None:
    el = parent.find(tag)
    if el is None:
        el = etree.SubElement(parent, tag)
    el.text = text


# --------------------------------------------------------------------------
# time helpers (informational only)

_EPOCH = datetime(1, 1, 1, tzinfo=UTC)


def _time_key(text: str | None) -> tuple:
    t = (text or "").strip()
    if t:
        try:
            raw = base64.b64decode(t, validate=True)
            if len(raw) == 8:
                return (0, int.from_bytes(raw, "little"))
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (0, int((dt - _EPOCH).total_seconds()))
        except ValueError:
            pass
    return (1, t)


def _now_like(doc_root: _Element) -> str:
    """Current time encoded in the document's time format (KDBX4 base64 or ISO)."""
    sample = next((t.text for t in doc_root.iter("CreationTime") if t.text), None)
    now = datetime.now(UTC).replace(microsecond=0)
    if sample and "-" not in sample and ":" not in sample:
        secs = int((now - _EPOCH).total_seconds())
        return base64.b64encode(secs.to_bytes(8, "little")).decode()
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# field maps


def _binary_value(value: _Element | None, bkey: BinaryKey) -> Any:
    if value is None:
        return None
    ref = value.get("Ref")
    if ref is not None:
        return ("ref", bkey(ref))
    return ("inline", _canon(value))


def _ref_identity(ref: str) -> Any:
    return ref.strip()


def _pool_bkey(pool: Sequence[bytes]) -> BinaryKey:
    def key(ref: str) -> Any:
        try:
            return ("content", bytes(pool[int(ref)]))
        except (ValueError, IndexError):
            return ("ref", ref.strip())

    return key


def _custom_data_items(container: _Element | None) -> dict[str, _Element]:
    items: dict[str, _Element] = {}
    if container is not None:
        for item in container.findall("Item"):
            items.setdefault(item.findtext("Key") or "", item)
    return items


def _entry_fields(entry: _Element, bkey: BinaryKey = _ref_identity) -> dict[FieldKey, Any]:
    """Semantic fields of an entry (without location/history/bookkeeping times)."""
    out: dict[FieldKey, Any] = {}
    for child in entry:
        if not _is_el(child):
            continue
        tag = child.tag
        if tag == "String":
            v = child.find("Value")
            text = None if v is None else (v.text or "")
            out[("S", child.findtext("Key") or "")] = (text, _is_protected(v))
        elif tag == "Binary":
            out[("B", child.findtext("Key") or "")] = _binary_value(child.find("Value"), bkey)
        elif tag == "CustomData":
            for k, item in _custom_data_items(child).items():
                out[("C", k)] = item.findtext("Value") or ""
        elif tag == "Times":
            for t in ("Expires", "ExpiryTime"):
                te = child.find(t)
                if te is not None:
                    out[("T", t)] = (te.text or "").strip()
        elif tag in _ENTRY_SIMPLE:
            val = _leaf_value(child)
            if val is not _MISSING:
                out[("E", tag)] = val
        elif tag in _ENTRY_IGNORED:
            continue
        else:
            out[("U", tag)] = out.get(("U", tag), ()) + (_canon(child),)
    return out


def _group_fields(group: _Element) -> dict[FieldKey, Any]:
    """Semantic fields of a group (without children, location or volatile data)."""
    out: dict[FieldKey, Any] = {}
    for child in group:
        if not _is_el(child):
            continue
        tag = child.tag
        if tag == "CustomData":
            for k, item in _custom_data_items(child).items():
                out[("C", k)] = item.findtext("Value") or ""
        elif tag in _GROUP_SIMPLE:
            val = _leaf_value(child)
            if val is not _MISSING:
                out[("E", tag)] = val
        elif tag in _GROUP_IGNORED:
            continue
        else:
            out[("U", tag)] = out.get(("U", tag), ()) + (_canon(child),)
    return out


def _content_key(fields: dict[FieldKey, Any]) -> tuple:
    return tuple(sorted(fields.items(), key=lambda kv: kv[0]))


def _history_counter(entry: _Element, bkey: BinaryKey) -> Counter:
    hist = entry.find("History")
    if hist is None:
        return Counter()
    return Counter(_content_key(_entry_fields(h, bkey)) for h in hist.findall("Entry"))


def _field_name(key: FieldKey) -> str:
    cat, name = key
    if cat == "B":
        return f"Attachment: {name}"
    if cat == "C":
        return f"CustomData: {name}"
    if cat == "L":
        return "Location"
    return name


def _field_sort(key: FieldKey) -> tuple:
    cat, name = key
    cats = "LSBCETU"
    std = _STD_STRINGS.index(name) if cat == "S" and name in _STD_STRINGS else len(_STD_STRINGS)
    return (cats.index(cat) if cat in cats else len(cats), std, name)


def _field_elems(el: _Element, key: FieldKey) -> list[_Element]:
    cat, name = key
    if cat in ("S", "B"):
        tag = "String" if cat == "S" else "Binary"
        return [c for c in el.findall(tag) if (c.findtext("Key") or "") == name]
    if cat == "C":
        cd = el.find("CustomData")
        if cd is None:
            return []
        return [i for i in cd.findall("Item") if (i.findtext("Key") or "") == name]
    if cat == "T":
        times = el.find("Times")
        if times is None:
            return []
        return times.findall(name)
    if cat in ("E", "U"):
        return el.findall(name)
    return []


def _apply_field(target: _Element, key: FieldKey, source: _Element | None, order: Sequence[str]) -> None:
    """Make ``target``'s representation of ``key`` identical to ``source``'s."""
    olds = _field_elems(target, key)
    news = [copy.deepcopy(e) for e in _field_elems(source, key)] if source is not None else []
    if olds:
        parent = olds[0].getparent()
        idx = parent.index(olds[0])
        for o in olds:
            parent.remove(o)
        for j, n in enumerate(news):
            parent.insert(idx + j, n)
        return
    if not news:
        return
    cat = key[0]
    if cat == "T":
        container = _get_or_create(target, "Times", order)
        for n in news:
            container.append(n)
    elif cat == "C":
        container = _get_or_create(target, "CustomData", order)
        for n in news:
            container.append(n)
    else:
        for n in news:
            _insert_ordered(target, n, order)


def _three_way(
    fl: dict[FieldKey, Any],
    fr: dict[FieldKey, Any],
    fb: dict[FieldKey, Any] | None,
    *,
    additive_without_base: bool = False,
) -> tuple[list[FieldKey], list[FieldKey]]:
    """Return (fields to take from remote, conflicting fields)."""
    take_remote: list[FieldKey] = []
    conflicts: list[FieldKey] = []
    keys = set(fl) | set(fr) | (set(fb) if fb is not None else set())
    for k in sorted(keys, key=_field_sort):
        lv, rv = fl.get(k, _MISSING), fr.get(k, _MISSING)
        if lv == rv:
            continue
        if fb is None:
            if additive_without_base and lv == _MISSING:
                take_remote.append(k)
            elif additive_without_base and rv == _MISSING:
                continue
            else:
                conflicts.append(k)
            continue
        bv = fb.get(k, _MISSING)
        if lv == bv:
            take_remote.append(k)
        elif rv == bv:
            continue
        else:
            conflicts.append(k)
    return take_remote, conflicts


# --------------------------------------------------------------------------
# history


def _strip_history(entry: _Element) -> _Element:
    c = copy.deepcopy(entry)
    for h in c.findall("History"):
        c.remove(h)
    return c


def _history_items(entry: _Element | None) -> list[_Element]:
    if entry is None:
        return []
    hist = entry.find("History")
    return [] if hist is None else hist.findall("Entry")


def _merge_history(versions: Iterable[_Element], bkey: BinaryKey = _ref_identity) -> list[_Element]:
    """Deduplicate versions by content and order them deterministically."""
    cands = []
    for v in versions:
        c = _strip_history(v)
        lmt = c.findtext("Times/LastModificationTime")
        canon = _canon(c)
        cands.append(((_time_key(lmt), repr(canon)), _content_key(_entry_fields(c, bkey)), c))
    cands.sort(key=lambda t: t[0])
    seen: set = set()
    out = []
    for _, key, c in cands:
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _set_history(entry: _Element, versions: list[_Element]) -> None:
    had = entry.find("History") is not None
    for h in entry.findall("History"):
        entry.remove(h)
    if versions or had:
        hist = etree.Element("History")
        hist.extend(versions)
        _insert_ordered(entry, hist, _ENTRY_ORDER)


# --------------------------------------------------------------------------
# binary pool


class _Pool:
    """Content-interned binary pool used during a merge."""

    def __init__(self) -> None:
        self.data: list[bytes] = []
        self._index: dict[bytes, int] = {}

    def intern(self, data: bytes) -> int:
        b = bytes(data)
        if b not in self._index:
            self._index[b] = len(self.data)
            self.data.append(b)
        return self._index[b]


def _binary_values(el: _Element) -> list[_Element]:
    return [v for b in el.iter("Binary") for v in b.findall("Value") if v.get("Ref") is not None]


def _rewrite_refs(root: _Element, pool: _Pool, side_pool: Sequence[bytes]) -> None:
    rootnode = root.find("Root")
    if rootnode is None:
        return
    mapping: dict[str, int] = {}
    for v in _binary_values(rootnode):
        ref = (v.get("Ref") or "").strip()
        if ref not in mapping:
            try:
                idx = int(ref)
                if idx < 0:
                    raise IndexError
                mapping[ref] = pool.intern(side_pool[idx])
            except (ValueError, IndexError):
                raise ValueError("attachment references a binary missing from the pool") from None
        v.set("Ref", str(mapping[ref]))


# --------------------------------------------------------------------------
# document index


class _Doc:
    """UUID index of one KDBX XML document."""

    def __init__(self, root: _Element, alias_root: str | None = None) -> None:
        if root.tag != "KeePassFile":
            raise ValueError("expected a KeePassFile element")
        self.root = root
        rootnode = root.find("Root")
        group = rootnode.find("Group") if rootnode is not None else None
        if group is None:
            raise ValueError("document has no root group")
        self.orig_root_uuid = _uuid_of(group)
        self.root_uuid = alias_root or self.orig_root_uuid
        self.aliased = self.root_uuid != self.orig_root_uuid
        self.groups: dict[str, _Element] = {}
        self.group_parent: dict[str, str | None] = {}
        self.entries: dict[str, _Element] = {}
        self.entry_parent: dict[str, str] = {}
        self.pos: dict[str, int] = {}
        self.deleted: dict[str, _Element] = {}
        self._walk(group, None)
        deleted = rootnode.find("DeletedObjects")
        if deleted is not None:
            for d in deleted.findall("DeletedObject"):
                self.deleted.setdefault(self._u(_uuid_of(d)), d)

    def _u(self, u: str) -> str:
        return self.root_uuid if u == self.orig_root_uuid else u

    def _walk(self, group: _Element, parent: str | None) -> None:
        gid = self._u(_uuid_of(group))
        self.groups[gid] = group
        self.group_parent[gid] = parent
        self.pos.setdefault(gid, len(self.pos))
        for child in group:
            if not _is_el(child):
                continue
            if child.tag == "Entry":
                eid = _uuid_of(child)
                if eid in self.entries:
                    continue
                self.entries[eid] = child
                self.entry_parent[eid] = gid
                self.pos.setdefault(eid, len(self.pos))
            elif child.tag == "Group":
                if self._u(_uuid_of(child)) in self.groups:
                    continue
                self._walk(child, gid)

    @property
    def meta(self) -> _Element | None:
        return self.root.find("Meta")


def _meta_signature(meta: _Element | None) -> tuple:
    if meta is None:
        return ()
    plain: dict[str, tuple] = {}
    custom: dict[str, str] = {}
    icons: dict[str, tuple] = {}
    for c in _children(meta):
        if c.tag in _META_BOOKKEEPING_TIMES or c.tag in _META_TAKE_LOCAL or c.tag == "Binaries":
            continue
        if c.tag == "CustomData":
            custom = {k: i.findtext("Value") or "" for k, i in _custom_data_items(c).items()}
        elif c.tag == "CustomIcons":
            icons = {_uuid_of(i): _icon_value(i) for i in c.findall("Icon")}
        else:
            plain[c.tag] = plain.get(c.tag, ()) + (_leaf_value(c),)
    return (sorted(plain.items()), sorted(custom.items()), sorted(icons.items()))


def _extras_signature(el: _Element | None, skip: set[str]) -> tuple:
    if el is None:
        return ()
    out: dict[str, tuple] = {}
    for c in _children(el):
        if c.tag not in skip:
            out[c.tag] = out.get(c.tag, ()) + (_canon(c),)
    return tuple(sorted(out.items()))


def _doc_signature(doc: _Doc, bkey: BinaryKey = _ref_identity) -> tuple:
    """Semantic signature of a whole document (order-insensitive)."""

    def norm(u: str | None) -> str | None:
        return "<root>" if u == doc.root_uuid else u

    groups = {
        norm(g): (_content_key(_group_fields(el)), norm(doc.group_parent[g]))
        for g, el in doc.groups.items()
    }
    entries = {
        e: (
            _content_key(_entry_fields(el, bkey)),
            norm(doc.entry_parent[e]),
            tuple(sorted(_history_counter(el, bkey).items(), key=repr)),
        )
        for e, el in doc.entries.items()
    }
    deleted = frozenset(norm(u) for u in doc.deleted)
    return (
        _meta_signature(doc.meta),
        _extras_signature(doc.root, {"Meta", "Root"}),
        _extras_signature(doc.root.find("Root"), {"Group", "DeletedObjects"}),
        sorted(groups.items(), key=lambda kv: kv[0] or ""),
        sorted(entries.items()),
        deleted,
    )


def _icon_value(icon: _Element) -> tuple:
    return tuple(sorted((c.tag, (c.text or "").strip()) for c in _children(icon) if c.tag in ("Data", "Name")))


# --------------------------------------------------------------------------
# public comparison


def semantic_equal(
    a: _Element,
    b: _Element,
    pool_a: Sequence[bytes] = (),
    pool_b: Sequence[bytes] = (),
) -> bool:
    """Compare two KDBX elements semantically.

    Volatile/access-only data (LastAccessTime, UsageCount, modification times),
    attribute order, layout whitespace and binary Ref numbering are ignored;
    attachments compare by content when pools are given.  History is compared
    as a multiset of semantic versions.  Supported: ``Entry``, ``Group`` (own
    fields plus children recursively), ``KeePassFile``; anything else is
    compared canonically.
    """
    ka = _pool_bkey(pool_a) if pool_a else _ref_identity
    kb = _pool_bkey(pool_b) if pool_b else _ref_identity
    if a.tag != b.tag:
        return False
    if a.tag == "Entry":
        return (
            _entry_fields(a, ka) == _entry_fields(b, kb)
            and _history_counter(a, ka) == _history_counter(b, kb)
        )
    if a.tag == "Group":
        return _group_tree_sig(a, ka) == _group_tree_sig(b, kb)
    if a.tag == "KeePassFile":
        return _doc_signature(_Doc(a), ka) == _doc_signature(_Doc(b), kb)
    return _canon(a) == _canon(b)


def _hash_bkey(pool: Sequence[bytes]) -> BinaryKey:
    def key(ref: str) -> Any:
        try:
            return ("sha256", hashlib.sha256(bytes(pool[int(ref)])).hexdigest())
        except (ValueError, IndexError):
            return ("ref", ref.strip())

    return key


def fingerprint(el: _Element, pool: Sequence[bytes] = ()) -> str:
    """Stable SHA-256 hex digest of an Entry's or Group's semantic content.

    Uses the same equivalence as :func:`semantic_equal` but excludes the
    History and the parent/location; for groups only the group's own fields
    (not its children) are covered.  Binary refs are hashed by content when a
    pool is given.
    """
    bkey = _hash_bkey(pool) if pool else _ref_identity
    if el.tag == "Entry":
        fields = _entry_fields(el, bkey)
    elif el.tag == "Group":
        fields = _group_fields(el)
    else:
        raise ValueError("fingerprint supports Entry and Group elements only")
    return hashlib.sha256(repr((el.tag, _content_key(fields))).encode("utf-8")).hexdigest()


def descends_from(ancestor: Side, descendant: Side) -> bool:
    """True if ``descendant`` provably contains all of ``ancestor``'s entry knowledge.

    Only content/history evidence is used (never timestamps).  Every ancestor
    entry must be present in the descendant with the same fingerprint, appear
    in the descendant entry's History, or be listed in its DeletedObjects.
    Every ancestor group must be present (any content) or listed as deleted.
    Location changes are irrelevant.
    """
    anc = _Doc(ancestor.root)
    desc = _Doc(descendant.root, anc.root_uuid)
    for u, a_el in anc.entries.items():
        if u in desc.deleted:
            continue
        d_el = desc.entries.get(u)
        if d_el is None:
            return False
        fp = fingerprint(a_el, ancestor.binaries)
        if fingerprint(d_el, descendant.binaries) == fp:
            continue
        if not any(fingerprint(h, descendant.binaries) == fp for h in _history_items(d_el)):
            return False
    return all(g in desc.groups or g in desc.deleted for g in anc.groups)


def _group_tree_sig(group: _Element, bkey: BinaryKey) -> tuple:
    entries = {}
    groups = {}
    for c in _children(group):
        if c.tag == "Entry":
            entries[_uuid_of(c)] = (
                _content_key(_entry_fields(c, bkey)),
                tuple(sorted(_history_counter(c, bkey).items(), key=repr)),
            )
        elif c.tag == "Group":
            groups[_uuid_of(c)] = _group_tree_sig(c, bkey)
    return (_content_key(_group_fields(group)), sorted(entries.items()), sorted(groups.items()))


# --------------------------------------------------------------------------
# the merger


@dataclass
class _Kept:
    elem: _Element
    parent: str | None


class _Merger:
    def __init__(self, base: Side | None, local: Side, remote: Side) -> None:
        self.pool = _Pool()
        self.l = self._load(local, None)
        self.r = self._load(remote, self.l.root_uuid)
        self.b = self._load(base, self.l.root_uuid) if base is not None else None
        self.conflicts: dict[str, Conflict] = {}
        self.notes: list[str] = []
        self.kept_entries: dict[str, _Kept] = {}
        self.kept_groups: dict[str, _Kept] = {}
        self.tentative_groups: dict[str, _Kept] = {}
        self.deletion_conflict_entries: set[str] = set()
        self.new_deletions: dict[str, str] = {}
        self.counts: Counter = Counter()

    def _load(self, side: Side, alias: str | None) -> _Doc:
        root = copy.deepcopy(side.root)
        _rewrite_refs(root, self.pool, side.binaries)
        return _Doc(root, alias)

    # ---- helpers

    def _efields(self, doc: _Doc, u: str, with_loc: bool = True) -> dict[FieldKey, Any]:
        f = _entry_fields(doc.entries[u])
        if with_loc:
            f[("L", "")] = doc.entry_parent[u]
        return f

    def _gfields(self, doc: _Doc, u: str) -> dict[FieldKey, Any]:
        f = _group_fields(doc.groups[u])
        if u != self.l.root_uuid:
            f[("L", "")] = doc.group_parent[u]
        return f

    def _record_deletion(self, u: str, base_el: _Element | None) -> None:
        when = ""
        if base_el is not None:
            when = base_el.findtext("Times/LastModificationTime") or ""
        self.new_deletions[u] = when

    def _add_conflict(
        self,
        kind: str,
        u: str,
        reason: str,
        fields: Iterable[FieldKey],
        L: _Element | None,
        R: _Element | None,
        B: _Element | None,
        lp: str | None,
        rp: str | None,
    ) -> None:
        if kind == "group":
            L, R, B = (None if x is None else _shell(x) for x in (L, R, B))
            title_tag = "Name"
        else:
            L, R, B = (None if x is None else copy.deepcopy(x) for x in (L, R, B))
            title_tag = "String[Key='Title']/Value"
        title = ""
        for el in (L, R, B):
            if el is not None:
                t = el.findtext(title_tag)
                if t:
                    title = t
                    break
        names = list(dict.fromkeys(_field_name(k) for k in sorted(fields, key=_field_sort)))
        cid = f"{kind}:{u}"
        self.conflicts[cid] = Conflict(cid, kind, u, reason, names, title, L, R, B, lp, rp)

    # ---- entries

    def merge_entries(self) -> None:
        l, r, b = self.l, self.r, self.b
        ids = set(l.entries) | set(r.entries) | (set(b.entries) if b else set())
        for u in sorted(ids):
            self._merge_entry(u)

    def _with_history(self, elem: _Element, sources: Iterable[_Element | None], extra: Iterable[_Element] = ()) -> _Element:
        out = copy.deepcopy(elem)
        versions: list[_Element] = []
        for s in sources:
            versions.extend(_history_items(s))
        versions.extend(extra)
        _set_history(out, _merge_history(versions))
        return out

    def _merge_entry(self, u: str) -> None:
        l, r, b = self.l, self.r, self.b
        L = l.entries.get(u)
        R = r.entries.get(u)
        B = b.entries.get(u) if b else None
        if L is None and R is None:
            if B is not None:
                self._record_deletion(u, B)
            return
        if L is not None and R is not None:
            if B is not None:
                self._merge_entry_3way(u, L, R, B)
            else:
                self._merge_entry_no_base(u, L, R)
            return
        # present on exactly one side
        side, other = (l, r) if L is not None else (r, l)
        el = L if L is not None else R
        parent = side.entry_parent[u]
        if B is not None:
            fs = self._efields(side, u)
            fb = self._efields(b, u)
            if fs == fb:
                self._record_deletion(u, B)
                self.counts["deleted"] += 1
                return
            reason = "edit-vs-delete" if L is not None else "delete-vs-edit"
            changed = [k for k in set(fs) | set(fb) if fs.get(k, _MISSING) != fb.get(k, _MISSING)]
            self.kept_entries[u] = _Kept(self._with_history(el, [el, B]), parent)
            self.deletion_conflict_entries.add(u)
            self._add_conflict(
                "entry", u, reason, changed, L, R, B,
                l.entry_parent.get(u), r.entry_parent.get(u),
            )
            return
        self.kept_entries[u] = _Kept(copy.deepcopy(el), parent)
        if u in other.deleted:
            self.deletion_conflict_entries.add(u)
            self._add_conflict(
                "entry", u, "no-ancestry", [], L, R, None,
                l.entry_parent.get(u), r.entry_parent.get(u),
            )
        else:
            self.counts["added"] += 1

    def _merge_entry_3way(self, u: str, L: _Element, R: _Element, B: _Element) -> None:
        l, r = self.l, self.r
        fl, fr, fb = self._efields(l, u), self._efields(r, u), self._efields(self.b, u)
        sources = [L, R, B]
        take_remote, conflicts = _three_way(fl, fr, fb)
        if conflicts:
            self.kept_entries[u] = _Kept(self._with_history(L, sources), l.entry_parent[u])
            reason = "move-conflict" if conflicts == [("L", "")] else "both-modified"
            self._add_conflict(
                "entry", u, reason, conflicts, L, R, B,
                l.entry_parent[u], r.entry_parent[u],
            )
            return
        if not take_remote or fr == fl:
            self.kept_entries[u] = _Kept(self._with_history(L, sources), l.entry_parent[u])
            return
        if fl == fb:
            self.kept_entries[u] = _Kept(self._with_history(R, sources), r.entry_parent[u])
            self.counts["took-remote"] += 1
            return
        # both sides changed different fields: field-level auto-merge
        merged = copy.deepcopy(L)
        parent = l.entry_parent[u]
        for k in take_remote:
            if k[0] == "L":
                parent = r.entry_parent[u]
                _apply_field(merged, ("E", "PreviousParentGroup"), R, _ENTRY_ORDER)
                _apply_field(merged, ("T", "LocationChanged"), R, _ENTRY_ORDER)
            else:
                _apply_field(merged, k, R, _ENTRY_ORDER)
        lmts = [x.findtext("Times/LastModificationTime") for x in (L, R)]
        lmts = [t for t in lmts if t]
        if lmts and merged.find("Times") is not None:
            _set_child_text(merged.find("Times"), "LastModificationTime", max(lmts, key=_time_key))
        pre = [_strip_history(L), _strip_history(R)]
        self.kept_entries[u] = _Kept(self._with_history(merged, sources, pre), parent)
        self.counts["field-merged"] += 1

    def _merge_entry_no_base(self, u: str, L: _Element, R: _Element) -> None:
        l, r = self.l, self.r
        fl, fr = self._efields(l, u), self._efields(r, u)
        sources = [L, R]
        if fl == fr:
            self.kept_entries[u] = _Kept(self._with_history(L, sources), l.entry_parent[u])
            return
        cl = _content_key(_entry_fields(L))
        cr = _content_key(_entry_fields(R))
        r_hist = {_content_key(_entry_fields(h)) for h in _history_items(R)}
        l_hist = {_content_key(_entry_fields(h)) for h in _history_items(L)}
        r_desc = cl in r_hist and cr not in l_hist
        l_desc = cr in l_hist and cl not in r_hist
        if cl != cr and r_desc:
            self.kept_entries[u] = _Kept(self._with_history(R, sources), r.entry_parent[u])
            self.counts["fast-forward"] += 1
            return
        if cl != cr and l_desc:
            self.kept_entries[u] = _Kept(self._with_history(L, sources), l.entry_parent[u])
            self.counts["fast-forward"] += 1
            return
        diff = [k for k in set(fl) | set(fr) if fl.get(k, _MISSING) != fr.get(k, _MISSING)]
        self.kept_entries[u] = _Kept(self._with_history(L, sources), l.entry_parent[u])
        self._add_conflict(
            "entry", u, "no-ancestry", diff, L, R, None, l.entry_parent[u], r.entry_parent[u],
        )

    # ---- groups

    def merge_groups(self) -> None:
        l, r, b = self.l, self.r, self.b
        ids = set(l.groups) | set(r.groups) | (set(b.groups) if b else set())
        for u in sorted(ids):
            self._merge_group(u)

    def _merge_group(self, u: str) -> None:
        l, r, b = self.l, self.r, self.b
        L = l.groups.get(u)
        R = r.groups.get(u)
        B = b.groups.get(u) if b else None
        if u == l.root_uuid:
            self._merge_root_group(L, R, B)
            return
        if L is None and R is None:
            if B is not None:
                self._record_deletion(u, B)
            return
        if L is not None and R is not None:
            fl, fr = self._gfields(l, u), self._gfields(r, u)
            fb = self._gfields(b, u) if B is not None else None
            take_remote, conflicts = _three_way(fl, fr, fb)
            if conflicts:
                reason = "both-modified" if fb is not None else "no-ancestry"
                if conflicts == [("L", "")]:
                    reason = "move-conflict"
                self.kept_groups[u] = _Kept(_shell(L), l.group_parent[u])
                self._add_conflict(
                    "group", u, reason, conflicts, L, R, B, l.group_parent[u], r.group_parent[u],
                )
                return
            merged = _shell(L)
            parent = l.group_parent[u]
            for k in take_remote:
                if k[0] == "L":
                    parent = r.group_parent[u]
                    _apply_field(merged, ("E", "PreviousParentGroup"), R, _GROUP_ORDER)
                    _apply_field(merged, ("T", "LocationChanged"), R, _GROUP_ORDER)
                else:
                    _apply_field(merged, k, R, _GROUP_ORDER)
            self.kept_groups[u] = _Kept(merged, parent)
            return
        side, other = (l, r) if L is not None else (r, l)
        el = L if L is not None else R
        parent = side.group_parent[u]
        if B is not None:
            fs, fb = self._gfields(side, u), self._gfields(b, u)
            if fs == fb:
                self.tentative_groups[u] = _Kept(_shell(el), parent)
                self._record_deletion(u, B)
                return
            changed = [k for k in set(fs) | set(fb) if fs.get(k, _MISSING) != fb.get(k, _MISSING)]
            self.kept_groups[u] = _Kept(_shell(el), parent)
            self._add_conflict(
                "group", u, "group-move-vs-delete", changed, L, R, B,
                l.group_parent.get(u), r.group_parent.get(u),
            )
            return
        self.kept_groups[u] = _Kept(_shell(el), parent)
        if u in other.deleted:
            self._add_conflict(
                "group", u, "no-ancestry", [], L, R, None,
                l.group_parent.get(u), r.group_parent.get(u),
            )

    def _merge_root_group(self, L: _Element, R: _Element | None, B: _Element | None) -> None:
        u = self.l.root_uuid
        if R is None or self.r.aliased:
            if self.r.aliased:
                self.notes.append("Remote root group has a different UUID; its contents were merged into the local root group.")
            self.kept_groups[u] = _Kept(_shell(L), None)
            return
        fl, fr = _group_fields(L), _group_fields(R)
        fb = _group_fields(B) if B is not None and not self.b.aliased else None
        take_remote, conflicts = _three_way(fl, fr, fb)
        merged = _shell(L)
        for k in take_remote:
            _apply_field(merged, k, R, _GROUP_ORDER)
        self.kept_groups[u] = _Kept(merged, None)
        if conflicts:
            reason = "both-modified" if fb is not None else "no-ancestry"
            self._add_conflict("group", u, reason, conflicts, L, R, B, None, None)

    # ---- structure

    def _close_required(self) -> None:
        """Restore tentatively deleted groups that still have kept content."""
        changed = True
        while changed:
            changed = False
            needed = {k.parent for k in self.kept_entries.values()}
            needed |= {k.parent for k in self.kept_groups.values() if k.parent}
            for p in sorted(x for x in needed if x and x not in self.kept_groups):
                if p in self.tentative_groups:
                    self.kept_groups[p] = self.tentative_groups.pop(p)
                    self.new_deletions.pop(p, None)
                    self.restored.add(p)
                    changed = True

    def _fix_cycles(self) -> None:
        for g in sorted(self.kept_groups):
            seen: list[str] = []
            cur: str | None = g
            while cur is not None and cur in self.kept_groups and cur not in seen:
                seen.append(cur)
                cur = self.kept_groups[cur].parent
            if cur is None or cur not in seen:
                continue
            cycle = seen[seen.index(cur):]
            fixable = [c for c in sorted(cycle) if c in self.l.groups and self.kept_groups[c].parent != self.l.group_parent[c]]
            if fixable:
                c = fixable[0]
                self.kept_groups[c].parent = self.l.group_parent[c]
            else:
                c = sorted(cycle)[0]
                self.kept_groups[c].parent = self.l.root_uuid
            cid = f"group:{c}"
            if cid in self.conflicts:
                conf = self.conflicts[cid]
                if "Location" not in conf.fields:
                    conf.fields.insert(0, "Location")
            else:
                B = self.b.groups.get(c) if self.b else None
                self._add_conflict(
                    "group", c, "move-conflict", [("L", "")], self.l.groups.get(c), self.r.groups.get(c), B,
                    self.l.group_parent.get(c), self.r.group_parent.get(c),
                )
            self._fix_cycles()
            return

    def _depth(self, g: str) -> int:
        d, cur, seen = 0, self.kept_groups[g].parent, set()
        while cur is not None and cur in self.kept_groups and cur not in seen:
            seen.add(cur)
            d += 1
            cur = self.kept_groups[cur].parent
        return d

    def _restored_conflicts(self) -> None:
        strong: set[str] = set()
        for g in sorted(self.restored, key=lambda x: (-self._depth(x), x)):
            child_entries = [e for e, k in self.kept_entries.items() if k.parent == g]
            child_groups = [c for c, k in self.kept_groups.items() if k.parent == g]
            is_strong = any(e not in self.deletion_conflict_entries for e in child_entries) or any(
                c not in self.restored or c in strong for c in child_groups
            )
            if is_strong:
                strong.add(g)
                B = self.b.groups.get(g) if self.b else None
                self._add_conflict(
                    "group", g, "group-move-vs-delete", [], self.l.groups.get(g), self.r.groups.get(g), B,
                    self.l.group_parent.get(g), self.r.group_parent.get(g),
                )
                self.conflicts[f"group:{g}"].fields = ["Contents"]
            else:
                self.notes.append(f"Group {g} kept because entries inside it have unresolved edit/delete conflicts.")

    def _fallback_parents(self) -> None:
        root = self.l.root_uuid
        for u, k in sorted(self.kept_entries.items()):
            if k.parent not in self.kept_groups:
                k.parent = root
                self.notes.append(f"Entry {u} placed in the root group because its group no longer exists.")
        for u, k in sorted(self.kept_groups.items()):
            if u != root and k.parent not in self.kept_groups:
                k.parent = root
                self.notes.append(f"Group {u} placed in the root group because its parent no longer exists.")

    def _order_key(self, u: str, parent: str, is_group: bool) -> tuple:
        lp = (self.l.group_parent if is_group else self.l.entry_parent).get(u, _MISSING)
        rp = (self.r.group_parent if is_group else self.r.entry_parent).get(u, _MISSING)
        if lp == parent:
            return (0, self.l.pos[u], u)
        if rp == parent:
            return (1, self.r.pos[u], u)
        if u in self.l.pos:
            return (2, self.l.pos[u], u)
        if u in self.r.pos:
            return (3, self.r.pos[u], u)
        return (4, 0, u)

    def _build_group(self, g: str) -> _Element:
        el = copy.deepcopy(self.kept_groups[g].elem)
        entries = sorted(
            (u for u, k in self.kept_entries.items() if k.parent == g),
            key=lambda u: self._order_key(u, g, False),
        )
        for u in entries:
            el.append(self.kept_entries[u].elem)
        groups = sorted(
            (u for u, k in self.kept_groups.items() if k.parent == g and u != self.l.root_uuid),
            key=lambda u: self._order_key(u, g, True),
        )
        for u in groups:
            el.append(self._build_group(u))
        return el

    # ---- meta & extras

    def _merge_items(
        self,
        lc: _Element | None,
        rc: _Element | None,
        bc: _Element | None,
        item_tag: str,
        key_of: Callable[[_Element], str],
        value_of: Callable[[_Element], Any],
        id_prefix: str,
        field_prefix: str,
    ) -> _Element | None:
        """Three-way merge of keyed items (Meta CustomData items, custom icons)."""
        def items(c: _Element | None) -> dict[str, _Element]:
            out: dict[str, _Element] = {}
            if c is not None:
                for i in c.findall(item_tag):
                    out.setdefault(key_of(i), i)
            return out

        li, ri = items(lc), items(rc)
        bi = items(bc) if self.b is not None else None
        if lc is None and rc is None:
            return None
        result = copy.deepcopy(lc) if lc is not None else etree.Element(rc.tag)
        res_items = items(result)
        for k in list(dict.fromkeys(list(li) + list(ri))):
            lv = value_of(li[k]) if k in li else _MISSING
            rv = value_of(ri[k]) if k in ri else _MISSING
            if lv == rv:
                continue
            if bi is None:
                if lv == _MISSING:
                    result.append(copy.deepcopy(ri[k]))
                elif rv != _MISSING:
                    self._meta_conflict(f"{id_prefix}{k}", f"{field_prefix}{k}", li.get(k), ri.get(k), None)
                continue
            bv = value_of(bi[k]) if k in bi else _MISSING
            if lv == bv:
                if rv == _MISSING:
                    result.remove(res_items[k])
                elif k in res_items:
                    result.replace(res_items[k], copy.deepcopy(ri[k]))
                else:
                    result.append(copy.deepcopy(ri[k]))
            elif rv == bv:
                continue
            else:
                self._meta_conflict(f"{id_prefix}{k}", f"{field_prefix}{k}", li.get(k), ri.get(k), bi.get(k))
        return result

    def _meta_conflict(self, cid_suffix: str, name: str, L: _Element | None, R: _Element | None, B: _Element | None) -> None:
        cid = f"meta:{cid_suffix}"
        self.conflicts[cid] = Conflict(
            cid, "meta", None, "meta", [name], name,
            None if L is None else copy.deepcopy(L),
            None if R is None else copy.deepcopy(R),
            None if B is None else copy.deepcopy(B),
            None, None,
        )

    def _merge_tags(
        self,
        result: _Element,
        lc: _Element | None,
        rc: _Element | None,
        bc: _Element | None,
        skip: set[str],
        prefix: str,
    ) -> None:
        """Three-way merge of the direct children of a container, keyed by tag."""
        def by_tag(c: _Element | None) -> dict[str, list[_Element]]:
            out: dict[str, list[_Element]] = {}
            if c is not None:
                for ch in _children(c):
                    if ch.tag not in skip:
                        out.setdefault(ch.tag, []).append(ch)
            return out

        lt, rt = by_tag(lc), by_tag(rc)
        bt = by_tag(bc) if self.b is not None else None
        for tag in list(dict.fromkeys(list(lt) + list(rt))):
            lv = tuple(_leaf_value(e) for e in lt.get(tag, [])) or _MISSING
            rv = tuple(_leaf_value(e) for e in rt.get(tag, [])) or _MISSING
            if lv == rv:
                continue
            take_remote = False
            if bt is None:
                if lv == _MISSING or all(v == _MISSING for v in lv):
                    take_remote = True
                elif rv == _MISSING or all(v == _MISSING for v in rv):
                    continue
                else:
                    self._meta_conflict(f"{prefix}{tag}", tag, lt[tag][0], rt[tag][0], None)
                    continue
            else:
                bv = tuple(_leaf_value(e) for e in bt.get(tag, [])) or _MISSING
                if lv == bv:
                    take_remote = True
                elif rv != bv:
                    self._meta_conflict(
                        f"{prefix}{tag}", tag,
                        lt[tag][0] if tag in lt else None,
                        rt[tag][0] if tag in rt else None,
                        bt[tag][0] if tag in bt else None,
                    )
                    continue
            if take_remote:
                _replace_tag(result, tag, [copy.deepcopy(e) for e in rt.get(tag, [])])

    def merge_meta(self, out_root: _Element) -> None:
        lm, rm = self.l.meta, self.r.meta
        bm = self.b.meta if self.b else None
        meta = out_root.find("Meta")
        if meta is None:
            if rm is None:
                return
            meta = etree.Element("Meta")
            out_root.insert(0, meta)
        skip = _META_BOOKKEEPING_TIMES | _META_TAKE_LOCAL | {"Binaries", "CustomData", "CustomIcons"}
        self._merge_tags(meta, lm, rm, bm, skip, "")
        for tag in sorted(_META_BOOKKEEPING_TIMES):
            vals = [m.findtext(tag) for m in (lm, rm) if m is not None and m.findtext(tag)]
            if vals and meta.find(tag) is not None:
                meta.find(tag).text = max(vals, key=_time_key)
            elif vals and rm is not None and rm.find(tag) is not None:
                meta.append(copy.deepcopy(rm.find(tag)))
        for tag, item_tag, key_of, value_of, field_prefix in (
            ("CustomData", "Item", lambda i: i.findtext("Key") or "", lambda i: i.findtext("Value") or "", "CustomData: "),
            ("CustomIcons", "Icon", _uuid_of, _icon_value, "CustomIcon: "),
        ):
            merged = self._merge_items(
                None if lm is None else lm.find(tag),
                None if rm is None else rm.find(tag),
                None if bm is None else bm.find(tag),
                item_tag, key_of, value_of, f"{tag}:", field_prefix,
            )
            if merged is not None:
                _replace_tag(meta, tag, [merged])

    def merge_extras(self, out_root: _Element) -> None:
        b_root = self.b.root if self.b else None
        self._merge_tags(out_root, self.l.root, self.r.root, b_root, {"Meta", "Root"}, "KeePassFile/")
        self._merge_tags(
            out_root.find("Root"), self.l.root.find("Root"), self.r.root.find("Root"),
            b_root.find("Root") if b_root is not None else None, {"Group", "DeletedObjects"}, "Root/",
        )

    def build_deleted(self, rootnode: _Element, present: set[str]) -> None:
        deleted = rootnode.find("DeletedObjects")
        if deleted is None:
            deleted = etree.Element("DeletedObjects")
            rootnode.append(deleted)
        for d in list(deleted):
            deleted.remove(d)
        seen: set[str] = set()
        docs = [self.l, self.r] + ([self.b] if self.b else [])
        for doc in docs:
            for u, d in doc.deleted.items():
                if u in seen or u in present:
                    continue
                seen.add(u)
                deleted.append(copy.deepcopy(d))
        for u in sorted(self.new_deletions):
            if u in seen or u in present:
                continue
            seen.add(u)
            d = etree.SubElement(deleted, "DeletedObject")
            etree.SubElement(d, "UUID").text = u
            etree.SubElement(d, "DeletionTime").text = self.new_deletions[u]

    # ---- driver

    def run(self) -> MergeResult:
        self.restored: set[str] = set()
        self.merge_entries()
        self.merge_groups()
        self._close_required()
        self._fix_cycles()
        self._close_required()
        self._restored_conflicts()
        self._fallback_parents()

        out_root = copy.deepcopy(self.l.root)
        rootnode = out_root.find("Root")
        rootnode.replace(rootnode.find("Group"), self._build_group(self.l.root_uuid))
        present = set(self.kept_entries) | set(self.kept_groups)
        self.build_deleted(rootnode, present)
        self.merge_meta(out_root)
        self.merge_extras(out_root)

        sig = _doc_signature(_Doc(out_root))
        changed_local = sig != _doc_signature(self.l)
        changed_remote = sig != _doc_signature(self.r)

        conflicts = [self.conflicts[k] for k in sorted(self.conflicts)]
        binaries = _compact(out_root, conflicts, self.pool)
        _rebuild_meta_binaries(out_root, binaries)

        notes = list(self.notes)
        for label, key in (
            ("entries auto-merged field by field", "field-merged"),
            ("entries updated from remote", "took-remote"),
            ("entries fast-forwarded using history ancestry", "fast-forward"),
            ("entries deleted on one side and unchanged on the other were removed", "deleted"),
        ):
            if self.counts[key]:
                notes.append(f"{self.counts[key]} {label}.")
        if conflicts:
            notes.append(f"{len(conflicts)} conflict(s) need resolution.")
        return MergeResult(out_root, binaries, conflicts, changed_local, changed_remote, notes)


def _replace_tag(container: _Element, tag: str, news: list[_Element]) -> None:
    olds = container.findall(tag)
    if olds:
        idx = container.index(olds[0])
        for o in olds:
            container.remove(o)
    else:
        idx = len(container)
    for j, n in enumerate(news):
        container.insert(idx + j, n)


def _compact(out_root: _Element, conflicts: list[Conflict], pool: _Pool) -> list[bytes]:
    """Renumber Refs in document order into a pool holding only used binaries."""
    mapping: dict[str, int] = {}
    data: list[bytes] = []
    targets: list[_Element] = []
    rootnode = out_root.find("Root")
    if rootnode is not None:
        targets.append(rootnode)
    for c in conflicts:
        targets.extend(x for x in (c.local, c.remote, c.base) if x is not None)
    for t in targets:
        for v in _binary_values(t):
            ref = v.get("Ref")
            if ref not in mapping:
                mapping[ref] = len(data)
                data.append(pool.data[int(ref)])
            v.set("Ref", str(mapping[ref]))
    return data


def _rebuild_meta_binaries(out_root: _Element, binaries: list[bytes]) -> None:
    meta = out_root.find("Meta")
    bins = meta.find("Binaries") if meta is not None else None
    if bins is None:
        return
    for c in list(bins):
        bins.remove(c)
    for i, data in enumerate(binaries):
        el = etree.SubElement(bins, "Binary", ID=str(i), Compressed="False")
        el.text = base64.b64encode(data).decode()


def merge(base: Side | None, local: Side, remote: Side) -> MergeResult:
    """Three-way merge ``local`` and ``remote`` against their common ``base``.

    ``base`` may be ``None`` when no trustworthy ancestor is known; then only
    additions and history-proven descendants are merged automatically.
    Inputs are never mutated and the result is deterministic.
    """
    return _Merger(base, local, remote).run()


# --------------------------------------------------------------------------
# resolution


def _find_conflict(result: MergeResult, conflict_id: str) -> Conflict:
    for c in result.conflicts:
        if c.id == conflict_id:
            return c
    raise ValueError(f"unknown conflict id: {conflict_id}")


def _live_entries(root: _Element) -> dict[str, _Element]:
    out: dict[str, _Element] = {}
    rootnode = root.find("Root")
    group = rootnode.find("Group") if rootnode is not None else None
    if group is None:
        return out
    stack = [group]
    while stack:
        g = stack.pop()
        for c in _children(g):
            if c.tag == "Entry":
                out.setdefault(_uuid_of(c), c)
            elif c.tag == "Group":
                stack.append(c)
    return out


def _live_groups(root: _Element) -> dict[str, _Element]:
    out: dict[str, _Element] = {}
    rootnode = root.find("Root")
    group = rootnode.find("Group") if rootnode is not None else None
    stack = [group] if group is not None else []
    while stack:
        g = stack.pop()
        out.setdefault(_uuid_of(g), g)
        stack.extend(_children(g, "Group"))
    return out


def _add_deleted_record(root: _Element, u: str) -> None:
    rootnode = root.find("Root")
    deleted = rootnode.find("DeletedObjects")
    if deleted is None:
        deleted = etree.SubElement(rootnode, "DeletedObjects")
    if any(_uuid_of(d) == u for d in deleted.findall("DeletedObject")):
        return
    d = etree.SubElement(deleted, "DeletedObject")
    etree.SubElement(d, "UUID").text = u
    etree.SubElement(d, "DeletionTime").text = _now_like(root)


def _remove_deleted_record(root: _Element, u: str) -> None:
    deleted = root.find("Root/DeletedObjects")
    if deleted is not None:
        for d in deleted.findall("DeletedObject"):
            if _uuid_of(d) == u:
                deleted.remove(d)


def _append_entry(group: _Element, entry: _Element) -> None:
    last = None
    for c in _children(group, "Entry"):
        last = c
    if last is not None:
        group.insert(group.index(last) + 1, entry)
    else:
        first_group = group.find("Group")
        if first_group is not None:
            group.insert(group.index(first_group), entry)
        else:
            group.append(entry)


def _place(current: _Element, new: _Element, groups: dict[str, _Element], parent: str | None) -> None:
    """Replace ``current`` with ``new``, moving it to ``parent`` if that group exists."""
    old_parent = current.getparent()
    target = groups.get(parent) if parent else None
    if target is None or target is old_parent:
        old_parent.replace(current, new)
    else:
        old_parent.remove(current)
        _append_entry(target, new)


def _set_uuid(el: _Element, u: str) -> None:
    node = el.find("UUID")
    if node is None:
        node = etree.Element("UUID")
        el.insert(0, node)
    node.text = u


def _resolve_entry(result: MergeResult, c: Conflict, choice: str, custom: _Element | None) -> None:
    entries = _live_entries(result.root)
    groups = _live_groups(result.root)
    current = entries.get(c.uuid)
    if current is None:
        raise ValueError("conflicted entry is no longer present in the merged tree")
    base_hist = [*_history_items(current), *_history_items(c.local), *_history_items(c.remote)]

    def versions_without(chosen: _Element, others: Iterable[_Element | None]) -> list[_Element]:
        out = list(base_hist)
        for o in others:
            if o is not None and not semantic_equal(_strip_history(o), _strip_history(chosen)):
                out.append(_strip_history(o))
        return _merge_history(out)

    if choice in ("local", "remote"):
        chosen = c.local if choice == "local" else c.remote
        other = c.remote if choice == "local" else c.local
        parent = c.local_parent if choice == "local" else c.remote_parent
        if chosen is None:
            current.getparent().remove(current)
            _add_deleted_record(result.root, c.uuid)
            return
        new = copy.deepcopy(chosen)
        _set_history(new, versions_without(chosen, [other]))
        _place(current, new, groups, parent)
        _remove_deleted_record(result.root, c.uuid)
    elif choice == "both":
        if c.local is None or c.remote is None:
            raise ValueError("'both' requires two existing versions")
        new = copy.deepcopy(c.local)
        _set_history(new, _merge_history(base_hist))
        _place(current, new, groups, c.local_parent)
        dup = _strip_history(c.remote)
        _set_uuid(dup, base64.b64encode(uuidlib.uuid4().bytes).decode())
        title = next((s for s in dup.findall("String") if s.findtext("Key") == "Title"), None)
        if title is None:
            title = etree.Element("String")
            etree.SubElement(title, "Key").text = "Title"
            etree.SubElement(title, "Value")
            _insert_ordered(dup, title, _ENTRY_ORDER)
        value = title.find("Value")
        if value is None:
            value = etree.SubElement(title, "Value")
        value.text = (value.text or "") + " (conflict copy)"
        target = groups.get(c.remote_parent or "")
        if target is None:
            target = new.getparent()
        _append_entry(target, dup)
    elif choice == "custom":
        if custom is None or custom.tag != "Entry":
            raise ValueError("'custom' requires an Entry element")
        new = copy.deepcopy(custom)
        _set_uuid(new, c.uuid)
        _set_history(new, versions_without(new, [c.local, c.remote]))
        _place(current, new, groups, None)
        _remove_deleted_record(result.root, c.uuid)
    else:
        raise ValueError(f"unknown choice: {choice}")


def _recycle_bin(root: _Element, groups: dict[str, _Element]) -> _Element | None:
    meta = root.find("Meta")
    if meta is None or (meta.findtext("RecycleBinEnabled") or "").strip().lower() != "true":
        return None
    u = (meta.findtext("RecycleBinUUID") or "").strip()
    if not u or u == _ZERO_UUID:
        return None
    return groups.get(u)


def _resolve_group(result: MergeResult, c: Conflict, choice: str, custom: _Element | None) -> None:
    groups = _live_groups(result.root)
    current = groups.get(c.uuid)
    if current is None:
        raise ValueError("conflicted group is no longer present in the merged tree")
    if choice == "both":
        raise ValueError("'both' is not supported for groups")
    if choice == "custom":
        if custom is None or custom.tag != "Group":
            raise ValueError("'custom' requires a Group element")
        chosen, parent = custom, None
    elif choice in ("local", "remote"):
        chosen = c.local if choice == "local" else c.remote
        parent = c.local_parent if choice == "local" else c.remote_parent
    else:
        raise ValueError(f"unknown choice: {choice}")

    if chosen is None:
        subtree = {_uuid_of(x) for x in current.iter("Group", "Entry") if x is not current}
        if any(o is not c and o.uuid in subtree for o in result.conflicts):
            raise ValueError("resolve conflicts inside this group before deleting it")
        if current.find(".//Entry") is not None:
            if current.getparent().tag == "Root":
                raise ValueError("the root group cannot be deleted")
            bin_group = _recycle_bin(result.root, groups)
            descendants = set(current.iter("Group"))
            if bin_group is None or bin_group in descendants:
                raise ValueError("group still contains entries and no recycle bin is available")
            current.getparent().remove(current)
            bin_group.append(current)
            return
        for g in current.iter("Group"):
            _add_deleted_record(result.root, _uuid_of(g))
        current.getparent().remove(current)
        return

    for ch in list(current):
        if not (_is_el(ch) and ch.tag in ("Entry", "Group")):
            current.remove(ch)
    for i, ch in enumerate(x for x in chosen if not (_is_el(x) and x.tag in ("Entry", "Group"))):
        current.insert(i, copy.deepcopy(ch))
    _set_uuid(current, c.uuid)
    target = groups.get(parent) if parent else None
    if target is not None and target is not current.getparent():
        if target in set(current.iter("Group")):
            raise ValueError("moving the group there would create a cycle")
        current.getparent().remove(current)
        target.append(current)
    _remove_deleted_record(result.root, c.uuid)


def _resolve_meta(result: MergeResult, c: Conflict, choice: str, custom: _Element | None) -> None:
    if choice == "both":
        raise ValueError("'both' is not supported for metadata")
    if choice == "custom":
        if custom is None:
            raise ValueError("'custom' requires an element")
        chosen = custom
    elif choice in ("local", "remote"):
        chosen = c.local if choice == "local" else c.remote
    else:
        raise ValueError(f"unknown choice: {choice}")
    path = c.id[len("meta:"):]
    root = result.root
    if path.startswith("KeePassFile/"):
        container, tag = root, path[len("KeePassFile/"):]
    elif path.startswith("Root/"):
        container, tag = root.find("Root"), path[len("Root/"):]
    else:
        meta = root.find("Meta")
        if meta is None:
            meta = etree.Element("Meta")
            root.insert(0, meta)
        for prefix, item_tag, key_of in (
            ("CustomData:", "Item", lambda i: i.findtext("Key") or ""),
            ("CustomIcons:", "Icon", _uuid_of),
        ):
            if path.startswith(prefix):
                key = path[len(prefix):]
                holder = meta.find(prefix[:-1])
                if holder is None:
                    holder = etree.SubElement(meta, prefix[:-1])
                news = [] if chosen is None else [copy.deepcopy(chosen)]
                olds = [i for i in holder.findall(item_tag) if key_of(i) == key]
                if olds:
                    idx = holder.index(olds[0])
                    for o in olds:
                        holder.remove(o)
                else:
                    idx = len(holder)
                for j, n in enumerate(news):
                    holder.insert(idx + j, n)
                return
        container, tag = meta, path
    _replace_tag(container, tag, [] if chosen is None else [copy.deepcopy(chosen)])


def resolve(result: MergeResult, conflict_id: str, choice: str, *, custom: _Element | None = None) -> None:
    """Apply a user decision in place on ``result.root`` and drop the conflict.

    ``choice`` is ``"local"``, ``"remote"``, ``"both"`` or ``"custom"``.  For
    entries the losing (non-identical) version is appended to History so the
    secret remains recoverable inside the encrypted vault.  Choosing a deleted
    side deletes the object (a DeletedObject record is added; the caller must
    warn the user).  Group deletion with remaining entries moves the group to
    the recycle bin when enabled, otherwise raises :class:`ValueError`.
    """
    c = _find_conflict(result, conflict_id)
    if c.kind == "entry":
        _resolve_entry(result, c, choice, custom)
    elif c.kind == "group":
        _resolve_group(result, c, choice, custom)
    else:
        _resolve_meta(result, c, choice, custom)
    result.conflicts.remove(c)
