"""T-04: password / passphrase generator."""

from __future__ import annotations

import inspect
import random
import secrets
import string

import pytest

from keylane.core import errors, generator
from keylane.core.generator import AMBIGUOUS, DEFAULT_SYMBOLS, MAX_LENGTH, PasswordSpec, entropy_bits, generate, validate, wordlist


def sample(spec: PasswordSpec, n: int = 300) -> list[str]:
    return [generate(spec).value for _ in range(n)]


# ---- randomness source


def test_uses_secrets_module_only():
    src = inspect.getsource(generator)
    assert "import random" not in src and "random." not in src.replace("randbelow", "")
    assert generator.secrets is secrets


def test_password_draws_come_from_secrets_choice(monkeypatch):
    calls = []

    def fake_choice(seq):
        calls.append(seq)
        return seq[len(calls) % len(seq)]

    monkeypatch.setattr(generator.secrets, "choice", fake_choice)
    monkeypatch.setattr(random, "choice", lambda *_: pytest.fail("random.choice must not be used"))
    monkeypatch.setattr(random, "random", lambda *_: pytest.fail("random.random must not be used"))
    spec = PasswordSpec(length=12, require_each=False)
    assert len(generate(spec).value) == 12
    assert len(calls) == 12


def test_passphrase_draws_come_from_secrets(monkeypatch):
    words, below = [], []
    monkeypatch.setattr(generator.secrets, "choice", lambda seq: words.append(seq) or seq[0])
    monkeypatch.setattr(generator.secrets, "randbelow", lambda n: below.append(n) or 0)
    g = generate(PasswordSpec(mode="passphrase", words=4, add_number=True))
    assert len(words) == 4 and all(w is wordlist() for w in words)
    assert below == [4, 10]
    first = wordlist()[0]
    assert g.value == "-".join([first + "0", first, first, first])


def test_rejection_sampling_failure_is_reported(monkeypatch):
    # A broken/degenerate source that never yields every class must not return a weak password silently.
    monkeypatch.setattr(generator.secrets, "choice", lambda seq: seq[0])
    with pytest.raises(errors.InvalidInput):
        generate(PasswordSpec(length=8))


# ---- defaults


def test_defaults_are_valid_and_strong():
    spec = PasswordSpec()
    validate(spec)
    g = generate(spec)
    assert len(g.value) == 24
    assert g.entropy_bits > 128
    assert set(g.value) <= set(string.ascii_letters + string.digits + DEFAULT_SYMBOLS)
    assert not set(DEFAULT_SYMBOLS) & set("\"'\\` ")
    p = generate(PasswordSpec(mode="passphrase"))
    assert p.value.count("-") >= 5  # a few EFF words (e.g. "t-shirt") contain hyphens
    assert p.entropy_bits == pytest.approx(6 * 12.925, abs=0.01)


def test_generated_repr_redacts_value():
    g = generate(PasswordSpec())
    assert g.value not in repr(g) and "redacted" in repr(g)


# ---- constraints over many samples


@pytest.mark.parametrize("length", [4, 5, 16, 64, MAX_LENGTH])
def test_length_and_required_classes(length):
    spec = PasswordSpec(length=length)
    for pw in sample(spec, 200 if length < 100 else 30):
        assert len(pw) == length
        assert any(c in string.ascii_lowercase for c in pw)
        assert any(c in string.ascii_uppercase for c in pw)
        assert any(c in string.digits for c in pw)
        assert any(c in DEFAULT_SYMBOLS for c in pw)


@pytest.mark.parametrize("classes", [
    dict(lower=True, upper=False, digits=False, symbols=False),
    dict(lower=False, upper=True, digits=True, symbols=False),
    dict(lower=False, upper=False, digits=True, symbols=False),
    dict(lower=True, upper=False, digits=False, symbols=True, symbol_set="@#"),
])
def test_charset_restricted_to_enabled_classes(classes):
    spec = PasswordSpec(length=10, **classes)
    allowed = ""
    allowed += string.ascii_lowercase if classes["lower"] else ""
    allowed += string.ascii_uppercase if classes["upper"] else ""
    allowed += string.digits if classes["digits"] else ""
    allowed += classes.get("symbol_set", DEFAULT_SYMBOLS) if classes["symbols"] else ""
    seen = set()
    for pw in sample(spec):
        assert set(pw) <= set(allowed)
        seen |= set(pw)
    # Every allowed character is reachable (sanity, not a statistical claim).
    assert seen == set(allowed)


