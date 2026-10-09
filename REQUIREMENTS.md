# Keylane

## Status and authorization

**Approved product requirements; implementation is not authorized.**

This document records the requirements agreed with the project owner on
2026-10-09. The owner approved creating this document, not writing application
code, installing dependencies, changing desktop configuration, accessing a real
vault, initializing/publishing a repository, or otherwise implementing the app.
The next agents must obtain separate implementation approval before doing those
things. Once implementation is approved, they may choose the engineering path
within this product contract; the owner does not want to prescribe a framework,
language, engine, or extraction/fork strategy.

Keylane is the working product name. The intended project directory is
`keylane/` within the owner's `alexpomsft` workspace. Public repository-name and
trademark availability have not been verified. Public GitHub publication is an
eventual goal, not authorized by this document.

Normative terms:

- **Must**: required for v1 acceptance.
- **Should**: preferred; deviations need a documented reason and must preserve
  the underlying user outcome.
- **May**: an implementation option, not a promised feature.
- **Deferred**: outside v1; preserve a viable future path where specified.

## 1. Product intent

Keylane is a standalone KeePass-compatible password manager for Linux/Wayland,
with an excellent Hyprland/Omarchy experience. Its defining interaction is:

**Global shortcut -> immediately type an entry name -> fuzzy-select the right
entry -> copy its password -> return to the previous application.**

The current KeePassXC UI is the motivation for a new product, not a reason to
discard its compatible storage behavior or proven code. KDBX compatibility is
the requirement. KeeWeb branding, its codebase, and its web/Electron architecture
are not requirements.

V1 must be useful enough for the owner to remove KeePassXC from the normal
workflow. It is not merely a read-only launcher over another password manager.
Entry creation, editing, strong password generation, and OneDrive-related file
reconciliation belong in v1.

Native feel is an outcome, not a technology label. A nominally native framework
with poor focus behavior, awkward navigation, or a foreign-looking interface
does not satisfy this document.

### Success criteria

1. Routine retrieval is fast, keyboard-complete, and does not interrupt the
   desktop layout.
2. Creating and saving a new credential is available directly from the launcher.
3. Full everyday vault management does not require another installed client.
4. Existing KDBX contents survive editing and synchronization without silent
   loss, including data for features whose UI is deferred.
5. Omarchy themes, fonts, scaling, tray, and keyboard conventions feel coherent.
6. Security limitations are explicit rather than concealed by optimistic UI.

## 2. V1 scope

| Area | Required in v1 |
| --- | --- |
| Platform | Linux/Wayland; Hyprland + Omarchy are the reference environment |
| Vaults | Create/open/save one configured local KDBX vault; permit switching files without treating both as concurrently active |
| Unlock | Master password, KeePass-compatible key files, or their combination; change vault credentials in settings |
| Retrieval | Global shortcut, fuzzy search, keyboard selection, password/username copy |
| Creation | Compact launcher-accessible form, generated password, save, explicit Save & copy |
| Management | Separate management window; entries, groups, credential settings, trash, history |
| Entry fields | Title, username, password, URLs, notes, tags, protected/unprotected custom fields |
| Generator | Secure random passwords, configurable defaults and per-generation constraints; optional passphrase mode |
| Storage | Immediate save on committed changes, recoverable encrypted backups, external-change handling |
| Reconciliation | Nonconflicting automatic merge, explicit ambiguous-conflict resolution, manual KDBX import/merge |
| Desktop | Resident per-user application, CLI control entry points, tray, consent-based autostart/shortcut setup |
| Appearance | Omarchy-aware styling, theme/font updates, light/dark fallback, high-DPI behavior |
| Security | Lock lifecycle, sensitive clipboard signaling, timed clipboard cleanup, safe diagnostics |
| Distribution | Arch-focused installable package and desktop entry |

### Required management operations

- Create/open a vault and change its master-password/key-file configuration.
- Create, rename, move, and recycle groups.
- Create, edit, move, and recycle entries.
- View trash, restore its contents, and permanently delete with confirmation.
- View entry history and restore a historical version without silently losing
  the current version.
- Edit protected/unprotected custom fields without losing their protection
  designation or unrelated fields.
- Review and resolve merge conflicts.
- Open settings and explicitly lock or quit the app.

### Deferred/non-goals

- Browser autofill/integration in v1.
- Windows, macOS, and X11 support.
- Fingerprint unlock; hardware challenge-response/YubiKey unlock.
- Dedicated TOTP, attachment, or passkey management UI.
- Autotype, automatic paste, or automatic typing into other applications.
- Built-in cloud accounts, OneDrive authentication, or a new synchronization
  service.
- Simultaneous multi-vault search/management.
- Plugin systems, spreadsheet-style bulk editing, non-KDBX import/export.
- A required Omarchy shell plugin or a KeePassXC runtime dependency.
- Persisted master passwords or automatically unlocking after app restart.

