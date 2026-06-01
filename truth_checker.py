#!/usr/bin/env python3
"""Periodic state-file reconciler.

Scans the most recently modified Claude Code session JSONL and compares
its input + output token totals against /tmp/claude_token_state.json.
If they drift past a threshold, rewrites the state file with the JSONL
truth so the display stays honest.

Run alongside display_bridge.py and (optionally) token_proxy.py:
    python3 truth_checker.py &
"""

import json, os, re, time, glob

import time as _time
from datetime import date

HOME      = os.path.expanduser("~")
PROJECTS  = os.path.join(HOME, ".claude", "projects")
STATE     = "/tmp/claude_token_state.json"
INTERVAL       = 5.0     # seconds between checks
SNAP_UP_DRIFT  = 0       # state behind truth: snap up on ANY drift (exact match)
SNAP_DOWN_HIGH = 100000  # state ahead by >this: assume proxy double-counted, snap back

PAT_TOK = re.compile(r'"(input_tokens|output_tokens)"\s*:\s*(\d+)')
PAT_TS  = re.compile(r'"timestamp"\s*:\s*"([0-9T:\-.Z]+)"')


PIN_FILE = "/tmp/claude_token_pinned.json"


def session_jsonls():
    """If a pin file exists, return only that session's JSONL.
    Otherwise return all session JSONLs touched today (cumulative mode)."""
    try:
        with open(PIN_FILE) as f:
            p = json.load(f).get("transcript_path", "")
        if p and os.path.exists(p):
            return [p]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    midnight = _time.mktime(date.today().timetuple())
    paths = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    return [q for q in paths if os.path.getmtime(q) >= midnight]


def jsonl_truth(paths):
    """Sum input_tokens + output_tokens across every line of every JSONL,
    only counting lines whose timestamp is today (handles JSONLs that span
    midnight)."""
    import datetime as _dt
    midnight = _time.mktime(date.today().timetuple())
    inp = out = 0
    for path in paths:
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    ts_m = PAT_TS.search(line)
                    if ts_m:
                        try:
                            s = ts_m.group(1).replace("Z", "+00:00")
                            if _dt.datetime.fromisoformat(s).timestamp() < midnight:
                                continue
                        except ValueError:
                            pass
                    for k, v in PAT_TOK.findall(line):
                        if k == "input_tokens":
                            inp += int(v)
                        else:
                            out += int(v)
        except OSError:
            continue
    return inp, out


def read_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_state(d):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.rename(tmp, STATE)


def reconcile():
    jsonls = session_jsonls()
    if not jsonls:
        return
    truth_in, truth_out = jsonl_truth(jsonls)
    truth_tot = truth_in + truth_out

    d = read_state()
    if d is None:
        d = {
            "session": {"input": 0, "output": 0, "requests": 0},
            "hour_tokens": 0, "cache_eff": 0.0, "top_model": "",
            "weekly": {"week_tokens": 0},
            "session_pct": -1.0, "week_pct": -1.0,
        }

    s = d.setdefault("session", {})
    state_in  = int(s.get("input", 0))
    state_out = int(s.get("output", 0))
    state_tot = state_in + state_out

    drift = state_tot - truth_tot

    # State behind: catch up to truth (exact match — SNAP_UP_DRIFT = 0).
    # State ahead by > SNAP_DOWN_HIGH: snap back (proxy double-count safeguard).
    # Otherwise (small positive drift): leave alone — proxy's mid-stream lead
    # is legitimate and lets the screen tick per-chunk during streaming.
    if drift < -SNAP_UP_DRIFT or drift > SNAP_DOWN_HIGH:
        s["input"]  = truth_in
        s["output"] = truth_out
        write_state(d)
        sign = "+" if drift > 0 else ""
        print(f"[truth] drift {sign}{drift:>+8,}  →  reset to {truth_tot:>10,}",
              flush=True)


def main():
    print(f"[truth] watching {PROJECTS}/*/*.jsonl  vs  {STATE}", flush=True)
    print(f"[truth] interval {INTERVAL}s  snap-up=any drift  "
          f"snap-down >{SNAP_DOWN_HIGH:,} tokens", flush=True)
    while True:
        try:
            reconcile()
        except Exception as e:
            print(f"[truth] error: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[truth] stopped")
