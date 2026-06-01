#!/usr/bin/env python3
"""Token usage analyzer for Claude Code.

Reads the Stop hook JSON payload, computes REAL metrics from the API
response (no estimated overhead — only what Claude actually reports),
generates actionable tips from local config, and emits a display payload.

Usage:
    echo '<json>' | python3 analyzer.py

Importable:
    from analyzer import analyze
    result = analyze(data_dict)
"""

import json, os, sys, time
from pathlib import Path

# Weekly aggregator lives next to this file
sys.path.insert(0, str(Path(__file__).parent))
try:
    from weekly import build_weekly as _build_weekly
    _HAS_WEEKLY = True
except ImportError:
    _HAS_WEEKLY = False

HOME             = Path.home()
GLOBAL_CLAUDE_MD = HOME / ".claude" / "CLAUDE.md"
PROJECT_CLAUDE_MD= Path(".claude") / "CLAUDE.md"
GLOBAL_SETTINGS  = HOME / ".claude" / "settings.json"
PROJECT_SETTINGS = Path(".claude") / "settings.json"
SKILLS_DIR       = HOME / ".claude" / "skills"
JSONL_LOG        = HOME / ".claude" / "token_analysis.jsonl"


# ── config readers ────────────────────────────────────────────────────────────

def _words(path):
    try: return len(path.read_text(errors="replace").split())
    except: return 0

def _json(path):
    try: return json.loads(path.read_text(errors="replace"))
    except: return {}

def _hook_count(settings, event):
    total = 0
    for entry in settings.get("hooks", {}).get(event, []):
        if isinstance(entry, dict):
            inner = entry.get("hooks", [entry])
            total += len(inner) if isinstance(inner, list) else 1
    return total

def _mcp_count(settings):
    return len(settings.get("mcpServers", {}))

def read_config():
    gs = _json(GLOBAL_SETTINGS)
    ps = _json(PROJECT_SETTINGS)
    skills = 0
    if SKILLS_DIR.is_dir():
        try: skills = sum(1 for _ in SKILLS_DIR.iterdir())
        except: pass
    return {
        "claude_md_words":   _words(GLOBAL_CLAUDE_MD) + _words(PROJECT_CLAUDE_MD),
        "hooks":             _hook_count(gs, "UserPromptSubmit") + _hook_count(ps, "UserPromptSubmit"),
        "session_hooks":     _hook_count(gs, "SessionStart")    + _hook_count(ps, "SessionStart"),
        "skills":            skills,
        "mcps":              _mcp_count(gs) + _mcp_count(ps),
    }


# ── real token flow (from the API, sums correctly) ───────────────────────────

def real_token_flow(data):
    """Break the actual API numbers into a flow that sums to 100%."""
    inp   = max(int(data.get("input_tokens", 0)), 0)
    out   = max(int(data.get("output_tokens", 0)), 0)
    cr    = max(int(data.get("cache_read_input_tokens", 0)), 0)
    cw    = max(int(data.get("cache_creation_input_tokens", 0)), 0)

    # Total context processed by the model this turn
    total_ctx = inp + cr + cw
    if total_ctx == 0:
        total_ctx = 1  # avoid div-by-zero

    def pct(n): return round(n / total_ctx * 100, 1)

    cache_eff = round(cr / total_ctx * 100, 1)   # % served from cache (cheap)

    return {
        "inp"       : inp,
        "out"       : out,
        "cache_read": cr,
        "cache_write": cw,
        "total_ctx" : total_ctx,
        "inp_pct"   : pct(inp),
        "out_pct"   : pct(out),
        "cr_pct"    : pct(cr),
        "cw_pct"    : pct(cw),
        "cache_eff" : cache_eff,
    }


# ── tips (rule-based, instant, from real data + local config) ─────────────────