Deferring advanced UI never permits dropping existing advanced data. Unsupported
unlock factors must produce an explicit unsupported-factor error, not an
incorrect-password message or a conversion attempt.

## 3. Primary UX: launcher

### L-01: Summoning and placement

The preferred personal shortcut is **Super+Alt+K**. It currently launches
KeePassXC on the reference machine. Replacing that binding is a separately
approved migration, not a free unused binding.

The launcher must:

- Appear as a compact overlay centered on the active monitor.
- Place keyboard focus directly in search, with no preliminary click.
- Avoid becoming a tiled window or reflowing existing windows.
- Remain legible under supported monitor scales and mixed-scale setups.
- Return focus to the previously active application on successful copy or
  dismissal, if that application still exists and restoration is permitted.
- Handle an unavailable prior window gracefully, without guessing a different
  application into which to type.
- Reuse the existing app instance. Repeated invocation must not create duplicate
  launchers, duplicated unlock prompts, or additional vault owners.

While the launcher is already visible, invoking its shortcut should focus the
existing input without discarding the current query. It must never trigger a
credential action merely because the shortcut was repeated.

### L-02: Search semantics

Search may match only:

- Entry title.
- Group path.
- URL/domain.
- Tags.

Do not search usernames, passwords, notes, protected or unprotected custom
fields, historical secrets, or attachment contents. A matching-looking username
must not make an otherwise nonmatching entry appear.

Ranking must favor meaningful title matches. Usage recency/frequency may break
otherwise comparable matches; it must not routinely bury a strong title match
below an unrelated frequently used entry. Normalize case and support ordinary
Unicode entry text. Exact fuzzy algorithm/tokenization is an engineering choice,
but must have deterministic tests and visibly useful results.

Each result must show title and enough username/group context to distinguish
duplicate titles. Duplicate titles are allowed. Never show the password in
results. Trash contents must not appear in normal retrieval.

Empty search shows recently used entries from this unlocked session, or a useful
empty state if there are none. Search/copy usage must not rewrite the vault.
Usage metadata remains local/in memory and is cleared on vault lock. Do not
persist a plaintext credential/search index or search-query log.

### L-03: Keyboard actions

| Input | Behavior |
| --- | --- |
| Typing | Updates fuzzy results |
| Up/Down | Selects a result |
| Enter | Copies selected password, dismisses, restores focus |
| Ctrl+Enter | Copies selected username, dismisses, restores focus |
| Ctrl+N | Opens compact entry creation with the current query as title |
| Ctrl+E | Opens the selected entry in the management editor |
| Escape | Cancels the current launcher/subflow without copying |

Provide a subtle action footer and visible accessible selection. Creation must
also be discoverable without memorizing Ctrl+N. Mouse navigation/copy controls
remain supported.

No selected result, an empty requested field, or a locked/stale/deleted entry
must cause copying some other entry or returning a success-shaped fallback.
Keep the UI actionable and explain the failure without exposing secret data.

### L-04: Locked invocation

If the vault is locked, the shortcut opens a focused unlock prompt. Successful
unlock resumes the requested launcher action. Failure retains a useful prompt
with an explicit error, not a blank launcher. Cancellation leaves the vault
locked and returns focus.

Missing vaults and unavailable key files must be distinguishable from wrong
credentials and corrupt/unsupported files. Do not retain an unlock password
after it has served its purpose.

## 4. Entry creation and editing

### C-01: Creation from search

Creation is always an explicit action. An unmatched search query must never
silently create an entry.

The compact form must:

- Prefill title from the search query.
- Offer a generated password and an explicit regenerate action.
- Allow username, URL, and destination group to be entered/selected.
- Show the destination vault/group clearly.
- Provide Save, Save & copy password, and Cancel.
- Allow access to the full editor when additional fields are needed.

Choose a configurable default destination group; the single active vault is the
v1 destination. Do not erase form values while switching to fuller editing.
Title equality is not an error and must not overwrite a pre-existing entry.

### C-02: Persistence before success

Save & copy must commit the entry successfully before copying its generated
password and dismissing. Until persistence succeeds, the form must not imply the
credential is safely stored.

Save failure, conflict, permission failure, or a unavailable vault path retains
the draft while the vault remains unlocked and permits retry/resolution. A
failed save must not yield a successful copy-and-close flow.

A later sync-client upload failure is not the same as a failed local save:
Keylane must not claim that OneDrive has uploaded a change merely because local
storage succeeded.

### C-03: Editing and drafts

The full editor includes all v1 fields. Passwords and protected custom fields
are masked by default and revealed only by explicit action. Copy and reveal
must be distinguishable actions.

Committed saves persist immediately; keystrokes in an uncommitted form are not
themselves commits. Closing/navigating away from a dirty editor prompts
Save / Discard / Cancel.

