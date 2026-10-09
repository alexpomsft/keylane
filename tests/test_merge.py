"""Tests for keylane.core.merge (three-way KDBX XML merge)."""

from __future__ import annotations

import base64
import copy
import random

import pytest
from lxml import etree

from keylane.core.merge import (
    Conflict,
    MergeResult,
    Side,
    descends_from,
    fingerprint,
    merge,
    resolve,
    semantic_equal,
)

# --------------------------------------------------------------------------
# XML builders (real KDBX4 structure, hand-built for speed)


def uid(n: int | str) -> str:
    raw = str(n).encode().ljust(16, b"\0")[:16]
    return base64.b64encode(raw).decode()


def ts(seconds: int) -> str:
    return base64.b64encode((63_000_000_000 + seconds).to_bytes(8, "little")).decode()


ROOT = uid("root")


def _times(parent: etree._Element, lmt: int = 0) -> etree._Element:
    times = etree.SubElement(parent, "Times")
    for tag in ("CreationTime", "LastModificationTime", "LastAccessTime", "ExpiryTime"):
        etree.SubElement(times, tag).text = ts(lmt)
    etree.SubElement(times, "Expires").text = "False"
    etree.SubElement(times, "UsageCount").text = "0"
    etree.SubElement(times, "LocationChanged").text = ts(lmt)
    return times


def mk_entry(u: str, title: str, *, user: str = "", password: str = "", url: str = "",
             notes: str = "", lmt: int = 0, attachments: dict[str, int] | None = None) -> etree._Element:
    e = etree.Element("Entry")
    etree.SubElement(e, "UUID").text = u
    etree.SubElement(e, "IconID").text = "0"
    _times(e, lmt)
    for key, value in (("Title", title), ("UserName", user), ("Password", password), ("URL", url), ("Notes", notes)):
        s = etree.SubElement(e, "String")
        etree.SubElement(s, "Key").text = key
        v = etree.SubElement(s, "Value")
        v.text = value
        if key == "Password":
            v.set("Protected", "True")
    for key, ref in (attachments or {}).items():
        b = etree.SubElement(e, "Binary")
        etree.SubElement(b, "Key").text = key
        etree.SubElement(b, "Value", Ref=str(ref))
    at = etree.SubElement(e, "AutoType")
    etree.SubElement(at, "Enabled").text = "True"
    return e


def mk_group(u: str, name: str, *children: etree._Element) -> etree._Element:
    g = etree.Element("Group")
    etree.SubElement(g, "UUID").text = u
    etree.SubElement(g, "Name").text = name
    etree.SubElement(g, "Notes")
    etree.SubElement(g, "IconID").text = "48"
    _times(g)
    etree.SubElement(g, "IsExpanded").text = "True"
    for c in children:
        g.append(c)
    return g


def mk_doc(*children: etree._Element, name: str = "DB", recycle: str | None = None) -> etree._Element:
    kp = etree.Element("KeePassFile")
    meta = etree.SubElement(kp, "Meta")
    etree.SubElement(meta, "Generator").text = "test"
    etree.SubElement(meta, "DatabaseName").text = name
    etree.SubElement(meta, "DatabaseNameChanged").text = ts(0)
    etree.SubElement(meta, "RecycleBinEnabled").text = "True" if recycle else "False"
    etree.SubElement(meta, "RecycleBinUUID").text = recycle or "AAAAAAAAAAAAAAAAAAAAAA=="
    etree.SubElement(meta, "CustomData")
    root = etree.SubElement(kp, "Root")
    root.append(mk_group(ROOT, "Root", *children))
    etree.SubElement(root, "DeletedObjects")
    return kp


def side(doc: etree._Element, pool: list[bytes] | None = None) -> Side:
    return Side(doc, list(pool or []))


def entries(doc: etree._Element) -> dict[str, etree._Element]:
    out = {}
    for e in doc.find("Root").iter("Entry"):
        if e.getparent().tag != "History":
            out[e.findtext("UUID")] = e
    return out


def groups(doc: etree._Element) -> dict[str, etree._Element]:
    return {g.findtext("UUID"): g for g in doc.find("Root").iter("Group")}


def parent_of(doc: etree._Element, u: str) -> str:
    el = entries(doc).get(u)
    if el is None:
        el = groups(doc)[u]
    return el.getparent().findtext("UUID")


def get_str(e: etree._Element, key: str) -> str | None:
    for s in e.findall("String"):
        if s.findtext("Key") == key:
            return s.findtext("Value") or ""
    return None


def set_str(doc: etree._Element, u: str, key: str, value: str, *, push_history: bool = False,
            lmt: int | None = None) -> None:
    e = entries(doc)[u]
    if push_history:
        snap = copy.deepcopy(e)
        for h in snap.findall("History"):
            snap.remove(h)
        hist = e.find("History")
        if hist is None:
            hist = etree.SubElement(e, "History")
        hist.append(snap)
    for s in e.findall("String"):
        if s.findtext("Key") == key:
            s.find("Value").text = value
            break
    else:
        s = etree.SubElement(e, "String")
        etree.SubElement(s, "Key").text = key
        etree.SubElement(s, "Value").text = value
    if lmt is not None:
        e.find("Times/LastModificationTime").text = ts(lmt)


