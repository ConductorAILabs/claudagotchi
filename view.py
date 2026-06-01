#!/usr/bin/env python3
"""Claudagotchi — live terminal viewer.

Renders the orange Claude block-guy and watches ~/.claude/claudagotchi.json.
As your real token usage feeds him (via the hook running `pet.py tick`), the
XP bar climbs, he chews, and he levels up. When you stop, he dozes off.

Run:
    python3 view.py

Keys:
    G        give food (demo: feed a random meal, so you can see it without
             waiting for real token usage)
    U        open / close the upgrade menu (spend banked XP)
    ↑/↓ Enter navigate / buy in the upgrade menu
    ESC / Q  quit
"""

from __future__ import annotations

import curses
import os
import time

import pet
from claudcade_engine import (
    Engine, Scene, Renderer, Input, Particles,
    GOLD, YELLOW, WHITE, GREEN, MAGENTA, CYAN, NEUTRAL, SELECT,
    lerp,
)

# Custom Claude-orange pairs (256-color terminals); fall back to GOLD.
ORANGE      = 9
ORANGE_DIM  = 10
FPS         = 30


# ── sprite frames (the same guy, different faces) ────────────────────────────
BODY_TOP = "  ▟████████▙ "
BODY_BOT = "  ▜████████▛ "

FACES = {
    "happy":   ("  █ ◕  ◕ █  ", "  █   ◡   █  "),
    "content": ("  █ •  • █  ", "  █   ‿   █  "),
    "eating":  ("  █ ◕  ◕ █  ", "  █   ◯   █  "),
    "cheer":   ("  █ ^  ^ █  ", "  █   ◡   █  "),
    "sleepy":  ("  █ –  – █  ", "  █   ‿   █  "),
    "hibernating": ("  █ –  – █  ", "  █   ﹏   █  "),
}


def sprite_for(mood: str) -> list[str]:
    top, bot = FACES.get(mood, FACES["content"])
    return [BODY_TOP, top, bot, BODY_BOT, "   ▘▘  ▝▝   "]