**Security lock takes precedence over draft recovery.** Desktop lock, suspend,
logout, shutdown, or manual vault lock drops unsaved plaintext drafts, clears
revealed values, and locks even if an ordinary dirty-editor prompt would have
been shown. Do not preserve these drafts in plaintext for later restoration.
Make this policy discoverable before a user relies on long-lived drafts.

Restoring a historical version must itself be a deliberate committed operation
and preserve recovery of the version it replaces.

### C-04: Generator

- Use a cryptographically secure random source from a mature implementation.
- Default random-password length: **24 characters**.
- Provide length and character-class controls, regenerate, and passphrase mode.
- Allow the basic defaults to be configured.
- Make website-specific constraints explicit rather than silently generating a
  weaker credential.
- Reject impossible/invalid generator configurations with a visible explanation.
- Generation, save, reveal, and copy remain intentional distinct operations;
  Save & copy is the explicit combined operation.
- Passwords/passphrases and intermediate generator state must not be logged.

The implementation team chooses safe character-set and passphrase word-list
defaults and documents their entropy assumptions and constraint behavior.
Do not invent a homegrown pseudo-random generator or cryptographic primitive.

## 5. Management window and tray

### M-01: Management surface

Provide a spacious, keyboard-complete management window with group navigation,
entry list, and editor/detail view. Avoid a browser-tab/dashboard aesthetic.
Use clear empty, loading, locked, dirty, saving, failed, and conflicted states.

Closing the manager does not exit or implicitly lock the resident app.
Minimizing/closing ordinary windows must not prevent shortcut use. Quit is a
distinct action that exits and clears unlocked state.

### M-02: Tray integration

Expose a native StatusNotifier-compatible tray item with:

- Primary activation: Open manager.
- Menu: Open manager, Quick search, Create entry, Lock/Unlock, Settings, Quit.
- An intelligible icon, tooltip, and locked/unlocked state without exposing
  entry names, secrets, or unnecessary vault details.
- Accessible menu labels and keyboard navigation where the shell supports them.

The installed Omarchy shell supports these tray items, including context menus.
Do not automatically pin the item, alter bar placement, or rearrange other
items. Native operation must not depend on installing a shell plugin.

## 6. Native Omarchy experience

### N-01: Visual/interaction acceptance

The visual reference is Omarchy's own menus and shell, not merely a similar
color palette. Acceptance requires:

- A restrained compact launcher, crisp type, clear selection, and coherent
  spacing rather than a miniature full manager.
- Live adaptation to the active Omarchy palette/font where available.
- Standalone light/dark fallback when Omarchy theme metadata is absent or invalid.
- Legible contrast and distinct focus, hover, selected, disabled, danger,
  conflict, and success states.
- Consistent scaling without clipped icons/text, blurry assets, or unusably small
  controls.
- Keyboard-complete retrieval, creation, unlock, edit, and conflict-resolution
  workflows; mouse operation is also supported.
- Correct focus, monitor placement, dismissal, and lifecycle behavior.
- Appropriate accessible names and non-color-only error/status distinctions.

Validate light and dark themes and switching theme/font while Keylane runs.
An unavailable theme source may use the documented fallback with an appropriate
nonsecret diagnostic; malformed data must not silently produce unreadable UI.
Do not hard-code one theme, current filesystem layout, or current Omarchy
release as the permanent contract.

### N-02: Personal reference snapshot

Read-only inspection on 2026-10-09 found:

| Component | Reference state |
| --- | --- |
| Hyprland | 0.56.2 |
| Omarchy | 4.0.3rc4-1 |
| Shell | Quickshell; `omarchy.tray` consumes StatusNotifier items |
| Theme | Tokyo Night |
| Font | JetBrainsMono Nerd Font |
| Clipboard | wl-clipboard 2.3.0; Omarchy shell clipboard capture |
| Shortcut | Super+Alt+K currently assigned to KeePassXC |

Theme state was available under the Omarchy current-theme state directory as
`colors.toml` and generated `shell.toml`; fontconfig is a source of font
selection. Installed Qt/QML, GTK4, Quickshell, and Node runtimes were observed.
Their availability does not select the implementation stack.

The installed clipboard capture script skips copies when
`CLIPBOARD_STATE=sensitive` or `x-kde-passwordManagerHint` is advertised. This is
evidence of a supported exclusion path on this snapshot, not a universal
guarantee. Recheck behavior at implementation/test time.

## 7. Vault lifecycle and security

### S-01: Threat model

Defend against offline vault theft, accidental disclosure through clipboard
history/diagnostics/UI, stale unlocked sessions, unsafe storage writes, and
unauthorized control requests.

Do not claim protection against root, a compromised compositor, or malware
running as the user's account. Other same-user programs may be able to observe
clipboard contents or memory. A local same-UID transport alone does not prove
that a browser/request is trusted.

