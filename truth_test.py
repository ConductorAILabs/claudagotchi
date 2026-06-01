#!/usr/bin/env python3
"""One-shot end-to-end truth comparator.

Walks the same chain as the live system:
  1. JSONL ground truth   — sum of input + output across today's session JSONLs
  2. State file           — /tmp/claude_token_state.json (what hooks produced)
  3. Slim payload         — what display_bridge.py would push to USB
                            (mimics its slimming logic exactly)

Prints a side-by-side report with deltas at each hop. If everything is honest,
all three columns should show identical input/output/total numbers.

Run:  python3 truth_test.py
"""

import json, os, re, glob, time, sys
from datetime import date

HOME     = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
STATE    = "/tmp/claude_token_state.json"
PIN_FILE = "/tmp/claude_token_pinned.json"

PAT_TS  = re.compile(r'"timestamp"\s*:\s*"([0-9T:\-.Z]+)"')
PAT_TOK = re.compile(
    r'"(input_tokens|output_tokens|cache_read_input_tokens|cache_creation_input_tokens)"\s*:\s*(\d+)'
)


def jsonls_today():
    """Pinned: only that session's transcript. Otherwise: every JSONL touched today."""
    try:
        with open(PIN_FILE) as f:
            p = json.load(f).get("transcript_path", "")
        if p and os.path.exists(p):
            return [p], "pinned"
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    midnight = time.mktime(date.today().timetuple())
    paths = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    return [p for p in paths if os.path.getmtime(p) >= midnight], "cumulative"


def jsonl_truth(paths):
    import datetime as _dt
    midnight = time.mktime(date.today().timetuple())
    inp = out = cr = cw = 0
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
                        v = int(v)
                        if   k == "input_tokens":                inp += v
                        elif k == "output_tokens":               out += v
                        elif k == "cache_read_input_tokens":     cr  += v
                        elif k == "cache_creation_input_tokens": cw  += v
        except OSError:
            continue
    return inp, out, cr, cw


def slim_from_state(d):
    """Mirror display_bridge.py's slim() exactly so we see what the chip gets."""
    sess = d.get("session", {}) or {}
    wk   = d.get("weekly", {}) or {}
    return {
        "session": {
            "input":    int(sess.get("input", 0)),
            "output":   int(sess.get("output", 0)),
            "requests": int(sess.get("requests", 0)),
        },
        "hour_tokens": int(d.get("hour_tokens", 0)),
        "cache_eff":   float(d.get("cache_eff", 0.0)),
        "top_model":   str(d.get("top_model", "")),
        "weekly":      {"week_tokens": int(wk.get("week_tokens", 0))},
        "session_pct": float(d.get("session_pct", -1.0)),
        "week_pct":    float(d.get("week_pct",    -1.0)),
    }


def fmt(n):
    return f"{n:>12,}"


def row(label, jsonl, state, slim):
    j = jsonl if jsonl is not None else ""
    s = state if state is not None else ""
    p = slim  if slim  is not None else ""
    js = fmt(j) if isinstance(j, int) else f"{j:>12}"
    ss = fmt(s) if isinstance(s, int) else f"{s:>12}"
    ps = fmt(p) if isinstance(p, int) else f"{p:>12}"
    print(f"  {label:<20} {js}  {ss}  {ps}")


def main():
    paths, mode = jsonls_today()
    truth_in, truth_out, truth_cr, truth_cw = jsonl_truth(paths)
    truth_total = truth_in + truth_out

    try:
        with open(STATE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read {STATE}: {e}")
        sys.exit(1)

    slim = slim_from_state(state)

    s_sess = state.get("session", {}) or {}
    s_in   = int(s_sess.get("input", 0))
    s_out  = int(s_sess.get("output", 0))
    s_cr   = int(s_sess.get("cache_read", 0))
    s_total = s_in + s_out

    p_sess = slim["session"]
    p_in   = p_sess["input"]
    p_out  = p_sess["output"]
    p_total = p_in + p_out

    print(f"\nMode: {mode}  ({len(paths)} JSONL{'s' if len(paths)!=1 else ''} scanned)\n")
    print(f"{'':22}{'JSONL truth':>12}  {'state.json':>12}  {'bridge slim':>12}")
    print(f"  {'-'*20} {'-'*12}  {'-'*12}  {'-'*12}")
    row("input_tokens",  truth_in,    s_in,    p_in)
    row("output_tokens", truth_out,   s_out,   p_out)
    row("cache_read",    truth_cr,    s_cr,    None)
    row("cache_write",   truth_cw,    None,    None)
    row("input+output",  truth_total, s_total, p_total)

    print()
    state_drift = s_total - truth_total
    slim_drift  = p_total - s_total
    state_tag = "OK" if state_drift == 0 else f"drift {state_drift:+,}"
    slim_tag  = "OK" if slim_drift  == 0 else f"drift {slim_drift:+,}"
    print(f"  state.json vs JSONL truth : {state_tag}")
    print(f"  bridge slim vs state.json : {slim_tag}")

    print(f"\nWeekly tokens (state)     : {state.get('weekly',{}).get('week_tokens',0):>14,}")
    print(f"  session_pct  (state)    : {state.get('session_pct', '?')}")
    print(f"  week_pct     (state)    : {state.get('week_pct',    '?')}")

    age_state = time.time() - os.path.getmtime(STATE)
    print(f"\nstate.json age            : {age_state:.1f}s")
    try:
        rl_age = time.time() - os.path.getmtime("/tmp/claude_rate_limits.json")
        print(f"rate_limits.json age      : {rl_age:.1f}s")
    except OSError:
        pass


if __name__ == "__main__":
    main()
