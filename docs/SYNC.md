# Sync, backups and recovery

Keylane manages a **local file**. A sync client (OneDrive, Syncthing, Nextcloud
and so on) moves that file between machines. Keylane never talks to the
service, and cannot know when an upload has finished.

## Detecting external changes

- A `QFileSystemWatcher` on the file and its directory, plus a 5 s stat poll,
  catches in-place writes, atomic replacement and re-created files. This
  happens even while the vault is **locked**: Keylane notes the change but
  decrypts nothing.
- Reads are *stable snapshots*: the file's identity (device, inode, size,
  mtime) must stay the same across the read. Files modified less than 50 ms ago
  are given time to settle, with a bounded number of retries.
- A file that fails to parse is retried once after 0.5 s, because it may be an
  incomplete sync write. If it still fails, Keylane reports a corrupt vault.
  It is never treated as empty, and the last good state stays in memory.

## Three-way reconciliation

Keylane keeps an encrypted **baseline**, a copy of the last file it read or
wrote, at `~/.local/state/keylane/vaults/<vault-id>/baseline.kdbx`:

- it is ordinary KDBX ciphertext under the vault's own key (`0600`), so no
  plaintext is ever written;
- it survives restarts.

When the file on disk changes, Keylane runs a deterministic **three-way merge**
(`core/merge.py`) of baseline, local and remote, matching entries and groups
by UUID:

- **Independent changes merge automatically**: different entries, different
  fields of one entry, additions on both sides, and moves that don't collide.
- **Timestamps never decide a winner.** Clocks may differ, so they are used
  only to order history.
- **Ambiguous cases become conflicts:**
  - both sides changed the same field;
  - an edit on one side and a deletion on the other;
  - incompatible group moves or deletions, missing ancestry;
  - competing attachments, custom data or history.

  The merged tree keeps a safe version and *both alternatives are retained*.
  Deletions are not resurrected, and an edit is not lost to a deletion.
- If there is no trustworthy baseline (first sight of a file, an unrelated
  copy), Keylane runs a two-way merge in which every difference is a conflict.

While conflicts are open:

- affected entries are marked in the launcher and the manager, and copying from
  them requires an explicit version choice;
- saves that would overwrite ambiguous data are blocked;
- everything else keeps working from the last unambiguous state.

The **Conflicts** page shows both versions with passwords masked until
revealed. You can keep one side, combine field by field, or keep both
(duplicate entry). Losing values stay in the entry's history.

### Write races

Immediately before replacing the file, Keylane re-reads it. If its hash
differs from what Keylane last saw, the save is abandoned, the new external
version is reconciled, and the save is retried. The retries are bounded, so an
active sync loop is surfaced rather than looped on.

There is **no distributed transaction** with the sync client. A remote write
can still land between Keylane's final check and the rename, or the sync client
can create its own "conflict copy" file. Use **Merge another KDBX file…** in
the manager for such copies. It asks for that file's own credentials, merges it
with the same rules, and never modifies the input file. Keylane never scans
sync folders or guesses conflict-copy names.

### Writes that never reached the server

At unlock, if the baseline is newer than the file on disk (for example, a save
was overwritten by an older remote copy while Keylane wasn't running), the two
are reconciled. Local edits are therefore not silently lost across restarts.

## Saves

Every committed change is saved immediately. Success is reported only after
this sequence completes:

1. Serialize, then decrypt and verify the new bytes in memory.
2. Write a sibling temporary file and `fsync` it.
3. Read it back and compare its hash.
4. `rename` it over the vault and `fsync` the directory.

Failures (read-only location, permissions, disk full, unavailable sync
directory) are reported by category and leave the previous file intact.
Launcher "save & copy" copies only after this sequence succeeds. If the save
fails, the draft stays open for retry.

## Backups

| | |
|---|---|
| Location | `~/.local/state/keylane/vaults/<vault-id>/backups/` (`0700` directory, `0600` files) |
| Content | the **encrypted** vault bytes as they were on disk before being replaced |
| When | before Keylane overwrites the file, at most once per `backups.min_interval_minutes` (default 5). Always before a merge result replaces an external version, or when unreadable recovery data is set aside. Identical bytes are deduplicated |
| Retention | newest `backups.keep` (default **20**, range 1–500); older ones are pruned |

**Credential changes:** a backup is encrypted with the password and key file
that were current *when it was taken*. After you change the master password,
older backups still open only with the **old** credentials. Keylane does not
re-encrypt or delete them, because they are your recovery path; delete them
yourself if the old password must stop working.

**Restore:** open any backup with Keylane (Settings → Vault → Open) or with
KeePassXC. Or copy it over the vault while Keylane is locked; Keylane validates
the newly read file at the next unlock.

**Missing vault:** if the configured path disappears (for example, an
unmounted sync folder), Keylane reports "vault missing" and keeps the last
snapshot read-only while unlocked. It never creates an empty vault in its
place.
