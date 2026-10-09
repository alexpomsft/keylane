"""Dependency-free path helpers shared by the lightweight CLI and the daemon."""

from __future__ import annotations

import os
from pathlib import Path


def runtime_dir() -> Path:
    override = os.environ.get("KEYLANE_RUNTIME_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and Path(base).is_dir():
        return Path(base) / "keylane"
    return Path("/tmp") / f"keylane-{os.getuid()}"
