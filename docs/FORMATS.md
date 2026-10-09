# KDBX formats

Keylane reads and writes standard KeePass databases. Encryption, KDFs and the
binary container come from **pykeepass**. Every edit is made directly on the
decrypted XML tree, so elements Keylane does not understand are preserved.

## Supported

| Item | Supported |
|---|---|
| Container | KDBX **3.0, 3.1, 4.0, 4.1**. Saved in the version it was opened in, never silently upgraded |
| Outer cipher | AES-256-CBC, ChaCha20, Twofish-CBC |
| KDF | AES-KDF (KDBX3/4), Argon2d, Argon2id. Parameters are preserved; the salt and IVs are rotated on save |
| Inner stream | Salsa20 (KDBX3), ChaCha20 (KDBX4) |
| Compression | GZip or none (preserved) |
| Credentials | password, key file, or both. Password-only and key-file-only work too |
| Key files | KeePass XML v1.0 and v2.0 (v2 hash verified), 32-byte binary, 64-character hex, and any other file (SHA-256 of its contents, KeePass rule) |

## Preserved on edit

Entry and group UUIDs, the group tree, icons, history, attachments (shared
binaries are deduplicated by content), protected-value flags, custom string
fields, entry/group/meta `CustomData`, TOTP settings (`otp` and KeePassXC
`TimeOtp-*` fields), AutoType, expiry, tags, `DeletedObjects`, the recycle-bin
configuration, and all unknown elements and attributes.

Timestamps are updated only for the item you change. KeePass-style history
snapshots are written before each edit and respect the database's
`HistoryMaxItems` and `HistoryMaxSize`.

## Not supported (fails explicitly)

| Input | Result |
|---|---|
| KeePass 1.x `.kdb` | "KeePass 1.x (.kdb) databases are not supported. Convert it to KDBX first." |
| KDBX 2.x, or ≥ 4.2 / 5.x | "KDBX version X.Y is not supported." |
| Unknown cipher or KDF UUID | Unsupported-format error; the file is not touched |
| YubiKey/challenge-response and other hardware factors | Unsupported-factor error (KeePassXC stores no marker in the file, so this shows up as wrong credentials) |
| Damaged or truncated file | "corrupt vault"; external writes in progress are retried before reporting (see [SYNC](SYNC.md)) |

## New vaults

New vaults use **KDBX 4.0, AES-256, Argon2id with 64 MiB and parallelism 2**.
The iteration count is benchmarked on the machine to about **1 second** of
unlock time, and is never below 2. Changing credentials keeps the cipher and
KDF and rotates the salts.

## Verification

`tests/test_kdbx.py` round-trips every supported variant and compares the
decrypted content semantically rather than byte for byte: salts and IVs change
on every save by design. It also runs interoperability checks against the
**KeePassXC CLI** (`keepassxc-cli`): vaults written by Keylane open in KeePassXC,
and vaults created and edited by KeePassXC open, edit and reopen in Keylane,
with password-only, key-file-only and combined credentials. Those tests are
skipped when `keepassxc-cli` is not installed.
