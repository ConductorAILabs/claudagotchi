#!/usr/bin/env python3
"""Claudagotchi USB bridge (bidirectional).

Streams the pet's state to the round-screen firmware whenever it changes, and
listens for "CMD ..." lines the knob/touch send back (BUY <upgrade>, PET) so the
real brain (pet.py) stays the source of truth.

    python3 pet_bridge.py            # uses /dev/cu.usbmodem1101

Needs pyserial:  pip3 install pyserial
Keep this running alongside the feeder hook (install_hook.py). The hook feeds
him from real tokens; this bridge mirrors his state to the screen.
"""

from __future__ import annotations

import glob
import json
import sys
import time

import pet

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial missing — run:  pip3 install pyserial")

DEV_HINT = sys.argv[1] if len(sys.argv) > 1 else ""
BAUD     = 115200
POLL_S   = 0.10
PUSH_MIN = 0.20      # min seconds between pushes (bunches rapid ticks)
HEARTBEAT_S = 4.0    # re-push even if unchanged, to re-sync after USB hiccups
import os

def resolve_port() -> str | None:
    """Find the device port — the S3 re-enumerates under slightly different
    names (usbmodem1101 / usbmodem11101 / ...), so auto-detect each time."""
    if DEV_HINT and os.path.exists(DEV_HINT):
        return DEV_HINT
    cands = sorted(glob.glob("/dev/cu.usbmodem*"))
    return cands[0] if cands else None


def slim(state: dict) -> dict:
    """Short-key payload the firmware parses. Keep it well under one CDC frame."""
    snap = pet.snapshot(state)
    st   = snap["stats"]
    sk   = snap["skin"]
    q    = snap["quest"]
    return {
        "n":   snap["name"][:12],
        "lv":  snap["level"],
        "xf":  round(snap["xp_frac"], 3),
        "tok": snap["total_tokens"],
        "bk":  snap["banked_xp"],
        "hp":  st["max_hp"],
        "atk": st["attack"],
        "df":  st["defense"],
        "sp":  st["speed"],
        "ac":  len(snap["achievements"]),
        "tr":  snap["train_ranks"],   # 6 training ranks (firmware computes costs)
        "ff":  snap["food_fresh"],    # 6 food freshness 0..9
        # cosmetics + quest
        "sb":  sk["body"],          # body color (RGB565)
        "sa":  sk["accent"],        # accent color
        "sr":  1 if sk["rainbow"] else 0,
        "sn":  sk["name"][:10],     # skin name
        "si":  sk["idx"],           # current skin index
        "su":  sk["unlocked"],
        "at":  snap["anim_tier"],
        "qa":  1 if q["active"] else 0,
        "qr":  q["remaining"],      # seconds left
        "qn":  q["name"][:12],
        "qw":  q["reward"],
    }


def handle_cmd(line: str) -> bool:
    """Apply a device command. Returns True if state changed."""
    parts = line.strip().split()
    if len(parts) < 1 or parts[0] != "CMD":
        return False
    if len(parts) >= 3 and parts[1] == "BUY":
        state = pet.load()
        ok, msg = pet.buy(state, parts[2])
        if ok:
            pet.save(state)
        print(f"[bridge] BUY {parts[2]}: {'ok' if ok else 'no'} ({msg})", flush=True)
        return ok
    if len(parts) >= 2 and parts[1] == "PET":
        if len(parts) >= 3 and parts[2].isdigit():   # PET <spot> = XP petting
            state = pet.load()
            ok, msg = pet.pet_spot(state, int(parts[2]))
            if ok:
                pet.save(state)
            print(f"[bridge] PET {msg}", flush=True)
            return ok
        print("[bridge] petted <3", flush=True)       # bare PET = affection
        return False
    if len(parts) >= 2 and parts[1] == "SKIN":
        state = pet.load()
        if len(parts) >= 3 and parts[2].isdigit():
            ok, msg = pet.set_skin(state, int(parts[2]))
        else:
            ok, msg = pet.cycle_skin(state)
        if ok:
            pet.save(state)
        print(f"[bridge] SKIN -> {msg}", flush=True)
        return ok
    if len(parts) >= 2 and parts[1] == "QUEST":
        state = pet.load()
        ok, msg = pet.start_quest(state)
        if ok:
            pet.save(state)
        print(f"[bridge] QUEST: {msg}", flush=True)
        return ok
    if len(parts) >= 2 and parts[1] == "FEED":
        state = pet.load()
        if len(parts) >= 3 and parts[2].isdigit():
            ok, msg = pet.feed_food(state, int(parts[2]))
        else:
            ok, msg = pet.snack(state)
        if ok:
            pet.save(state)
        print(f"[bridge] FEED: {msg}", flush=True)
        return ok
    return False


def main() -> None:
    print(f"[bridge] auto-detecting usbmodem port @ {BAUD}", flush=True)
    while True:
        dev = resolve_port()
        if not dev:
            time.sleep(1.0)
            continue
        try:
            ser = serial.Serial(dev, BAUD, timeout=0)
        except (serial.SerialException, OSError) as e:
            print(f"[bridge] open {dev} failed: {e} — retrying", flush=True)
            time.sleep(1.0)
            continue

        last_payload = None
        last_push = 0.0
        rx = ""
        print(f"[bridge] connected on {dev}", flush=True)
        try:
            while True:
                # read commands coming back from the device
                try:
                    chunk = ser.read(256)
                except (serial.SerialException, OSError):
                    break
                if chunk:
                    rx += chunk.decode("utf-8", "replace")
                    while "\n" in rx:
                        line, rx = rx.split("\n", 1)
                        if line.startswith("CMD"):
                            handle_cmd(line)

                # push current state on change or heartbeat
                state = pet.load()
                payload = json.dumps(slim(state), separators=(",", ":"))
                now = time.monotonic()
                changed = payload != last_payload
                if (changed and now - last_push >= PUSH_MIN) or \
                   (now - last_push >= HEARTBEAT_S):
                    try:
                        ser.write(("\n" + payload + "\n").encode())
                        ser.flush()
                    except (serial.SerialException, OSError):
                        break
                    last_payload = payload
                    last_push = now
                    if changed:
                        print(f"[bridge] -> {payload[:80]}", flush=True)

                time.sleep(POLL_S)
        finally:
            try:
                ser.close()
            except Exception:
                pass
        print("[bridge] disconnected — reopening", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[bridge] stopped")
