#!/usr/bin/env python3
"""Claudagotchi — the brain.

Pure game logic, no curses. Turns your real Claude Code token usage into a
persistent creature that eats tokens, banks XP, and levels up.

State lives in ~/.claude/claudagotchi.json (survives across sessions).

Self-contained: it sums today's tokens straight from ~/.claude/projects/*.jsonl
the same way the old token_display hook did, so it does NOT depend on the
token_display pipeline being installed.

CLI:
    python3 pet.py tick            # feed from real token usage (run by the hook)
    python3 pet.py status          # print state as pretty JSON
    python3 pet.py feed 50000      # manually feed N tokens (for the simulator)
    python3 pet.py spend power     # spend banked XP on an upgrade (Phase 2)
    python3 pet.py reset           # hatch a fresh egg

Importable:
    import pet
    s = pet.load(); pet.tick(s); pet.save(s)
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import hmac
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

# ── type aliases (dict-as-struct shapes) ─────────────────────────────────────
# State: the persisted creature blob (see new_state for the full key list).
# Card:  a shareable progression card (see _card_payload / export_card).
# Stats: derived combat stats (see combat_stats).
# Event: the small change-description feed() returns for the viewer to animate.
State = dict[str, Any]
Card  = dict[str, Any]
Stats = dict[str, Any]
Event = dict[str, Any]

# ── paths ──────────────────────────────────────────────────────────────────
HOME          = Path.home()
STATE_PATH    = HOME / ".claude" / "claudagotchi.json"
PROJECTS_ROOT = HOME / ".claude" / "projects"

# ── tuning ───────────────────────────────────────────────────────────────────
TOKENS_PER_XP = 1000       # 1 XP per 1000 tokens (input+output only — see tokens_today)
LEVEL_BASE    = 50         # cumulative-XP curve constant: C(L) = LEVEL_BASE*(L-1)^2
EAT_WINDOW_S  = 3.0        # how long after a meal the viewer shows "eating"
HIBERNATE_S   = 30 * 60    # idle this long with no food -> he sleeps

# Trainings. cost(rank) = BASE*(rank+1) — repeating the same stat costs more
# each time (its own diminishing returns). Order here is the firmware list order.
class Upgrade(TypedDict):
    label: str
    desc: str
    base: int

UPGRADES: dict[str, Upgrade] = {
    "vigor": {"label": "Vigor", "desc": "+HP",   "base": 120},
    "power": {"label": "Power", "desc": "+ATK",  "base": 120},
    "guard": {"label": "Guard", "desc": "+DEF",  "base": 140},
}

# Foods. (name, base_tokens). Eating the SAME food repeatedly makes it less
# effective; effectiveness recovers over time (rewards variety).
FOODS = [
    ("Cookie",  2500),
    ("Pizza",   5000),
    ("Combo",   9000),
    ("Feast",  18000),
    ("Payload", 40000),
]
FOOD_DECAY     = 0.6      # multiplier per repeated use of the same food
FOOD_RECOVER_S = 90.0     # one "use" is forgotten every 90s
FOOD_MIN_EFF   = 0.25     # never drops below 25% effective

# ── cosmetics unlocked by level ───────────────────────────────────────────────
def rgb565(hexstr: str) -> int:
    h = hexstr.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

# Color skins for the mascot body. (name, body, accent, unlock_level, rainbow)
SKINS = [
    ("Terracotta", "#D77757", "#FF8700",  0, False),   # the brand default
    ("Sky",        "#4FA3FF", "#9AD0FF",  5, False),
    ("Forest",     "#5BC873", "#A8F0B0", 10, False),
    ("Berry",      "#FF5FA2", "#FFB3D1", 15, False),
    ("Gold",       "#FFC53D", "#FFE08A", 20, False),
    ("Cyan",       "#36D7E0", "#A0F0F5", 30, False),
    ("Violet",     "#A07CFF", "#D2C2FF", 40, False),
    ("Rainbow",    "#FFFFFF", "#FFFFFF", 50, True),     # firmware cycles the hue
]

def skins_unlocked(level: int) -> list[int]:
    return [i for i, s in enumerate(SKINS) if level >= s[3]]

# Animation tiers — each adds an *ambient* effect around him. His face/shape
# never changes (always the mascot, just blinks); these are motion/FX.
ANIM_UNLOCKS = [(0, "blink"), (3, "sparkles"), (8, "sparkles2"),
                (15, "starfield"), (25, "starfield2"), (40, "confetti")]

def anim_tier(level: int) -> int:
    return sum(1 for lv, _ in ANIM_UNLOCKS if level >= lv)   # 1..6

# ── quests — pick from random time/XP offers; he earns a lump reward but eats
# no tokens while away. Offers vary, so some are better value than others.
QUEST_NAMES = ["Debug the Void", "Refactor Caverns", "Token Mines", "The Long Compile",
               "Cache Quarry", "Stack Climb", "Heap Trek", "Null Marsh"]
QUEST_MINUTES = [3, 5, 8, 12, 18, 25, 35]
QUEST_OFFERS  = 3

def quest_active(state: State) -> bool:
    return state.get("quest_end", 0) > 0

def quest_remaining(state: State) -> int:
    return max(0, int(state["quest_end"] - _now())) if quest_active(state) else 0

def gen_quest_offers(state: State) -> list:
    """3 random {min, reward} offers; the reward/min rate varies so some are deals."""
    lvl = state["level"]
    offers = []
    for _ in range(QUEST_OFFERS):
        mins = random.choice(QUEST_MINUTES)
        rate = random.uniform(0.6, 1.7)
        offers.append({"min": mins, "reward": int(mins * (60 + lvl * 12) * rate)})
    return offers

def ensure_quest_offers(state: State) -> bool:
    """Generate offers once when idle; returns True if it created them (so save)."""
    if quest_active(state) or state.get("quest_offers"):
        return False
    state["quest_offers"] = gen_quest_offers(state)
    return True

def start_quest(state: State, idx: int = 0) -> tuple[bool, str]:
    if quest_active(state):
        return False, "already away"
    offers = state.get("quest_offers") or gen_quest_offers(state)
    o = offers[max(0, min(int(idx), len(offers) - 1))]
    state["quest_end"]    = _now() + o["min"] * 60
    state["quest_reward"] = int(o["reward"])
    state["quest_name"]   = random.choice(QUEST_NAMES)
    state["quest_offers"] = []
    return True, f"{o['min']}m / {o['reward']} XP"

def _complete_quest_if_due(state: State) -> Event | None:
    """Timer up → grant the reward as a lump of food and clear the quest."""
    if quest_active(state) and _now() >= state["quest_end"]:
        reward = int(state.get("quest_reward", 0))
        state["quest_end"] = 0
        state["last_quest_done_ts"] = _now()
        state["quest_offers"] = []          # fresh offers generated next idle tick
        ev = feed(state, reward)
        ev["quest_done"] = True
        ev["quest_reward"] = reward
        return ev
    return None

def cycle_skin(state: State) -> tuple[bool, str]:
    unlocked = skins_unlocked(state["level"])
    if not unlocked:
        return False, "none"
    cur = state.get("skin", 0)
    nxt = unlocked[(unlocked.index(cur) + 1) % len(unlocked)] if cur in unlocked else unlocked[0]
    state["skin"] = nxt
    return True, SKINS[nxt][0]

def set_skin(state: State, idx: int) -> tuple[bool, str]:
    idx = int(idx)
    if idx in skins_unlocked(state["level"]):
        state["skin"] = idx
        return True, SKINS[idx][0]
    return False, "locked"

# FEED action: a manual treat (small food) with a short cooldown.
SNACK_TOKENS   = 1500
SNACK_COOLDOWN = 45.0
def snack(state: State) -> tuple[bool, str]:
    if _now() - state.get("last_snack_ts", 0) < SNACK_COOLDOWN:
        return False, "still full"
    state["last_snack_ts"] = _now()
    feed(state, SNACK_TOKENS)
    return True, f"snack +{SNACK_TOKENS}"

# PET action: tap head/back/belly for a little XP. Same spot repeated = less;
# mixing spots builds a streak, and a full head+back+belly cycle = a combo bonus.
PET_SPOTS    = ["head", "back", "belly"]
PET_BASE     = 50
PET_COMBO    = 200
PET_RESET_S  = 8.0       # idle this long resets the streak/diminishing

def pet_spot(state: State, spot: int) -> tuple[bool, str]:
    spot = int(spot) % 3
    now = _now()
    if now - state.get("pet_ts", 0) > PET_RESET_S:      # cold start: reset combo
        state["pet_same"] = 0; state["pet_streak"] = 0; state["pet_recent"] = []
    state["pet_ts"] = now
    last = state.get("pet_last", -1)
    if spot == last:                                    # same spot -> diminishing
        state["pet_same"] = state.get("pet_same", 0) + 1
        state["pet_streak"] = 0
        mult = max(0.2, 0.5 ** state["pet_same"])
    else:                                               # variety -> small streak bonus
        state["pet_same"] = 0
        state["pet_streak"] = state.get("pet_streak", 0) + 1
        mult = 1.0 + min(state["pet_streak"], 4) * 0.15
    reward = int(PET_BASE * mult)
    recent = (state.get("pet_recent", []) + [spot])[-3:]
    combo = (len(recent) == 3 and len(set(recent)) == 3)  # head+back+belly cycle
    if combo:
        reward += PET_COMBO
        recent = []
    state["pet_recent"] = recent
    state["pet_last"] = spot
    feed(state, reward)
    msg = f"{PET_SPOTS[spot]} +{reward}" + (" COMBO!" if combo else "")
    return True, msg

def _food_uses(state: State) -> list[float]:
    u = state.get("food_uses")
    if not isinstance(u, list) or len(u) != len(FOODS):
        u = [0.0] * len(FOODS)
        state["food_uses"] = u
    return u

def _food_recovery(state: State, now: float) -> float:
    """How many "uses" each food has forgotten since food_ts (0+ , time-based)."""
    return max(0.0, (now - state.get("food_ts", now)) / FOOD_RECOVER_S)

def _food_eff(uses: float) -> float:
    """Effectiveness 0..1 for a food with `uses` repeats remaining (clamped)."""
    return max(FOOD_MIN_EFF, FOOD_DECAY ** max(0.0, uses))

def food_effectiveness(state: State) -> list[float]:
    """Current 0..1 effectiveness for each food (after time recovery, no mutation)."""
    dec = _food_recovery(state, _now())
    return [_food_eff(x - dec) for x in _food_uses(state)]

def feed_food(state: State, idx: int) -> tuple[bool, str]:
    """Feed food #idx. Effectiveness drops the more you repeat the same food,
    and recovers over time. Returns (ok, message)."""
    idx = int(idx)
    if not (0 <= idx < len(FOODS)):
        return False, "no such food"
    u = _food_uses(state)
    now = _now()
    dec = _food_recovery(state, now)
    if dec > 0:                                   # apply time recovery to all foods
        u = [max(0.0, x - dec) for x in u]
        state["food_uses"] = u
    state["food_ts"] = now
    eff  = _food_eff(u[idx])
    gain = int(FOODS[idx][1] * eff)
    u[idx] += 1.0
    feed(state, gain)
    return True, f"{FOODS[idx][0]} +{gain}"

# ── token accounting ─────────────────────────────────────────────────────────
_PAT_TS  = re.compile(r'"timestamp"\s*:\s*"([0-9T:\-.Z]+)"')
_PAT_TOK = re.compile(
    r'"(input_tokens|output_tokens|cache_read_input_tokens|cache_creation_input_tokens)"\s*:\s*(\d+)'
)


def tokens_today(projects_root: Path = PROJECTS_ROOT) -> int:
    """Sum all tokens (input+output+cache) across every session JSONL touched
    today, counting only lines timestamped since local midnight."""
    today       = datetime.date.today()
    midnight_ts = time.mktime(today.timetuple())
    total = 0
    pattern = str(projects_root / "*" / "*.jsonl")
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) < midnight_ts:
                continue
        except OSError:
            continue
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    m = _PAT_TS.search(line)
                    if m:
                        try:
                            ts = datetime.datetime.fromisoformat(
                                m.group(1).replace("Z", "+00:00")
                            ).timestamp()
                            if ts < midnight_ts:
                                continue
                        except ValueError:
                            pass
                    for key, val in _PAT_TOK.findall(line):
                        # Count real work only — input + output. Cache-read tokens
                        # are the model re-reading cached context every turn (billions
                        # per day) and would swamp everything, so they're excluded.
                        if key == "input_tokens" or key == "output_tokens":
                            total += int(val)
        except OSError:
            continue
    return total


# ── level math (0-based: a fresh pet is level 0) ──────────────────────────────
def level_for_xp(xp: int) -> int:
    """C(L) = LEVEL_BASE*L^2 is the cumulative XP to reach level L. Level 0 at xp 0."""
    if xp <= 0:
        return 0
    return int((xp / LEVEL_BASE) ** 0.5)


def xp_floor(level: int) -> int:
    return LEVEL_BASE * level ** 2


def xp_ceiling(level: int) -> int:
    return LEVEL_BASE * (level + 1) ** 2


def level_progress(xp: int, level: int) -> tuple[int, int, float]:
    """(xp_into_level, xp_span_of_level, fraction 0..1)."""
    floor   = xp_floor(level)
    ceiling = xp_ceiling(level)
    span    = max(1, ceiling - floor)
    into    = max(0, xp - floor)
    return into, span, min(1.0, into / span)


# ── derived battle stats ──────────────────────────────────────────────────────
# Canonical: ALL combat stats derive from level + upgrade ranks. Nothing else is
# trusted — this is the anti-cheat backbone (a card can't just claim hp=99999).
def combat_stats(level: int, upgrades: dict[str, int]) -> Stats:
    v = int(upgrades.get("vigor", 0))
    p = int(upgrades.get("power", 0))
    g = int(upgrades.get("guard", 0))
    return {
        "max_hp":  20 + level * 4 + v * 5,
        "attack":  5 + level + p * 2,
        "defense": g * 2,
        "speed":   10 + level,                           # initiative
        "crit":    min(0.5, 0.03 + level * 0.003 + p * 0.004),
        "type":    "NORMAL",
    }

def stats(state: State) -> Stats:
    return combat_stats(state["level"], state.get("upgrades", {}))


# ── battle cards (data only, never code) ──────────────────────────────────────
CARD_VERSION = 1
# Friendly tamper-detection only. NOT a real secret (it ships in the source).
# Competitive/online play must replace this with server-side signing (Ed25519).
_CARD_SECRET = b"claudagotchi-card-v1"
_LEVEL_CAP   = 999

def _card_payload(name: str, level: int, lxp: int, upgrades: dict[str, int],
                  skin: int, sub_pets: list[Any]) -> Card:
    """The exact, canonical shape that gets signed — progression facts only.
    Note: NO hp/atk here. Stats are always re-derived, so they can't be forged."""
    return {
        "v":            CARD_VERSION,
        "name":         str(name)[:16],
        "level":        int(level),
        "lifetime_xp":  int(lxp),
        "upgrades":     {k: int(upgrades.get(k, 0)) for k in UPGRADES},
        "skin":         int(skin),
        "sub_pets":     list(sub_pets),
    }

