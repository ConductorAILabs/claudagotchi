# Token Display

Live Claude Code token-usage monitor on a Waveshare ESP32-S3-LCD-1.47, driven over USB-CDC from your Mac. Updates in real time — every tool call ticks the rings up; the moment you hit enter the total starts climbing.

```
  CONDUCTOR LABS TOKEN TRACKER

           2,153,847
         TOKENS  TODAY

         ◯ 27%      ◯ 9%
          5 HR       WEEK
```

Pink/purple/black, monospace, 1.2s ease-out tween between values. Onboard WS2812 LED (GPIO 38) does a rainbow spin during each tween, then goes dark.

Number shown = **today's tokens summed across every Claude Code session you've run**. Always climbs through the day; doesn't reset when you switch projects.

## What's where

| File | Purpose |
| --- | --- |
| `firmware/token_display/token_display.ino` | ESP32-S3 sketch — ST7789 driver, JSON parser, ring/number rendering, animation. |
| `firmware/token_display/FreeMono*.h` | Bundled Adafruit GFX font headers (so the sketch is self-contained). |
| `firmware/build/` | Compiled artifacts (gitignored — regenerated on each compile). |
| `firmware/backup/` | Partial stock-firmware backup: bootloader + 320 KB of app0. **Not enough for a full restore** — full FFAT contents weren't dumped. |
| `display_bridge.py` | macOS daemon — watches `/tmp/claude_token_state.json` and pushes to `/dev/cu.usbmodem1101`. Required. |
| `hook.sh` | Claude Code Stop + PostToolUse hook — sums usage from the session JSONL, runs the analyzer, atomically writes the state file. |
| `prompt_hook.sh` | UserPromptSubmit hook — bumps the last-known state by `prompt_len/4` tokens for an instant tick when you press enter. |
| `analyzer.py`, `weekly.py` | Analyzer + weekly aggregator (used by `hook.sh`). |
| `pi_display.py`, `service.sh`, `token-display.service` | Legacy Pi server — unused now, kept for reference. |

## First-time setup

### 1. Hardware

Waveshare ESP32-S3-LCD-1.47 (172×320 ST7789 over SPI, ESP32-S3 with 8 MB PSRAM / 16 MB flash).