def history_values(e: etree._Element, key: str) -> list[str | None]:
    hist = e.find("History")
    return [] if hist is None else [get_str(h, key) for h in hist.findall("Entry")]


def move(doc: etree._Element, u: str, target: str) -> None:
    el = entries(doc).get(u)
    if el is None:
        el = groups(doc)[u]
    el.getparent().remove(el)
    tgt = groups(doc)[target]
    if el.tag == "Entry":
        first_group = tgt.find("Group")
        if first_group is not None:
            tgt.insert(tgt.index(first_group), el)
            return
    tgt.append(el)


def delete(doc: etree._Element, u: str, *, record: bool = True) -> None:
    el = entries(doc).get(u)
    if el is None:
        el = groups(doc)[u]
    uuids = [u]
    if el.tag == "Group":
        uuids = [x.findtext("UUID") for x in el.iter("Group", "Entry") if x.getparent().tag != "History"]
    el.getparent().remove(el)
    if record:
        dobj = doc.find("Root/DeletedObjects")
        for x in uuids:
            d = etree.SubElement(dobj, "DeletedObject")
            etree.SubElement(d, "UUID").text = x
            etree.SubElement(d, "DeletionTime").text = ts(5)


def add_entry(doc: etree._Element, group: str, e: etree._Element) -> None:
    g = groups(doc)[group]
    first_group = g.find("Group")
    if first_group is not None:
        g.insert(g.index(first_group), e)
    else:
        g.append(e)


def ser(el: etree._Element) -> bytes:
    return etree.tostring(el, method="c14n")


def three(base: etree._Element) -> tuple[etree._Element, etree._Element, etree._Element]:
    return base, copy.deepcopy(base), copy.deepcopy(base)


def run(base, local, remote, *, pools=((), (), ())) -> MergeResult:
    b = None if base is None else side(base, list(pools[0]))
    return merge(b, side(local, list(pools[1])), side(remote, list(pools[2])))


def by_id(res: MergeResult, cid: str) -> Conflict:
    return next(c for c in res.conflicts if c.id == cid)


E1, E2, E3 = uid("e1"), uid("e2"), uid("e3")
G1, G2, G3 = uid("g1"), uid("g2"), uid("g3")


def base_doc() -> etree._Element:
    return mk_doc(
        mk_entry(E1, "Mail", user="me", password="pw1", url="https://mail"),
        mk_group(G1, "Work", mk_entry(E2, "VPN", user="w", password="vpn1")),
        mk_group(G2, "Home", mk_entry(E3, "Router", password="rt1")),
    )


# --------------------------------------------------------------------------
# basic three-way behaviour


def test_identical_inputs_no_change():
    base, local, remote = three(base_doc())
    res = run(base, local, remote)
    assert res.conflicts == []
    assert not res.changed_local and not res.changed_remote
    assert semantic_equal(res.root, local)


def test_independent_edits_to_different_entries():
    base, local, remote = three(base_doc())
    set_str(local, E1, "Password", "pw1-local", push_history=True)
    set_str(remote, E2, "Password", "vpn-remote", push_history=True)
    res = run(base, local, remote)
    assert res.conflicts == []
    cur = entries(res.root)
    assert get_str(cur[E1], "Password") == "pw1-local"
    assert get_str(cur[E2], "Password") == "vpn-remote"
    assert history_values(cur[E2], "Password") == ["vpn1"]
    assert res.changed_local and res.changed_remote


def test_same_entry_different_fields_auto_merges_with_history():
    base, local, remote = three(base_doc())
    set_str(local, E1, "UserName", "me-local", lmt=50)
    set_str(remote, E1, "URL", "https://remote", lmt=40)
    res = run(base, local, remote)
    assert res.conflicts == []
    e = entries(res.root)[E1]
    assert get_str(e, "UserName") == "me-local"
    assert get_str(e, "URL") == "https://remote"
    hist = e.find("History").findall("Entry")
    pairs = {(get_str(h, "UserName"), get_str(h, "URL")) for h in hist}
    assert ("me-local", "https://mail") in pairs
    assert ("me", "https://remote") in pairs
    assert all(h.find("History") is None for h in hist)
    assert e.findtext("Times/LastModificationTime") == ts(50)


def test_only_remote_changed_takes_remote_including_location():
    base, local, remote = three(base_doc())
    set_str(remote, E1, "Notes", "n")
    move(remote, E1, G2)
    res = run(base, local, remote)
    assert res.conflicts == []
    assert get_str(entries(res.root)[E1], "Notes") == "n"
    assert parent_of(res.root, E1) == G2
    assert res.changed_local and not res.changed_remote


def test_competing_password_edit_conflict_local_kept():
    base, local, remote = three(base_doc())
    set_str(local, E1, "Password", "L-secret")
    set_str(remote, E1, "Password", "R-secret")
    set_str(remote, E1, "URL", "https://r")
    res = run(base, local, remote)
    assert len(res.conflicts) == 1
    c = res.conflicts[0]
    assert (c.id, c.kind, c.reason, c.uuid) == (f"entry:{E1}", "entry", "both-modified", E1)
    assert c.fields == ["Password"]
    assert c.title == "Mail"
    assert get_str(c.local, "Password") == "L-secret"
    assert get_str(c.remote, "Password") == "R-secret"
    assert get_str(c.base, "Password") == "pw1"
    assert c.local_parent == ROOT and c.remote_parent == ROOT
    assert get_str(entries(res.root)[E1], "Password") == "L-secret"
    # notes never leak secrets
    assert not any("secret" in n for n in res.notes)


