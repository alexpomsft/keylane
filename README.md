# Keylane

A keyboard-first password manager for Linux/Wayland that reads and writes
standard **KeePass KDBX** files. Built for Hyprland/Omarchy: one shortcut opens
a fast overlay, you type a few letters, press Enter, and the password is on the
clipboard and out of clipboard history.

- **Launcher overlay**: a layer-shell card on the active monitor that never
  reflows your tiles. Fuzzy search over titles, groups, URLs and tags only.
  On 10,000 entries it opens in about 8 ms and updates results within about
  14 ms (p50; see [docs/PERFORMANCE.md](docs/PERFORMANCE.md)).
- **Create without leaving the keyboard**: Ctrl+N in the launcher, then Tab
  through the fields. The password is pre-generated. Enter saves and copies;
  Ctrl+E hands the draft over to the manager.
- **Manager window**: groups, entries, custom fields, attachments, history,
  trash, generator, settings and conflict resolution.
- **Your files stay KeePass files**: lossless KDBX 3.1/4.x editing, checked
  against KeePassXC ([docs/FORMATS.md](docs/FORMATS.md)).
- **Safe with sync clients like OneDrive**:
  - writes are atomic;
  - edits are merged against a three-way baseline;
  - competing edits are kept, never silently dropped ([docs/SYNC.md](docs/SYNC.md)).
- **Locks when it should**:
  - on desktop lock, suspend and shutdown, with plaintext dropped *before* sleep;
  - no inactivity timeout unless you configure one
    ([docs/SECURITY.md](docs/SECURITY.md)).
- **Looks native**: uses Omarchy's live palette and font, follows theme
  changes instantly, and has light/dark fallbacks elsewhere.

## Quick start

```sh
python -m venv --system-site-packages .venv      # PySide6 ≥ 6.6 from the system
.venv/bin/pip install -e '.[test]'
scripts/fetch-layershell.sh                       # only if layer-shell-qt isn't installed
.venv/bin/keylane                                 # opens the launcher; first run offers create/open
```

Bind it to a key yourself (Keylane never edits your compositor config):

```lua
-- Omarchy, ~/.config/hypr/bindings.lua (pick a free chord, or unbind the old one first)
o.bind("SUPER + ALT + K", "Keylane", "keylane")
```

For classic `hyprland.conf` syntax and other setups, see
[docs/INSTALL.md](docs/INSTALL.md).

## Launcher keys

| Key | Action |
|---|---|
| type | search |
| ↑/↓, Tab/Shift+Tab, Ctrl+J/K, PgUp/PgDn | move |
| Enter | copy password |
| Ctrl+Enter | copy username |
| Ctrl+U | copy URL |
| Ctrl+N | create (title prefilled from the query) |
| Ctrl+E | edit in the manager |
| Ctrl+L | lock |
| Esc | clear the query, then close |

In the create form: Tab moves through the fields, Enter saves and copies the
password, Ctrl+S saves only, Ctrl+E opens the draft in the manager, and Esc
returns to search.

The manager shows its shortcuts in tooltips and button hints. The main ones:

| Key | Action |
|---|---|
| Ctrl+N | new entry |
| Ctrl+F or `/` | search |
| Ctrl+G | groups |
| Ctrl+S | save |
| Ctrl+H | reveal |
| Ctrl+R | generate |
| Ctrl+Shift+R | generator options |
| Ctrl+Shift+F | add field |
| Ctrl+Shift+H | history |
| Ctrl+C / Ctrl+B / Ctrl+U | copy password / username / URL |
| Delete | move to trash |
| Ctrl+L | lock |
| Ctrl+, | settings |
| Ctrl+W | close |
| Ctrl+Q | quit |

## Command line

```
keylane [search [QUERY]] | new [TITLE] | manager | settings | lock | status | quit | daemon | version
```

The first command starts the resident process, which always starts locked.
Later commands reach it over a same-user socket in a few milliseconds. The
CLI never accepts or prints secrets.

## Configuration

`~/.config/keylane/config.toml` is written by the Settings page. Invalid values
fall back to defaults with a visible warning.

```toml
[vault]
path = "~/OneDrive/keys.kdbx"
keyfile = ""                        # optional
[security]
lock_on_desktop_lock = true
lock_on_sleep = true
inactivity_timeout_minutes = 0      # 0 = off (default); 1–1440
clipboard_seconds = 20              # 5–600
[backups]
keep = 20                           # 1–500 encrypted snapshots
min_interval_minutes = 5
[appearance]
theme = "auto"                      # auto | omarchy | dark | light
[diagnostics]
log_level = "info"                  # info | debug (never logs secrets)
```

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | processes, modules, stack decisions |
| [SECURITY](docs/SECURITY.md) | threat model, lock semantics, clipboard, memory limits |
| [FORMATS](docs/FORMATS.md) | supported KDBX revisions, ciphers, KDFs, key files |
| [SYNC](docs/SYNC.md) | reconciliation, baseline, backups, recovery |
| [PERFORMANCE](docs/PERFORMANCE.md) | method and results of the 10k-entry benchmark |
| [GENERATOR](docs/GENERATOR.md) | password and passphrase defaults, EFF word list |
| [INSTALL](docs/INSTALL.md) | dependencies, packaging, key binding, uninstall |

## Development

```sh
.venv/bin/python -m pytest                 # unit, interop (keepassxc-cli) and property tests
.venv/bin/python scripts/bench.py          # search engine benchmark (headless)
.venv/bin/python scripts/bench.py --desktop  # end-to-end launcher latency (needs wtype + Hyprland)
KEYLANE_QML_DEV=1 keylane daemon           # reload QML from the source tree
```

## License

GPL-3.0-or-later. The EFF large word list is © Electronic Frontier Foundation,
used under CC BY 3.0 (see [docs/GENERATOR.md](docs/GENERATOR.md)).
