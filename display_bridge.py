#!/usr/bin/env python3
"""USB display bridge.

Holds /dev/cu.usbmodem1101 open continuously and pushes JSON updates whenever
/tmp/claude_token_state.json changes. Eliminates the open/close cycle that
was tripping the ESP32-S3 board when many hooks fired at once.

Run:  python3 display_bridge.py &
"""

import json, os, sys, time, subprocess

DEV       = "/dev/cu.usbmodem101"
STATE     = "/tmp/claude_token_state.json"
POLL_MS   = 100


def configure_line():
    subprocess.run(
        ["stty", "-f", DEV, "115200", "raw", "-echo", "-hupcl"],
        check=False, stderr=subprocess.DEVNULL,
    )


def push_line(payload_bytes):
    """Write to the device via shell redirect — same path that works manually.
    Catches timeout/error so a single hung write doesn't kill the bridge."""
    try:
        proc = subprocess.run(
            ["sh", "-c", f"cat > {DEV}"],
            input=payload_bytes,
            timeout=2.0,
            check=False,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[bridge] push error ({type(e).__name__}) — will retry", flush=True)
        return False


COOLDOWN_MS    = 1200   # min gap between USB pushes — bunches rapid proxy
                        # ticks so 5 fast +40s collapse into one +200 visible
                        # tween rather than a flurry of small animations.
HEARTBEAT_MS   = 5000   # force re-push of current state even if unchanged


def main():
    print(f"[bridge] watching {STATE} -> {DEV}", flush=True)
    last_mtime    = 0.0
    last_payload  = None
    pending_line  = None
    last_push_ms  = 0.0
    line_configured = False

    while True:
        if not os.path.exists(DEV):
            line_configured = False
            time.sleep(0.5)
            continue
        if not line_configured:
            configure_line()
            line_configured = True
            print(f"[bridge] line configured on {DEV}", flush=True)

        try:
            mtime = os.path.getmtime(STATE)
        except OSError:
            time.sleep(POLL_MS / 1000.0)
            continue

        # Re-read if state file changed; keep latest content as pending
        if mtime != last_mtime:
            last_mtime = mtime
            try:
                with open(STATE) as f:
                    data = json.load(f)
                # Slim payload — only what the firmware reads, keeps it small
                # enough to fit in one USB-CDC frame on the ESP32-S3.
                sess = data.get("session", {}) or {}
                live = data.get("live",   {}) or {}
                # Minimal slim — only fields the firmware renders.
                # Stay well under the USB-CDC buffer threshold (~280 bytes
                # is the empirical danger zone) so the JSON line never
                # truncates mid-parse on the chip side.
                # +1 calibration so screen matches /usage; statusline's
                # rate_limits snapshot is one tick behind /usage's fetch.
                _spct = float(data.get("session_pct", -1.0))
                _wpct = float(data.get("week_pct",    -1.0))
                if _spct >= 0: _spct = min(100.0, _spct + 1.0)
                if _wpct >= 0: _wpct = min(100.0, _wpct + 1.0)
                slim = {
                    "session": {
                        "input":  int(sess.get("input", 0)),
                        "output": int(sess.get("output", 0)),
                    },
                    "session_pct": _spct,
                    "week_pct":    _wpct,
                    "live": {
                        "output":      int(live.get("output", 0)),
                        "cache_write": int(live.get("cache_write", 0)),
                    },
                }
                pending_line = "\n" + json.dumps(slim, separators=(",", ":")) + "\n"
            except (json.JSONDecodeError, OSError) as e:
                print(f"[bridge] read err: {e}", flush=True)

        # Push if (a) content changed AND cooldown elapsed, OR
        # (b) heartbeat elapsed since last push (re-sync chip after USB drops).
        now_ms = time.monotonic() * 1000.0
        content_changed = pending_line and pending_line != last_payload
        cooldown_ok     = (now_ms - last_push_ms) >= COOLDOWN_MS
        heartbeat_due   = (pending_line
                           and (now_ms - last_push_ms) >= HEARTBEAT_MS)

        if pending_line and ((content_changed and cooldown_ok) or heartbeat_due):
            if push_line(pending_line.encode()):
                tag = "tick" if content_changed else "beat"
                last_payload = pending_line
                last_push_ms = now_ms
                print(f"[bridge] {tag} {len(pending_line)} bytes", flush=True)
            else:
                print(f"[bridge] push failed — reconfiguring", flush=True)
                line_configured = False

        time.sleep(POLL_MS / 1000.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[bridge] stopped")