def _pw_conflict() -> MergeResult:
    base, local, remote = three(base_doc())
    set_str(local, E1, "Password", "L-secret")
    set_str(remote, E1, "Password", "R-secret")
    move(remote, E1, G1)
    return run(base, local, remote)


def test_resolve_local_puts_remote_in_history():
    res = _pw_conflict()
    resolve(res, f"entry:{E1}", "local")
    assert res.conflicts == []
    e = entries(res.root)[E1]
    assert get_str(e, "Password") == "L-secret"
    assert "R-secret" in history_values(e, "Password")
    assert parent_of(res.root, E1) == ROOT


def test_resolve_remote_puts_local_in_history_and_moves():
    res = _pw_conflict()
    resolve(res, f"entry:{E1}", "remote")
    e = entries(res.root)[E1]
    assert get_str(e, "Password") == "R-secret"
    assert "L-secret" in history_values(e, "Password")
    assert parent_of(res.root, E1) == G1


def test_resolve_both_creates_copy():
    res = _pw_conflict()
    resolve(res, f"entry:{E1}", "both")
    cur = entries(res.root)
    assert get_str(cur[E1], "Password") == "L-secret"
    copies = [e for u, e in cur.items() if u != E1 and get_str(e, "Password") == "R-secret"]
    assert len(copies) == 1
    assert get_str(copies[0], "Title") == "Mail (conflict copy)"
    assert parent_of(res.root, copies[0].findtext("UUID")) == G1
    assert copies[0].findtext("UUID") != E1


def test_resolve_custom():
    res = _pw_conflict()
    c = res.conflicts[0]
    custom = copy.deepcopy(c.local)
    for s in custom.findall("String"):
        if s.findtext("Key") == "Password":
            s.find("Value").text = "combined"
    resolve(res, c.id, "custom", custom=custom)
    e = entries(res.root)[E1]
    assert get_str(e, "Password") == "combined"
    hv = history_values(e, "Password")
    assert "L-secret" in hv and "R-secret" in hv


def test_resolve_errors():
    res = _pw_conflict()
    with pytest.raises(ValueError):
        resolve(res, "entry:nope", "local")
    with pytest.raises(ValueError):
        resolve(res, f"entry:{E1}", "bogus")
    with pytest.raises(ValueError):
        resolve(res, f"entry:{E1}", "custom")
    assert len(res.conflicts) == 1


# --------------------------------------------------------------------------
# deletions


def test_delete_unchanged_propagates_and_is_not_resurrected():
    base, local, remote = three(base_doc())
    delete(remote, E3)
    res = run(base, local, remote)
    assert res.conflicts == []
    assert E3 not in entries(res.root)
    assert E3 in {d.findtext("UUID") for d in res.root.iter("DeletedObject")}
    assert res.changed_local and not res.changed_remote


def test_delete_without_record_still_recorded():
    base, local, remote = three(base_doc())
    delete(local, E3, record=False)
    res = run(base, local, remote)
    assert E3 not in entries(res.root)
    assert E3 in {d.findtext("UUID") for d in res.root.iter("DeletedObject")}


def test_edit_vs_delete():
    base, local, remote = three(base_doc())
    set_str(local, E3, "Password", "rt-new")
    delete(remote, E3)
    res = run(base, local, remote)
    c = by_id(res, f"entry:{E3}")
    assert c.reason == "edit-vs-delete" and c.remote is None and c.fields == ["Password"]
    assert get_str(entries(res.root)[E3], "Password") == "rt-new"
    assert E3 not in {d.findtext("UUID") for d in res.root.iter("DeletedObject")}
    resolve(res, c.id, "remote")
    assert E3 not in entries(res.root)
    assert E3 in {d.findtext("UUID") for d in res.root.iter("DeletedObject")}


def test_delete_vs_edit_keeps_remote_version_in_remote_location():
    base, local, remote = three(base_doc())
    delete(local, E1)
    set_str(remote, E1, "Notes", "kept")
    move(remote, E1, G2)
    res = run(base, local, remote)
    c = by_id(res, f"entry:{E1}")
    assert c.reason == "delete-vs-edit" and c.local is None
    assert set(c.fields) == {"Location", "Notes"}
    assert get_str(entries(res.root)[E1], "Notes") == "kept"
    assert parent_of(res.root, E1) == G2
    resolve(res, c.id, "remote")
    assert get_str(entries(res.root)[E1], "Notes") == "kept"


def test_deleted_on_both_sides():
    base, local, remote = three(base_doc())
    delete(local, E1)
    delete(remote, E1)
    res = run(base, local, remote)
    assert res.conflicts == [] and E1 not in entries(res.root)
    assert [d.findtext("UUID") for d in res.root.iter("DeletedObject")].count(E1) == 1


def test_additions_on_both_sides_kept():
    base, local, remote = three(base_doc())
    add_entry(local, G1, mk_entry(uid("nl"), "NewL", password="a"))
    add_entry(remote, G1, mk_entry(uid("nr"), "NewR", password="b"))
    add_entry(remote, ROOT, mk_entry(uid("nr2"), "NewR2"))
    res = run(base, local, remote)
    assert res.conflicts == []
    g1 = groups(res.root)[G1]
    assert [e.findtext("UUID") for e in g1.findall("Entry")] == [E2, uid("nl"), uid("nr")]
    assert parent_of(res.root, uid("nr2")) == ROOT


