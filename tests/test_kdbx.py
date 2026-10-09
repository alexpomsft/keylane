"""T-01 / T-02: KDBX round trips, semantic preservation and reference-client interop.

All data is synthetic. Interop tests drive the real ``keepassxc-cli`` binary
and are skipped when it is not installed.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from lxml import etree

from keylane.core import errors, kdbx
from keylane.core.kdbx import (
    CustomField,
    EntryData,
    VaultDocument,
    aes_kdf,
    get_string,
    hex_to_b64,
    make_composite,
)

PW = "synthetic-kdbx-pass"
OTP_URI = "otpauth://totp/Synthetic:alice?secret=JBSWY3DPEHPK3PXP&period=30&digits=6&issuer=Synthetic"
ATTACHMENT = b"\x00\x01synthetic attachment bytes\xff"
KXC = shutil.which("keepassxc-cli")
needs_kxc = pytest.mark.skipif(KXC is None, reason="keepassxc-cli (reference client) not installed")


REAL_TUNER = kdbx.tune_argon2_iterations


@pytest.fixture(scope="module", autouse=True)
def fast_kdf_tuning():
    """Skip the ~0.6 s Argon2 benchmark in VaultDocument.create (kdf_target_ms=1 yields the minimum anyway)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(kdbx, "tune_argon2_iterations", lambda *a, **k: 2)
        yield


# --------------------------------------------------------------------------
# helpers


def c14n(el) -> bytes:
    return etree.tostring(el, method="c14n")


def reopen(doc: VaultDocument, composite=None) -> VaultDocument:
    data = doc.to_bytes()
    doc.mark_saved(doc.verify_bytes(data))
    return VaultDocument.open_bytes(data, composite or doc.composite)


def version_of(data: bytes) -> tuple[int, int]:
    minor, major = struct.unpack("<HH", data[8:12])
    return major, minor


def entry_by_title(doc: VaultDocument, title: str):
    return next(s for s in doc.summaries(include_trash=True) if s.title == title)


def add_attachment(doc: VaultDocument, hexid: str, name: str, data: bytes) -> None:
    ref = doc.kp.add_binary(data, protected=True)
    e = doc.entry_el(hexid)
    b = etree.Element("Binary")
    etree.SubElement(b, "Key").text = name
    etree.SubElement(b, "Value").set("Ref", str(ref))
    e.findall("String")[-1].addnext(b)


def add_custom_data(parent, key: str, value: str) -> None:
    cd = parent.find("CustomData")
    if cd is None:
        cd = etree.SubElement(parent, "CustomData")
    item = etree.SubElement(cd, "Item")
    etree.SubElement(item, "Key").text = key
    etree.SubElement(item, "Value").text = value


def build_rich(composite) -> tuple[VaultDocument, dict[str, str]]:
    """A vault exercising groups, history, attachments, TOTP, custom data, protected fields."""
    doc = VaultDocument.create(composite, name="Rich", kdf_target_ms=1)
    work = doc.create_group("Work", None)
    servers = doc.create_group("Servers", work)
    alpha = doc.create_entry(EntryData(
        None, title="Alpha", username="alice", password="alpha-pw-1", url="https://alpha.example",
        notes="line one\nline two", tags=["t1", "t2"], group_id=work,
        fields=[CustomField("otp", OTP_URI, True), CustomField("Plain", "visible-value", False),
                CustomField("Secret PIN", "4711", True)]))
    beta = doc.create_entry(EntryData(None, title="Beta", username="bob", password="beta-pw", group_id=servers))
    gamma = doc.create_entry(EntryData(None, title="Gamma", password="gamma-pw"))
    d = doc.read_entry(alpha)
    d.password = "alpha-pw-2"
    assert doc.update_entry(d)
    add_attachment(doc, alpha, "notes.bin", ATTACHMENT)
    add_custom_data(doc.meta, "SyntheticMetaKey", "meta-value")
    add_custom_data(doc.entry_el(alpha), "KPXC_SYNTHETIC", "entry-custom")
    etree.SubElement(doc.meta, "SyntheticUnknownElement").text = "keep me"
    return doc, {"alpha": alpha, "beta": beta, "gamma": gamma, "work": work, "servers": servers}


@pytest.fixture(scope="module")
def rich():
    """Saved bytes of the rich vault (built once per module: vault creation benchmarks the KDF)."""
    composite = make_composite(PW, None)
    doc, ids = build_rich(composite)
    data = doc.to_bytes()
    return data, ids


