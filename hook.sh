#!/bin/bash
# Claude Code Stop hook — reads real usage from the session JSONL,
# runs the analyzer, and pushes the result over USB-CDC to the
# ESP32-S3 token display.

ANALYZER="/Users/jeffmiddleton/Desktop/token_display/analyzer.py"
LATEST_FILE="/tmp/claude_token_latest.json"
DISPLAY_DEV="/dev/cu.usbmodem1101"

# ── 1. Read the Stop hook payload to get session_id ──────────────────────────
INPUT=$(cat)
echo "$INPUT" > /tmp/claude_hook_raw.json

# ── 1a. Filter: only proceed if this hook came from the pinned session ───────
#         If /tmp/claude_token_pinned.json exists, compare session_id; otherwise
#         honor every session (cumulative-across-sessions mode).
PINNED_OK=$(echo "$INPUT" | python3 -c "
import json, sys, os
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

SESSION_JSONL=$(echo "$INPUT" | python3 -c "
import json, sys, os
d = json.load(sys.stdin)
p = d.get('transcript_path', '')
if p and os.path.exists(p):
    print(p)
else:
    sid = d.get('session_id','')
    if sid:
        cwd = os.getcwd()
        ps = cwd.replace('/', '-')
        cand = f\"{os.path.expanduser('~')}/.claude/projects/{ps}/{sid}.jsonl\"
        if os.path.exists(cand):
            print(cand)
" 2>/dev/null)

if [ -z "$SESSION_JSONL" ] || [ ! -f "$SESSION_JSONL" ]; then exit 0; fi

# ── 3. Sum tokens — pinned session only if /tmp/claude_token_pinned.json
#       exists, otherwise cumulative across every today JSONL ────────────────
COMBINED=$(python3 - "$HOME/.claude/projects" <<'PYEOF'
import json, sys, re, time, datetime, os, glob

projects_root = sys.argv[1]

pinned_path = None
try:
    p = json.load(open('/tmp/claude_token_pinned.json'))
    cand = p.get('transcript_path', '')
    if cand and os.path.exists(cand):
        pinned_path = cand
except Exception:
    pinned_path = None

pat_ts  = re.compile(r'"timestamp"\s*:\s*"([0-9T:\-.Z]+)"')
pat_tok = re.compile(
    r'"(input_tokens|output_tokens|cache_read_input_tokens|cache_creation_input_tokens)"\s*:\s*(\d+)'
)
pat_mod = re.compile(r'"model"\s*:\s*"(claude-[^"]+)"')

today       = datetime.date.today()
midnight_ts = time.mktime(today.timetuple())
hour_ago    = time.time() - 3600

inp = out = cr = cw = 0
hour_inp = hour_out = 0
requests = 0
models = {}

# Pinned: scan only that session's JSONL.
# Otherwise: cumulative across every JSONL touched today.
if pinned_path:
    jsonls = [pinned_path]
else:
    jsonls = glob.glob(os.path.join(projects_root, "*", "*.jsonl"))
    jsonls = [p for p in jsonls if os.path.getmtime(p) >= midnight_ts]

for jsonl_path in jsonls:
    try:
        with open(jsonl_path, "r", errors="replace") as f:
            for line in f:
                ts = None
                ts_m = pat_ts.search(line)
                if ts_m:
                    try:
                        s = ts_m.group(1).replace("Z", "+00:00")
                        ts = datetime.datetime.fromisoformat(s).timestamp()
                        if ts < midnight_ts:
                            continue
                    except ValueError:
                        ts = None

                line_inp = line_out = 0
                for key, val in pat_tok.findall(line):
                    v = int(val)
                    if   key == "input_tokens":               inp += v; line_inp = v
                    elif key == "output_tokens":              out += v; line_out = v
                    elif key == "cache_read_input_tokens":    cr += v
                    elif key == "cache_creation_input_tokens": cw += v

                if line_inp and line_out:
                    requests += 1
                    if ts and ts >= hour_ago:
                        hour_inp += line_inp; hour_out += line_out
                    mod_m = pat_mod.search(line)
                    if mod_m:
                        m = mod_m.group(1)
                        models[m] = models.get(m, 0) + 1
    except OSError:
        continue

top_model   = max(models, key=models.get) if models else ""
hour_tokens = hour_inp + hour_out

payload = {
    "input_tokens":                inp,
    "output_tokens":               out,
    "cache_read_input_tokens":     cr,
    "cache_creation_input_tokens": cw,
    "cost_usd":                    0.0,
    "hour_tokens":                 hour_tokens,
    "top_model":                   top_model,
    "session_totals": {
        "input":      inp,
        "output":     out,
        "cache_read": cr,
        "cache_write": cw,
        "cost":       0.0,
        "requests":   requests,
    }
}
print(json.dumps(payload))
PYEOF
) 2>/dev/null || COMBINED="{}"

# ── 4. Run analyzer ───────────────────────────────────────────────────────────
ANALYZED=$(echo "$COMBINED" | python3 "$ANALYZER" 2>/dev/null) || ANALYZED="{}"

# ── 4b. Merge in real rate-limit percentages from statusLine capture ─────────
#       Only refresh the rings on Stop events — between turns the file is
#       frozen so /usage and the screen show the same value when opened.
HOOK_EVENT=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try: print(json.load(sys.stdin).get('hook_event_name',''))
except: print('')
" 2>/dev/null)
export HOOK_EVENT

ANALYZED=$(printf '%s' "$ANALYZED" | python3 -c "
import json, sys, os
try:
    a = json.load(sys.stdin)
except Exception:
    print('{}'); sys.exit(0)
ev = os.environ.get('HOOK_EVENT', '')
# Always preserve 'live' from the previous state write — the proxy ticks it
# per-chunk during streaming and we must not clobber its values when this
# hook's state-rewrite lands.
try:
    prev = json.load(open('/tmp/claude_token_state.json'))
except Exception:
    prev = {}
if 'live' in prev:
    a['live'] = prev['live']
if ev == 'Stop':
    # Refresh from the latest statusLine capture.
    try:
        rl = json.load(open('/tmp/claude_rate_limits.json'))
        a['session_pct'] = float((rl.get('five_hour') or {}).get('used_percentage', 0.0))
        a['week_pct']    = float((rl.get('seven_day')  or {}).get('used_percentage', 0.0))
    except Exception:
        pass
else:
    # Mid-turn: preserve the previous values from the last state write so the
    # rings don't drift ahead of /usage. New token counts still pass through.
    if 'session_pct' in prev: a['session_pct'] = float(prev['session_pct'])
    if 'week_pct'    in prev: a['week_pct']    = float(prev['week_pct'])
print(json.dumps(a, separators=(',',':')))
" 2>/dev/null)

# ── 5. Save locally + atomic-write state file (bridge daemon picks it up) ────
echo "$ANALYZED" > "$LATEST_FILE" 2>/dev/null || true

STATE=/tmp/claude_token_state.json
TMP="${STATE}.tmp.$$"
printf '%s' "$ANALYZED" > "$TMP" 2>/dev/null && mv "$TMP" "$STATE" 2>/dev/null

exit 0
