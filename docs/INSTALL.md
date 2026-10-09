# Installing Keylane

## Requirements

| Dependency | Why | Arch package |
|---|---|---|
| Python ≥ 3.12 | runtime | `python` |
| PySide6 ≥ 6.6 (Qt 6, QML) | UI | `pyside6` |
| pykeepass 4.x (with lxml, argon2-cffi) | KDBX container and crypto | `python-pykeepass` |
| rapidfuzz ≥ 3 | typo-tolerant search tier | `python-rapidfuzz` |
| layer-shell-qt 6 | the overlay launcher (`wlr-layer-shell`) | `layer-shell-qt` |
| wl-clipboard | clipboard copy and clear (`wl-copy`) | `wl-clipboard` |
| systemd (logind, `systemd-inhibit`) | lock before sleep, on session lock and on shutdown | `systemd` |

Optional: `keepassxc` (`keepassxc-cli` runs the interop tests) and `wtype`
(the desktop latency benchmark).

Without layer-shell-qt, Keylane falls back to an ordinary window for the
launcher. Everything still works, but the window is managed by the compositor
(tiling, focus rules), so the overlay experience is worse.

## Arch Linux / Omarchy (package)

```sh
sudo pacman -S --needed pyside6 python-pykeepass python-rapidfuzz layer-shell-qt wl-clipboard
cd packaging && makepkg -si
```

The package installs:

- the `keylane` command;
- a desktop entry (Keylane, with Quick Search and Lock actions);
- the icon;
- the documentation in `/usr/share/doc/keylane`.

## From source (any distribution)

```sh
python -m venv --system-site-packages .venv   # reuse the distro's PySide6/Qt
.venv/bin/pip install -e '.[test]'
.venv/bin/keylane
```

Use the distro's PySide6 when possible, so that its Qt matches the system
layer-shell-qt. If your distribution has no layer-shell-qt package,
`scripts/fetch-layershell.sh` downloads the Arch build into
`vendor/layershellqt/`. That build only works with the same Qt minor version.
You can also point `KEYLANE_LAYERSHELL_DIR` at any install prefix's `lib`
directory, the one containing `libLayerShellQtInterface.so.6` and
`qt6/qml/org/kde/layershell`.

## Key binding

Keylane never edits your compositor configuration. Add a binding yourself.
`keylane` with no arguments opens quick search, and starts the resident
process if it isn't running.

**Omarchy** (`~/.config/hypr/bindings.lua`). Pick a free chord, or unbind the
old one first:

```lua
-- hl.unbind("SUPER + ALT + K")   -- only if something else already uses it
o.bind("SUPER + ALT + K", "Keylane", "keylane")
```

**Classic `hyprland.conf`:**

```ini
bind = SUPER ALT, K, exec, keylane
```

**Other compositors (sway, river, …):** bind any key to `keylane`.

Other useful commands to bind:

| Command | Opens |
|---|---|
| `keylane new` | the create form |
| `keylane manager` | the manager window |
| `keylane lock` | locks the vault |

### Optional: no compositor animation

On Hyprland, layer surfaces may get a fade or slide animation. Keylane's
surfaces use the namespace `keylane`. For a truly instant overlay, use the same
rule Omarchy uses for its own panels:

```lua
hl.layer_rule({ match = { namespace = "keylane" }, no_anim = true, animation = "none" })
```

## Starting at login (optional)

There is no autostart by default. The first `keylane` invocation starts the
resident process, which costs a one-time Qt/QML start-up delay. To pay that
cost at login instead, add `keylane daemon` to your autostart:
`o.launch_on_start("keylane daemon")` in Omarchy's `autostart.lua`, or
`exec-once = keylane daemon` in `hyprland.conf`. The vault
stays locked until you unlock it.

## Files

| Path | Contents |
|---|---|
| `~/.config/keylane/config.toml` | settings (vault path, key file path, preferences; never secrets) |
| `~/.local/state/keylane/vaults/<id>/` | encrypted sync baseline and encrypted backups (usage ranking is memory-only and cleared on lock) |
| `$XDG_RUNTIME_DIR/keylane/` | control socket (`0700`) |

## Environment variables

| Variable | Effect |
|---|---|
| `KEYLANE_LAYERSHELL_DIR` | extra layer-shell-qt location, searched first |
| `KEYLANE_RUNTIME_DIR` | overrides the socket directory (tests, isolated instances) |
| `KEYLANE_TRACE=1` | logs timing for frames, searches and GC pauses (no secrets) |
| `KEYLANE_QML_DEV=1` | loads QML from the source tree (development) |

## Uninstall

```sh
keylane quit                       # locks and exits the resident process
sudo pacman -R keylane             # or: pip uninstall keylane
```

Your vault is **never** inside Keylane's directories, so uninstalling cannot
touch it. To remove Keylane's own data as well:

```sh
rm -r ~/.config/keylane ~/.local/state/keylane
```

This deletes the backups and the sync baseline. Keep the backups if you might
need them.