Use established KDBX encryption/KDF implementations. No custom cryptography.
Document actual memory-handling guarantees, not a promise of perfect zeroization.

### S-02: Lifecycle state model

The implementation must distinguish at least:

| State | Required behavior |
| --- | --- |
| Not running | Control invocation may start the app; it starts locked |
| No configured vault | Offer create/open; never assume a user's vault path |
| Locked | No searchable decrypted index, secrets, drafts, or active reveals |
| Unlocking | One prompt/task; explicit error/cancellation; no duplicate requests |
| Unlocked | Launcher and management can operate on current vault state |
| Saving/reconciling | Mutations serialize; affected data cannot report premature success |
| Conflict | Competing data retained; ambiguous save blocked pending resolution |
| Locking | Invalidate UI/operations, clear owned clipboard, release plaintext state |
| Quitting | Prevent new requests; clear secrets and terminate the resident owner |

These need not be one flat enum; orthogonal session/storage states are fine.
The observable behavior matters more than the representation.

### S-03: Lock triggers and defaults

Lock on desktop/session lock, suspend/hibernate, logout/shutdown, explicit app
lock, and app exit. **No inactivity timeout by default.** Provide a configurable
optional inactivity timeout with documented activity semantics.

Closing the manager, dismissing the launcher, or being idle does not lock under
default settings. Resuming/unlocking the desktop does not automatically decrypt
the vault. App restart always starts locked.

Use suitable desktop/session signals and verify the actual reference environment.
Do not assume that every Hyprland lock pathway emits the same signal. If a hook
is required, install it only with consent and make missing coverage visible.
For suspend, plaintext must be dropped before sleep rather than only after
resume. Shutdown callbacks alone must not be the sole secret-lifetime defense.

Lock must invalidate in-flight search, copy, reveal, unlock, and generation
callbacks so that a late callback cannot repopulate secrets. Reconciliation may
not continue holding decrypted state across a lock merely to finish a save.
Storage consistency must survive interruption at each stage.

### S-04: Memory, filesystem, and diagnostics

- Never persist a plaintext master password, decrypted index, secret draft,
  generator output, or merge-conflict document.
- Encrypted vault snapshots/backups are permitted; define their retention and
  protection rather than letting them grow without bound.
- Minimize copies/lifetimes of secrets across UI, engine, IPC, subprocesses, and
  clipboard buffers; clear/release on lock and quit.
- Prevent core dumps and secret-bearing crash diagnostics where feasible, and
  document remaining OS/swap/runtime limitations.
- Do not place credentials in command-line arguments, shell interpolation,
  environment variables, notifications, telemetry, logs, or error reports.
- No telemetry/cloud dependency in v1.
- Restrict user-local control endpoints and sensitive local files appropriately.
- Treat vault/XML/attachment/custom-data input as untrusted; parsing failures
  must not damage the original file.
- Secret-adjacent diagnostics must use safe identifiers/error categories rather
  than dump vault contents, search queries, or arbitrary parser input.

Fingerprint unlock is future work. Fingerprint authentication cannot by itself
derive an existing KDBX key; any later convenience-unlock design must specify
secure key custody, enrollment, revocation, fallback, and lock semantics.

## 8. Clipboard contract

### P-01: Sensitive copy

Advertise a recognized password-manager sensitive hint and use the supported
Wayland/Omarchy exclusion path. Apply safe treatment to credential copy actions,
not only the default Enter action. No secret-bearing success notification.

Sensitive signaling is a convention, not a Wayland access-control boundary.
Validate end-to-end with the running history backend using synthetic credentials.
Do not infer successful history exclusion solely from an available CLI option.
If supported exclusion cannot be established, provide a visible, nonsecret
warning rather than refuse all copying. Strict refusal is not the v1 default.

### P-02: Timed/owned cleanup

Default clipboard lifetime: **20 seconds**, configurable.

Clear on expiry, vault lock, and quit only if Keylane still owns the selection
associated with that copy. A subsequent unrelated copy must never be cleared.
Cancel/invalidate old timers when ownership changes or a new credential is
copied. Prefer ownership/token-based tracking over polling plaintext clipboard
contents merely to compare strings.

Selection loss, compositor disconnect, failed clipboard ownership, and a copy
attempt racing with lock must not produce false success.
Do not change the primary selection by default or rely on "paste once" behavior
as the sole security mechanism.

## 9. KDBX fidelity and persistence

### D-01: Compatibility and supported formats

Support contemporary KeePass-compatible KDBX3 and KDBX4 read/edit/write behavior
needed by the reference use case. State the exact supported revisions, ciphers,
KDFs, and key-file variants in implementation documentation and tests.
Unsupported revisions/factors/features must fail explicitly or be offered in
clearly read-only mode if they can be read safely.

