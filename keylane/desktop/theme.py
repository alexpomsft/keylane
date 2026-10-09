"""Theme tokens: live Omarchy palette/font, or built-in light/dark fallback."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

DARK = {
    "mode": "dark", "accent": "#7aa2f7", "background": "#16161e", "lighter_background": "#1f2030",
    "foreground": "#c0caf5", "dark_foreground": "#6b7194", "red": "#f7768e", "yellow": "#e0af68",
    "green": "#9ece6a", "border": "#c0caf5",
}
LIGHT = {
    "mode": "light", "accent": "#2e5cb8", "background": "#f6f6f4", "lighter_background": "#ebebe8",
    "foreground": "#24262e", "dark_foreground": "#6c6f7a", "red": "#c4314b", "yellow": "#8f5e00",
    "green": "#3f7a1b", "border": "#24262e",
}

_GRADIENT_COLOR = re.compile(r"rgba?\(([0-9a-fA-F]{6,8})\)|#([0-9a-fA-F]{6,8})")


def omarchy_theme_dir(override: str = "") -> Path:
    if override:
        return Path(override).expanduser()
    # Omarchy itself hardcodes ~/.local/state; honour XDG_STATE_HOME only if it holds a theme.
    default = Path.home() / ".local/state/omarchy/current/theme"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and (Path(xdg) / "omarchy/current/theme").is_dir():
        return Path(xdg) / "omarchy/current/theme"
    return default


def _color(spec, refs: dict, fallback: str) -> str:
    if not isinstance(spec, str):
        return fallback
    if spec.startswith("hyprland."):
        spec = refs.get(spec.split(".", 1)[1], fallback)
    m = _GRADIENT_COLOR.search(spec)
    if not m:
        return fallback
    hexv = m.group(1) or m.group(2)
    if len(hexv) == 8:  # rgba -> QML #AARRGGBB
        hexv = hexv[6:] + hexv[:6]
    return "#" + hexv


def _alpha(v, fallback: float) -> float:
    return float(v) if isinstance(v, (int, float)) and 0 <= v <= 1 else fallback


def _font_family() -> str:
    try:
        out = subprocess.run(["fc-match", "-f", "%{family[0]}", "monospace"], capture_output=True, text=True, timeout=1)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "monospace"


def _hypr_rounding() -> int:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return 0
    try:
        from .hyprland import request_json

        v = request_json("j/getoption decoration:rounding")
        return max(0, min(24, int(v.get("int", 0))))
    except Exception:
        return 0


def load(mode: str = "auto", override_dir: str = "") -> dict:
    """Return a flat token dict for QML. Never raises."""
    tokens: dict = {"source": "fallback"}
    colors, shell = {}, {}
    if mode in ("auto", "omarchy"):
        d = omarchy_theme_dir(override_dir)
        try:
            colors = tomllib.loads((d / "colors.toml").read_text("utf-8"))
        except (OSError, ValueError):
            colors = {}
        try:
            shell = tomllib.loads((d / "shell.toml").read_text("utf-8"))
        except (OSError, ValueError):
            shell = {}
    if colors:
        base = DARK if colors.get("mode", "dark") != "light" else LIGHT
        tokens["source"] = "omarchy"
    else:
        base = LIGHT if mode == "light" else DARK
    pal = {k: _color(colors.get(k), {}, v) if colors else v for k, v in base.items() if k != "mode"}
    pal["mode"] = colors.get("mode", base["mode"]) if colors else base["mode"]
    refs = {k: v for k, v in (shell.get("hyprland") or {}).items()}
    L = shell.get("launcher") or shell.get("menu") or {}
    fg = pal["foreground"]
    tokens.update({
        "mode": pal["mode"],
        "accent": pal["accent"],
        "bg": _color(L.get("background"), refs, pal["background"]),
        "bgAlpha": _alpha(L.get("background-alpha"), 0.97),
        "surface": pal.get("lighter_background", pal["background"]),
        "fg": _color(L.get("text"), refs, fg),
        "muted": pal.get("dark_foreground", fg),
        "border": _color(L.get("border"), refs, pal.get("border", fg)),
        "borderAlpha": _alpha(L.get("border-alpha"), 1.0),
        "scrim": _color(L.get("scrim"), refs, pal["background"]),
        "scrimAlpha": _alpha(L.get("scrim-alpha"), 0.5),
        "selBg": _color(L.get("selected-background"), refs, fg),
        "selBgAlpha": _alpha(L.get("selected-background-alpha"), 0.08),
        "selFg": _color(L.get("selected-text"), refs, pal["accent"]),
        "selBorder": _color(L.get("selected-border"), refs, fg),
        "selBorderAlpha": _alpha(L.get("selected-border-alpha"), 0.25),
        "danger": pal.get("red", "#f7768e"),
        "warning": pal.get("yellow", "#e0af68"),
        "success": pal.get("green", "#9ece6a"),
        "fontFamily": _font_family(),
        "fontBase": int((shell.get("font") or {}).get("base-size", 12)) if isinstance((shell.get("font") or {}).get("base-size", 12), int) else 12,
        "radius": _hypr_rounding() if tokens["source"] == "omarchy" else 6,
    })
    tokens["fontBase"] = max(9, min(24, tokens["fontBase"]))
    return tokens


def watch_paths(override_dir: str = "") -> list[str]:
    d = omarchy_theme_dir(override_dir)
    return [str(d), str(d / "colors.toml"), str(d / "shell.toml"), str(d.parent)]