# --------------------------------------------------------------------------
# groups


def test_group_rename_and_entry_move():
    base, local, remote = three(base_doc())
    groups(local)[G1].find("Name").text = "Job"
    move(remote, E1, G1)
    res = run(base, local, remote)
    assert res.conflicts == []
    assert groups(res.root)[G1].findtext("Name") == "Job"
    assert parent_of(res.root, E1) == G1


def test_group_moved_to_different_parents():
    base, local, remote = three(base_doc())
    root_g = groups(base)[ROOT]
    root_g.append(mk_group(G3, "Misc"))
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    move(local, G3, G1)
    move(remote, G3, G2)
    res = run(base, local, remote)
    c = by_id(res, f"group:{G3}")
    assert (c.kind, c.reason, c.fields) == ("group", "move-conflict", ["Location"])
    assert c.local_parent == G1 and c.remote_parent == G2
    assert parent_of(res.root, G3) == G1
    resolve(res, c.id, "remote")
    assert parent_of(res.root, G3) == G2


def test_group_move_cycle_prevented():
    base = mk_doc(mk_group(G1, "A"), mk_group(G2, "B"))
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    move(local, G1, G2)
    move(remote, G2, G1)
    res = run(base, local, remote)
    c = by_id(res, f"group:{G2}")
    assert c.reason == "move-conflict"
    assert parent_of(res.root, G1) == G2
    assert parent_of(res.root, G2) == ROOT


def test_group_deleted_while_other_side_added_entry():
    base, local, remote = three(base_doc())
    delete(remote, G1)
    add_entry(local, G1, mk_entry(uid("x"), "New in Work", password="n"))
    res = run(base, local, remote)
    c = by_id(res, f"group:{G1}")
    assert c.reason == "group-move-vs-delete" and c.remote is None
    cur = entries(res.root)
    assert uid("x") in cur and parent_of(res.root, uid("x")) == G1
    # E2 was unchanged locally -> its deletion is propagated
    assert E2 not in cur
    assert G1 not in {d.findtext("UUID") for d in res.root.iter("DeletedObject")}
    with pytest.raises(ValueError):
        resolve(res, c.id, "remote")  # still holds entries, no recycle bin
    resolve(res, c.id, "local")
    assert res.conflicts == []


def test_group_delete_resolution_uses_recycle_bin():
    bin_u = uid("bin")
    base = mk_doc(mk_group(G1, "Work", mk_entry(E2, "VPN")), mk_group(bin_u, "Recycle Bin"), recycle=bin_u)
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    delete(remote, G1)
    add_entry(local, G1, mk_entry(uid("x"), "New"))
    res = run(base, local, remote)
    resolve(res, f"group:{G1}", "remote")
    assert parent_of(res.root, G1) == bin_u
    assert uid("x") in entries(res.root)


def test_group_deleted_unchanged_contents_deleted():
    base, local, remote = three(base_doc())
    delete(local, G1)
    res = run(base, local, remote)
    assert res.conflicts == []
    assert G1 not in groups(res.root) and E2 not in entries(res.root)


def test_group_deleted_but_entry_edited_other_side():
    base, local, remote = three(base_doc())
    delete(local, G1)
    set_str(remote, E2, "Password", "vpn2")
    res = run(base, local, remote)
    assert [c.id for c in res.conflicts] == [f"entry:{E2}"]
    assert res.conflicts[0].reason == "delete-vs-edit"
    assert G1 in groups(res.root)
    assert get_str(entries(res.root)[E2], "Password") == "vpn2"


def test_group_modified_vs_deleted():
    base, local, remote = three(base_doc())
    delete(local, G2)
    groups(remote)[G2].find("Name").text = "House"
    res = run(base, local, remote)
    c = by_id(res, f"group:{G2}")
    assert c.reason == "group-move-vs-delete" and c.local is None and c.fields == ["Name"]
    assert groups(res.root)[G2].findtext("Name") == "House"


def test_group_both_not_allowed():
    base, local, remote = three(base_doc())
    groups(local)[G1].find("Name").text = "A"
    groups(remote)[G1].find("Name").text = "B"
    res = run(base, local, remote)
    c = by_id(res, f"group:{G1}")
    assert c.reason == "both-modified" and c.fields == ["Name"]
    with pytest.raises(ValueError):
        resolve(res, c.id, "both")
    resolve(res, c.id, "remote")
    assert groups(res.root)[G1].findtext("Name") == "B"
    assert E2 in entries(res.root)


# --------------------------------------------------------------------------
# no ancestry


def test_no_base_identical():
    _, local, remote = three(base_doc())
    res = run(None, local, remote)
    assert res.conflicts == [] and not res.changed_local and not res.changed_remote


def test_no_base_fast_forward_from_history():
    _, local, remote = three(base_doc())
    set_str(remote, E1, "Password", "newer", push_history=True)
    res = run(None, local, remote)
    assert res.conflicts == []
    assert get_str(entries(res.root)[E1], "Password") == "newer"
    # and symmetric
    res2 = run(None, remote, local)
    assert res2.conflicts == []
    assert get_str(entries(res2.root)[E1], "Password") == "newer"


