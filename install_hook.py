#!/usr/bin/env python3
"""Register (or remove) the Claudagotchi feeder hook in ~/.claude/settings.json.

    python3 install_hook.py          # install
    python3 install_hook.py --remove # uninstall

Idempotent: running install twice won't add duplicates.
"""
import json
import pathlib
import sys

HOOK = "/Users/jeffmiddleton/Desktop/claudagachi/hook_feed.sh"
EVENTS = ("PostToolUse", "Stop")
SETTINGS = pathlib.Path.home() / ".claude" / "settings.json"


def load() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    remove = "--remove" in sys.argv
    s = load()
    hooks = s.setdefault("hooks", {})

    for ev in EVENTS:
        lst = hooks.setdefault(ev, [])
        has = any(
            h.get("command") == HOOK
            for entry in lst if isinstance(entry, dict)
            for h in entry.get("hooks", [])
        )
        if remove:
            hooks[ev] = [
                entry for entry in lst
                if not (isinstance(entry, dict) and any(
                    h.get("command") == HOOK for h in entry.get("hooks", [])))
            ]
        elif not has:
            lst.append({"matcher": "", "hooks": [{"type": "command", "command": HOOK}]})

    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2))
    print(("Removed" if remove else "Installed") + f" feeder hook for {', '.join(EVENTS)}.")
    print("Restart Claude Code (or open a new session) for it to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