Preserve an existing vault's format, cipher/KDF settings, protection metadata,
entry/group IDs, history, custom fields/data, attachment binaries/references,
TOTP and other advanced metadata, and relevant timestamps/relationships unless
the operation deliberately changes them. Unknown-but-preservable fields must
remain intact. Do not silently rewrite to a newer format or strip unrecognized
extensions.

New-vault defaults should use the selected mature engine's secure contemporary
configuration, documented and covered by interoperability tests. Algorithm/KDF
tuning is an implementation decision, not permission to weaken existing vaults.

Test semantic decrypted round-trip fidelity rather than expecting identical
encrypted bytes: encryption salts/IVs and legitimate edit metadata may change.

### D-02: Safe saves

- Serialize writes through the vault owner; one authoritative in-memory model.
- Save each committed mutation immediately.
- Use atomic replacement where the filesystem permits, with appropriate file
  and directory durability handling.
- Maintain bounded encrypted recoverable backups/snapshots.
- Detect read-only paths, disk-full, permissions, incomplete writes, corrupt
  external files, missing files, and unavailable sync directories explicitly.
- Do not destroy the last valid vault when a write or validation step fails.
- Coordinate Keylane's own instances; detect conflicting local ownership and
  warn about concurrent use by other clients where possible.
- Do not assume a sync client honors application locks or that atomic local
  replacement makes distributed edits conflict-free.

On credential changes, backups may retain the old encryption credentials.
Explain this and provide an intentional backup/recovery policy; do not silently
erase recovery data or imply all old snapshots now use the new key.

## 10. OneDrive reconciliation

### R-01: Scope and inputs

The owner uses an external OneDrive client to synchronize the KDBX file.
Keylane manages the local vault file; it does not authenticate to OneDrive or
claim knowledge of remote upload/completion.

Watch/recheck the configured path for external changes, including atomic
replacement and transient incomplete writes. Read stable snapshots before
parsing; retry bounded transient changes without treating corrupted input as
an empty vault.

Also provide an explicit **Merge another KDBX file** workflow for sync-created
conflict copies. Do not scan OneDrive folders automatically or associate files
by a guessed filename pattern. The imported file may need its own credentials;
never assume it shares the active vault key.

### R-02: Baselines and safe automatic reconciliation

Track a trustworthy common baseline where one is available, using stable entry
and group IDs and the relevant KDBX deletion/history metadata. Implementers must
document how the baseline survives needed restarts without writing plaintext.

Automatically reconcile changes proven independent of one another, including
compatible distinct-entry additions/edits. Field-level automatic merging is
allowed only where independence can actually be established.

Timestamp order alone is not sufficient proof of which edit should win.
Clocks may differ. If ancestry is unavailable or ambiguous, preserve alternatives
and ask for resolution rather than inventing a safe baseline.

### R-03: Ambiguous cases

Surface competing edits, edit-versus-delete, incompatible group moves/deletions,
and conflicts involving custom data/history/binaries without silent loss.
Do not resurrect a deliberate deletion merely because another copy still has
the entry, or propagate it over a competing edit without resolution.

The conflict UI must:

- Explain the affected entry/group and conflicting fields.
- Retain both versions and allow selection/combination or preservation of both.
- Keep passwords/protected fields masked until explicit reveal.
- Retain the losing secret/history in encrypted recovery where necessary;
  destructive final disposal must be intentional.
- Preserve uncommitted local edits while unlocked.
- Block overwriting ambiguous data until the user resolves it.

An unresolved conflict must not falsely mark a pending edit/create as saved.
Unaffected retrieval may continue from the last committed, unambiguous state;
ambiguous entries must be visibly marked and require deliberate version choice
rather than silently copying a supposedly authoritative password.

### R-04: Write races and recovery

Recheck external version identity immediately before committing a reconciled
save, and handle another arriving change by retrying/reconciling or surfacing
the conflict. Preserve recoverable encrypted inputs before replacement.
Avoid unbounded retry loops while synchronization is active.

There is no distributed transaction with the external sync client. Document
this limitation and test rapid replacement/race recovery rather than promising
that arbitrary remote writes can never race a local write.

While locked, observe that an external change occurred without decrypting or
automatically unlocking. Unlock/load must validate the latest available state.
For incompatible credentials, corruption, or an unsupported externally changed
format, stop with a clear recovery path rather than overwrite the changed file.

## 11. Architecture constraints, not a prescribed stack

### A-01: Resident owner

Use one long-lived per-user application as the authority for the vault,
unlocked search index, commits, reconciliation, clipboard, and lock lifecycle.
No root/system daemon is required.

The launcher and manager must not each open independently writable vault
copies. An app-internal engine/service boundary should allow additional UI and
future integration adapters without bypassing lock or persistence policy.
This does not mandate separate OS processes or a public general-purpose API.

