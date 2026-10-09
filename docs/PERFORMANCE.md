# Performance

Keylane's performance contract (REQUIREMENTS §13, T-11) is measured, not
estimated. On a **10,000-entry** vault:

- warm shortcut → focused search field: ≤ 50 ms;
- keystroke → updated results frame: ≤ 50 ms at p95.

## Results

The vault is a synthetic 10,000-entry vault (`scripts/make-demo-vault.py`):

- 7 groups (nested), 1,429 custom protected fields, tags and notes;
- the **production KDF** (Argon2id, 64 MiB, tuned to about 1 s).

| Measurement | p50 | p95 | p99 | max | Target |
|---|---|---|---|---|---|
| Shortcut → card visible and focused (warm, 48 invocations) | 5.6 ms | 15.1 ms | 15.5 ms | — | ≤ 50 ms ✅ |
| Key press → updated results frame (typed queries) | 13.6 ms | 38.2 ms | 53.2 ms | 54.2 ms | ≤ 50 ms p95 ✅ |
| Search engine: query → ranked rows (headless) | 1.29 ms | 6.2 ms | — | 18 ms | — |
| Search index build after unlock | 130 ms warm (344 ms first run) | | | | — |
| Re-index after an edit | 14 ms | | | | — |
| Unlock: KDF + decrypt/parse (1.5–1.8 s), summaries (0.22 s), index (0.14 s) | ≈ 1.9–2.1 s | | | | intentionally expensive |

The remaining key → frame tail is Qt Quick rendering and compositor frame
pacing; the 120 Hz panel gives 8.3 ms frame slots. The search itself rarely
exceeds 6 ms.

### Hardware and software

| | |
|---|---|
| Machine | Apple M2 Max (12 cores), 64 GB, Asahi Linux (Arch Linux ARM), kernel 7.1 |
| Display | internal 3024×1964 at 120 Hz, scale 2 |
| Desktop | Hyprland 0.56.2 (Omarchy) |
| Stack | Python 3.14, PySide6/Qt 6.11, pykeepass 4.2, layer-shell-qt 6.7 |
| CPU governor | `schedutil` (default) — **not** pinned to performance |

The governor matters. Between keystrokes the cores clock down, so the first
search after a pause runs 3–4× slower than a hot loop. The benchmark sleeps
between keystrokes to reproduce real typing, and the engine was designed for
that cold-cache, low-clock case.

## Method

`scripts/bench.py --desktop` starts a **separate** resident Keylane with an
isolated config, state and runtime directory and a freshly generated vault. It
never touches your profile.

- **Open:** the script runs the same `keylane` command a key binding would and
  records `CLOCK_MONOTONIC` before the request. Keylane (with
  `KEYLANE_TRACE=1`) logs the first swapped frame in which the card is visible
  and the search field has focus.
- **Keys:** `wtype` types queries one character at a time into the overlay.
  Each key press is matched to the first frame swapped after the result model
  was updated for that query.
- The script first checks that the overlay is mapped and focused. If the
  overlay is dismissed externally, the run is skipped rather than typed
  elsewhere.

`scripts/bench.py` without `--desktop` runs the headless engine benchmark,
which times query → ranked, model-ready rows.

```sh
.venv/bin/python scripts/bench.py                     # engine, 10k entries
.venv/bin/python scripts/bench.py --desktop --runs 4  # live, needs wtype + Wayland
```

## What makes it fast

- **Resident process.** The shortcut sends one small Unix-socket request to an
  already-running instance with an already-built QML scene. Nothing is
  imported, decrypted or compiled on the hot path.
- **Precomputed index.** At unlock, titles, paths, hosts and tags are
  normalized once (case-folded, accents stripped) into newline-separated text
  blobs.
- **Banded scoring.** Each tier is found with one C-level regex scan over a
  blob instead of per-entry Python scoring:
  - exact, prefix, word-prefix and substring tiers;
  - tokens, initials, and subsequence tiers (possessive patterns, no
    backtracking).

  Lower tiers are skipped as soon as enough higher-scored titles exist to fill
  the visible list. A test checks that the result is **identical** to a
  brute-force reference scorer.
- **Incremental model updates.** The launcher list updates its rows in place
  rather than resetting, so delegates are reused.
- **GC settling.** Long-lived index objects are moved out of the cyclic
  garbage collector (`gc.freeze`) after each index build. This removed
  15–20 ms gen-2 pauses from typing.
