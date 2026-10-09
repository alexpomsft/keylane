"""Command-line entry point.

Kept deliberately light: control commands only import the socket client so a
keyboard shortcut reaches the resident owner within a few milliseconds.
"""

from __future__ import annotations

import os
import sys

USAGE = """usage: keylane [command]

commands:
  search [QUERY]   open quick search (default)
  new [TITLE]      open the create form
  manager          open the manager window
  settings         open settings
  lock             lock the vault
  status           print non-secret status as JSON
  quit             lock and exit the resident process
  daemon           run the resident owner in the foreground
  version          print the version

Keylane never prints, exports or accepts secrets on the command line.
"""


def _spawn_daemon(initial: str, args: dict) -> int:
    import json
    import subprocess

    env = dict(os.environ)
    env["KEYLANE_INITIAL"] = json.dumps({"cmd": initial, "args": args})
    subprocess.Popen([sys.executable, "-m", "keylane", "daemon"], env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else "search"
    rest = " ".join(argv).strip()
    if cmd in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0
    if cmd in ("version", "--version", "-V"):
        from . import __version__

        print(f"keylane {__version__}")
        return 0
    if cmd == "daemon":
        from .app import run

        return run()
    if cmd not in ("search", "new", "manager", "settings", "lock", "status", "quit"):
        print(f"keylane: unknown command {cmd!r}\n", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2
    args = {}
    if rest:
        if len(rest) > 512:
            print("keylane: argument too long", file=sys.stderr)
            return 2
        args = {"query": rest} if cmd == "search" else {"title": rest} if cmd == "new" else {}

    from .core import ipc

    try:
        resp = ipc.request(cmd, args)
    except (OSError, ValueError):
        resp = None
    if resp is None:
        if cmd == "status":
            print('{"running": false}')
            return 0
        if cmd in ("lock", "quit"):
            return 0  # nothing running means nothing is unlocked
        _spawn_daemon(cmd, args)
        if os.environ.get("KEYLANE_WAIT"):
            import time

            for _ in range(100):
                time.sleep(0.05)
                if ipc.ping():
                    break
        return 0
    if cmd == "status":
        import json

        print(json.dumps(resp.get("status", resp)))
    elif not resp.get("ok", False):
        print(f"keylane: {resp.get('error', 'request failed')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
