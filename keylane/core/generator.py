"""Password and passphrase generation using the OS CSPRNG (``secrets``).

Defaults (documented in docs/GENERATOR.md):
* Random password: 24 characters from lower+upper+digits+symbols with at least
  one of each enabled class (rejection sampling keeps the distribution uniform
  over all valid passwords). Default symbols avoid quotes, backslash, backtick
  and space, which are commonly rejected by websites and break shell pasting.
* Passphrase: 6 words from the EFF large word list (7776 words, ~12.9 bits
  per word, ~77.5 bits total) separated by "-".
"""

from __future__ import annotations

import math
import secrets
import string
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib import resources

from . import errors

DEFAULT_SYMBOLS = "!#$%&*+-.:;=?@^_~"
EXTENDED_SYMBOLS = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
AMBIGUOUS = set("Il1O0o|`'\"")
MAX_LENGTH = 256


@dataclass
class PasswordSpec:
    mode: str = "password"  # "password" | "passphrase"
    length: int = 24
    lower: bool = True
    upper: bool = True
    digits: bool = True
    symbols: bool = True
    symbol_set: str = DEFAULT_SYMBOLS
    exclude_ambiguous: bool = False
    exclude_chars: str = ""
    require_each: bool = True
    words: int = 6
    separator: str = "-"
    capitalize: bool = False
    add_number: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PasswordSpec":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        spec = cls(**known)
        return spec


@dataclass(frozen=True)
class Generated:
    value: str
    entropy_bits: float
    description: str = field(default="")

    def __repr__(self) -> str:
        return f"Generated(<redacted>, {self.entropy_bits:.0f} bits)"


def _classes(spec: PasswordSpec) -> list[str]:
    excluded = set(spec.exclude_chars)
    if spec.exclude_ambiguous:
        excluded |= AMBIGUOUS
    out = []
    for enabled, chars in ((spec.lower, string.ascii_lowercase), (spec.upper, string.ascii_uppercase),
                           (spec.digits, string.digits), (spec.symbols, spec.symbol_set)):
        if enabled:
            filtered = "".join(sorted(set(c for c in chars if c not in excluded and c.isprintable() and not c.isspace())))
            if not filtered:
                raise errors.InvalidInput("A selected character class has no characters left after exclusions.")
            out.append(filtered)
    return out


def validate(spec: PasswordSpec) -> None:
    if spec.mode == "passphrase":
        if not (3 <= spec.words <= 20):
            raise errors.InvalidInput("Passphrases need between 3 and 20 words.")
        if len(spec.separator) > 3 or any(c in spec.separator for c in "\n\r\t"):
            raise errors.InvalidInput("The word separator must be at most 3 visible characters.")
        return
    if spec.mode != "password":
        raise errors.InvalidInput("Unknown generator mode.")
    if not (4 <= spec.length <= MAX_LENGTH):
        raise errors.InvalidInput(f"Length must be between 4 and {MAX_LENGTH}.")
    classes = _classes(spec)
    if not classes:
        raise errors.InvalidInput("Select at least one character type.")
    if spec.require_each and spec.length < len(classes):
        raise errors.InvalidInput("The password is too short to include every selected character type.")


def entropy_bits(spec: PasswordSpec) -> float:
    if spec.mode == "passphrase":
        bits = spec.words * math.log2(len(wordlist()))
        if spec.add_number:
            bits += math.log2(10)
        return bits
    classes = _classes(spec)
    alphabet = len(set("".join(classes)))
    bits = spec.length * math.log2(alphabet)
    if spec.require_each and len(classes) > 1:
        # Probability mass removed by the "one of each" constraint (upper bound on loss).
        p_ok = 1.0
        for c in classes:
            p_ok -= (1 - len(c) / alphabet) ** spec.length
        bits += math.log2(max(p_ok, 1e-12))
    return bits


@lru_cache(maxsize=1)
def wordlist() -> tuple[str, ...]:
    text = resources.files("keylane.data").joinpath("eff_large_wordlist.txt").read_text("utf-8")
    words = tuple(w.strip() for w in text.splitlines() if w.strip())
    if len(words) != 7776 or len(set(words)) != 7776:
        raise RuntimeError("bundled word list is damaged")
    return words


def generate(spec: PasswordSpec) -> Generated:
    validate(spec)
    if spec.mode == "passphrase":
        words = wordlist()
        chosen = [secrets.choice(words) for _ in range(spec.words)]
        if spec.capitalize:
            chosen = [w.capitalize() for w in chosen]
        if spec.add_number:
            i = secrets.randbelow(len(chosen))
            chosen[i] = chosen[i] + str(secrets.randbelow(10))
        return Generated(spec.separator.join(chosen), entropy_bits(spec), f"{spec.words}-word passphrase")
    classes = _classes(spec)
    alphabet = "".join(sorted(set("".join(classes))))
    for _ in range(10_000):
        pw = "".join(secrets.choice(alphabet) for _ in range(spec.length))
        if not spec.require_each or all(any(c in cls for c in pw) for cls in classes):
            return Generated(pw, entropy_bits(spec), f"{spec.length} characters")
    raise errors.InvalidInput("These constraints are too strict to generate a password.")