def test_no_base_divergent_conflict():
    _, local, remote = three(base_doc())
    set_str(local, E1, "Password", "a")
    set_str(remote, E1, "Password", "b")
    res = run(None, local, remote)
    c = by_id(res, f"entry:{E1}")
    assert c.reason == "no-ancestry" and c.fields == ["Password"] and c.base is None


def test_no_base_deleted_objects_vs_present():
    _, local, remote = three(base_doc())
    delete(remote, E3)
    res = run(None, local, remote)
    c = by_id(res, f"entry:{E3}")
    assert c.reason == "no-ancestry" and c.remote is None
    assert E3 in entries(res.root)


def test_no_base_additions_kept():
    _, local, remote = three(base_doc())
    add_entry(remote, G2, mk_entry(uid("r"), "R"))
    res = run(None, local, remote)
    assert res.conflicts == [] and uid("r") in entries(res.root)


def test_base_present_but_object_not_in_base_and_deleted_other_side():
    base, local, remote = three(base_doc())
    add_entry(local, ROOT, mk_entry(uid("n"), "N"))
    d = etree.SubElement(remote.find("Root/DeletedObjects"), "DeletedObject")
    etree.SubElement(d, "UUID").text = uid("n")
    etree.SubElement(d, "DeletionTime").text = ts(1)
    res = run(base, local, remote)
    assert by_id(res, f"entry:{uid('n')}").reason == "no-ancestry"


# --------------------------------------------------------------------------
# clock skew


def test_clock_skew_only_changed_side_wins():
    base, local, remote = three(base_doc())
    set_str(local, E1, "Notes", "", lmt=10_000)  # unchanged content, newer timestamp
    set_str(remote, E1, "Password", "remote-pw", lmt=-10_000)
    res = run(base, local, remote)
    assert res.conflicts == []
    assert get_str(entries(res.root)[E1], "Password") == "remote-pw"


@pytest.mark.parametrize("l_time,r_time", [(100, 5), (5, 100)])
def test_clock_skew_both_changed_conflict_regardless(l_time, r_time):
    base, local, remote = three(base_doc())
    set_str(local, E1, "Password", "l", lmt=l_time)
    set_str(remote, E1, "Password", "r", lmt=r_time)
    res = run(base, local, remote)
    assert by_id(res, f"entry:{E1}").reason == "both-modified"
    assert get_str(entries(res.root)[E1], "Password") == "l"


# --------------------------------------------------------------------------
# binaries


def test_attachment_added_remote_with_different_pool():
    base = mk_doc(mk_entry(E1, "A", attachments={"a.txt": 0}), mk_entry(E2, "B"))
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    # remote pool order differs: index 0 is new data, index 1 is a.txt
    for v in remote.iter("Value"):
        if v.get("Ref") == "0":
            v.set("Ref", "1")
    e2 = entries(remote)[E2]
    b = etree.SubElement(e2, "Binary")
    etree.SubElement(b, "Key").text = "b.bin"
    etree.SubElement(b, "Value", Ref="0")
    res = run(base, local, remote, pools=([b"AAA"], [b"AAA", b"unused"], [b"BBB", b"AAA"]))
    assert res.conflicts == []
    cur = entries(res.root)

    def content(e, key):
        for bb in e.findall("Binary"):
            if bb.findtext("Key") == key:
                return res.binaries[int(bb.find("Value").get("Ref"))]

    assert content(cur[E1], "a.txt") == b"AAA"
    assert content(cur[E2], "b.bin") == b"BBB"
    assert sorted(res.binaries) == [b"AAA", b"BBB"]
    assert not res.changed_remote and res.changed_local


def test_attachment_same_key_different_content_conflict():
    base = mk_doc(mk_entry(E1, "A", attachments={"a.txt": 0}))
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    res = run(base, local, remote, pools=([b"orig"], [b"local"], [b"remote"]))
    c = by_id(res, f"entry:{E1}")
    assert c.fields == ["Attachment: a.txt"]
    lref = int(c.local.find("Binary/Value").get("Ref"))
    rref = int(c.remote.find("Binary/Value").get("Ref"))
    bref = int(c.base.find("Binary/Value").get("Ref"))
    assert (res.binaries[lref], res.binaries[rref], res.binaries[bref]) == (b"local", b"remote", b"orig")
    cur_ref = int(entries(res.root)[E1].find("Binary/Value").get("Ref"))
    assert res.binaries[cur_ref] == b"local"
    resolve(res, c.id, "remote")
    e = entries(res.root)[E1]
    assert res.binaries[int(e.find("Binary/Value").get("Ref"))] == b"remote"
    hist_refs = [int(h.find("Binary/Value").get("Ref")) for h in e.find("History")]
    assert b"local" in [res.binaries[i] for i in hist_refs]


def test_semantic_equal_binary_content_not_ref():
    a = mk_entry(E1, "A", attachments={"x": 0})
    b = mk_entry(E1, "A", attachments={"x": 1})
    assert semantic_equal(a, b, [b"q"], [b"z", b"q"])
    assert not semantic_equal(a, b, [b"q"], [b"q", b"z"])


