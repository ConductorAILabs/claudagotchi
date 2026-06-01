#!/bin/bash
# Claudagotchi feeder hook.
# Register on Claude Code PostToolUse + Stop. On each event it re-sums today's
# real token usage and feeds the delta to the creature. Self-contained — does
# not depend on the old token_display pipeline.
#
# Install (adds to ~/.claude/settings.json):
#   python3 /Users/jeffmiddleton/Desktop/claudagachi/install_hook.py
#
# Or wire it by hand under "hooks": { "PostToolUse": [...], "Stop": [...] }.

cat >/dev/null 2>&1   # drain the hook JSON payload; pet.py reads usage itself

# Run detached so the hook returns instantly and never adds turn latency.
python3 /Users/jeffmiddleton/Desktop/claudagachi/pet.py tick >/dev/null 2>&1 &
exit 0
