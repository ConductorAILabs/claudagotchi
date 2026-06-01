# Claudagotchi — design & roadmap

A Tamagotchi where **the creature is the Claude icon guy** (orange block art),
and his food is **your real Claude Code token usage**. The more tokens you burn,
the more he eats, the more he levels up. You spend banked XP to upgrade him,
unlock sub-pets, then export him as a fighter into the Claudcade arena.

He never dies — neglect just sends him to sleep (hibernate) until you come back.

## The pieces (and where they come from)

| Piece | Status | File / source |
|---|---|---|
| Token → food pipeline | reused from `token_display` | `hook_feed.sh` → `pet.py tick` |
| Pet brain (XP, levels, save) | **built** | `pet.py` (state in `~/.claude/claudagotchi.json`) |
| Terminal viewer (the orange guy) | **built** | `view.py` on `claudcade_engine.py` |
| XP economy + upgrades | **built (starter)** | `pet.py` UPGRADES + `view.py` U-menu |
| Sub-pets | planned | will reuse `claudemon.py` `Species` model |
| Character-card export | planned | serialize `pet.snapshot()` → portable card |
| Arena battles | planned | load cards into `fight.py` / `claudemon.py` |
| Leaderboard upload | planned | `claudcade_scores.py` (already Postgres-backed) |
| Web single-player | planned | `claudcade-site` (Netlify) |

`claudemon.py`, `fight.py`, `claudcade_scores.py`, and the web site all live in
`~/Desktop/claudegames` — Claudagotchi grows the character; those games are
where he fights.

## Core loop (a → b → c → d, the user's vision)

- **(a) Eat tokens → XP.** Every PostToolUse/Stop event, the hook re-sums today's
  tokens and feeds the delta. 100 tokens = 1 XP. Level curve is
  `C(L) = 50·(L−1)²` cumulative XP to reach level L.
- **(b) Spend XP to upgrade.** Banked XP = lifetime XP − spent. Buy Vigor (+HP),
  Power (+ATK), Focus (+XP rate). Press **U** in the viewer. *(starter done)*
- **(c) Unlock sub-pets.** Higher levels unlock companion creatures using the
  claudemon `Species` model (FIRE/WATER/GRASS, hp/dmg/sprite). They orbit him
  and add party slots for battles. *(next)*
- **(d) Save & battle.** Export your guy + sub-pets as a character card, upload
  to the Claudcade arena, and battle other players' cards — in the ASCII engine
  or the web build. *(next)*

## Stats model (battle-compatible from day one)

`pet.stats()` already derives claudemon-style stats so the character drops
straight into the existing battle engines:

```
max_hp = 20 + level*4 + vigor*5
attack = 5  + level   + power*2
type   = NORMAL                  # Phase 4: map top_model → FIRE/WATER/GRASS
```

## Phases

1. **Care toy (DONE):** real tokens feed him, he levels, the orange guy lives in
   a terminal with an animated XP bar, eating/sleep/cheer moods, and an upgrade
   menu. Hook makes it run while you work.
2. **Economy depth:** richer upgrade tree, cosmetics (crowns/auras by milestone),
   achievements surfaced in-view, daily/streak bonuses.
3. **Sub-pets:** unlock + manage companions on the claudemon Species model.
4. **Arena:** character-card export/import, load into `fight.py`, leaderboard
   upload via `claudcade_scores`, then the web single-player on `claudcade-site`.

## Ideas parked for later

- `top_model` (Opus/Sonnet/Haiku) tints his color / sets his battle type.
- Cache-efficiency from `analyzer.py` → a "well-fed vs junk-food" health stat.
- Prestige/rebirth at max level: hatch a fresh egg, keep a permanent badge.