def test_meta_binaries_rebuilt_kdbx3():
    base = mk_doc(mk_entry(E1, "A", attachments={"a": 0}))
    etree.SubElement(base.find("Meta"), "Binaries")
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    add_entry(remote, ROOT, mk_entry(E2, "B", attachments={"b": 1}))
    res = run(base, local, remote, pools=([b"a"], [b"a"], [b"a", b"b"]))
    bins = res.root.find("Meta/Binaries").findall("Binary")
    assert [base64.b64decode(x.text) for x in bins] == res.binaries
    assert [x.get("ID") for x in bins] == ["0", "1"]


# --------------------------------------------------------------------------
# custom data, unknown elements, protected flag, meta


def _add_cd(e: etree._Element, key: str, value: str) -> None:
    cd = e.find("CustomData")
    if cd is None:
        cd = etree.SubElement(e, "CustomData")
    for item in cd.findall("Item"):
        if item.findtext("Key") == key:
            item.find("Value").text = value
            return
    item = etree.SubElement(cd, "Item")
    etree.SubElement(item, "Key").text = key
    etree.SubElement(item, "Value").text = value


def test_entry_custom_data_conflict_and_merge():
    base, local, remote = three(base_doc())
    _add_cd(entries(base)[E1], "KPXC_x", "0")
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    _add_cd(entries(local)[E1], "KPXC_x", "1")
    _add_cd(entries(remote)[E1], "KPXC_x", "2")
    _add_cd(entries(remote)[E1], "KPXC_y", "new")
    res = run(base, local, remote)
    c = by_id(res, f"entry:{E1}")
    assert c.fields == ["CustomData: KPXC_x"]


def test_entry_custom_data_independent_items_merge():
    base, local, remote = three(base_doc())
    _add_cd(entries(local)[E1], "a", "1")
    _add_cd(entries(remote)[E1], "b", "2")
    res = run(base, local, remote)
    assert res.conflicts == []
    items = {i.findtext("Key"): i.findtext("Value") for i in entries(res.root)[E1].find("CustomData")}
    assert items == {"a": "1", "b": "2"}


def test_meta_custom_data_and_fields():
    base, local, remote = three(base_doc())
    _add_cd(base.find("Meta"), "k", "0")
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    _add_cd(local.find("Meta"), "k", "L")
    _add_cd(remote.find("Meta"), "k", "R")
    _add_cd(remote.find("Meta"), "other", "x")
    local.find("Meta/DatabaseName").text = "Ldb"
    remote.find("Meta/DatabaseName").text = "Rdb"
    remote.find("Meta/DatabaseNameChanged").text = ts(99)
    res = run(base, local, remote)
    ids = {c.id for c in res.conflicts}
    assert ids == {"meta:CustomData:k", "meta:DatabaseName"}
    assert all(c.kind == "meta" and c.reason == "meta" for c in res.conflicts)
    meta = res.root.find("Meta")
    assert meta.findtext("DatabaseName") == "Ldb"
    assert meta.findtext("DatabaseNameChanged") == ts(99)
    assert {i.findtext("Key"): i.findtext("Value") for i in meta.find("CustomData")} == {"k": "L", "other": "x"}
    resolve(res, "meta:DatabaseName", "remote")
    resolve(res, "meta:CustomData:k", "remote")
    assert res.root.findtext("Meta/DatabaseName") == "Rdb"
    assert {i.findtext("Key"): i.findtext("Value") for i in res.root.find("Meta/CustomData")}["k"] == "R"
    assert res.conflicts == []


def test_unknown_elements_preserved_and_merged():
    base, local, remote = three(base_doc())
    etree.SubElement(entries(local)[E1], "FutureThing", a="1").text = "x"
    etree.SubElement(groups(remote)[G1], "GroupExt").text = "y"
    etree.SubElement(remote.find("Meta"), "MetaExt").text = "z"
    etree.SubElement(remote, "TopExt").text = "t"
    set_str(remote, E1, "URL", "u2")
    res = run(base, local, remote)
    assert res.conflicts == []
    e = entries(res.root)[E1]
    assert e.find("FutureThing").get("a") == "1" and get_str(e, "URL") == "u2"
    assert groups(res.root)[G1].findtext("GroupExt") == "y"
    assert res.root.findtext("Meta/MetaExt") == "z"
    assert res.root.findtext("TopExt") == "t"


def test_unknown_element_conflict():
    base, local, remote = three(base_doc())
    etree.SubElement(entries(base)[E1], "FutureThing").text = "0"
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    entries(local)[E1].find("FutureThing").text = "1"
    entries(remote)[E1].find("FutureThing").text = "2"
    res = run(base, local, remote)
    assert by_id(res, f"entry:{E1}").fields == ["FutureThing"]


def test_protected_flag_preserved_and_compared():
    base, local, remote = three(base_doc())
    for s in entries(remote)[E1].findall("String"):
        if s.findtext("Key") == "UserName":
            s.find("Value").set("Protected", "True")
    set_str(local, E1, "Notes", "n")
    res = run(base, local, remote)
    assert res.conflicts == []
    e = entries(res.root)[E1]
    vals = {s.findtext("Key"): s.find("Value").get("Protected") for s in e.findall("String")}
    assert vals["UserName"] == "True" and vals["Password"] == "True"
    assert get_str(e, "Notes") == "n"