def halo_for(mood: str, level: int, tick: int) -> str:
    """A sunburst halo above his head whose ray count grows with level."""
    if mood in ("sleepy", "hibernating"):
        return " z  z  Z "
    rays = min(9, 1 + level // 5)            # more rays as he levels
    twinkle = "✶✦" [(tick // 6) % 2]
    return (" " + (twinkle + " ") * rays).rstrip()


MOOD_LABEL = {
    "eating": "EATING",  "happy": "HAPPY", "content": "CONTENT",
    "sleepy": "SLEEPY",  "hibernating": "HIBERNATING",
}


def commas(n: int) -> str:
    return f"{n:,}"


class PetScene(Scene):
    def on_enter(self) -> None:
        # True Claude-orange if the terminal supports 256 colors.
        try:
            if curses.COLORS >= 256:
                curses.init_pair(ORANGE,     208, -1)   # #ff8700
                curses.init_pair(ORANGE_DIM, 130, -1)   # darker orange
                self.orange     = ORANGE
                self.orange_dim = ORANGE_DIM
            else:
                self.orange = self.orange_dim = GOLD
        except curses.error:
            self.orange = self.orange_dim = GOLD

        self.state      = pet.load()
        self.snap       = pet.snapshot(self.state)
        self.fx         = Particles()
        self.shown_frac = self.snap["xp_frac"]      # animated XP bar
        self.shown_lvl  = self.snap["level"]
        self.prev_tok   = self.state["total_tokens"]
        self.eat_until  = 0.0
        self.cheer_until = 0.0
        self.flash_msg  = ""
        self.flash_until = 0.0
        self.menu_open  = False
        self.menu_cur   = 0
        self.menu_keys  = list(pet.UPGRADES.keys())
        self._last_mtime = 0.0
        self._last_reload = 0.0

    # ── data refresh ──────────────────────────────────────────────────────────
    def _reload(self) -> None:
        """Re-read the state file if it changed on disk (the hook writes it)."""
        try:
            m = os.path.getmtime(pet.STATE_PATH)
        except OSError:
            return
        if m == self._last_mtime:
            return
        self._last_mtime = m
        self.state = pet.load()
        self._on_state_change()

    def _on_state_change(self) -> None:
        self.snap = pet.snapshot(self.state)
        tok = self.state["total_tokens"]
        if tok > self.prev_tok:
            self._trigger_eat(tok - self.prev_tok)
        if self.snap["level"] > self.shown_lvl:
            self.cheer_until = time.monotonic() + 2.5
            self._flash(f"LEVEL UP!  {self.snap['level']}")
        self.prev_tok = tok

    def _trigger_eat(self, amount: int) -> None:
        now = time.monotonic()
        self.eat_until = now + pet.EAT_WINDOW_S
        self._flash(f"+{commas(amount)} tokens")
        cx, cy = self.engine.W // 2, self.engine.H // 2
        for dx in (-6, 6, 0):
            self.fx.explode(cx + dx, cy - 2)

    def _flash(self, msg: str) -> None:
        self.flash_msg = msg
        self.flash_until = time.monotonic() + 2.2

    def _mood(self) -> str:
        now = time.monotonic()
        if now < self.cheer_until:
            return "cheer"
        if now < self.eat_until:
            return "eating"
        return pet.mood(self.state)

    # ── update ─────────────────────────────────────────────────────────────────
    def update(self, inp: Input, tick: int, dt: float) -> str | None:
        now = time.monotonic()

        # Periodic disk reload (the hook updates the file out-of-band).
        if now - self._last_reload > 0.25:
            self._last_reload = now
            self._reload()

        if inp.pressed(27) or inp.just_pressed(ord('q'), ord('Q')):
            if self.menu_open:
                self.menu_open = False
            else:
                return "quit"

        if self.menu_open:
            if inp.just_pressed(curses.KEY_UP, ord('w'), ord('W')):
                self.menu_cur = (self.menu_cur - 1) % len(self.menu_keys)
            if inp.just_pressed(curses.KEY_DOWN, ord('s'), ord('S')):
                self.menu_cur = (self.menu_cur + 1) % len(self.menu_keys)
            if inp.just_pressed(ord('\n'), 10, 13):
                key = self.menu_keys[self.menu_cur]
                ok, msg = pet.buy(self.state, key)
                if ok:
                    pet.save(self.state)
                    self._on_state_change()
                self._flash(("✓ " if ok else "✗ ") + msg)
            if inp.just_pressed(ord('u'), ord('U')):
                self.menu_open = False
        else:
            if inp.just_pressed(ord('u'), ord('U')):
                self.menu_open = True
                self.menu_cur = 0
            # Demo feed — give a random-ish meal so you can watch him eat.
            if inp.just_pressed(ord('g'), ord('G')):
                meal = 25_000 + (tick * 137) % 90_000
                pet.feed(self.state, meal)
                pet.save(self.state)
                self._on_state_change()

        # Animate the XP bar / level smoothly toward the real values.
        target = self.snap["level"] + self.snap["xp_frac"]
        shown  = self.shown_lvl + self.shown_frac
        shown  = lerp(shown, target, min(1.0, 6.0 * dt))
        self.shown_lvl  = int(shown)
        self.shown_frac = shown - self.shown_lvl

        self.fx.update()
        return None

    # ── draw ─────────────────────────────────────────────────────────────────────
    def draw(self, r: Renderer, tick: int) -> None:
        s = self.snap
        mood = self._mood()

        r.header(
            "CLAUDAGOTCHI",
            left=f"Lv {s['level']}",
            right=f"{commas(s['total_tokens'])} eaten",
            color=self.orange,
        )

        cx = r.W // 2
        sp = sprite_for(mood)
        sw = max(len(line) for line in sp)
        srow = 5
        scol = cx - sw // 2

        # halo / sunburst above him
        halo = halo_for(mood, s["level"], tick)
        body_color = self.orange_dim if mood == "hibernating" else self.orange
        r.center(srow - 1, halo, GOLD, bold=True)
        r.sprite(srow, scol, sp, body_color, bold=True)

        # name + mood
        r.center(srow + 6, f"« {s['name']} »", WHITE, bold=True)
        r.center(srow + 7, MOOD_LABEL.get(mood, mood.upper()),
                 GREEN if mood in ("happy", "eating", "cheer") else NEUTRAL)

        # XP bar
        bar_w = min(40, r.W - 16)
        bar_col = cx - bar_w // 2
        brow = srow + 9
        r.text(brow, bar_col - 6, "XP", YELLOW, bold=True)
        r.bar(brow, bar_col, self.shown_frac, 1.0, bar_w,
              fill_color=self.orange, empty_color=NEUTRAL)
        nxt = pet.xp_ceiling(s["level"]) - s["lifetime_xp"]
        r.center(brow + 1, f"{max(0, nxt)} XP to level {s['level'] + 1}", NEUTRAL, dim=True)

        # stats line
        st = s["stats"]
        statline = (f"HP {st['max_hp']}   ATK {st['attack']}   "
                    f"BANK {commas(s['banked_xp'])} XP   "
                    f"♦ {len(s['achievements'])}")
        r.center(brow + 3, statline, CYAN)

        # flash message (level up / +tokens)
        if time.monotonic() < self.flash_until:
            r.center(srow + 3, self.flash_msg, MAGENTA, bold=True)

        self.fx.draw(r)

        if self.menu_open:
            self._draw_menu(r)

        r.footer("G give food   U upgrades   ESC/Q quit", color=self.orange_dim)

    def _draw_menu(self, r: Renderer) -> None:
        w, h = 40, len(self.menu_keys) + 4
        col = (r.W - w) // 2
        row = (r.H - h) // 2
        r.box(row, col, h, w, self.orange, title="UPGRADES", double=True)
        r.text(row + 1, col + 2, f"Banked: {commas(self.snap['banked_xp'])} XP",
               YELLOW, bold=True)
        for i, key in enumerate(self.menu_keys):
            up   = pet.UPGRADES[key]
            rank = self.state["upgrades"].get(key, 0)
            cost = pet.upgrade_cost(self.state, key)
            sel  = (i == self.menu_cur)
            label = f"{up['label']} r{rank}  ({cost} XP)  {up['desc']}"
            r.text(row + 2 + i, col + 2, ("▸ " if sel else "  ") + label,
                   SELECT if sel else NEUTRAL, bold=sel)


def main() -> None:
    Engine("Claudagotchi", fps=FPS).scene("pet", PetScene()).run("pet")


if __name__ == "__main__":
    main()
