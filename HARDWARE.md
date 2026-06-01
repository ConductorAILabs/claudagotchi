# Claudagotchi on the round screen

Target: **Seeed XIAO ESP32-S3** (or C3) + **GC9A01 240×240 round IPS**,
capacitive touch (CST816S, I2C) + rotary encoder knob.

The device only *renders*. The Mac brain (`pet.py`) owns all state; the bridge
(`pet_bridge.py`) streams it over USB and applies the knob/touch commands the
device sends back.

```
real tokens ─▶ hook_feed.sh ─▶ pet.py (state) ─▶ pet_bridge.py ─USB─▶ round screen
                                          ▲                              │
                                          └────── CMD BUY / PET ◀────────┘
```

## 1. Wiring (XIAO S3 — pins set in the .ino CONFIG block)

GC9A01 is **SPI** (the picture). Touch is **I2C**. The knob is 2 GPIOs + a button.
GC9A01 is write-only, so the MISO pad (D9) is reused for the encoder.

| Screen pin | XIAO pad | GPIO (S3) | GPIO (C3) |
|---|---|---|---|
| SCK / CLK   | D8  | 7  | 8  |
| SDA / MOSI  | D10 | 9  | 10 |
| DC          | D3  | 4  | 5  |
| CS          | D1  | 2  | 3  |
| RST         | D0  | 1  | 2  |
| BL (backlight) | D2 | 3 | 4 |
| **Touch** SDA | D4 | 5 | 6 |
| **Touch** SCL | D5 | 6 | 7 |
| **Knob** A    | D9 | 8 | 9 |
| **Knob** B    | D6 | 43 | 21 |
| **Knob** SW (press) | D7 | 44 | 20 |
| VCC → 3V3, GND → GND | — | — | — |

> Switching to a C3? In `claudagotchi_round.ino` set `BOARD_XIAO_S3 0` and
> `BOARD_XIAO_C3 1`. If the panel has no touch or no knob, set `ENABLE_TOUCH 0`
> / `ENABLE_ENCODER 0` — the screen still works fed over USB.

## 2. Toolchain

```bash
brew install arduino-cli
arduino-cli core install esp32:esp32
arduino-cli lib install "GFX Library for Arduino" "ArduinoJson"
pip3 install pyserial
```

## 3. Build & flash

```bash
cd ~/Desktop/claudagachi/firmware
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 \
  --build-path ./build_round ./claudagotchi_round
arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3 \
  -p /dev/cu.usbmodem1101 --input-dir ./build_round ./claudagotchi_round
```

(For a C3 use `--fqbn esp32:esp32:XIAO_ESP32C3`.) You should see
`CLAUDAGOTCHI / waiting for Mac…` on the circle.

## 4. Run the bridge

```bash
cd ~/Desktop/claudagachi
python3 pet_bridge.py        # streams state + handles knob commands
```

He should pop in immediately. Use Claude Code normally (with the feeder hook
installed) and he eats your tokens live.

## 5. Using it

- **Turn the knob** — switch pages: HOME ↺ STATS ↺ UPGRADES.
- **Press the knob** — on UPGRADES, buy the selected upgrade (XP spent in the
  real brain); elsewhere it flips to the next page.
- **Tap the screen** — pet him on HOME (hearts); elsewhere advances the page.
- **Eat / level up** — food flash + chew on HOME; the rim arc fills; a rainbow
  sweep + cheer face on level-up.
- **Idle** — he dims and falls asleep (Zzz).

## Notes / first-flash gotchas

- **This firmware is v1 and has not been compiled on this machine** — flash it
  and we'll iterate. Most likely tweaks: the exact touch IC address
  (`CST816_ADDR`, try `0x15` then `0x2E`/`0x5A`) and encoder A/B direction.
- If colors look swapped (blue creature), the panel wants BGR — flip with an
  `Arduino_GC9A01(bus, RST, 0, true)` → add `, false, ...` BGR flag or invert in
  the driver init.
- On a C3, the 240×240 canvas (~115 KB) fits in SRAM; if it ever fails to
  allocate, drop the `Arduino_Canvas` and draw straight to `out` (some flicker).
