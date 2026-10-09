# Security

## Threat model

Keylane defends against:

- **offline theft of the vault file**: it is standard KDBX encryption, unchanged;
- **accidental disclosure** through clipboard history, logs, notifications,
  the command line or the UI;
- **stale unlocked sessions** across desktop lock, suspend and shutdown;
- **unsafe writes** that could destroy the last valid vault;
- **unauthorized control requests** from other users.

It does **not** claim protection against root, a compromised compositor, or
malware running as your user. Same-user programs can read the clipboard while
a secret is on it, can ptrace a dumpable process (Keylane makes itself
non-dumpable, see below), and can connect to the control socket. The control
protocol therefore has no command that returns vault content.

Keylane uses established cryptography only: pykeepass (pycryptodomex, argon2-cffi).
It has no custom crypto.

## Lifecycle

| State | Behavior |
|---|---|
| Not running | Any `keylane` command starts the owner, which starts **locked** |
| No vault configured | Setup page offers create or open; no path is ever guessed |
| Locked | No index, secrets, drafts or reveals exist in memory |
| Unlocking | A single in-flight unlock; wrong credentials are reported; Esc cancels |
| Unlocked | Launcher and manager work on the last committed snapshot |
| Saving / reconciling | Serialized on the worker thread; success is reported only after a durable save |
| Conflict | Ambiguous entries are marked; overwriting saves are blocked until resolved |
| Locking | Epoch bump, owned clipboard cleared, drafts discarded, document wiped |
| Quitting | Lock, then exit; new requests are refused |

### Lock triggers

| Trigger | Default | How it is detected |
|---|---|---|
| Desktop lock | on | logind `Session.Lock` *and* Hyprland session-lock state (polled over IPC every 750 ms, because Omarchy locks through Quickshell, which does not use logind) |
| Suspend / hibernate | on | logind `PrepareForSleep`, holding a **delay inhibitor**, so plaintext is dropped *before* sleep; the inhibitor is released after locking and re-acquired on resume |
| Shutdown / logout | always | logind `PrepareForShutdown`, SIGTERM/SIGINT/SIGHUP, app exit |
| Explicit | — | Ctrl+L, tray "Lock", `keylane lock` |
| Inactivity | **off** | Optional, `security.inactivity_timeout_minutes` (1–1440) |

- Unlocking the desktop or resuming never unlocks the vault.
- Closing the manager or dismissing the launcher does not lock.
- If a trigger cannot be detected (no logind, no `systemd-inhibit`, no
  session-lock source), Settings lists the gap and the log records it.
  Keylane does not pretend to have coverage it lacks.
- **Inactivity** means no vault use through Keylane: unlocking, copying a
  field, opening, creating or saving an entry, and saving settings each
  restart the timer. Merely having the launcher or manager open, or activity
  in other applications, does not count.
- **Locking discards dirty drafts.** An unsaved launcher draft or manager edit
  is lost; the manager warns before an explicit lock or quit with unsaved
  changes.

## Clipboard

- Secrets are written to `wl-copy --foreground --sensitive` on **stdin**,
  never on argv or in the environment. `--sensitive` makes wl-clipboard
  offer the `x-kde-passwordManagerHint` type, and the child also gets
  `CLIPBOARD_STATE=sensitive`. Omarchy's clipboard capture, Klipper, CopyQ and
  GPaste honor these.
- Clipboard history exclusion is checked at copy time. If an unknown or
  non-honoring history tool is running (cliphist without Omarchy's filter,
  clipman, clipse, a bare `wl-paste --watch`), Keylane warns visibly. It never
  claims exclusion it cannot confirm.
- Clearing is ownership based. The `wl-copy` process lives exactly as long as
  it owns the selection. After `security.clipboard_seconds` (default 20 s) the
  clipboard is cleared **only if that process still owns it**, so a later
  unrelated copy is never cleared. A new copy cancels the previous timer.
  Lock and quit clear an owned selection immediately.
- A copy that fails (no compositor, `wl-copy` exits, lock races the copy) is
  reported as a failure, never as success.
- The primary selection is never touched.

## Memory, files and diagnostics

- The owner disables core dumps (`RLIMIT_CORE=0`) and marks itself
  non-dumpable (`PR_SET_DUMPABLE=0`). That also blocks same-user ptrace and
  `/proc/<pid>/mem` reads, and keeps it out of systemd-coredump.
- **What is wiped on lock:**
  - the composite key bytes are zeroed;
  - the decrypted XML tree and every snapshot are dropped, and a full garbage
    collection runs;
  - the search index and drafts are released.
- **What is not guaranteed:** Python strings are immutable and Qt copies text
  internally, so freed plaintext may linger in process memory until it is
  reused. Keylane minimizes lifetimes and copies (secrets are fetched per
  action, never cached in the UI), but it cannot promise zeroization.
- Swap is not locked. Use encrypted swap or zram if this matters to you.
- **Never written to disk:** the master password, decrypted vault, search
  index, drafts, generator output, or merge documents. On disk you find only:
  - `config.toml`, settings only;
  - encrypted KDBX bytes: the vault, the baseline and backups, all `0600`
    inside `0700` directories under `~/.local/state/keylane/`;
  - log lines with categories and opaque IDs only, never titles, queries,
    usernames, URLs or secrets.
- The control socket lives in `$XDG_RUNTIME_DIR/keylane` (`0700`):
  - peers are verified with `SO_PEERCRED` to have the same UID;
  - requests are limited to 4 KiB, one JSON object per line;
  - unknown commands and fields are rejected.
  - The command line never accepts or prints secrets.
- Vault XML, attachments and custom data are treated as untrusted:
  - parsing uses lxml without network access or external entities (lxml ≥ 5
    defaults; key files use a parser with entity resolution disabled);
  - parse failures never touch the original file.

## Reporting

This is a personal project. Report issues privately to the maintainer; please
do not attach real vaults.