def test_volatile_data_ignored():
    base, local, remote = three(base_doc())
    t = entries(remote)[E1].find("Times")
    t.find("LastAccessTime").text = ts(999)
    t.find("UsageCount").text = "7"
    groups(remote)[G1].find("IsExpanded").text = "False"
    res = run(base, local, remote)
    assert res.conflicts == []
    assert not res.changed_local and not res.changed_remote


def test_history_union_dedup():
    base, local, remote = three(base_doc())
    set_str(local, E1, "Notes", "x", push_history=True)
    set_str(remote, E2, "Notes", "y", push_history=True)
    set_str(remote, E1, "URL", "https://mail", push_history=True)  # no content change, history only
    res = run(base, local, remote)
    hist = entries(res.root)[E1].find("History").findall("Entry")
    assert len(hist) == 1


# --------------------------------------------------------------------------
# determinism / immutability


def test_determinism_and_inputs_unmodified():
    base, local, remote = three(base_doc())
    set_str(local, E1, "Password", "L")
    set_str(remote, E1, "Password", "R")
    add_entry(remote, G2, mk_entry(uid("r"), "R", attachments={"f": 0}))
    delete(local, G1)
    before = [ser(x) for x in (base, local, remote)]
    pools = ([], [], [b"data"])
    r1 = run(base, local, remote, pools=pools)
    r2 = run(base, local, remote, pools=pools)
    assert [ser(x) for x in (base, local, remote)] == before
    assert etree.tostring(r1.root) == etree.tostring(r2.root)
    assert r1.binaries == r2.binaries
    assert [(c.id, c.fields) for c in r1.conflicts] == [(c.id, c.fields) for c in r2.conflicts]


# --------------------------------------------------------------------------
# fingerprint / descends_from


def test_fingerprint_semantics():
    a = mk_entry(E1, "A", password="p", attachments={"x": 0})
    b = copy.deepcopy(a)
    b.find("Times/LastAccessTime").text = ts(77)
    b.find("Times/LastModificationTime").text = ts(77)
    etree.SubElement(b, "History").append(mk_entry(E1, "old"))
    b.find("Binary/Value").set("Ref", "1")
    assert fingerprint(a, [b"q"]) == fingerprint(b, [b"z", b"q"])
    assert fingerprint(a, [b"q"]) != fingerprint(b, [b"q", b"z"])
    c = copy.deepcopy(a)
    c.find("String[Key='Password']/Value").text = "other"
    assert fingerprint(a) != fingerprint(c)
    g = mk_group(G1, "W", mk_entry(E2, "x"))
    assert fingerprint(g) == fingerprint(mk_group(G1, "W"))
    assert len(fingerprint(g)) == 64


def test_descends_from_edit_with_history():
    base = base_doc()
    remote = copy.deepcopy(base)
    set_str(remote, E1, "Password", "new", push_history=True)
    move(remote, E3, G1)
    assert descends_from(side(base), side(remote))
    assert not descends_from(side(remote), side(base))


def test_descends_from_stale_copy_false():
    base = base_doc()
    local = copy.deepcopy(base)
    set_str(local, E2, "Password", "newer", push_history=True)
    assert not descends_from(side(local), side(base))


def test_descends_from_edit_without_history_false():
    base = base_doc()
    remote = copy.deepcopy(base)
    set_str(remote, E1, "Password", "new")
    assert not descends_from(side(base), side(remote))


def test_descends_from_deletions():
    base = base_doc()
    remote = copy.deepcopy(base)
    delete(remote, E3, record=False)
    assert not descends_from(side(base), side(remote))
    remote = copy.deepcopy(base)
    delete(remote, E3, record=True)
    assert descends_from(side(base), side(remote))
    remote = copy.deepcopy(base)
    delete(remote, G1, record=True)
    assert descends_from(side(base), side(remote))
    remote = copy.deepcopy(base)
    groups(remote)[ROOT].append(copy.deepcopy(entries(remote)[E2]))
    delete(remote, G1, record=False)
    assert not descends_from(side(base), side(remote))


def test_descends_from_binary_pools():
    base = mk_doc(mk_entry(E1, "A", attachments={"a": 0}))
    remote = copy.deepcopy(base)
    remote.find(".//Binary/Value").set("Ref", "1")
    assert descends_from(side(base, [b"x"]), side(remote, [b"y", b"x"]))
    assert not descends_from(side(base, [b"x"]), side(remote, [b"x", b"y"]))


# --------------------------------------------------------------------------
# property-style: no silent loss