Provide small CLI control entry points to summon search/create/manager and
request lock/status/quit. Exact command spelling belongs to implementation.
CLI/control IPC must not become an unauthenticated arbitrary secret-export API.
Constrain requests, permissions, message size, and lifecycle/rate behavior.

Do not reopen/decrypt the vault for each keystroke. Do not start extra vault
owners when the CLI is invoked repeatedly.

### A-02: Future multiple vaults

V1 has one active/configured vault. Internally identify a vault separately from
its entries and keep destination/ownership boundaries explicit, so future
multiple-vault support does not require discarding the persistence/security
model. Do not build multi-vault UI or sync settings now.

### A-03: Future browser integration

Prefer eventual compatibility with the unmodified KeePassXC-Browser extension.
Preserve a path for a native-messaging adapter/proxy that talks to the resident
owner and obeys its unlock, per-entry authorization, lookup, generation, and
save policies. Do not add v1 browser-facing sockets/actions merely to demonstrate
extensibility.

Research indicates that an alternative application can register a native host
and implement the server-side protocol. This is not yet a tested compatibility
promise. It entails framing, cryptographic pairing, protocol/version behavior,
URL matching, explicit consent, and browser-origin/extension allow-list handling.

The stock extension expects the KeePassXC native-host name. Registration can
compete with an existing KeePassXC installation for a browser/profile. A future
setup must detect this and make takeover/restoration explicit; it must not
silently overwrite another application's manifest.

Implementing a protocol client that talks to an installed KeePassXC would not
meet the standalone requirement. A future new extension is acceptable only if
upstream-extension compatibility proves impractical and the product decision is
revisited.

### A-04: Technology and licensing

The owner explicitly prioritizes UX and behavior over framework/language/library
preferences. No requirement mandates Qt/QML, Rust, GTK, Electron, KeeWeb,
Quickshell, or a KeePassXC fork.

Mature format/cryptography/storage reuse is preferred over new primitives.
GPL-compatible reuse is acceptable, but not mandatory; comply with actual
upstream/dependency licenses and attribution. Record the chosen license and
dependencies before public distribution.

Research options, not selections:

| Option | Relevant evidence/risk |
| --- | --- |
| KeePassXC format/core code | Established KDBX implementation and reconciliation behavior; GPL obligations; not packaged as an independent supported engine library |
| kdbxweb | KeeWeb-used TypeScript/JavaScript KDBX3/4 implementation; Argon2 integration needs supplying; fidelity/merge behavior must be verified |
| keepass-rs | Rust option whose researched README labels write support experimental; must not assume safe lossless editing from available data types |

Do not turn any of these observations into an unverified guarantee of safe
write-back, native overlay behavior, or performance.

## 12. Configuration and setup

| Setting | Required initial behavior |
| --- | --- |
| Active vault | Explicitly selected; one active file in v1 |
| Key file | Optional explicit selection; never ingest secrets from guessed paths |
| Destination group | Configurable and visible when creating |
| Shortcut | Personal preference Super+Alt+K; configurable; conflict-aware setup |
| Lock on desktop lock/sleep/exit | Enabled |
| Inactivity timeout | Disabled by default; optional configurable timeout |
| Clipboard lifetime | 20 seconds; configurable |
| Clipboard history uncertainty | Warn; do not default to refusing copy |
| Password length | 24; configurable |
| Generator character/passphrase defaults | Secure implementation-selected documented defaults |
| Autostart | Opt-in setup; starts locked |
| Theme/font | Follow supported Omarchy sources; standalone fallback |
| Backup retention | Bounded, configurable, documented secure default chosen by implementers |
| Diagnostic verbosity | Nonsecret diagnostics; no telemetry |

Validate settings and explain invalid values. Do not silently ignore security
configuration. Avoid adding unrelated tunables merely because a framework makes
them easy.

Ship an Arch-focused installable package, desktop entry, tray assets, and
clear install/uninstall instructions. User configuration must not be overwritten
by package upgrades.

Setup must separately request permission for autostart, binding replacement,
window/lock integration, and any required clipboard integration. Back up edited
user configuration and preserve unrelated changes. Never edit packaged
`/usr/share/omarchy/` files. Revalidate Hyprland config after approved changes
using the supported reload/error checks. Uninstall removes only Keylane-owned
integration; it must not blindly restore stale configurations over user edits.

## 13. Performance and acceptance

### Performance contract

On the reference machine, with the resident app and vault already unlocked,
using a synthetic **10,000-entry vault**:

- Shortcut to visible, keyboard-focused search: **at most 150 ms**.
- Query input to visibly updated results: **at most 50 ms**.

Measure end-to-end visible behavior, not only fuzzy-algorithm execution.
Document hardware/session, fixture composition, event/timing method, repeated
measurements and tail behavior. Cold startup and KDBX unlock are separate
measurements because expensive key derivation is intentional. Do not weaken
KDF parameters to reach launcher targets.

