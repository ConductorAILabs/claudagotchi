#!/bin/bash
# Claude Code statusLine script.
# Side-effect: captures `rate_limits` from the input JSON to a temp file
# so hook.sh can include real Anthropic-side limit percentages in the
# token_display payload. Prints minimal status text to stdout.

INPUT=$(cat)

# DEBUG: snapshot the full input so we can identify which field Claude Code
# uses for the "↑ X tokens" indicator. Remove this line once mapped.
printf '%s' "$INPUT" > /tmp/claude_statusline_input.json

OUT=/tmp/claude_rate_limits.json
TMP="${OUT}.tmp.$$"

printf '%s' "$INPUT" | python3 -c "
import json, sys, os
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
rl = d.get('rate_limits') or {}
out = {
    'five_hour': {
        'used_percentage': float((rl.get('five_hour') or {}).get('used_percentage', 0.0)),
        'resets_at':       int  ((rl.get('five_hour') or {}).get('resets_at',       0)),
    },
    'seven_day': {
        'used_percentage': float((rl.get('seven_day') or {}).get('used_percentage', 0.0)),
        'resets_at':       int  ((rl.get('seven_day') or {}).get('resets_at',       0)),
    },
}
sys.stdout.write(json.dumps(out, separators=(',',':')))
" > "$TMP" 2>/dev/null && mv "$TMP" "$OUT" 2>/dev/null

# Minimal status line — empty keeps the bottom bar clean
exit 0
