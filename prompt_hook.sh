#!/bin/bash
# Claude Code UserPromptSubmit hook — fires the moment the user hits enter,
# before Claude responds. Reads the last known token state, adds a rough
# estimate for the new prompt's input tokens, and pushes to the display so
# the user sees the bar/total tick up immediately.

LATEST_FILE="/tmp/claude_token_latest.json"
DISPLAY_DEV="/dev/cu.usbmodem1101"

INPUT=$(cat)
echo "$INPUT" > /tmp/claude_prompt_raw.json

# Honor pin file: skip if this prompt isn't from the pinned session.
PINNED_OK=$(echo "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
sid = d.get('session_id', '')
try:
    p = json.load(open('/tmp/claude_token_pinned.json'))
    pinned = p.get('session_id', '')
    print('1' if (not pinned) or sid == pinned else '0')
except Exception:
    print('1')
" 2>/dev/null)

if [ "$PINNED_OK" != "1" ]; then exit 0; fi

# Build a teaser payload: take the last analyzed state, bump session.input
# by an estimate of the prompt size (~4 chars/token).
TEASER=$(python3 - <<PYEOF
import json, sys

prompt_input = $(printf %s "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    p = d.get('prompt', '') or ''
    print(json.dumps({'len': len(p)}))
except Exception:
    print(json.dumps({'len': 0}))
")

est_tokens = max(1, prompt_input['len'] // 4)

try:
    state = json.load(open("$LATEST_FILE"))
except Exception:
    state = {
        "session": {"input":0,"output":0,"cache_read":0,"requests":0,"cost":0.0},
        "hour_tokens": 0,
        "cache_eff": 0.0,
        "top_model": "",
        "weekly": {"week_tokens": 0},
    }

sess = state.setdefault("session", {})
sess["input"]    = int(sess.get("input", 0))    + est_tokens
sess["requests"] = int(sess.get("requests", 0)) + 1
state["hour_tokens"] = int(state.get("hour_tokens", 0)) + est_tokens

wk = state.setdefault("weekly", {}) or {}
wk["week_tokens"] = int(wk.get("week_tokens", 0)) + est_tokens
state["weekly"] = wk

print(json.dumps(state))
PYEOF
)

# Atomic-write to the state file the bridge daemon watches
if [ -n "$TEASER" ]; then
    STATE=/tmp/claude_token_state.json
    TMP="${STATE}.tmp.$$"
    printf '%s' "$TEASER" > "$TMP" 2>/dev/null && mv "$TMP" "$STATE" 2>/dev/null
fi

exit 0
