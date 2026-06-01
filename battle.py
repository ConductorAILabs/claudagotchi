#!/usr/bin/env python3
"""Claudagotchi — local battle engine (Phase 1).

Two character cards fight in a deterministic, turn-based simulation. No network,
no code execution: cards are pure data (see pet.export_card / pet.validate_card),
stats are always re-derived, and the same (cardA, cardB, seed) always produces
the same result — so any party can re-run it to verify the outcome.

CLI:
    python3 battle.py export [file]            # write the current pet's card
    python3 battle.py fight A.card.json B.card.json [seed]
    python3 battle.py demo                      # current pet vs a rival

Importable:
    import battle
    result = battle.battle(card_a, card_b, seed=42)
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

import pet
from pet import Card

# A fighter is the mutable per-battle combat record built by mk() below.
Fighter = dict[str, Any]
# A battle result / report (winner, rounds, replayable log, validation issues).
Result = dict[str, Any]

# Type triangle (matches claudemon, for when skins/models map to types later).
MATCHUP = {
    "FIRE":   {"GRASS": 2.0, "WATER": 0.5},
    "WATER":  {"FIRE":  2.0, "GRASS": 0.5},
    "GRASS":  {"WATER": 2.0, "FIRE":  0.5},
    "NORMAL": {},
}
MAX_ROUNDS = 300


def _rng(seed: int) -> Callable[[], float]:
    """Deterministic xorshift32 -> floats in [0,1). Pure, reproducible."""
    s = seed & 0xFFFFFFFF or 0x1234
    state = [s]
    def nxt() -> float:
        x = state[0]
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        state[0] = x & 0xFFFFFFFF
        return state[0] / 0xFFFFFFFF
    return nxt


def _type_mult(att: str, dfn: str) -> float:
    return MATCHUP.get(att, {}).get(dfn, 1.0)


def _seed_from(a_name: str, b_name: str, seed: int) -> int:
    h = seed * 2654435761
    for c in (a_name + "|" + b_name):
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h & 0xFFFFFFFF


def battle(card_a: Card, card_b: Card, seed: int = 42) -> Result:
    """Simulate A vs B. Returns winner, rounds, a replayable log, and any
    validation issues found in either card."""
    okA, A, issuesA = pet.validate_card(card_a)
    okB, B, issuesB = pet.validate_card(card_b)
    if not okA or not okB or A is None or B is None:
        return {"error": "invalid card", "issues": {"a": issuesA, "b": issuesB}}

    rnd = _rng(_seed_from(A["name"], B["name"], seed))

    def mk(c: Card) -> Fighter:
        s = c["stats"]
        return {"name": c["name"], "hp": s["max_hp"], "max_hp": s["max_hp"],
                "atk": s["attack"], "crit": s["crit"], "speed": s["speed"],
                "type": s["type"]}
    fa, fb = mk(A), mk(B)

    # initiative: higher speed strikes first; ties broken deterministically
    first, second = (fa, fb) if (fa["speed"], rnd()) >= (fb["speed"], rnd()) else (fb, fa)

    log = [f"{fa['name']} (HP {fa['hp']}/ATK {fa['atk']}) vs "
           f"{fb['name']} (HP {fb['hp']}/ATK {fb['atk']})",
           f"{first['name']} is faster and strikes first."]

    def strike(att: Fighter, dfn: Fighter) -> None:
        variance = 0.8 + 0.4 * rnd()                  # +-20%
        crit = rnd() < att["crit"]
        mult = _type_mult(att["type"], dfn["type"])
        dmg = max(1, round(att["atk"] * variance * mult * (1.8 if crit else 1.0)))
        dfn["hp"] = max(0, dfn["hp"] - dmg)
        tag = " CRIT!" if crit else ""
        if mult > 1: tag += " (super effective)"
        elif mult < 1: tag += " (resisted)"
        log.append(f"{att['name']} hits {dfn['name']} for {dmg}{tag}  "
                   f"[{dfn['name']} {dfn['hp']}/{dfn['max_hp']}]")

    rounds = 0
    while fa["hp"] > 0 and fb["hp"] > 0 and rounds < MAX_ROUNDS:
        rounds += 1
        strike(first, second)
        if second["hp"] <= 0:
            break
        strike(second, first)

    if fa["hp"] == fb["hp"]:
        winner = max((fa, fb), key=lambda f: (f["hp"], f["max_hp"]))
    else:
        winner = fa if fa["hp"] > fb["hp"] else fb
    loser = fb if winner is fa else fa
    log.append(f"WINNER: {winner['name']}  ({winner['hp']}/{winner['max_hp']} HP left)")

    return {
        "winner": winner["name"], "loser": loser["name"], "rounds": rounds,
        "final": {fa["name"]: fa["hp"], fb["name"]: fb["hp"]},
        "log": log,
        "issues": {"a": issuesA, "b": issuesB},
        "sig_ok": {"a": A["sig_ok"], "b": B["sig_ok"]},
        "seed": seed,
    }


def _rival_card(level: int, name: str = "Rival") -> Card:
    """A quick opponent card for demo/testing, signed like a real one."""
    st = pet.new_state()
    st["name"] = name
    st["lifetime_xp"] = pet.xp_floor(level) + 1
    st["level"] = level
    st["upgrades"] = {"vigor": max(0, level // 6), "power": max(0, level // 5)}
    return pet.export_card(st)


def _print_result(res: Result) -> None:
    if "error" in res:
        print("ERROR:", res["error"], res.get("issues")); return
    for line in res["log"]:
        print("  " + line)
    for side in ("a", "b"):
        if res["issues"][side]:
            print(f"  [validation {side}] " + "; ".join(res["issues"][side]))
        if not res["sig_ok"][side]:
            print(f"  [warn] card {side} failed checksum (edited/unsigned)")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "demo"

    if cmd == "export":
        card = pet.export_card(pet.load())
        path = argv[2] if len(argv) > 2 else "claude.card.json"
        with open(path, "w") as f:
            json.dump(card, f, indent=2)
        print(f"wrote {path}  (Lv {card['level']}, sig {card['sig']})")
        return 0

    if cmd == "fight":
        if len(argv) < 4:
            print("usage: battle.py fight A.card.json B.card.json [seed]"); return 1
        a = json.load(open(argv[2])); b = json.load(open(argv[3]))
        seed = int(argv[4]) if len(argv) > 4 else 42
        _print_result(battle(a, b, seed))
        return 0

    if cmd == "demo":
        you = pet.export_card(pet.load())
        rival = _rival_card(max(1, you["level"] - 3), "Rival")
        print(f"== {you['name']} (Lv {you['level']}) vs {rival['name']} (Lv {rival['level']}) ==")
        _print_result(battle(you, rival, 42))
        return 0

    print("commands: export | fight | demo")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
