# 🟠 Claudagotchi

A Tamagotchi where the pet is the **Claude icon guy** (orange block art) and his
food is **your real Claude Code token usage**. Burn tokens → he eats → he levels
up. Spend XP to upgrade him; later, take him into the Claudcade arena to battle.

He never dies — if you stop using Claude he just hibernates until you're back.

> Built on top of the `token_display` token pipeline and the `claudcade_engine`
> terminal game engine. See **DESIGN.md** for the full roadmap.
> The original token-display README is preserved as `README_token_display.md`.

## Play it now

```bash
cd ~/Desktop/claudagachi
./play.sh            # or: python3 view.py   (needs a real terminal)
```

In the viewer:

| Key | Action |
|---|---|
| **G** | give food (demo feed — watch him eat without waiting for real tokens) |
| **U** | open/close the upgrade menu (spend banked XP) |
| ↑/↓ Enter | navigate / buy upgrades |
| **ESC / Q** | quit |

## Feed him from real usage

Register the feeder hook so he eats automatically as you use Claude Code:

```bash
python3 install_hook.py          # adds PostToolUse + Stop hooks
# later, to remove:
python3 install_hook.py --remove
```

Then open a new Claude Code session and work normally — every tool call feeds him.

## The brain (CLI)

```bash
python3 pet.py status       # show his current state
python3 pet.py tick         # feed from today's real token usage
python3 pet.py feed 50000   # manually feed N tokens
python3 pet.py spend power  # buy an upgrade with banked XP
python3 pet.py reset        # hatch a fresh egg
```

State lives in `~/.claude/claudagotchi.json`.

## Files

| File | Purpose |
|---|---|
| `pet.py` | game brain — tokens → XP, levels, upgrades, save (pure logic, no UI) |
| `view.py` | live terminal viewer of the orange Claude guy |
| `claudcade_engine.py` | the terminal game engine (vendored) |
| `hook_feed.sh` | Claude Code hook → runs `pet.py tick` |
| `install_hook.py` | register/remove the feeder hook in settings.json |
| `play.sh` | launch the viewer |
| `DESIGN.md` | full design + roadmap (sub-pets, arena, web) |

The `firmware/`, `display_bridge.py`, `analyzer.py`, etc. are inherited from the
original `token_display` project — kept for reference / a possible hardware build.
