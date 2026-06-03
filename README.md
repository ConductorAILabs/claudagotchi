# 🟠 Claudagotchi

A Tamagotchi whose pet is the **Claude Code mascot (Clawd)** and whose food is
**your real Claude Code token usage**. Use Claude → he eats → he levels up. Spend
the XP to train his stats, feed him treats, dress him in unlocked colors, and send
him on quests. He never dies; if you stop using Claude he just naps.

It runs two ways:

- **On a round LCD** — an **Elecrow CrowPanel 1.28″ ESP32‑S3 Rotary** display
  (round GC9A01 + touch + a rotary knob + 5 RGB LEDs). This is the real toy. → see **[HARDWARE.md](HARDWARE.md)**
- **In your terminal** — `view.py`, no hardware needed.

```
real token usage ─▶ hook_feed.sh ─▶ pet.py (the brain) ─▶ pet_bridge.py ─USB─▶ round screen
                                          ▲                                       │
                                          └──────── CMD FEED/TRAIN/PET/SKIN/QUEST ◀┘
```

`pet.py` is the single source of truth (state in `~/.claude/claudagotchi.json`).
The screen is a pure renderer; the knob/touch send commands back to the brain.

---

## Run it on the Elecrow screen (the real thing)

Full wiring/pins/flashing are in **[HARDWARE.md](HARDWARE.md)**. Short version:

```bash
# one-time
arduino-cli core install esp32:esp32
arduino-cli lib install "GFX Library for Arduino" "ArduinoJson" "Adafruit NeoPixel"
pip3 install pyserial

# flash the firmware to the board
cd firmware
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --build-path ./build_round ./claudagotchi_round
arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3 -p $(ls /dev/cu.usbmodem*|head -1) \
  --input-dir ./build_round ./claudagotchi_round

# start the bridge (streams state to the screen, handles knob/touch)
cd .. && python3 pet_bridge.py
```

Then **feed him from real usage** (below) and he updates live.

### Controls
- **Turn the knob** — carousel through the 4 screens: **HOME · ACTIONS · STYLE · QUEST**.
- **HOME** — Clawd, his level + XP bar; tap him to pet (hearts).
- **ACTIONS** — tap a tile:
  - **FEED** — 4 foods; tap one for a treat. Repeating the same food is less
    effective (recovers over time), so vary it.
  - **TRAIN** — spend banked XP on Vigor (+HP) / Power (+ATK) / Guard (+DEF);
    each rank costs more.
  - **QUEST** — opens the quest screen.
  - **PET** — tap his head / back / belly for bonus XP; same spot = less, a full
    head→back→belly cycle = a **COMBO**.
  - every interior screen has a **↩ back** arrow and a **?** help button.
- **STYLE** — pick from 8 colors in a grid; locked ones show their unlock level.
- **QUEST** — choose one of 3 random time/XP offers and send him off. While away
  he earns the reward but eats no tokens.

### The 5 LEDs
- spinning **comet** in his color = gaining XP/tokens (works even on a quest)
- spinning **rainbow** = level up
- **all‑flash ×3** = quest departs / returns (then dark — no steady glow while away)
- otherwise **off**

---

## Feed him from real usage

Register the Claude Code hook so every tool call feeds him automatically:

```bash
python3 install_hook.py          # adds PostToolUse + Stop hooks
python3 install_hook.py --remove # to undo
```

Open a **new Claude Code session** and work normally — he eats as you go.
He's fed by **input + output tokens** (cache reads are excluded so the number
stays meaningful); 1,000 tokens = 1 XP.

---

## Run it in a terminal (no hardware)

```bash
./play.sh                  # or: python3 view.py   (needs a real terminal)
```
`G` give food · `U` upgrades · `↑/↓ Enter` navigate · `ESC/Q` quit.

## Brain CLI

```bash
python3 pet.py status      # current state as JSON
python3 pet.py tick        # feed from today's real token usage
python3 pet.py feed 50000  # manually feed N tokens
python3 pet.py spend power # spend banked XP on an upgrade
python3 pet.py reset       # start fresh at level 0
```

## Battles (local)

```bash
python3 battle.py export claude.card.json   # save your fighter (signed data card)
python3 battle.py fight a.card.json b.card.json   # deterministic duel
```
Cards are pure data; stats are re‑derived and validated, so a card can't inject
code or fake stats — see `battle.py` / `pet.validate_card`.

## Files

| File | Purpose |
|---|---|
| `pet.py` | the brain — tokens→XP, levels, foods, training, skins, quests, battle cards |
| `pet_bridge.py` | streams state to the screen over USB; applies knob/touch commands |
| `firmware/claudagotchi_round/` | ESP32‑S3 firmware for the round display |
| `hook_feed.sh` / `install_hook.py` | Claude Code hook that feeds him live |
| `view.py` / `claudcade_engine.py` | terminal viewer (engine vendored) |
| `battle.py` | local, deterministic battle engine + card export/validate |
| `HARDWARE.md` / `DESIGN.md` | hardware build guide / design notes |

`archive/` holds the original `token_display` project this was forked from —
reference only, not used by Claudagotchi.