### Acceptance matrix

All tests use synthetic data initially. No personal vault fixture, copied
credential, or sensitive machine configuration may enter the public repository.

| ID | Required acceptance evidence |
| --- | --- |
| T-01 | KDBX3/4 and documented key-file/KDF/cipher fixtures open, edit, save, and reopen in an independent reference client |
| T-02 | No-op and unrelated-field edits preserve IDs, groups, history, attachments, TOTP/custom data, protected fields, and unknown supported metadata semantically |
| T-03 | Create/edit/move/recycle/restore/permanent-delete/history-restore behavior matches the scope; cancellations do not mutate storage |
| T-04 | Generator uses secure randomness, respects valid constraints/defaults, and visibly rejects invalid combinations |
| T-05 | Save & copy never succeeds before durable local save; injected disk-full/permissions/interruption errors preserve valid vault/recoverable draft state |
| T-06 | Independent sync edits reconcile; same-field competing edits, delete/edit, group-move/delete, missing ancestry, binaries/custom data, and clock skew preserve alternatives |
| T-07 | Atomic external replacements, incomplete/corrupt remote writes, changes during save, and restart during reconciliation do not silently discard data |
| T-08 | Explicit merge of another KDBX handles separate credentials, wrong key, unsupported format, cancellation, and conflicts without modifying the input file |
| T-09 | Search includes only agreed fields; username/notes/custom-field-only queries do not match; duplicate titles disambiguate; trash is excluded |
| T-10 | Launcher shortcuts, no-match behavior, creation/edit transitions, Escape, repeated invocation, deleted/stale entry races, and missing values behave explicitly |
| T-11 | Unlocked warm 10,000-entry measurements meet the 150/50 ms targets |
| T-12 | Launcher input focus, active-monitor placement, no tiling/reflow, scaling, previous-window destruction, and focus restoration work in the reference session |
| T-13 | Tray actions, primary activation, menu behavior, manager close, explicit quit, and opt-in autostart starting locked work |
| T-14 | Desktop-lock and all supported sleep paths drop plaintext before sleep; unlock does not automatically reopen vault; late async results cannot revive secrets |
| T-15 | Default idle does not lock; configured inactivity timeout does; lock discards dirty drafts as documented |
| T-16 | Synthetic credential copy is excluded from the active Omarchy history backend; unsupported exclusion warns; timers/lock/quit never clear a later unrelated copy |
| T-17 | Clipboard failures, compositor disconnect, rapid copies, lock/copy races, and ownership loss do not report false success |
| T-18 | Logs, notifications, control responses, process arguments, crash artifacts, and persistent state contain no synthetic secrets/plaintext indices/drafts |
| T-19 | Unapproved/control requests, oversized/malformed IPC, duplicate instances, and requests during lock/quit are handled safely |
| T-20 | Light/dark themes, live theme/font changes, fallback styling, keyboard accessibility, and all empty/error/conflict states pass a visual/interaction review |
| T-21 | Fresh installation/upgrade/uninstall preserve user configuration; binding conflicts require consent; no packaged Omarchy files are modified |
| T-22 | Backup bounds, restore/recovery, original credentials after credential changes, and missing/unavailable vault paths are documented and tested |

Use appropriate unit, integration, fault-injection, UI, and manual desktop tests.
Automate what can be reliable; manual visual/focus validation remains necessary.
Run the chosen project's standard build/type checks, formatters, and linters.
Record test evidence and unresolved failures; a working happy path is not release
acceptance.

Independent reference-client use is a development/test requirement, not a
KeePassXC runtime dependency for users.

## 14. Implementation handoff

The following is a suggested risk-first milestone order, **not a mandated
implementation path**. Agents may reorder or combine work if all requirements
and acceptance evidence remain covered.

1. **Obtain implementation approval.** Confirm permitted system changes and
   test-vault access; recheck current desktop facts. Select and document stack,
   engine, license, and supported KDBX capabilities.
2. **Retire foundational risks.** Demonstrate synthetic lossless round trips,
   save/recovery behavior, reconciliation/conflict handling, and native
   launcher/focus feasibility before relying on them in a finished product.
3. **Deliver the retrieval slice.** Resident owner, lifecycle, unlock, indexed
   search, shortcut/control surface, sensitive clipboard and performance.
4. **Deliver daily replacement workflows.** Compact creation/generation,
   management editor/groups/trash/history/settings, tray, and complete sync UI.
5. **Polish and distribute.** Omarchy-native visual/interaction acceptance,
   consent-based setup, packaging, fault/security validation, and documentation.
6. **Future browser milestone.** Separately test the actual unmodified extension,
   native-host registration, pairing/authorization, matching, and save behavior.

