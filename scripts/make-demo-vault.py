#!/usr/bin/env python3
"""Create a synthetic KDBX 4 vault for demos, development and benchmarks.

    python scripts/make-demo-vault.py OUT.kdbx [--entries N] [--password PW] [--fast]

Never point this at a real vault: it refuses to overwrite an existing file.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from keylane.core import generator, storage  # noqa: E402
from keylane.core.kdbx import CustomField, EntryData, VaultDocument, make_composite  # noqa: E402

SERVICES = [
    ("GitHub", "https://github.com/login"), ("GitLab", "https://gitlab.com/users/sign_in"),
    ("Proton Mail", "https://account.proton.me"), ("Fastmail", "https://app.fastmail.com"),
    ("Hetzner Cloud", "https://console.hetzner.cloud"), ("Cloudflare", "https://dash.cloudflare.com"),
    ("Amazon", "https://www.amazon.com"), ("Bank of Example", "https://online.bank.example"),
    ("Tailscale", "https://login.tailscale.com"), ("Steam", "https://store.steampowered.com/login"),
    ("Spotify", "https://accounts.spotify.com"), ("Figma", "https://www.figma.com/login"),
    ("Linear", "https://linear.app/login"), ("Notion", "https://www.notion.so/login"),
    ("Home router", "http://192.168.1.1"), ("NAS", "https://nas.local:5001"),
    ("Arch Wiki", "https://wiki.archlinux.org"), ("AUR", "https://aur.archlinux.org/login"),
    ("Discord", "https://discord.com/login"), ("Matrix", "https://app.element.io"),
]
GROUPS = ["Personal", "Work", "Finance", "Infrastructure", "Social"]
WORDS = ["alpha", "bravo", "cedar", "delta", "ember", "fjord", "gamma", "harbor", "iris", "juniper", "koala",
         "lumen", "maple", "nova", "orbit", "pixel", "quartz", "raven", "sierra", "tundra"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--entries", type=int, default=len(SERVICES))
    ap.add_argument("--password", default="demo")
    ap.add_argument("--keyfile", type=Path)
    ap.add_argument("--fast", action="store_true", help="cheap KDF (tests/benchmarks only)")
    a = ap.parse_args()
    if a.out.exists():
        print(f"refusing to overwrite {a.out}", file=sys.stderr)
        return 1
    rnd = random.Random(42)
    doc = VaultDocument.create(make_composite(a.password, a.keyfile), name=a.out.stem, kdf_target_ms=50 if a.fast else 1000)
    root = next(g for g in doc.groups() if g.depth == 0)
    gids = {}
    for name in GROUPS:
        gids[name] = doc.create_group(name, root.id)
    gids["Servers"] = doc.create_group("Servers", gids["Infrastructure"])
    spec = generator.PasswordSpec()
    for i in range(a.entries):
        if i < len(SERVICES):
            title, url = SERVICES[i]
        else:
            title = f"{rnd.choice(WORDS).title()} {rnd.choice(WORDS)} {i}"
            url = f"https://{rnd.choice(WORDS)}{i}.example.com/login"
        group = rnd.choice(list(gids.values()))
        fields = [CustomField("Recovery code", generator.generate(spec).value, True)] if i % 7 == 0 else []
        doc.create_entry(EntryData(
            id=None, title=title, username=f"{rnd.choice(WORDS)}.{rnd.choice(WORDS)}@example.com",
            password=generator.generate(spec).value, url=url,
            notes="Synthetic demo entry." if i % 3 == 0 else "", tags=["demo"] if i % 5 == 0 else [],
            fields=fields, group_id=group,
        ))
    storage.atomic_replace(a.out, doc.to_bytes(), mode=0o600)
    print(f"wrote {a.out} ({a.entries} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
