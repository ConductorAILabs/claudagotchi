#!/usr/bin/env python3
"""Weekly usage aggregator.

Reads Claude Code's stats-cache.json (has daily messageCount + toolCallCount
going back months) and our own token_analysis.jsonl (has per-request token
counts). Returns a weekly summary dict for the Pi dashboard.

This is called from analyzer.py to enrich the payload.
"""

import json, time, re, glob, os
from datetime import datetime, timedelta, date
from pathlib import Path

HOME             = Path.home()
STATS_CACHE      = HOME / ".claude" / "stats-cache.json"
PROJECTS_ROOT    = HOME / ".claude" / "projects"
SESSION_FILE     = Path("/tmp/claude_token_session.json")


def _load_stats_cache():
    try:
        return json.loads(STATS_CACHE.read_text())
    except:
        return {}


def _daily_stats_map(cache):
    """Return {date_str -> {messageCount, toolCallCount, sessionCount}}."""
    result = {}
    for entry in cache.get("dailyActivity", []):
        d = entry.get("date", "")
        if d:
            result[d] = entry
    return result


_PAT_TS  = re.compile(r'"timestamp"\s*:\s*"([0-9T:\-.Z]+)"')
_PAT_TOK = re.compile(r'"(input_tokens|output_tokens)"\s*:\s*(\d+)')


def _read_token_log_week():
    """Scan ~/.claude/projects/*/*.jsonl and bucket per-request input+output
    tokens by date for the last 7 days. Each JSONL line is one API request,
    so we sum deltas — no cumulative-snapshot double-counting."""
    week_ago = time.time() - 7 * 86400
    by_date = {}
    jsonls = glob.glob(str(PROJECTS_ROOT / "*" / "*.jsonl"))
    jsonls = [p for p in jsonls if os.path.getmtime(p) >= week_ago]

    for path in jsonls:
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    ts_m = _PAT_TS.search(line)
                    if not ts_m:
                        continue
                    try:
                        s = ts_m.group(1).replace("Z", "+00:00")
                        ts = datetime.fromisoformat(s).timestamp()
                    except ValueError:
                        continue
                    if ts < week_ago:
                        continue
                    line_tok = 0
                    for key, val in _PAT_TOK.findall(line):
                        line_tok += int(val)
                    if line_tok:
                        d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                        by_date[d] = by_date.get(d, 0) + line_tok
        except OSError:
            continue
    return by_date


def _read_session():
    try:
        return json.loads(SESSION_FILE.read_text())
    except:
        return {}


def build_weekly():
    """Return weekly summary dict for inclusion in the display payload."""
    cache     = _load_stats_cache()
    daily_map = _daily_stats_map(cache)
    tok_week  = _read_token_log_week()
    session   = _read_session().get("totals", {})

    today     = date.today()
    days_out  = []
    week_msg  = 0
    week_tok  = 0

    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        stats = daily_map.get(ds, {})
        msg   = int(stats.get("messageCount", 0))
        tools = int(stats.get("toolCallCount", 0))
        tok   = int(tok_week.get(ds, 0))

        # Today: add live session data too
        if i == 0:
            sess_inp = int(session.get("input", 0))
            sess_out = int(session.get("output", 0))
            tok += sess_inp + sess_out

        label = d.strftime("%a").upper()[:3]
        if i == 0:
            label = "TODAY"

        days_out.append({
            "label"   : label,
            "date"    : ds,
            "messages": msg,
            "tools"   : tools,
            "tokens"  : tok,
            "is_today": i == 0,
        })
        week_msg += msg
        week_tok += tok

    # Max messages any day (for bar scaling)
    max_msg = max((d["messages"] for d in days_out), default=1) or 1

    return {
        "days"      : days_out,
        "week_msgs" : week_msg,
        "week_tokens": week_tok,
        "daily_avg_msgs": week_msg // 7,
        "max_day_msgs": max_msg,
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(build_weekly(), indent=2))