def open_rich(rich) -> VaultDocument:
    return VaultDocument.open_bytes(rich[0], make_composite(PW, None))


def kxc_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "kxc-home"
    for sub in ("config", "cache", "data", "state"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"),
               XDG_DATA_HOME=str(home / "data"), XDG_STATE_HOME=str(home / "state"), QT_QPA_PLATFORM="offscreen")
    return env


def kxc(tmp_path: Path, *args: str, stdin: str = "", check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run([KXC, *args], input=stdin, capture_output=True, text=True, timeout=60,
                          env=kxc_env(tmp_path), cwd=tmp_path)
    if check and proc.returncode != 0:
        raise AssertionError(f"keepassxc-cli {args[0]} failed rc={proc.returncode}: {proc.stderr.strip()}")
    return proc


def kxc_attr(tmp_path, db, entry, attr, *, pw=PW, keyfile=None) -> str:
    auth = (["-k", str(keyfile)] if keyfile else []) + ([] if pw is not None else ["--no-password"])
    out = kxc(tmp_path, "show", "-q", "-s", *auth, "-a", attr, str(db), entry, stdin=f"{pw}\n" if pw is not None else "")
    return out.stdout.rstrip("\n")


# --------------------------------------------------------------------------
# T-01: Keylane round trips


def test_create_save_reopen_kdbx4_password(tmp_path, monkeypatch):
    monkeypatch.setattr(kdbx, "tune_argon2_iterations", REAL_TUNER)
    doc, ids = build_rich(make_composite(PW, None))
    path = tmp_path / "rich.kdbx"
    path.write_bytes(doc.to_bytes())
    data = path.read_bytes()
    assert version_of(data) == (4, 0)
    back = VaultDocument.open_bytes(data, make_composite(PW, None))
    info = back.format_info()
    assert (info.version, info.cipher, info.kdf) == ("KDBX 4.0", "AES-256", "Argon2id")
    assert info.kdf_detail.startswith("64 MiB")
    assert info.entry_count == 3
    a = back.read_entry(ids["alpha"])
    assert (a.title, a.username, a.password, a.url, a.notes) == ("Alpha", "alice", "alpha-pw-2", "https://alpha.example", "line one\nline two")
    assert a.tags == ["t1", "t2"] and a.group_id == ids["work"]
    assert {f.key: (f.value, f.protected) for f in a.fields} == {
        "otp": (OTP_URI, True), "Plain": ("visible-value", False), "Secret PIN": ("4711", True)}
    assert a.attachments == [("notes.bin", len(ATTACHMENT))]
    assert a.history_count == 1 and back.read_history(ids["alpha"], 0).password == "alpha-pw-1"
    assert back.summary(back.entry_el(ids["alpha"])).has_otp
    assert {g.path for g in back.groups()} >= {"", "Work", "Work / Servers"}
    with pytest.raises(errors.WrongCredentials):
        VaultDocument.open_bytes(data, make_composite("not-the-password", None))


def test_keyfile_composites_round_trip(tmp_path):
    kf = tmp_path / "raw.key"
    kf.write_bytes(os.urandom(32))
    for pw in (PW, None):
        composite = make_composite(pw, kf)
        doc = VaultDocument.create(composite, name="K", kdf_target_ms=1)
        eid = doc.create_entry(EntryData(None, title="KF", password="kf-pw"))
        data = doc.to_bytes()
        back = VaultDocument.open_bytes(data, make_composite(pw, kf))
        assert back.get_field(eid, "Password") == "kf-pw"
        with pytest.raises(errors.WrongCredentials):
            VaultDocument.open_bytes(data, make_composite(pw if pw else "x", None))
        other = tmp_path / "other.key"
        other.write_bytes(os.urandom(32))
        with pytest.raises(errors.WrongCredentials):
            VaultDocument.open_bytes(data, make_composite(pw, other))


def test_keyfile_formats_are_equivalent(tmp_path):
    raw = os.urandom(32)
    hexkey = tmp_path / "hex.key"
    hexkey.write_bytes(raw.hex().encode())
    binkey = tmp_path / "bin.key"
    binkey.write_bytes(raw)
    import hashlib
    h = hashlib.sha256(raw).digest()[:4].hex().upper()
    xml = tmp_path / "v2.keyx"
    xml.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<KeyFile><Meta><Version>2.0</Version></Meta>'
                   f'<Key><Data Hash="{h}">{raw.hex().upper()}</Data></Key></KeyFile>')
    composites = {make_composite(PW, p).raw for p in (hexkey, binkey, xml)}
    assert len(composites) == 1
    bad = tmp_path / "bad.keyx"
    bad.write_text(f'<KeyFile><Meta><Version>2.0</Version></Meta><Key><Data Hash="00000000">{raw.hex()}</Data></Key></KeyFile>')
    with pytest.raises(errors.KeyFileInvalid):
        make_composite(PW, bad)
    with pytest.raises(errors.KeyFileMissing):
        make_composite(PW, tmp_path / "missing.key")
    with pytest.raises(errors.InvalidInput):
        make_composite(None, None)


