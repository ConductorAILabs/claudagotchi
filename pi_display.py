#!/usr/bin/env python3
"""Token ticker — monospace, usage bars, Battlefield dark aesthetic."""

import os, sys, json, time, threading, math, re

sys.path.insert(0, "/home/pi/chromasound")
os.environ["SDL_VIDEODRIVER"] = "offscreen"
os.environ["LG_WD"] = "/tmp"

import pygame
from flask import Flask, request, jsonify

app = Flask(__name__)

_lock        = threading.Lock()
_state       = None
_dirty       = threading.Event()
_dirty.set()

W, H  = 480, 320
SPLIT = 300   # vertical divider: left panel=0..300, right panel=300..480

# ── palette ───────────────────────────────────────────────────────────────────
BG    = (  3,   5,   9)
LINE  = ( 18,  26,  40)
AMBER = (255, 155,   0)
BLUE  = ( 40, 160, 255)
GREEN = (  0, 210, 100)
RED   = (220,  45,  45)
WHITE = (235, 240, 245)
DIM   = ( 55,  70,  90)
PANEL = ( 10,  14,  22)

MONO_BOLD = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"
MONO_REG  = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"

SESSION_LIMIT = 1_930_000   # calibrated: 521K input+output = 27% → ~1.93M
WEEK_LIMIT    = 13_000_000  # calibrated: 521K week tokens = 4% → ~13M

def good(pct):
    if pct <= 50: return GREEN
    if pct <= 75: return AMBER
    return RED

def _model_abbrev(raw):
    raw = (raw or "").lower()
    if "opus"   in raw: name = "OPUS"
    elif "sonnet" in raw: name = "SONNET"
    elif "haiku"  in raw: name = "HAIKU"
    else: return raw.upper()[:10] if raw else "--"
    m = re.search(r'(\d+)[.\-](\d+)', raw)
    return f"{name} {m.group(1)}.{m.group(2)}" if m else name

@app.route("/update", methods=["POST"])
def update():
    with _lock:
        global _state
        _state = request.get_json(force=True, silent=True) or {}
    _dirty.set()
    return jsonify({"ok": True})

@app.route("/health")
def health():
    with _lock:
        return jsonify(_state or {"status": "waiting"})

def tk(n):
    if n >= 1_000_000: return f"{n/1e6:.2f}M"
    if n >= 1_000:     return f"{n/1000:.1f}K"
    return str(n)