class Mutator:
    def __init__(self, rng: random.Random, doc: etree._Element, tag: str) -> None:
        self.rng, self.doc, self.tag, self.n = rng, doc, tag, 0

    def token(self) -> str:
        self.n += 1
        return f"{self.tag}-{self.n}"

    def step(self) -> None:
        rng = self.rng
        ents = sorted(entries(self.doc))
        grps = sorted(groups(self.doc))
        op = rng.choice(["edit", "edit", "edit", "add", "delete", "move", "addgroup", "rename", "movegroup", "delgroup"])
        if op == "edit" and ents:
            set_str(self.doc, rng.choice(ents), rng.choice(["Title", "UserName", "Password", "Notes", "Custom"]),
                    self.token(), push_history=rng.random() < 0.5, lmt=rng.randint(-100, 100))
        elif op == "add":
            add_entry(self.doc, rng.choice(grps), mk_entry(uid(self.token()), self.token(), password=self.token()))
        elif op == "delete" and ents:
            delete(self.doc, rng.choice(ents), record=rng.random() < 0.8)
        elif op == "move" and ents:
            move(self.doc, rng.choice(ents), rng.choice(grps))
        elif op == "addgroup":
            groups(self.doc)[rng.choice(grps)].append(mk_group(uid(self.token()), self.token()))
        elif op == "rename" and len(grps) > 1:
            g = rng.choice([g for g in grps if g != ROOT])
            groups(self.doc)[g].find("Name").text = self.token()
        elif op == "movegroup" and len(grps) > 1:
            g = rng.choice([g for g in grps if g != ROOT])
            desc = {x.findtext("UUID") for x in groups(self.doc)[g].iter("Group")}
            targets = [t for t in grps if t not in desc]
            move(self.doc, g, rng.choice(targets))
        elif op == "delgroup" and len(grps) > 1 and rng.random() < 0.5:
            delete(self.doc, rng.choice([g for g in grps if g != ROOT]), record=True)


def random_base(rng: random.Random) -> etree._Element:
    doc = mk_doc()
    gids = [ROOT]
    for i in range(5):
        g = uid(f"bg{i}")
        groups(doc)[rng.choice(gids)].append(mk_group(g, f"G{i}"))
        gids.append(g)
    for i in range(14):
        add_entry(doc, rng.choice(gids), mk_entry(uid(f"be{i}"), f"T{i}", user=f"u{i}", password=f"p{i}", notes=f"n{i}"))
    return doc


def string_values(e: etree._Element) -> set[str]:
    return {s.findtext("Value") or "" for s in e.findall("String")}


def all_result_values(res: MergeResult) -> set[str]:
    vals: set[str] = set()
    containers = [res.root.find("Root")] + [x for c in res.conflicts for x in (c.local, c.remote) if x is not None]
    for cont in containers:
        for e in cont.iter("Entry"):
            vals |= string_values(e)
    return vals


def entry_sig(doc: etree._Element, u: str):
    e = copy.deepcopy(entries(doc)[u])
    for h in e.findall("History"):
        e.remove(h)
    return (sorted((s.findtext("Key"), s.findtext("Value") or "") for s in e.findall("String")), parent_of(doc, u))


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("with_base", [True, False])
def test_property_no_silent_loss(seed: int, with_base: bool):
    rng = random.Random(seed)
    base = random_base(rng)
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    ml, mr = Mutator(rng, local, "L"), Mutator(rng, remote, "R")
    for _ in range(rng.randint(1, 12)):
        ml.step()
    for _ in range(rng.randint(1, 12)):
        mr.step()
    before = [ser(x) for x in (base, local, remote)]
    res = run(base if with_base else None, local, remote)
    assert [ser(x) for x in (base, local, remote)] == before
    assert etree.tostring(res.root) == etree.tostring(run(base if with_base else None, local, remote).root)

    found = all_result_values(res)
    current = entries(res.root)
    assert len(current) == sum(1 for e in res.root.find("Root").iter("Entry") if e.getparent().tag != "History")
    deleted_records = {d.findtext("UUID") for d in res.root.iter("DeletedObject")}
    conflict_ids = {c.id for c in res.conflicts}
    for this, other in ((local, remote), (remote, local)):
        for u, e in entries(this).items():
            if u in current:
                missing = string_values(e) - found
                if missing and with_base and u in entries(base):
                    # superseded only if this side never changed the entry (the other side's
                    # deliberate edit wins); any value this side introduced must survive
                    assert entry_sig(this, u)[0] == entry_sig(base, u)[0], f"lost values of {u}"
                else:
                    assert not missing, f"lost values {missing} of {u}"
                assert u not in deleted_records
            else:
                # deletion only when the deleting side's counterpart was unchanged
                assert with_base, "nothing may be deleted without a base"
                assert u in entries(base)
                assert u not in entries(other)
                assert entry_sig(this, u) == entry_sig(base, u)
                assert f"entry:{u}" not in conflict_ids
    # every group in the result exists once and the tree is a proper tree
    gl = [g.findtext("UUID") for g in res.root.find("Root").iter("Group")]
    assert len(gl) == len(set(gl))

    # resolving every conflict must work (or refuse explicitly) without losing current data
    for c in list(res.conflicts):
        choice = rng.choice(["local", "remote", "both"])
        try:
            resolve(res, c.id, choice)
        except ValueError:
            resolve(res, c.id, "local" if c.local is not None else "remote")
    assert res.conflicts == []


def test_group_delete_refused_while_nested_conflict_pending():
    base = mk_doc(mk_group(G1, "Outer", mk_group(G2, "Inner")))
    local, remote = copy.deepcopy(base), copy.deepcopy(base)
    delete(remote, G1)
    groups(local)[G1].find("Name").text = "Outer2"
    groups(local)[G2].find("Name").text = "Inner2"
    res = run(base, local, remote)
    assert {c.id for c in res.conflicts} == {f"group:{G1}", f"group:{G2}"}
    with pytest.raises(ValueError):
        resolve(res, f"group:{G1}", "remote")
    resolve(res, f"group:{G2}", "remote")
    assert G2 not in groups(res.root)
    resolve(res, f"group:{G1}", "remote")
    assert G1 not in groups(res.root) and res.conflicts == []