def test_rejects_non_kdbx_and_truncated(rich):
    data = rich[0]
    comp = make_composite(PW, None)
    with pytest.raises(errors.NotAVault):
        VaultDocument.open_bytes(b"\x00" * 64, comp)
    kdb1 = struct.pack("<II", 0x9AA2D903, 0xB54BFB65) + b"\x00" * 32
    with pytest.raises(errors.UnsupportedFormat):
        VaultDocument.open_bytes(kdb1, comp)
    with pytest.raises(errors.CorruptVault):
        VaultDocument.open_bytes(data[: len(data) // 2], comp)


def test_aes_kdf_matches_reference_implementation():
    from pykeepass.kdbx_parsing.common import aes_kdf as reference

    for rounds in (0, 1, 2, 17, 65_537):
        key, comp = os.urandom(32), os.urandom(32)
        assert aes_kdf(key, rounds, comp) == reference(key, rounds, comp)


# ---- KDBX 3.1 (AES-KDF), built through pykeepass' container definition


def to_kdbx3(doc: VaultDocument, rounds: int = 1000) -> bytes:
    from construct import Container
    from pykeepass.kdbx_parsing import KDBX

    def item(i, d):
        return Container(id=i, data=d)

    dh = Container(
        cipher_id=item("cipher_id", "aes256"),
        compression_flags=item("compression_flags", Container(compression=True)),
        master_seed=item("master_seed", os.urandom(32)),
        transform_seed=item("transform_seed", os.urandom(32)),
        transform_rounds=item("transform_rounds", rounds),
        encryption_iv=item("encryption_iv", os.urandom(16)),
        protected_stream_key=item("protected_stream_key", os.urandom(32)),
        stream_start_bytes=item("stream_start_bytes", os.urandom(32)),
        protected_stream_id=item("protected_stream_id", "salsa20"),
        end=item("end", b""),
    )
    k = Container(header=Container(value=Container(sig1=b"\x03\xd9\xa2\x9a", sig2=b"\x67\xfb\x4b\xb5", minor_version=1,
                                                   major_version=3, dynamic_header=dh)),
                  body=Container(payload=Container(xml=doc.kp.tree)))
    buf = io.BytesIO()
    KDBX.build_stream(k, buf, password=doc.composite, keyfile=None, transformed_key=None, decrypt=True)
    return buf.getvalue()


def test_kdbx3_round_trip_keeps_format(tmp_path):
    comp = make_composite(PW, None)
    doc = VaultDocument.create(comp, name="Three", kdf_target_ms=1)
    eid = doc.create_entry(EntryData(None, title="Old", username="u3", password="p3",
                                     fields=[CustomField("Hidden", "h3", True)]))
    data3 = to_kdbx3(doc)
    assert version_of(data3) == (3, 1)
    d3 = VaultDocument.open_bytes(data3, comp)
    assert d3.format_info().version == "KDBX 3.1" and d3.format_info().kdf == "AES-KDF"
    e = d3.read_entry(eid)
    e.notes = "edited in keylane"
    assert d3.update_entry(e)
    nid = d3.create_entry(EntryData(None, title="New3", password="np3"))
    back = reopen(d3, make_composite(PW, None))
    assert back.version == (3, 1)
    assert back.read_entry(eid).notes == "edited in keylane"
    assert get_string(back.entry_el(eid), "Hidden") == ("h3", True)
    assert back.get_field(nid, "Password") == "np3"
    assert back.read_history(eid, 0).notes == ""


# --------------------------------------------------------------------------
# T-02: semantic preservation


def test_noop_save_is_semantically_identical(rich):
    doc = open_rich(rich)
    before = c14n(doc.root)
    pool = list(doc.binaries())
    ids = rich[1]
    for eid in ids["alpha"], ids["beta"], ids["gamma"]:
        assert doc.update_entry(doc.read_entry(eid)) is False
    assert c14n(doc.root) == before
    back = reopen(doc)
    assert c14n(back.root) == before
    assert back.binaries() == pool
    assert back._binary_flags() == doc._binary_flags()


def test_unrelated_edit_preserves_everything_else(rich):
    ids = rich[1]
    doc = open_rich(rich)
    orig = {k: c14n(doc.find(v)) for k, v in ids.items() if k != "alpha"}
    alpha_el = doc.entry_el(ids["alpha"])
    alpha_parts = {tag: [c14n(x) for x in alpha_el.findall(tag)] for tag in ("Binary", "CustomData", "AutoType")}
    alpha_strings = {s.findtext("Key"): c14n(s) for s in alpha_el.findall("String")}
    alpha_hist = [c14n(h) for h in alpha_el.find("History")]
    meta_cd = c14n(doc.meta.find("CustomData"))
    pool = list(doc.binaries())
    flags = doc._binary_flags()

    d = doc.read_entry(ids["alpha"])
    d.url = "https://alpha.example/changed"
    assert doc.update_entry(d) is True
    back = reopen(doc)

    a = back.entry_el(ids["alpha"])
    assert a.findtext("UUID") == hex_to_b64(ids["alpha"])
    assert get_string(a, "URL") == ("https://alpha.example/changed", False)
    for key, raw in alpha_strings.items():
        if key != "URL":
            assert c14n(next(s for s in a.findall("String") if s.findtext("Key") == key)) == raw, key
    for tag, items in alpha_parts.items():
        assert [c14n(x) for x in a.findall(tag)] == items, tag
    hist = list(a.find("History"))
    assert [c14n(h) for h in hist[:-1]] == alpha_hist
    assert get_string(hist[-1], "URL")[0] == "https://alpha.example"
    assert get_string(a, "otp") == (OTP_URI, True)
    assert get_string(a, "Password") == ("alpha-pw-2", True)
    assert get_string(a, "Secret PIN") == ("4711", True)
    assert get_string(a, "Plain") == ("visible-value", False)

    # Only the edited entry (and its parent's own data) changed: siblings, other groups' entries, meta untouched.
    for k in ("beta", "gamma", "servers"):
        assert c14n(back.find(ids[k])) == orig[k], k
    assert c14n(back.meta.find("CustomData")) == meta_cd
    assert back.meta.findtext("SyntheticUnknownElement") == "keep me"
    assert back.binaries() == pool and back._binary_flags() == flags
    assert back.read_entry(ids["alpha"]).attachments == [("notes.bin", len(ATTACHMENT))]


def test_move_recycle_restore_keep_ids_and_attachments(rich):
    ids = rich[1]
    doc = open_rich(rich)
    doc.move_entry(ids["alpha"], ids["servers"])
    doc.recycle(ids["alpha"])
    back = reopen(doc)
    s = entry_by_title(back, "Alpha")
    assert s.id == ids["alpha"] and s.in_trash and s.origin_path == "Work / Servers"
    back.restore(ids["alpha"])
    back = reopen(back)
    e = back.read_entry(ids["alpha"])
    assert e.group_id == ids["servers"]
    assert e.attachments == [("notes.bin", len(ATTACHMENT))]
    assert get_string(back.entry_el(ids["alpha"]), "otp") == (OTP_URI, True)


def test_history_restore_round_trip(rich):
    ids = rich[1]
    doc = open_rich(rich)
    hist_len = len(doc.history(ids["alpha"]))
    doc.restore_history(ids["alpha"], 0)
    back = reopen(doc)
    assert back.get_field(ids["alpha"], "Password") == "alpha-pw-1"
    assert len(back.history(ids["alpha"])) == hist_len + 1
    assert back.read_history(ids["alpha"], hist_len).password == "alpha-pw-2"


# --------------------------------------------------------------------------
# T-01: interop with the reference client (KeePassXC)


@needs_kxc
def test_keylane_vault_opens_in_keepassxc(rich, tmp_path):
    db = tmp_path / "rich.kdbx"
    db.write_bytes(rich[0])
    listing = kxc(tmp_path, "ls", "-q", "-R", "-f", str(db), stdin=f"{PW}\n").stdout.split()
    assert {"Gamma", "Work/", "Work/Alpha", "Work/Servers/", "Work/Servers/Beta"} <= set(listing)
    assert kxc_attr(tmp_path, db, "/Work/Alpha", "Password") == "alpha-pw-2"
    assert kxc_attr(tmp_path, db, "/Work/Alpha", "UserName") == "alice"
    assert kxc_attr(tmp_path, db, "/Work/Alpha", "URL") == "https://alpha.example"
    assert kxc_attr(tmp_path, db, "/Work/Alpha", "Notes") == "line one\nline two"
    assert kxc_attr(tmp_path, db, "/Work/Alpha", "Plain") == "visible-value"
    assert kxc_attr(tmp_path, db, "/Work/Alpha", "Secret PIN") == "4711"
    assert kxc_attr(tmp_path, db, "/Work/Servers/Beta", "Password") == "beta-pw"
    totp = kxc(tmp_path, "show", "-q", "-t", str(db), "/Work/Alpha", stdin=f"{PW}\n").stdout.strip()
    assert re.fullmatch(r"\d{6}", totp)
    out = tmp_path / "exported.bin"
    kxc(tmp_path, "attachment-export", "-q", str(db), "/Work/Alpha", "notes.bin", str(out), stdin=f"{PW}\n")
    assert out.read_bytes() == ATTACHMENT
    wrong = kxc(tmp_path, "ls", "-q", str(db), stdin="wrong\n", check=False)
    assert wrong.returncode != 0


@needs_kxc
@pytest.mark.parametrize("with_password", [True, False])
def test_keylane_keyfile_vault_opens_in_keepassxc(tmp_path, with_password):
    kf = tmp_path / "vault.key"
    kf.write_bytes(os.urandom(32))
    pw = PW if with_password else None
    doc = VaultDocument.create(make_composite(pw, kf), name="KF", kdf_target_ms=1)
    doc.create_entry(EntryData(None, title="Keyed", username="kuser", password="keyed-pw"))
    db = tmp_path / "kf.kdbx"
    db.write_bytes(doc.to_bytes())
    assert kxc_attr(tmp_path, db, "/Keyed", "Password", pw=pw, keyfile=kf) == "keyed-pw"
    no_key = kxc(tmp_path, "ls", "-q", str(db), stdin=f"{PW}\n", check=False)
    assert no_key.returncode != 0


@needs_kxc
def test_keepassxc_vault_opens_edits_and_reopens(tmp_path):
    db = tmp_path / "kxc.kdbx"
    kxc(tmp_path, "db-create", "-q", "-p", "-t", "100", str(db), stdin=f"{PW}\n{PW}\n")
    kxc(tmp_path, "mkdir", "-q", str(db), "/Work", stdin=f"{PW}\n")
    kxc(tmp_path, "add", "-q", "-u", "alice", "--url", "https://mail.example", "--notes", "first note", "-p",
        str(db), "/Mail", stdin=f"{PW}\nmail-pw\n")
    kxc(tmp_path, "add", "-q", "-p", str(db), "/Work/VPN", stdin=f"{PW}\nvpn-pw\n")
    kxc(tmp_path, "edit", "-q", "-u", "vpn-user", str(db), "/Work/VPN", stdin=f"{PW}\n")
    att = tmp_path / "att.bin"
    att.write_bytes(ATTACHMENT)
    kxc(tmp_path, "attachment-import", "-q", str(db), "/Mail", "cert.bin", str(att), stdin=f"{PW}\n")
    original = db.read_bytes()

    doc = VaultDocument.open_bytes(original, make_composite(PW, None))
    fmt = doc.version
    mail = entry_by_title(doc, "Mail")
    vpn = entry_by_title(doc, "VPN")
    assert vpn.group_path == "Work"
    m = doc.read_entry(mail.id)
    assert (m.username, m.password, m.url, m.notes) == ("alice", "mail-pw", "https://mail.example", "first note")
    assert m.attachments == [("cert.bin", len(ATTACHMENT))]
    v = doc.read_entry(vpn.id)
    assert (v.username, v.password) == ("vpn-user", "vpn-pw")
    assert v.history_count >= 1  # KeePassXC recorded the edit in history
    root_uuid = doc.root_group.findtext("UUID")

    m.notes = "edited by keylane"
    m.fields = [CustomField("Extra", "extra-value", True)]
    assert doc.update_entry(m)
    doc.create_entry(EntryData(None, title="FromKeylane", username="kl", password="kl-pw", group_id=vpn.group_id))
    data = doc.to_bytes()
    doc.mark_saved(doc.verify_bytes(data))
    db.write_bytes(data)
    assert version_of(data) == fmt

    assert kxc_attr(tmp_path, db, "/Mail", "Notes") == "edited by keylane"
    assert kxc_attr(tmp_path, db, "/Mail", "Password") == "mail-pw"
    assert kxc_attr(tmp_path, db, "/Mail", "Extra") == "extra-value"
    assert kxc_attr(tmp_path, db, "/Work/FromKeylane", "Password") == "kl-pw"
    assert kxc_attr(tmp_path, db, "/Work/VPN", "UserName") == "vpn-user"
    out = tmp_path / "out.bin"
    kxc(tmp_path, "attachment-export", "-q", str(db), "/Mail", "cert.bin", str(out), stdin=f"{PW}\n")
    assert out.read_bytes() == ATTACHMENT

    # And KeePassXC can keep editing it; Keylane sees the result with stable ids.
    kxc(tmp_path, "edit", "-q", "-t", "Mail Renamed", str(db), "/Mail", stdin=f"{PW}\n")
    again = VaultDocument.open_bytes(db.read_bytes(), make_composite(PW, None))
    renamed = again.read_entry(mail.id)
    assert renamed.title == "Mail Renamed" and renamed.notes == "edited by keylane"
    assert again.root_group.findtext("UUID") == root_uuid


@needs_kxc
def test_keepassxc_keyfile_vault_round_trip(tmp_path):
    db = tmp_path / "kxk.kdbx"
    kf = tmp_path / "kxc.keyx"
    kxc(tmp_path, "db-create", "-q", "-p", "-t", "100", "--set-key-file", str(kf), str(db), stdin=f"{PW}\n{PW}\n")
    assert kf.exists()
    kxc(tmp_path, "add", "-q", "-k", str(kf), "-u", "kuser", "-p", str(db), "/Keyed", stdin=f"{PW}\nkeyed-pw\n")

    with pytest.raises(errors.WrongCredentials):
        VaultDocument.open_bytes(db.read_bytes(), make_composite(PW, None))
    doc = VaultDocument.open_bytes(db.read_bytes(), make_composite(PW, kf))
    s = entry_by_title(doc, "Keyed")
    assert doc.get_field(s.id, "Password") == "keyed-pw"
    e = doc.read_entry(s.id)
    e.password = "rotated-pw"
    doc.update_entry(e)
    db.write_bytes(doc.to_bytes())
    assert kxc_attr(tmp_path, db, "/Keyed", "Password", keyfile=kf) == "rotated-pw"
    assert kxc_attr(tmp_path, db, "/Keyed", "UserName", keyfile=kf) == "kuser"


@needs_kxc
def test_keylane_kdbx4_edited_by_keepassxc(rich, tmp_path):
    ids = rich[1]
    db = tmp_path / "rich.kdbx"
    db.write_bytes(rich[0])
    kxc(tmp_path, "add", "-q", "-u", "new-user", "-p", str(db), "/Work/AddedByKxc", stdin=f"{PW}\nadded-pw\n")
    kxc(tmp_path, "edit", "-q", "-u", "bobby", str(db), "/Work/Servers/Beta", stdin=f"{PW}\n")
    doc = VaultDocument.open_bytes(db.read_bytes(), make_composite(PW, None))
    assert doc.version[0] == 4
    added = entry_by_title(doc, "AddedByKxc")
    assert added.group_id == ids["work"] and doc.get_field(added.id, "Password") == "added-pw"
    beta = doc.read_entry(ids["beta"])
    assert beta.username == "bobby" and beta.password == "beta-pw"
    alpha = doc.read_entry(ids["alpha"])
    assert alpha.attachments == [("notes.bin", len(ATTACHMENT))]
    assert {f.key: (f.value, f.protected) for f in alpha.fields}["otp"] == (OTP_URI, True)
    assert doc.meta.find("CustomData") is not None
    assert any(i.findtext("Key") == "SyntheticMetaKey" for i in doc.meta.find("CustomData"))


@needs_kxc
def test_kdbx3_written_by_keylane_opens_in_keepassxc(tmp_path):
    comp = make_composite(PW, None)
    doc = VaultDocument.create(comp, name="Three", kdf_target_ms=1)
    doc.create_entry(EntryData(None, title="Old", password="p3"))
    d3 = VaultDocument.open_bytes(to_kdbx3(doc), comp)
    s = entry_by_title(d3, "Old")
    e = d3.read_entry(s.id)
    e.username = "edited-u3"
    d3.update_entry(e)
    db = tmp_path / "v3.kdbx"
    db.write_bytes(d3.to_bytes())
    assert version_of(db.read_bytes()) == (3, 1)
    assert kxc_attr(tmp_path, db, "/Old", "UserName") == "edited-u3"
    assert kxc_attr(tmp_path, db, "/Old", "Password") == "p3"
