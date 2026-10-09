# Architecture

## Process model

Keylane is a single **resident owner process**: `keylane daemon`, or whichever
process the first `keylane` invocation spawns. It owns:

- the vault engine and the only decrypted document (when unlocked);
- the QML UI: a launcher overlay, the manager window and a tray item;
- desktop integration: lock and sleep watchers, clipboard ownership, the
  Omarchy theme watcher;
- a local control socket.

`keylane <command>` is a thin client. It imports only the socket client (about
25 ms in total, including interpreter start-up), sends one JSON line and exits.
If no owner is running, it starts one, and the owner always starts **locked**.
A second owner refuses to start while the socket is live.

```
 keylane search ──unix socket (same UID, 0700 dir, ≤4 KiB JSON)──▶ ┌──────────────────────────┐
                                                                    │ Qt GUI thread            │
                                                                    │  Bridge (QObject) ◀─QML──┤ Launcher (layer-shell)
                                                                    │  SessionWatch, Tray      │ Manager window
                                                                    │        │ submit(job, epoch)
                                                                    │        ▼                 │
                                                                    │ vault worker thread      │
                                                                    │  VaultService ─ VaultDocument (pykeepass + lxml)
                                                                    │        │  storage: atomic_replace, BackupStore
                                                                    │        ▼  merge: 3-way reconcile
                                                                    │  immutable Snapshot(index, groups, trash) → GUI
                                                                    └──────────────────────────┘
```

## Threads and the lock epoch

- All decrypted-document access happens on **one worker thread**
  (`VaultService`). Mutations, saves and reconciliation are therefore
  serialized without locks.
- The GUI thread only reads an immutable `Snapshot`. It holds non-secret
  summaries, the search index, the group tree and trash, and is swapped
  atomically after every committed change.
- Secret values (a password to copy or reveal) are requested per operation,
  delivered once, and not retained by the bridge.
- Every job carries the **lock epoch** that was current when it was submitted.
  Locking increments the epoch, so a job that finishes after a lock drops its
  result instead of delivering it. A slow unlock, copy or reveal can never
  repopulate state.

## Modules

| Module | Responsibility |
|---|---|
| `core/kdbx.py` | `VaultDocument`: KDBX container via pykeepass, all edits directly on the lxml tree, so unknown elements survive; summaries, history, trash, groups, binaries |
| `core/merge.py` | Three-way merge of KDBX trees (entries, groups, history, binaries, deletions) with explicit conflicts |
| `core/storage.py` | Stable reads, atomic durable writes, private files, bounded encrypted backups |
| `core/service.py` | Lifecycle state machine, worker thread, saves, reconciliation, baseline, conflicts |
| `core/search.py` | Tiered fuzzy search over title, group path, URL host and tags |
| `core/generator.py` | Password/passphrase generator (`secrets`, rejection sampling) |
| `core/clipboard.py` | `wl-copy` ownership, history exclusion, timed clear |
| `core/ipc.py` | Control socket server/client, request validation, SO_PEERCRED |
| `core/config.py` | TOML settings with validation and visible warnings |
| `desktop/session_watch.py` | logind sleep/shutdown/lock and Hyprland session-lock detection |
| `desktop/hyprland.py` | Read-only Hyprland IPC (active monitor, lock state, focus restore) |
| `desktop/theme.py` | Omarchy palette/font tokens, light/dark fallback |
| `ui/bridge.py` | QObject API for QML, list models, timers |
| `ui/qml/` | Launcher, manager, editor, generator, settings, conflicts, trash |
| `ui/trace.py` | Opt-in latency tracer (`KEYLANE_TRACE=1`) |

## Stack decisions

| Decision | Choice | Why |
|---|---|---|
| Language/UI | Python 3.12+, PySide6/QML (Qt 6) | Qt Wayland and QML make the overlay, animations and theming cheap; the resident process removes startup cost from the shortcut path |
| KDBX engine | pykeepass (container, crypto, KDF) + direct lxml editing | Mature, KeePassXC-interoperable crypto and KDF; editing the XML tree directly preserves unknown fields and custom data exactly (D-01). No custom cryptography |
| Overlay | layer-shell-qt (`org.kde.layershell`), overlay layer, exclusive keyboard | A real layer surface does not tile, reflow or steal a workspace slot. Shown on the active monitor |
| Fuzzy search | Own tiered ranking + rapidfuzz for typos | Deterministic, explainable order (exact > prefix > word prefix > substring > tokens > initials > subsequence > typo); see `core/search.py` |
| Clipboard | `wl-copy --foreground` with secret on stdin | The process lifetime gives a reliable ownership signal for safe clearing |
| Control transport | Unix socket in `$XDG_RUNTIME_DIR/keylane`, JSON lines | Same-UID only, tiny, no D-Bus activation needed |
| Lock detection | logind (delay inhibitor) + Hyprland session-lock polling | Omarchy locks via Quickshell, which bypasses logind's Lock signal |
| Packaging | setuptools + Arch PKGBUILD | Matches the reference distribution |
| License | GPL-3.0-or-later | Compatible with pykeepass (GPL-3.0) and PySide6 (LGPL) |

## Search ranking

Only these fields are indexed: **title, group path, URL host and tags**.
Usernames, passwords, notes and custom fields are never indexed.

Title tiers, from best to worst:

| Tier | Score |
|---|---|
| exact | 1000 |
| prefix | 900 |
| word prefix | 800 |
| substring | 700 |
| all tokens | 650 |
| initials (camelCase aware: `GitHub` → `gh`) | 600 |
| subsequence | 400–499 |
| typo (rapidfuzz, ≥4 characters, only when results are scarce) | ≤300 |

- The secondary fields use the same tiers at half weight, so a strong title
  match always wins.
- Recent use adds at most 60 points, which reorders only comparable matches.
- Ties break by title, then by entry ID, so results are fully deterministic.

The engine never scores entries one by one in Python:

- Titles and the *distinct* secondary texts are kept in newline-separated
  blobs, and each tier is computed by C-level regex scans over them.
- Bands that provably cannot reach the top 50 (even with the maximum usage
  boost) are skipped.
- A brute-force reference scorer in the test suite guards exact equivalence.
