"""User configuration (~/.config/keylane/config.toml) and state paths.

Configuration never contains secrets. Invalid values are reported with an
explanation and replaced by the documented default for that key only.
"""

from __future__ import annotations

import os
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import errors, storage
from .generator import PasswordSpec, validate as validate_spec
from .paths import runtime_dir  # noqa: F401  (re-exported)


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "keylane"


def state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "keylane"


@dataclass
class Settings:
    # [vault]
    vault_id: str = ""
    vault_path: str = ""
    keyfile_path: str = ""
    default_group: str = ""  # entry-group UUID hex; empty = root
    # [security]
    lock_on_desktop_lock: bool = True
    lock_on_sleep: bool = True
    inactivity_timeout_minutes: int = 0  # 0 = disabled (default)
    clipboard_seconds: int = 20
    # [backups]
    backup_keep: int = 20
    backup_min_interval_minutes: int = 5
    # [appearance]
    theme: str = "auto"  # auto | omarchy | dark | light
    omarchy_theme_dir: str = ""
    # [diagnostics]
    log_level: str = "info"  # info | debug (debug is still non-secret)
    # [generator]
    generator: PasswordSpec = field(default_factory=PasswordSpec)

    warnings: list[str] = field(default_factory=list, compare=False)

    @property
    def vault(self) -> Path | None:
        return Path(self.vault_path).expanduser() if self.vault_path else None

    @property
    def keyfile(self) -> Path | None:
        return Path(self.keyfile_path).expanduser() if self.keyfile_path else None

    def vault_state_dir(self) -> Path:
        return state_home() / "vaults" / (self.vault_id or "unconfigured")


_SCHEMA = {
    "vault": {"id": ("vault_id", str), "path": ("vault_path", str), "keyfile": ("keyfile_path", str), "default_group": ("default_group", str)},
    "security": {
        "lock_on_desktop_lock": ("lock_on_desktop_lock", bool),
        "lock_on_sleep": ("lock_on_sleep", bool),
        "inactivity_timeout_minutes": ("inactivity_timeout_minutes", int),
        "clipboard_seconds": ("clipboard_seconds", int),
    },
    "backups": {"keep": ("backup_keep", int), "min_interval_minutes": ("backup_min_interval_minutes", int)},
    "appearance": {"theme": ("theme", str), "omarchy_theme_dir": ("omarchy_theme_dir", str)},
    "diagnostics": {"log_level": ("log_level", str)},
}


def _validate(s: Settings) -> list[str]:
    w = []
    d = Settings()
    if not (0 <= s.inactivity_timeout_minutes <= 24 * 60):
        w.append("security.inactivity_timeout_minutes must be 0 (off) to 1440; using 0 (off).")
        s.inactivity_timeout_minutes = d.inactivity_timeout_minutes
    if not (5 <= s.clipboard_seconds <= 600):
        w.append("security.clipboard_seconds must be between 5 and 600; using 20.")
        s.clipboard_seconds = d.clipboard_seconds
    if not (1 <= s.backup_keep <= 500):
        w.append("backups.keep must be between 1 and 500; using 20.")
        s.backup_keep = d.backup_keep
    if not (0 <= s.backup_min_interval_minutes <= 24 * 60):
        w.append("backups.min_interval_minutes must be between 0 and 1440; using 5.")
        s.backup_min_interval_minutes = d.backup_min_interval_minutes
    if s.theme not in ("auto", "omarchy", "dark", "light"):
        w.append("appearance.theme must be auto, omarchy, dark or light; using auto.")
        s.theme = "auto"
    if s.log_level not in ("info", "debug"):
        w.append("diagnostics.log_level must be info or debug; using info.")
        s.log_level = "info"
    if s.default_group and not all(c in "0123456789abcdef" for c in s.default_group):
        w.append("vault.default_group is not a group id; using the root group.")
        s.default_group = ""
    if s.vault_id and not all(c in "0123456789abcdef-" for c in s.vault_id):
        w.append("vault.id is invalid; a new id was assigned.")
        s.vault_id = uuid.uuid4().hex
    try:
        validate_spec(s.generator)
    except errors.InvalidInput as exc:
        w.append(f"generator: {exc.user_message} Using defaults.")
        s.generator = PasswordSpec()
    return w


def load(path: Path | None = None) -> Settings:
    path = path or config_home() / "config.toml"
    s = Settings()
    if not path.exists():
        return s
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        s.warnings.append(f"config.toml could not be read ({type(exc).__name__}); using defaults.")
        return s
    for section, keys in _SCHEMA.items():
        sec = raw.get(section, {})
        if not isinstance(sec, dict):
            s.warnings.append(f"[{section}] must be a table; ignored.")
            continue
        for key, (attr, typ) in keys.items():
            if key not in sec:
                continue
            val = sec[key]
            if typ is int and isinstance(val, bool) or not isinstance(val, typ):
                s.warnings.append(f"{section}.{key} has the wrong type; using the default.")
                continue
            setattr(s, attr, val)
        unknown = set(sec) - set(keys)
        for k in sorted(unknown):
            s.warnings.append(f"Unknown setting {section}.{k} ignored.")
    gen = raw.get("generator", {})
    if isinstance(gen, dict):
        s.generator = PasswordSpec.from_dict(gen)
    s.warnings.extend(_validate(s))
    return s


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    out = ['"']
    for ch in s:
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def dumps(s: Settings) -> str:
    lines = ["# Keylane configuration. Contains no secrets.", ""]
    for section, keys in _SCHEMA.items():
        lines.append(f"[{section}]")
        for key, (attr, _t) in keys.items():
            lines.append(f"{key} = {_toml_value(getattr(s, attr))}")
        lines.append("")
    lines.append("[generator]")
    for k, v in s.generator.to_dict().items():
        lines.append(f"{k} = {_toml_value(v)}")
    lines.append("")
    return "\n".join(lines)


def save(s: Settings, path: Path | None = None) -> None:
    path = path or config_home() / "config.toml"
    _validate(s)
    if s.vault_path and not s.vault_id:
        s.vault_id = uuid.uuid4().hex
    storage.write_private(path, dumps(s).encode("utf-8"))