def generate_tips(data, cfg, flow):
    tips = []

    # Real cache efficiency
    ce = flow["cache_eff"]
    if ce < 30 and flow["cache_read"] > 0:
        tips.append(
            f"Cache only served {ce:.0f}% of context — break <5 min ago? "
            "Send a ping before stepping away to keep the 5-min cache warm."
        )
    elif ce >= 60:
        tips.append(
            f"Cache hit {ce:.0f}% — great. Keep conversations alive rather "
            "than /clear to maintain this."
        )

    # Cache write (means cache just rebuilt)
    if flow["cache_write"] > 500:
        tips.append(
            f"Cache rebuild: {flow['cache_write']:,} tokens written to cache. "
            "Normal on first turn — cache warms up for cheap reads after this."
        )

    # Big output
    if flow["out"] > 800:
        tips.append(
            f"Output was {flow['out']:,} tokens — extended thinking may be on. "
            "Hit Alt+T to toggle off if you don't need step-by-step reasoning."
        )

    # CLAUDE.md bloat
    words = cfg["claude_md_words"]
    if words > 1500:
        tips.append(
            f"CLAUDE.md is {words:,} words (target <1,200). "
            f"Paying ~{int((words-1200)*1.33):,} extra tokens/turn just for rules."
        )

    # Too many MCPs
    mcps = cfg["mcps"]
    if mcps > 4:
        tips.append(
            f"{mcps} MCP servers connected — each sends its full schema every turn. "
            "Run /mcp disable <name> to drop the ones you're not using today."
        )

    # Too many hooks
    hooks = cfg["hooks"]
    if hooks > 2:
        tips.append(
            f"{hooks} UserPromptSubmit hooks inject context on every prompt. "
            "Audit with: cat ~/.claude/settings.json | jq .hooks.UserPromptSubmit"
        )

    # Too many skills
    skills = cfg["skills"]
    if skills > 5:
        tips.append(
            f"{skills} skills installed — unused ones still load. "
            "Disable all but 3-4 you actually invoke."
        )

    return tips[:4]   # cap to avoid overflow on display


# ── core analysis ─────────────────────────────────────────────────────────────

def analyze(data):
    cfg     = read_config()
    flow    = real_token_flow(data)
    tips    = generate_tips(data, cfg, flow)
    session = data.get("session_totals", {})

    payload = {
        # Real token flow — percentages of total_ctx, sum to ~100%
        "flow": {
            "cache_read" : {"tokens": flow["cache_read"],  "pct": flow["cr_pct"],  "label": "Cached (cheap)"},
            "fresh_input": {"tokens": flow["inp"],          "pct": flow["inp_pct"], "label": "Fresh input"},
            "cache_write": {"tokens": flow["cache_write"],  "pct": flow["cw_pct"], "label": "Cache write"},
            "output"     : {"tokens": flow["out"],          "pct": flow["out_pct"], "label": "Output"},
        },
        "cache_eff"    : flow["cache_eff"],
        "total_ctx"    : flow["total_ctx"],
        "tips"         : tips,
        "last": {
            "input"      : flow["inp"],
            "output"     : flow["out"],
            "cache_read" : flow["cache_read"],
            "cache_write": flow["cache_write"],
            "cost"       : float(data.get("cost_usd", 0.0)),
            "total_ctx"  : flow["total_ctx"],
        },
        "session": {
            "input"     : int(session.get("input", 0)),
            "output"    : int(session.get("output", 0)),
            "cache_read": int(session.get("cache_read", 0)),
            "cache_write": int(session.get("cache_write", 0)),
            "requests"  : int(session.get("requests", 0)),
            "cost"      : float(session.get("cost", 0.0)),
        },
        "config": {
            "claude_md_words": cfg["claude_md_words"],
            "hooks"          : cfg["hooks"],
            "skills"         : cfg["skills"],
            "mcps"           : cfg["mcps"],
        },
        "hour_tokens"  : int(data.get("hour_tokens", 0)),
        "top_model"    : str(data.get("top_model",   "")),
        "timestamp"    : time.time(),
        "weekly"       : _build_weekly() if _HAS_WEEKLY else {},
    }
    return payload


# ── logging ───────────────────────────────────────────────────────────────────

def append_log(payload):
    try:
        JSONL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with JSONL_LOG.open("a") as f:
            f.write(json.dumps(payload, separators=(",",":")) + "\n")
    except: pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    raw = sys.stdin.read()
    try: data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"analyzer: bad JSON: {e}\n"); sys.exit(1)
    result = analyze(data)
    append_log(result)
    print(json.dumps(result, separators=(",",":")))

if __name__ == "__main__":
    main()
