# Claudagotchi on the round screen

Hardware: **Elecrow CrowPanel 1.28″ HMI ESP32‑S3 Rotary Display** — an
ESP32‑S3R8 (8 MB PSRAM, 16 MB flash) with a round **GC9A01 240×240** IPS panel,
**CST816** capacitive touch, a **rotary knob** (turn + press), and a **5× WS2812**
RGB strip. It enumerates over native USB (no buttons needed to flash).

The board only renders. The Mac brain (`pet.py`) owns all state; `pet_bridge.py`
streams it over USB and applies the knob/touch commands the device sends back:

```
real tokens ─▶ hook_feed.sh ─▶ pet.py (state) ─▶ pet_bridge.py ─USB─▶ round screen
                                          ▲                              │
                                          └─ CMD FEED/TRAIN/PET/SKIN/QUEST ◀┘
```

## Pin map (already wired on the board; set in the .ino CONFIG block)

`BOARD_CROWPANEL_S3` in `firmware/claudagotchi_round/claudagotchi_round.ino`:

| Function | GPIO |
|---|---|
| GC9A01 SCK | 10 |
| GC9A01 MOSI | 11 |
| GC9A01 DC | 3 |
| GC9A01 CS | 9 |
| GC9A01 RST | 14 |
| Backlight (BL) | 46 |
| **LCD power enable** | **1** (must be HIGH or the screen stays dark) |
| Touch I2C SDA / SCL | 6 / 7  (CST816 @ `0x15`) |
| Knob A / B / press | 45 / 42 / 41 |
| WS2812 strip data | 48 (5 LEDs) |

(Pin blocks for a bare XIAO‑S3/C3 + GC9A01 module are also in the CONFIG section
if you ever drive a different board — flip the `BOARD_*` defines.)

## Toolchain

```bash
brew install arduino-cli
arduino-cli core install esp32:esp32
arduino-cli lib install "GFX Library for Arduino" "ArduinoJson" "Adafruit NeoPixel"
pip3 install pyserial
```

## Build & flash

```bash
cd firmware
PORT=$(ls /dev/cu.usbmodem* | head -1)
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --build-path ./build_round ./claudagotchi_round
arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3 -p "$PORT" --input-dir ./build_round ./claudagotchi_round
```

(The `XIAO_ESP32S3` fqbn just gives a working native‑USB CDC config; the actual
pins come from the .ino. Flashing needs no buttons — native USB.) You should see
`CLAUDAGOTCHI / waiting for Mac…` on the circle until the bridge connects.

## Run it

```bash
cd ..                 # repo root
python3 install_hook.py     # feed him from real Claude Code usage (new session to take effect)
python3 pet_bridge.py       # stream state to the screen + handle knob/touch (auto-detects the port)
```

`pet_bridge.py` auto-detects the `usbmodem` port (the S3 re-enumerates under
slightly different names), survives USB unplugs, and reconnects on its own.

## Controls & LEDs

See the **Controls** and **LEDs** sections in [README.md](README.md) — knob =
4‑screen carousel (HOME/ACTIONS/STYLE/QUEST), touch drives the menus, and the
strip shows XP/level/quest activity in the active skin color.

## First-flash / tuning notes

- **Screen stuck on "waiting for Mac…"** — it's not getting a parseable payload.
  The firmware sets `Serial.setRxBufferSize(2048)` so large state frames aren't
  truncated; if you add many fields to the wire payload keep it well under that.
- **Colors swapped (blue Clawd)** — the panel wants BGR; flip the color order in
  the `Arduino_GC9A01(...)` init.
- **Knob scrubs backwards** — swap `PIN_ENC_A` / `PIN_ENC_B`. (The knob is read on
  hardware interrupts, so it shouldn't miss detents.)
- **LED color looks off** — colors are gamma‑corrected from the exact 24‑bit skin
  color; if your strip is RGB (not GRB) change `NEO_GRB` in the strip init.
- **Touch taps land on the wrong item** — the touch X/Y is likely rotated vs the
  display; adjust the mapping in `onTouch` / `pollTouch`.