# ── draw helpers ──────────────────────────────────────────────────────────────
def blit_c(surf, font, text, color, cx, cy):
    s = font.render(str(text), True, color)
    surf.blit(s, (cx - s.get_width()//2, cy - s.get_height()//2))

def blit_l(surf, font, text, color, x, cy):
    s = font.render(str(text), True, color)
    surf.blit(s, (x, cy - s.get_height()//2))

def blit_r(surf, font, text, color, x, cy):
    s = font.render(str(text), True, color)
    surf.blit(s, (x - s.get_width(), cy - s.get_height()//2))

def hline(surf, y, x0=0, x1=W, col=LINE, thick=1):
    pygame.draw.rect(surf, col, (x0, y, x1-x0, thick))

def usage_bar(surf, x, y, w, h, pct, color):
    pygame.draw.rect(surf, LINE, (x, y, w, h))
    fw = max(0, int(w * min(pct/100, 1.0)))
    if fw > 0:
        pygame.draw.rect(surf, color, (x, y, fw, h))
    for tick_pct in [25, 50, 75]:
        tx = x + int(w * tick_pct/100)
        pygame.draw.rect(surf, BG, (tx, y, 1, h))

# ── main render ───────────────────────────────────────────────────────────────
def draw(surf, fonts, state):
    surf.fill(BG)

    ce        = float(state.get("cache_eff",   0.0))
    session   = state.get("session", {})
    weekly    = state.get("weekly",  {})

    sess_in   = int(session.get("input",      0))
    sess_out  = int(session.get("output",     0))
    sess_cr   = int(session.get("cache_read", 0))
    sess_tok  = sess_in + sess_out
    reqs      = int(session.get("requests",   0))
    hour_tok  = int(state.get("hour_tokens",  0))
    top_model = state.get("top_model", "")

    week_tok = int(weekly.get("week_tokens", 0)) if weekly else 0
    if week_tok < sess_tok:
        week_tok = sess_tok

    sess_pct = min(sess_tok / SESSION_LIMIT * 100, 100)
    week_pct = min(week_tok / WEEK_LIMIT   * 100, 100)
    sc = good(sess_pct)
    wc = good(week_pct)
    model_short = _model_abbrev(top_model)

    # ── HEADER ────────────────────────────────────────────────────────────────
    pygame.draw.rect(surf, PANEL, (0, 0, W, 26))
    hline(surf, 26, col=AMBER)
    blit_l(surf, fonts["xs"], "CLAUDE CODE // TOKEN MONITOR", DIM, 8, 13)
    blit_r(surf, fonts["xs"], time.strftime("%H:%M"), DIM, W-8, 13)

    # ── VERTICAL DIVIDER ──────────────────────────────────────────────────────
    pygame.draw.rect(surf, LINE, (SPLIT, 27, 1, H - 27))

    # ── LEFT: BIG TOTAL ───────────────────────────────────────────────────────
    blit_l(surf, fonts["big"], f"{sess_tok:,}", WHITE, 8, 76)
    blit_l(surf, fonts["xs"],  "TOKENS THIS SESSION", DIM, 8, 110)

    hline(surf, 122, x1=SPLIT)

    # ── LAST HOUR ─────────────────────────────────────────────────────────────
    blit_l(surf, fonts["xs"], "LAST HOUR", DIM, 8, 136)
    blit_r(surf, fonts["md"], tk(hour_tok), AMBER, SPLIT - 8, 136)

    hline(surf, 153, x1=SPLIT)

    # ── USAGE BARS ────────────────────────────────────────────────────────────
    BW = SPLIT - 78
    BX = 8

    by = 170
    blit_l(surf, fonts["xs"], "SESSION", DIM, BX, by - 6)
    usage_bar(surf, BX, by + 4, BW, 12, sess_pct, sc)
    blit_r(surf, fonts["xs"], f"{sess_pct:.0f}%", sc, SPLIT - 8, by + 10)

    by2 = by + 32
    blit_l(surf, fonts["xs"], "WEEK", DIM, BX, by2 - 6)
    usage_bar(surf, BX, by2 + 4, BW, 12, week_pct, wc)
    blit_r(surf, fonts["xs"], f"{week_pct:.1f}%", wc, SPLIT - 8, by2 + 10)

    hline(surf, by2 + 26, x1=SPLIT)

    by3 = by2 + 40
    blit_l(surf, fonts["xs"], "CACHE HIT", DIM, BX, by3 - 6)
    usage_bar(surf, BX, by3 + 4, BW, 12, ce, GREEN)
    blit_r(surf, fonts["xs"], f"{ce:.0f}%", GREEN, SPLIT - 8, by3 + 10)

    # ── RIGHT PANEL: IN / OUT / MODEL / REQS ─────────────────────────────────
    rcx     = (SPLIT + W) // 2   # = 390
    block_h = (H - 26) // 4      # = 73

    right_stats = [
        ("IN",    tk(sess_in),    BLUE ),
        ("OUT",   tk(sess_out),   WHITE),
        ("MODEL", model_short,    AMBER),
        ("REQS",  str(reqs),      DIM  ),
    ]
    for i, (label, val, col) in enumerate(right_stats):
        cy = 26 + i * block_h + block_h // 2
        blit_c(surf, fonts["md"], val,   col, rcx, cy - 8)
        blit_c(surf, fonts["xs"], label, DIM, rcx, cy + 16)
        if i < len(right_stats) - 1:
            hline(surf, 26 + (i + 1) * block_h, x0=SPLIT + 1, col=LINE)


def draw_splash(surf, fonts):
    surf.fill(BG)
    t = time.monotonic()
    p = int(abs(math.sin(t * 1.5)) * 140 + 80)
    hline(surf, H//2 - 50, col=(p, p//2, 0))
    hline(surf, H//2 + 50, col=(p, p//2, 0))
    blit_c(surf, fonts["big"], "-- : -- : --", (p, p, p), W//2, H//2 - 14)
    blit_c(surf, fonts["xs"],  "AWAITING FIRST RESPONSE", DIM, W//2, H//2 + 22)


def display_loop():
    pygame.init(); pygame.font.init()
    screen = pygame.display.set_mode((W, H))

    def F(path, size):
        try:    return pygame.font.Font(path, size)
        except: return pygame.font.Font(None, size)

    fonts = {
        "big": F(MONO_BOLD, 48),
        "md" : F(MONO_BOLD, 28),
        "sm" : F(MONO_REG,  18),
        "xs" : F(MONO_REG,  15),
    }

    try:
        import RPi.GPIO as GPIO; from spi_display import ILI9488; import config
        GPIO.setmode(GPIO.BCM); GPIO.setwarnings(False)
        hw = ILI9488(screen_id=config.SCREEN_LEFT)
        print("[display] ILI9488 ok", flush=True)
    except Exception as e:
        hw = None
        print(f"[display] headless: {e}", flush=True)

    while True:
        _dirty.wait(timeout=0.5)
        _dirty.clear()

        with _lock:
            state = _state

        if state is None:
            draw_splash(screen, fonts)
        else:
            draw(screen, fonts, state)

        pygame.display.flip()
        if hw:
            try:
                hw.blit(screen)
            except Exception as e:
                print(f"[blit] err: {e}", flush=True)


if __name__ == "__main__":
    threading.Thread(target=display_loop, daemon=True).start()
    print("[server] Token display on 0.0.0.0:5052", flush=True)
    app.run(host="0.0.0.0", port=5052, threaded=True, debug=False)