Connect via USB-C. macOS should enumerate two ports:
- `/dev/cu.usbmodem101` — USB-JTAG (used by `esptool` for flashing)
- `/dev/cu.usbmodem1101` — USB-CDC (used by the firmware's `Serial`)

If only one shows up, unplug + replug; sometimes only the CDC enumerates after a reset.

### 2. Toolchain

```bash
brew install arduino-cli esptool
arduino-cli core install esp32:esp32
arduino-cli lib install "GFX Library for Arduino" "Adafruit GFX Library" "ArduinoJson"
```

### 3. Build & flash

```bash
cd firmware
arduino-cli compile \
  --fqbn 'esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi' \
  --build-path ./build ./token_display

arduino-cli upload \
  --fqbn 'esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi' \
  --port /dev/cu.usbmodem1101 \
  --input-dir ./build ./token_display
```

You should see the splash ("CLAUDE CODE / TOKEN MONITOR / AWAITING DATA") within a few seconds.

### 4. Start the bridge

```bash
nohup python3 /Users/jeffmiddleton/Desktop/token_display/display_bridge.py \
  > /tmp/display_bridge.log 2>&1 &
```

Confirm it opened the port:
```bash
tail -f /tmp/display_bridge.log
# expect: [bridge] line configured on /dev/cu.usbmodem1101
```

To survive reboots, install as a `launchd` agent (TODO — currently launched manually).

### 5. Register the Claude Code hooks

The hooks should already be in `~/.claude/settings.json`. If not:

```python
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".claude" / "settings.json"
s = json.loads(p.read_text())
hooks = s.setdefault("hooks", {})
ROOT = "/Users/jeffmiddleton/Desktop/token_display"

for ev, script in [
    ("Stop",             f"{ROOT}/hook.sh"),
    ("PostToolUse",      f"{ROOT}/hook.sh"),
    ("UserPromptSubmit", f"{ROOT}/prompt_hook.sh"),
]:
    lst = hooks.setdefault(ev, [])
    if not any(h.get("command") == script
               for entry in lst if isinstance(entry, dict)
               for h in entry.get("hooks", [])):
        lst.append({"matcher": "", "hooks": [{"type": "command", "command": script}]})

p.write_text(json.dumps(s, indent=2))
print("Done")
PY
```

Make sure both shell hooks are executable:
```bash
chmod +x hook.sh prompt_hook.sh
```

### 6. Test the flow

Send a fake payload through the same path the hooks use:

```bash
cat > /tmp/claude_token_state.json <<'EOF'
{"session":{"input":50000,"output":50000,"requests":3},"hour_tokens":1000,"cache_eff":80.0,"top_model":"claude-sonnet-4-6","weekly":{"week_tokens":250000}}
EOF
```

Within ~200 ms the screen should animate to **100,000** with rings filled in.

## Update flow (in real time)

| Event | Hook | What happens |
| --- | --- | --- |
| You hit enter on a prompt | `prompt_hook.sh` (UserPromptSubmit) | Reads `/tmp/claude_token_latest.json`, adds `prompt_len/4` to session input, writes `/tmp/claude_token_state.json`. |
| Claude calls a tool (Bash/Edit/etc.) | `hook.sh` (PostToolUse) | Re-reads the session JSONL, runs the analyzer, writes the state file. Fires multiple times per turn. |
| Claude finishes a turn | `hook.sh` (Stop) | Same as PostToolUse — final settle on real numbers. |
| State file changes | `display_bridge.py` | Polls `mtime` every 100 ms; on change, shells `cat > /dev/cu.usbmodem1101`. |
| Bytes arrive at firmware | `loop()` | Accumulates until `\n`, parses JSON, snapshots current values as `prev`, sets `tgt`, starts 1.2 s ease-out tween. |

## Troubleshooting

### Screen stuck on splash / not updating

1. **Bridge dead?** `pgrep -f display_bridge.py` — restart if missing.
2. **Device hung?** Reset it:
   ```bash
   esptool --port /dev/cu.usbmodem1101 --before default-reset --after hard-reset run
   ```
3. **Hook not firing?** Check timestamps on `/tmp/claude_hook_raw.json`, `/tmp/claude_prompt_raw.json`, `/tmp/claude_token_state.json` — should refresh on every prompt/turn.
4. **State file stale?** `cat /tmp/claude_token_state.json | python3 -m json.tool` — verify it's a sane payload.

### Wrong numbers / very low totals

`hook.sh` scans every `~/.claude/projects/*/*.jsonl` modified today and sums all tokens since local midnight (ISO-8601 timestamps parsed in line). If totals look low:
- Confirm Claude Code is writing JSONLs (`ls -la ~/.claude/projects/`).
- The total includes every session today across every project — switching projects doesn't reset it.

### Device won't enumerate as `usbmodem1101`

After flashing, only `usbmodem101` (USB-JTAG) sometimes shows. Unplug + replug for the CDC port to come back.

### "Pushed N bytes" in bridge log but screen doesn't change

Means the host-side write went out, but the chip dropped/lost it. Almost always: device is hung. Hard-reset (see above) and try again. The shell-redirect path (`cat > /dev/cu.usbmodem1101`) is the only write method that reliably reaches the chip on macOS — don't switch the bridge to `pyserial` or `os.write` on a held-open fd.

## Hardware pinout (reference)

ESP32-S3-LCD-1.47 → ST7789:
| Signal | GPIO |
| --- | --- |
| SCK  | 40 |
| MOSI | 45 |
| DC   | 41 |
| CS   | 42 |
| RST  | 39 |
| BL   | 48 |

Visible window inside the ST7789 frame: 172×320 starting at column offset 34. Sketch uses `rotation=3` for landscape (320×172).

## Color palette (RGB565, used in the sketch)

| Name | Hex | Use |
| --- | --- | --- |
| `C_BG` | `0x0000` | Black background |
| `C_PINK` | `0xF81F` | Big number / SESSION ring |
| `C_HOTPINK` | `0xF8D2` | WEEK ring |
| `C_PURPLE` | `0x801F` | CACHE ring |
| `C_DEEP` | `0x180A` | Ring track (very dark purple) |
| `C_LAVENDER` | `0xC59F` | Ring labels |
| `C_DIMPURPLE` | `0x4015` | Subtitle |
| `C_WHITE` | `0xFFFF` | Percentages inside rings |