def test_exclude_ambiguous_and_custom_exclusions():
    spec = PasswordSpec(length=40, exclude_ambiguous=True, exclude_chars="abcXYZ9")
    for pw in sample(spec):
        assert not set(pw) & AMBIGUOUS
        assert not set(pw) & set("abcXYZ9")


def test_whitespace_and_unprintable_symbols_are_never_emitted():
    spec = PasswordSpec(length=30, lower=False, upper=False, digits=False, symbols=True, symbol_set="a \t\n\x00b")
    for pw in sample(spec, 50):
        assert set(pw) <= {"a", "b"}


def test_require_each_false_allows_any_mix():
    spec = PasswordSpec(length=4, require_each=False, lower=True, upper=True, digits=True, symbols=True)
    validate(spec)
    assert all(len(pw) == 4 for pw in sample(spec, 50))


def test_passphrase_options():
    words = set(wordlist())
    for value in sample(PasswordSpec(mode="passphrase", words=5, separator="_."), 50):
        parts = value.split("_.")
        assert len(parts) == 5 and set(parts) <= words
    for value in sample(PasswordSpec(mode="passphrase", words=3, separator="", capitalize=True), 20):
        assert value[0].isupper()
    for value in sample(PasswordSpec(mode="passphrase", words=4, separator=" ", add_number=True), 50):
        parts = value.split(" ")
        assert len(parts) == 4
        assert sum(p[-1].isdigit() for p in parts) == 1
        assert all(p.rstrip(string.digits) in words for p in parts)


def test_wordlist_is_intact():
    wl = wordlist()
    assert len(wl) == 7776 == len(set(wl))
    assert all(w and w == w.strip() for w in wl)


def test_entropy_estimate_matches_alphabet():
    spec = PasswordSpec(length=20, lower=True, upper=False, digits=False, symbols=False)
    assert entropy_bits(spec) == pytest.approx(20 * 4.7004, abs=0.01)
    assert generate(spec).entropy_bits == pytest.approx(entropy_bits(spec))


# ---- invalid combinations


@pytest.mark.parametrize("spec, message", [
    (PasswordSpec(length=3), "Length"),
    (PasswordSpec(length=MAX_LENGTH + 1), "Length"),
    (PasswordSpec(lower=False, upper=False, digits=False, symbols=False), "at least one"),
    (PasswordSpec(lower=False, upper=False, digits=True, symbols=False, exclude_chars=string.digits), "no characters left"),
    (PasswordSpec(symbols=True, symbol_set=""), "no characters left"),
    (PasswordSpec(lower=False, upper=False, digits=True, symbols=False, exclude_ambiguous=True, exclude_chars="23456789"), "no characters left"),
    (PasswordSpec(mode="passphrase", words=2), "between 3 and 20"),
    (PasswordSpec(mode="passphrase", words=21), "between 3 and 20"),
    (PasswordSpec(mode="passphrase", separator="----"), "separator"),
    (PasswordSpec(mode="passphrase", separator="\n"), "separator"),
    (PasswordSpec(mode="pin"), "Unknown generator mode"),
])
def test_invalid_specs_are_rejected_clearly(spec, message):
    with pytest.raises(errors.InvalidInput) as exc:
        validate(spec)
    assert message in exc.value.user_message
    with pytest.raises(errors.InvalidInput):
        generate(spec)


def test_from_dict_ignores_unknown_keys():
    spec = PasswordSpec.from_dict({"length": 30, "bogus": 1})
    assert spec.length == 30 and spec == PasswordSpec(length=30)
    assert PasswordSpec.from_dict(None) == PasswordSpec()
    assert PasswordSpec.from_dict(spec.to_dict()) == spec
