# Password generator

All randomness comes from the OS CSPRNG via Python's `secrets` module.

## Random password (default)

| Option | Default |
|---|---|
| Length | **24** (4–256) |
| Classes | lowercase, uppercase, digits, symbols — all on |
| Symbols | `!#$%&*+-.:;=?@^_~` (no quotes, backslash, backtick or space; these are often rejected by sites or break shell pasting). The extended set is all ASCII punctuation |
| Require each class | on: at least one character from every enabled class |
| Exclude ambiguous | off: `I l 1 O 0 o \| \` ' "` |
| Exclude characters | none (free text) |

The "require each class" rule uses **rejection sampling**: uniform passwords
are drawn and those missing a class are discarded. The result is uniform over
all valid passwords, and no positions are biased.

Strength is the exact entropy of that distribution:

| Setting | Entropy |
|---|---|
| Default (79-symbol alphabet × 24) | ≈ 151 bits |
| Default with ambiguous characters excluded | ≈ 148 bits |
| 16 characters, all classes | ≈ 101 bits |

## Passphrase

| Option | Default |
|---|---|
| Words | **6** (3–20) from the EFF large word list (7,776 words, 12.9 bits each) |
| Separator | `-` (up to 3 visible characters) |
| Capitalize | off |
| Add a digit | off (appends 0–9 to one random word, +3.3 bits) |

The default passphrase has **≈ 77.5 bits** of entropy.

## Where it is used

- The launcher create flow (`Ctrl+N`, or Enter on "Create …"): fill in the
  title and username; the password is pre-generated. Press `Ctrl+R` to
  regenerate.
- The manager's entry editor.

Generator settings are saved in `config.toml` (`[generator]`) as
non-secret preferences. Generated values never appear in logs or `repr`.

## Attribution

`keylane/data/eff_large_wordlist.txt` is the **EFF Large Wordlist for
Passphrases** by the Electronic Frontier Foundation, used under the
[Creative Commons Attribution 3.0 License](https://creativecommons.org/licenses/by/3.0/us/).
Source: <https://www.eff.org/dice>. The dice-roll column was removed; the
words are unchanged.