def _sign(payload: Card) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_CARD_SECRET, raw, hashlib.sha256).hexdigest()[:16]

def export_card(state: State) -> Card:
    """Serialize the pet to a shareable card. Pure data + a checksum."""
    p = _card_payload(state["name"], state["level"], state["lifetime_xp"],
                      state.get("upgrades", {}), state.get("skin", 0),
                      state.get("sub_pets", []))
    p["sig"] = _sign(p)
    return p

def _upgrade_spend(upgrades: dict[str, int]) -> int:
    """Total XP that buying these ranks would have cost (ranks 1..r)."""
    total = 0
    for k, r in upgrades.items():
        if k in UPGRADES:
            total += UPGRADES[k]["base"] * (int(r) * (int(r) + 1) // 2)
    return total

def validate_card(card: Card) -> tuple[bool, Card | None, list[str]]:
    """Validate + normalize an untrusted card. Returns (ok, normalized, issues).

    Trust nothing: re-derive stats, clamp impossible values, verify the checksum.
    A hostile card can't inject code (it's data), inflate stats (derived), claim a
    level its XP can't support, or own upgrades it couldn't afford."""
    issues = []
    if not isinstance(card, dict):
        return False, None, ["card is not an object"]
    if card.get("v") != CARD_VERSION:
        issues.append(f"version {card.get('v')} != {CARD_VERSION}")

    name  = str(card.get("name", "?"))[:16]
    level = int(card.get("level", 1))
    lxp   = max(0, int(card.get("lifetime_xp", 0)))
    ups   = {k: max(0, int(card.get("upgrades", {}).get(k, 0))) for k in UPGRADES}
    skin  = int(card.get("skin", 0))
    sub   = list(card.get("sub_pets", []))

    # checksum (detects casual edits; not cryptographically strong)
    expect = _sign(_card_payload(name, level, lxp, ups, skin, sub))
    sig_ok = (card.get("sig") == expect)
    if not sig_ok:
        issues.append("checksum invalid — card was edited or corrupted")

    # consistency clamps (anti-cheat)
    max_lvl = level_for_xp(lxp)
    if level > max_lvl:
        issues.append(f"level {level} unsupported by XP -> clamped to {max_lvl}")
        level = max_lvl
    if _upgrade_spend(ups) > lxp:
        issues.append("upgrades cost more XP than ever earned -> reset to 0")
        ups = {k: 0 for k in UPGRADES}
    level = max(1, min(level, _LEVEL_CAP))

    norm = {
        "name": name, "level": level, "lifetime_xp": lxp,
        "upgrades": ups, "skin": skin, "sub_pets": sub,
        "sig_ok": sig_ok, "stats": combat_stats(level, ups),
    }
    return True, norm, issues


# ── persistence ──────────────────────────────────────────────────────────────
def _now() -> float:
    return time.time()


def new_state() -> State:
    now = _now()
    return {
        "version":         1,
        "name":            "Claude",
        "created_at":      now,
        "total_tokens":    0,
        "lifetime_xp":     0,
        "spent_xp":        0,
        "level":           0,
        "last_snack_ts":   0.0,
        "act_n":           0,
        "food_uses":       [0.0] * len(FOODS),
        "food_ts":         0.0,
        "pet_last":        -1,
        "pet_same":        0,
        "pet_streak":      0,
        "pet_recent":      [],
        "pet_ts":          0.0,
        "last_seen_today": 0,
        "last_seen_date":  datetime.date.today().isoformat(),
        "last_fed_ts":     0.0,
        "last_meal":       0,
        "last_meal_ts":    0.0,
        "last_levelup_ts": 0.0,
        "upgrades":        {k: 0 for k in UPGRADES},
        "sub_pets":        [],
        "achievements":    [],
        "skin":            0,
        "quest_end":       0.0,
        "quest_reward":    0,
        "quest_name":      "",
        "quest_offers":    [],
        "last_quest_done_ts": 0.0,
    }


def load() -> State:
    try:
        s = json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return new_state()
    # forward-compat: backfill any missing keys
    base = new_state()
    for k, v in base.items():
        s.setdefault(k, v)
    for k in UPGRADES:
        s["upgrades"].setdefault(k, 0)
    return s


def save(state: State) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    os.replace(tmp, STATE_PATH)


# ── core: feed the creature ───────────────────────────────────────────────────
def feed(state: State, tokens: int) -> Event:
    """Add `tokens` of food. Returns a small event dict describing what changed
    (meal size, whether a level-up happened) for the viewer to animate."""
    tokens = max(0, int(tokens))
    if tokens <= 0:
        return {"meal": 0, "leveled": False, "from_level": state["level"]}

    from_level = state["level"]
    now = _now()

    state["total_tokens"] += tokens
    state["lifetime_xp"]   = state["total_tokens"] // TOKENS_PER_XP
    state["level"]         = max(state["level"], level_for_xp(state["lifetime_xp"]))
    state["last_meal"]     = tokens
    state["last_meal_ts"]  = now
    state["last_fed_ts"]   = now

    leveled = state["level"] > from_level
    if leveled:
        state["last_levelup_ts"] = now
        _check_achievements(state)
    return {"meal": tokens, "leveled": leveled, "from_level": from_level,
            "to_level": state["level"]}


def tick(state: State, projects_root: Path = PROJECTS_ROOT) -> Event:
    """Read real token usage and feed the delta since we last looked."""
    today_total = tokens_today(projects_root)
    today_date  = datetime.date.today().isoformat()

    if state["last_seen_date"] != today_date:
        delta = today_total            # new day: the daily counter reset under us
    else:
        delta = max(0, today_total - state["last_seen_today"])

    state["last_seen_today"] = today_total
    state["last_seen_date"]  = today_date

    # Token-activity pulse — bumped on ANY real usage (even during a quest) so the
    # LEDs can light up on XP/token activity regardless of whether it's credited.
    if delta > 0:
        state["act_n"] = state.get("act_n", 0) + 1

    # Away on a quest: he doesn't eat (no XP credit). Advance the counter so tokens
    # earned while away aren't dumped on him on return; reward lands when due.
    if quest_active(state):
        done = _complete_quest_if_due(state)
        if done:
            return done
        return {"meal": 0, "leveled": False, "from_level": state["level"], "on_quest": True}

    return feed(state, delta)


# ── XP economy (Phase 2) ──────────────────────────────────────────────────────
def banked_xp(state: State) -> int:
    return max(0, state["lifetime_xp"] - state["spent_xp"])


def upgrade_cost(state: State, key: str) -> int:
    rank = state["upgrades"].get(key, 0)
    return UPGRADES[key]["base"] * (rank + 1)


def buy(state: State, key: str) -> tuple[bool, str]:
    if key not in UPGRADES:
        return False, f"unknown upgrade '{key}'"
    cost = upgrade_cost(state, key)
    if banked_xp(state) < cost:
        return False, f"need {cost} XP, have {banked_xp(state)}"
    state["spent_xp"] += cost
    state["upgrades"][key] += 1
    return True, f"{UPGRADES[key]['label']} -> rank {state['upgrades'][key]}"


# ── achievements ──────────────────────────────────────────────────────────────
_MILESTONES = [
    ("hatched",   lambda s: True,                          "Hatched!"),
    ("1m",        lambda s: s["total_tokens"] >= 1_000_000, "Ate 1M tokens"),
    ("10m",       lambda s: s["total_tokens"] >= 10_000_000,"Ate 10M tokens"),
    ("lvl10",     lambda s: s["level"] >= 10,               "Reached level 10"),
    ("lvl25",     lambda s: s["level"] >= 25,               "Reached level 25"),
]


def _check_achievements(state: State) -> list[str]:
    earned = set(state.get("achievements", []))
    fresh = []
    for key, test, _label in _MILESTONES:
        if key not in earned and test(state):
            earned.add(key)
            fresh.append(key)
    state["achievements"] = sorted(earned)
    return fresh


# ── status helpers for the viewer ─────────────────────────────────────────────
def mood(state: State) -> str:
    """eating | happy | content | sleepy | hibernating"""
    now = _now()
    if state["last_meal_ts"] and (now - state["last_meal_ts"]) < EAT_WINDOW_S:
        return "eating"
    fed = state["last_fed_ts"]
    if not fed:
        return "content"
    idle = now - fed
    if idle > HIBERNATE_S:
        return "hibernating"
    if idle > HIBERNATE_S / 3:
        return "sleepy"
    return "happy" if idle < 120 else "content"


def snapshot(state: State) -> dict[str, Any]:
    into, span, frac = level_progress(state["lifetime_xp"], state["level"])
    lvl = state["level"]
    unlocked = skins_unlocked(lvl)
    sidx = state.get("skin", 0)
    if sidx not in unlocked:
        sidx = 0
    sname, bodyhex, acchex, _lv, rainbow = SKINS[sidx]
    return {
        "name":         state["name"],
        "level":        lvl,
        "total_tokens": state["total_tokens"],
        "lifetime_xp":  state["lifetime_xp"],
        "banked_xp":    banked_xp(state),
        "xp_into":      into,
        "xp_span":      span,
        "xp_frac":      frac,
        "mood":         mood(state),
        "last_meal":    state["last_meal"],
        "stats":        stats(state),
        "upgrades":     dict(state["upgrades"]),
        "train_ranks":  [int(state["upgrades"].get(k, 0)) for k in UPGRADES],
        "food_fresh":   [round(e * 9) for e in food_effectiveness(state)],
        "achievements": list(state["achievements"]),
        "skin": {"idx": sidx, "name": sname, "body": rgb565(bodyhex),
                 "accent": rgb565(acchex), "rainbow": rainbow,
                 "unlocked": len(unlocked), "total": len(SKINS)},
        "anim_tier": anim_tier(lvl),
        "act_n":     state.get("act_n", 0),
        "led":       int(bodyhex.lstrip("#"), 16),     # exact 24-bit skin color for the LEDs
        "quest": {"active": quest_active(state),
                  "remaining": quest_remaining(state),
                  "name": state.get("quest_name", ""),
                  "reward": int(state.get("quest_reward", 0)),
                  "offers": [{"min": o["min"], "reward": int(o["reward"])}
                             for o in state.get("quest_offers", [])]},
    }


# ── CLI ────────────────────────────────────────────────────────────────────────
def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    state = load()

    if cmd == "tick":
        ev = tick(state); save(state)
        if ev["meal"]:
            msg = f"+{ev['meal']:,} tokens"
            if ev["leveled"]:
                msg += f"  LEVEL UP -> {ev['to_level']}"
            print(msg)
        return 0

    if cmd == "feed":
        n = int(argv[2]) if len(argv) > 2 else 0
        ev = feed(state, n); save(state)
        print(json.dumps(ev))
        return 0

    if cmd == "spend":
        key = argv[2] if len(argv) > 2 else ""
        ok, msg = buy(state, key)
        if ok:
            save(state)
        print(("OK: " if ok else "NO: ") + msg)
        return 0 if ok else 1

    if cmd == "quest":
        ok, msg = start_quest(state)
        if ok:
            save(state)
        print(("Away: " if ok else "NO: ") + msg)
        return 0

    if cmd == "skin":
        ok, msg = cycle_skin(state)
        if ok:
            save(state)
        print(("Skin: " if ok else "NO: ") + msg)
        return 0

    if cmd == "card":
        print(json.dumps(export_card(state), indent=2))
        return 0

    if cmd == "reset":
        s = new_state()
        # baseline against today's already-eaten tokens so he truly starts at 0
        s["last_seen_today"] = tokens_today()
        save(s)
        print("Fresh egg hatched at level 0.")
        return 0

    # default: status
    print(json.dumps(snapshot(state), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
