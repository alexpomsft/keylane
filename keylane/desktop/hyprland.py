"""Minimal Hyprland IPC client (request socket only, no config changes)."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def socket_path() -> Path | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    p = Path(rt) / "hypr" / sig / ".socket.sock"
    return p if p.exists() else None


def request(cmd: str, timeout: float = 0.5) -> bytes:
    p = socket_path()
    if p is None:
        raise OSError("Hyprland IPC unavailable")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(p))
        s.sendall(cmd.encode())
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
        return out
    finally:
        s.close()


def request_json(cmd: str):
    return json.loads(request(cmd) or b"null")


def available() -> bool:
    return socket_path() is not None


def active_window() -> str | None:
    try:
        w = request_json("j/activewindow")
    except (OSError, ValueError):
        return None
    return w.get("address") if isinstance(w, dict) else None


def _window_exists(address: str) -> bool:
    try:
        return any(c.get("address") == address for c in request_json("j/clients"))
    except (OSError, ValueError):
        return False


def focus_window(address: str) -> bool:
    """Return focus to ``address`` if it still exists. Lua (0.55+) then legacy syntax."""
    if not address or not all(c in "0123456789abcdefx" for c in address.lower()):
        return False
    if not _window_exists(address):
        return False
    for cmd in (f'dispatch hl.dsp.focus({{ window = "address:{address}" }})', f"dispatch focuswindow address:{address}"):
        try:
            if request(cmd).strip() == b"ok":
                return True
        except OSError:
            return False
    return False


def session_locked() -> bool | None:
    """True when an ext-session-lock is active (see omarchy-hyprland-session-locked)."""
    try:
        mons = request_json("j/monitors")
    except (OSError, ValueError):
        return None
    if not isinstance(mons, list):
        return None
    if any("LOCK" in (m.get("solitaryBlockedBy") or []) for m in mons):
        return True
    if any("WORKSPACE" not in (m.get("solitaryBlockedBy") or []) for m in mons):
        return False
    return None