Foundational verification is an engineering obligation, not a demand for a
specific prototype/fork strategy or repeated permission for ordinary technical
choices. If an approach cannot meet the contract, pursue a viable alternative.
If meeting the contract itself would require a product/security/scope change,
report the blocker and obtain the owner's decision rather than quietly
downgrading requirements.

### Explicitly delegated engineering decisions

- Framework/language, process boundaries, and native-overlay mechanism.
- Engine/version/reuse strategy, dependency management, and final license.
- Safe new-vault KDF/cipher defaults within mature supported implementations.
- Exact fuzzy algorithm, component design, typography/spacing details.
- CLI spelling, local control transport, and ownership/version tracking.
- File watcher/reconciliation algorithm and encrypted baseline/recovery layout.
- Backup bounds/defaults, passphrase word list, and validated configuration limits.
- Package recipe/build tooling and CI/test implementation.

These decisions must be documented by the implementation team and justified
against this requirements contract. They are not unresolved requests for the
owner to design the code.

## 15. Risks, caveats, and evidence

| Risk | Required response |
| --- | --- |
| "Native" reduced to colors | Review against actual Omarchy menu interactions, focus, scaling and theme transitions |
| Engine parses but loses data on save | Gate writes with semantic fixtures, independent reopening, and unsupported-feature handling |
| Sync merge guessed from timestamps | Establish ancestry where possible; retain alternatives where not |
| Sync writes race local persistence | Detect/reconcile, retain encrypted recovery inputs, and state distributed limitations |
| Clipboard convention mistaken for isolation | Verify active history exclusion; warn on uncertainty; document same-user readers |
| Screen-lock signals incomplete | Test actual lock/sleep pathways; install missing hooks only with consent |
| Future browser promise premature | Treat server-side compatibility as a research-backed possibility until actual extension tests pass |
| GPL/library assumptions wrong | Verify exact reused-file and dependency licenses before adopting/distributing |
| Latency "measured" inside only the backend | Measure shortcut-to-focused UI and input-to-visible results |
| Public project leaks personal state | Use synthetic fixtures, safe diagnostics, and review publication contents |

### Source references

The conversation's research inspected upstream code/docs and the installed
reference desktop read-only. It did not build engines, run format round-trip
tests, or pair an actual browser extension. Treat feasibility findings as inputs,
not acceptance evidence.

- KeeWeb and kdbxweb:
  - https://github.com/keeweb/keeweb
  - https://github.com/keeweb/kdbxweb
  - https://github.com/keeweb/kdbxweb/blob/master/README.md
- Alternative Rust implementation:
  - https://github.com/sseemayer/keepass-rs
- KeePassXC format/core and licensing:
  - https://github.com/keepassxreboot/keepassxc
  - https://github.com/keepassxreboot/keepassxc/blob/2.7.12/COPYING
  - https://github.com/keepassxreboot/keepassxc/tree/2.7.12/src/format
  - https://github.com/keepassxreboot/keepassxc/tree/2.7.12/src/core
- Browser host/proxy/server:
  - https://github.com/keepassxreboot/keepassxc-browser
  - https://github.com/keepassxreboot/keepassxc/blob/2.7.12/src/browser/NativeMessageInstaller.cpp
  - https://github.com/keepassxreboot/keepassxc/blob/2.7.12/src/browser/BrowserHost.cpp
  - https://github.com/keepassxreboot/keepassxc/blob/2.7.12/src/browser/BrowserAction.cpp
  - https://github.com/keepassxreboot/keepassxc/blob/2.7.12/src/browser/BrowserMessageBuilder.cpp
  - https://github.com/keepassxreboot/keepassxc/blob/2.7.12/src/proxy/NativeMessagingProxy.cpp
- Clipboard conventions/tooling:
  - https://github.com/bugaevc/wl-clipboard
  - https://man.archlinux.org/man/wl-copy.1.en
  - https://man.archlinux.org/man/wl-paste.1.en
  - https://github.com/sentriz/cliphist
- Desktop context:
  - https://github.com/basecamp/omarchy
  - https://github.com/hyprwm/Hyprland
  - https://github.com/hyprwm/hypridle
  - https://github.com/hyprwm/hyprlock
  - https://quickshell.org/

Relevant local reference paths, to recheck rather than edit blindly:

- `/usr/share/omarchy/shell/plugins/bar/widgets/Tray.qml`
- `/usr/share/omarchy/shell/plugins/clipboard/Clipboard.qml`
- `/usr/share/omarchy/shell/plugins/clipboard/capture.sh`
- `/usr/share/omarchy/default/themed/shell.toml.tpl`
- `~/.local/state/omarchy/current/theme/`
- `~/.config/hypr/bindings.lua`

Do not publish a real vault, key file, browser pairing key, clipboard store, or
personal configuration dump along with these references.
