"""
Claudcade Engine v2
───────────────────
A terminal game engine built on Python's curses library.
No dependencies beyond the standard library. macOS, Linux, WSL.

Quick start
-----------
    from claudcade_engine import Engine, Scene, Input, Renderer
    from claudcade_engine import PLAYER, ENEMY, GOOD, GOLD, NEUTRAL

    class Game(Scene):
        def on_enter(self):
            self.score = 0

        def update(self, inp: Input, tick: int, dt: float) -> str | None:
            if inp.pause:
                return "quit"
            if inp.just_pressed(ord('f')):   # single-frame fire
                self.score += 1

        def draw(self, r: Renderer, tick: int):
            r.header("MY GAME", left=f"SCORE {self.score}")
            r.center(r.H // 2, "Hello terminal!", PLAYER, bold=True)

    Engine("My Game", fps=30).scene("game", Game()).run("game")

Color constants
---------------
Semantic aliases (preferred):
    PLAYER ENEMY GOOD GOLD NEUTRAL SPECIAL SELECT WATER

Raw color names (same values):
    CYAN RED GREEN YELLOW WHITE MAGENTA HIGHLIGHT BLUE

Drawing coordinates
-------------------
Renderer methods take (row, col) — row 0 is the TOP of the terminal.
Entity.x / Entity.y are (col, row) in screen space.
Use r.at(entity, sprite) to avoid manual conversion.

Delta time
----------
update() receives dt (seconds since last frame, typically 0.033 at 30 fps).
Write all movement as:  entity.x += speed * dt * 30   (30 = "at 30fps" reference)
"""

from __future__ import annotations

import curses
import enum
import json
import math
import random
import time
from pathlib import Path
from typing import Callable, Literal, TypedDict

# ── TypedDicts ─────────────────────────────────────────────────────────────────

class StarDict(TypedDict):
    x:   float
    y:   float
    spd: float
    ch:  str
    cp:  int


class _ParticleDict(TypedDict):
    x:        float
    y:        float
    vx:       float
    vy:       float
    life:     int
    max_life: int
    char:     str
    color:    int


class BulletDict(TypedDict, total=False):
    wx:    float
    y:     float
    vx:    float
    owner: str
    dead:  bool


class PlatformDict(TypedDict):
    x: float
    y: float
    w: float


class ParticleDict(TypedDict):
    x:    float
    y:    float
    life: int
    type: str


class _FadeState(TypedDict):
    to:     str
    ticks:  int
    t:      int
    phase:  Literal['out', 'in']


# ── Color constants ────────────────────────────────────────────────────────────

class Color(enum.IntEnum):
    """Color-pair indices — match setup_colors() init order. Members are also
    accessible by semantic name (PLAYER == CYAN, ENEMY == RED, etc.)."""
    CYAN      = 1
    RED       = 2
    GREEN     = 3
    YELLOW    = 4
    WHITE     = 5
    MAGENTA   = 6
    HIGHLIGHT = 7
    BLUE      = 8
    # Semantic aliases (same int values as the names above)
    PLAYER    = 1
    ENEMY     = 2
    GOOD      = 3
    GOLD      = 4
    NEUTRAL   = 5
    SPECIAL   = 6
    SELECT    = 7
    WATER     = 8

# Module-level shortcuts so callers can write CYAN, ENEMY, etc.
# IntEnum members are ints; existing `color: int` signatures still accept these.
CYAN      = Color.CYAN
RED       = Color.RED
GREEN     = Color.GREEN
YELLOW    = Color.YELLOW
WHITE     = Color.WHITE
MAGENTA   = Color.MAGENTA
HIGHLIGHT = Color.HIGHLIGHT
BLUE      = Color.BLUE
PLAYER    = Color.PLAYER
ENEMY     = Color.ENEMY
GOOD      = Color.GOOD
GOLD      = Color.GOLD
NEUTRAL   = Color.NEUTRAL
SPECIAL   = Color.SPECIAL
SELECT    = Color.SELECT
WATER     = Color.WATER


# ── Input ─────────────────────────────────────────────────────────────────────

class Input:
    """
    Keyboard and mouse state for the current frame.

    Common aliases:
        inp.up / down / left / right   WASD or arrow keys
        inp.fire                        J, F, or mouse click
        inp.jump                        SPACE
        inp.confirm                     ENTER or SPACE
        inp.pause                       ESC

    Single-frame detection:
        inp.just_pressed(ord('j'))      True only on first frame key is down
        inp.just_released(ord('j'))     True only on first frame key is up

    Raw check:
        inp.pressed(ord('x'))           True every frame key is held

    Mouse:
        inp.mouse_click                 True on left-button press/click
        inp.mouse_pressed               set of buttons pressed this frame
                                          (1=left, 2=middle, 3=right, 4=scroll)
        inp.mouse_row, inp.mouse_col    last reported mouse position (or -1)
    """

    # Class-level state — Input owns its own previous-frame snapshot. _poll()
    # captures and rotates it; callers don't manage it directly.
    _last: Input | None = None

    def __init__(self) -> None:
        self.keys:          set[int] = set()
        self.mouse_pressed: set[int] = set()
        self.mouse_row:     int      = -1
        self.mouse_col:     int      = -1
        self._prev:         Input | None = None

    @property
    def mouse_click(self) -> bool:
        """True if the left mouse button was pressed this frame."""
        return 1 in self.mouse_pressed

    def pressed(self, *keys: int) -> bool:
        """True every frame any of the given keys are held."""
        return any(k in self.keys for k in keys)

    def just_pressed(self, *keys: int) -> bool:
        """True only on the first frame any of the given keys go down."""
        if self._prev is None:
            return self.pressed(*keys)
        return any(k in self.keys and k not in self._prev.keys for k in keys)

    def just_released(self, *keys: int) -> bool:
        """True only on the first frame any of the given keys come up."""
        if self._prev is None:
            return False
        return any(k not in self.keys and k in self._prev.keys for k in keys)

    @property
    def up(self)      -> bool: return self.pressed(curses.KEY_UP,    ord('w'), ord('W'))
    @property
    def down(self)    -> bool: return self.pressed(curses.KEY_DOWN,  ord('s'), ord('S'))
    @property
    def left(self)    -> bool: return self.pressed(curses.KEY_LEFT,  ord('a'), ord('A'))
    @property
    def right(self)   -> bool: return self.pressed(curses.KEY_RIGHT, ord('d'), ord('D'))
    @property
    def fire(self)    -> bool: return self.pressed(ord('j'), ord('J'), ord('f'), ord('F')) or 1 in self.mouse_pressed
    @property
    def jump(self)    -> bool: return self.pressed(ord(' '))
    @property
    def confirm(self) -> bool: return self.pressed(ord('\n'), 10, 13, ord(' '))
    @property
    def pause(self)   -> bool: return self.pressed(27)

    # Frames of "stickiness" applied to recently-seen keys. Terminal key
    # auto-repeat is unreliable across emulators — macOS Terminal and iTerm
    # default to ~500ms before the first repeat fires, then ~30ms between
    # subsequent repeats. At 60 FPS that ~500ms gap is 30 frames of silence
    # after the initial press. A grace window shorter than that produces a
    # noticeable "press, move one cell, stall for half a second, then start
    # moving" stutter. 20 frames (333ms) covers most terminals' initial
    # delay without making single taps feel mushy.
    _REPEAT_GRACE = 20

    # Class-level age tracker: key -> frames since last seen.
    _key_age: dict[int, int] = {}

    @classmethod
    def _poll(cls, scr: curses.window) -> Input:
        """Poll one frame of input. Rotates class-level _last so just_pressed /
        just_released always have the prior frame to compare against. Recently-
        seen keys remain in inp.keys for _REPEAT_GRACE frames to compensate
        for unreliable terminal auto-repeat."""
        inp = cls()
        inp._prev = cls._last
        seen_this_frame: set[int] = set()
        while True:
            k = scr.getch()
            if k == -1:
                break
            if k == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bst = curses.getmouse()
                    inp.mouse_row, inp.mouse_col = my, mx
                    if bst & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                        inp.mouse_pressed.add(1)
                    if bst & (curses.BUTTON2_PRESSED | curses.BUTTON2_CLICKED):
                        inp.mouse_pressed.add(2)
                    if bst & (curses.BUTTON3_PRESSED | curses.BUTTON3_CLICKED):
                        inp.mouse_pressed.add(3)
                except curses.error:
                    pass
            else:
                seen_this_frame.add(k)

        # Age all tracked keys by one frame, drop expired ones.
        cls._key_age = {k: a + 1 for k, a in cls._key_age.items()
                        if a + 1 < cls._REPEAT_GRACE}
        # Reset age for keys actually seen this frame.
        for k in seen_this_frame:
            cls._key_age[k] = 0
        # inp.keys = every key still within the grace window.
        inp.keys = set(cls._key_age.keys())

        cls._last = inp
        return inp


# ── Bounds-safe primitive ──────────────────────────────────────────────────────
# Shared bounds-safe addstr wrapper used across games. Cheaper than each game
# defining a local `_p()` closure with the same try/except + bounds check.

def at_safe(scr: curses.window, H: int, W: int, row: int, col: int, s: str, attr: int = 0) -> None:
    """Bounds-safe addstr: silently no-ops outside the terminal."""
    try:
        if 0 <= row < H - 1 and 0 <= col < W:
            scr.addstr(row, col, s[:max(0, W - col)], attr)
    except curses.error:
        pass


# ── Renderer ───────────────────────────────────────────────────────────────────

class Renderer:
    """
    All drawing for one frame. Coordinates are (row, col), row 0 = top.

    Every method is bounds-safe — drawing outside the terminal is silently ignored.

    Color arguments accept module-level constants (PLAYER, ENEMY, GOOD, etc.)
    or the raw pair integers 1-8.
    """

    def __init__(self, scr: curses.window, H: int, W: int) -> None:
        self._scr = scr
        self.H = H
        self.W = W

    # ── Primitives ──────────────────────────────────────────────────────────────

    def text(self, row: int, col: int, s: str,
             color: int = NEUTRAL, bold: bool = False,
             dim: bool = False, reverse: bool = False,
             blink: bool = False) -> None:
        """Draw a string at (row, col). Silently clipped at terminal edges."""
        attr = curses.color_pair(color)
        if bold:    attr |= curses.A_BOLD
        if dim:     attr |= curses.A_DIM
        if reverse: attr |= curses.A_REVERSE
        if blink:   attr |= curses.A_BLINK
        try:
            if 0 <= row < self.H - 1 and 0 <= col < self.W:
                self._scr.addstr(row, col, s[:max(0, self.W - col)], attr)
        except curses.error:
            pass

    def center(self, row: int, s: str,
               color: int = NEUTRAL, bold: bool = False,
               dim: bool = False) -> None:
        """Draw s centered horizontally on row."""
        self.text(row, max(0, (self.W - len(s)) // 2), s, color, bold, dim)

    def fill(self, row: int, col: int, h: int, w: int,
             char: str = ' ', color: int = NEUTRAL) -> None:
        """Fill a rectangle with a character — useful for clearing regions."""
        line = (char * w)[:w]
        for r in range(row, min(row + h, self.H - 1)):
            self.text(r, col, line, color)

    # ── Shapes ──────────────────────────────────────────────────────────────────

    def box(self, row: int, col: int, h: int, w: int,
            color: int = NEUTRAL, title: str = "",
            double: bool = True) -> None:
        """Draw a border box, optionally with a centered title.

        double=True  ╔═══╗  (default)
        double=False ┌───┐
        """
        tl, tr, bl, br, hz, vt = (
            ('╔', '╗', '╚', '╝', '═', '║') if double else
            ('┌', '┐', '└', '┘', '─', '│')
        )
        self.text(row,       col, tl + hz * (w - 2) + tr, color, bold=True)
        self.text(row + h-1, col, bl + hz * (w - 2) + br, color, bold=True)
        for r in range(1, h - 1):
            self.text(row + r, col,         vt, color)
            self.text(row + r, col + w - 1, vt, color)
        if title:
            t = f' {title} '
            self.text(row, col + max(0, (w - len(t)) // 2), t, color, bold=True)

    def outer_border(self, color: int = NEUTRAL) -> None:
        """Draw the full outer terminal border."""
        self.box(0, 0, self.H - 1, self.W, color)

    # ── Progress bars ────────────────────────────────────────────────────────────

    def bar(self, row: int, col: int,
            value: float, maximum: float, width: int,
            fill_color: int = GOOD, empty_color: int = NEUTRAL,
            label: str = "") -> None:
        """Horizontal progress bar.  ████████░░░░  Optionally suffixed with label."""
        ratio  = max(0.0, min(1.0, value / maximum if maximum > 0 else 0.0))
        filled = int(width * ratio)
        self.text(row, col,          '█' * filled,           fill_color, bold=True)
        self.text(row, col + filled, '░' * (width - filled), empty_color)
        if label:
            self.text(row, col + width + 1, label, NEUTRAL)

    def vbar(self, top_row: int, col: int, height: int,
             value: float, maximum: float,
             fill_color: int = GOOD, empty_color: int = NEUTRAL) -> None:
        """Vertical progress bar — top=empty, bottom=full."""
        ratio  = max(0.0, min(1.0, value / maximum if maximum > 0 else 0.0))
        filled = int(height * ratio)
        empty  = height - filled
        for i in range(empty):
            self.text(top_row + i, col, '░', empty_color)
        for i in range(filled):
            self.text(top_row + empty + i, col, '█', fill_color, bold=True)

    # ── Sprites and menus ────────────────────────────────────────────────────────

    def sprite(self, row: int, col: int, lines: list[str],
               color: int = NEUTRAL, bold: bool = True) -> None:
        """Draw a multi-line ASCII sprite."""
        for i, line in enumerate(lines):
            self.text(row + i, col, line, color, bold)

    def menu(self, row: int, col: int,
             options: list[str], cursor: int,
             color_selected: int = SELECT,
             color_normal: int = NEUTRAL,
             width: int = 20,
             cursor_char: str = '▸') -> None:
        """Vertical menu with a cursor indicator.

        Cursor wraps automatically. Selected item is highlighted.
        """
        cursor = cursor % max(1, len(options))
        for i, opt in enumerate(options):
            prefix = f'{cursor_char} ' if i == cursor else '  '
            label  = (prefix + opt).ljust(width)[:width]
            self.text(row + i, col, label,
                      color_selected if i == cursor else color_normal,
                      bold=(i == cursor))

    # ── Text utilities ────────────────────────────────────────────────────────────

    def wrapped_text(self, row: int, col: int, text: str, width: int,
                     color: int = NEUTRAL, bold: bool = False) -> int:
        """Word-wrap text into a fixed column width. Returns number of rows used."""
        words  = text.split()
        lines: list[str] = []
        current = ''
        for word in words:
            test = (current + ' ' + word).strip()
            if len(test) <= width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word[:width]
        if current:
            lines.append(current)
        for i, line in enumerate(lines):
            self.text(row + i, col, line, color, bold)
        return len(lines)

    def dialog(self, row: int, col: int, width: int,
               speaker: str, text: str,
               tick: int = 0,
               speaker_color: int = PLAYER,
               portrait: list[str] | None = None,
               prompt: str = '▼') -> None:
        """Standard RPG dialog box: speaker name tag, optional portrait, wrapped text.

        The advance prompt blinks using tick. Prompt appears at bottom-right of box.
        """
        port_w   = max((len(l) for l in portrait), default=0) + 2 if portrait else 0
        text_col = col + 2 + port_w
        text_w   = width - port_w - 4
        lines    = max(3, len(self._wrap(text, text_w)))
        h        = lines + 4

        self.box(row, col, h, width, NEUTRAL, double=False)
        tag = f' {speaker} '
        self.text(row, col + 2, tag, speaker_color, bold=True)

        if portrait:
            for i, line in enumerate(portrait[:h - 2]):
                self.text(row + 1 + i, col + 2, line, speaker_color)

        self.wrapped_text(row + 1, text_col, text, text_w, NEUTRAL)

        if (tick // 15) % 2 == 0:
            self.text(row + h - 2, col + width - 4, prompt, GOLD, bold=True)

    def _wrap(self, text: str, width: int) -> list[str]:
        """Internal word-wrap helper."""
        words, lines, current = text.split(), [], ''
        for word in words:
            test = (current + ' ' + word).strip()
            if len(test) <= width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word[:width]
        if current:
            lines.append(current)
        return lines

    # ── Standard screens ─────────────────────────────────────────────────────────

    def header(self, game_name: str,
               left: str = "", right: str = "",
               color: int = GOLD) -> None:
        """Standard 3-row HUD header (rows 0–2)."""
        self.text(0, 0, '╔' + '═' * (self.W - 2) + '╗', NEUTRAL, bold=True)
        self.text(1, 0, '║', NEUTRAL, bold=True)
        self.text(1, self.W - 1, '║', NEUTRAL, bold=True)
        self.text(1, 1, '▓▒░', NEUTRAL, dim=True)
        self.text(1, self.W - 4, '░▒▓', NEUTRAL, dim=True)
        self.text(1, 5, f'★ {game_name} ★', color, bold=True)
        if left:
            self.text(1, 5 + len(game_name) + 5, left, ENEMY, bold=True)
        if right:
            self.text(1, self.W - len(right) - 4, right, GOOD)
        self.text(2, 0, '╠' + '═' * (self.W - 2) + '╣', NEUTRAL, bold=True)

    def footer(self, controls: str, color: int = NEUTRAL) -> None:
        """Controls hint in the bottom two rows (rows H-3 and H-2)."""
        self.text(self.H - 3, 0, '╠' + '═' * (self.W - 2) + '╣', NEUTRAL, bold=True)
        self.text(self.H - 2, 1, controls.center(self.W - 2)[:self.W - 2], color)

    def pause_overlay(self, game_name: str, controls: list[str]) -> None:
        """Minimal pause panel — just `PAUSED` + Resume/Quit prompts.

        Earlier versions painted a full-screen ░ scrim and rendered a
        boxed CONTROLS table on every frame, which cost ~6000+ curses
        writes per frame and read as overdesigned. The `controls`
        argument is preserved for caller-compat but is no longer drawn.
        """
        del controls  # kept for API compatibility; intentionally unused

        title = f' {game_name}  ·  PAUSED '
        prompt = '[ R ] Resume     [ Q ] Quit'

        bw = max(len(title), len(prompt)) + 6
        bh = 5
        by = max(1, (self.H - bh) // 2)
        bx = max(1, (self.W - bw) // 2)

        # Clear only the panel rectangle (no full-screen scrim) so the game
        # stays visible behind the menu and we don't pay for an H*W write.
        for r in range(bh):
            self.text(by + r, bx, ' ' * bw, color=NEUTRAL)

        # Single-line frame
        self.box(by, bx, bh, bw, color=SELECT)

        # Centered title row + prompt row
        self.text(by + 1, bx + (bw - len(title))  // 2, title,  color=GOLD,    bold=True)
        self.text(by + 3, bx + (bw - len(prompt)) // 2, prompt, color=NEUTRAL, bold=True)

    def gameover_screen(self, title: str = "GAME  OVER",
                        score_line: str = "", player_label: str = "",
                        rank: int | None = None, tick: int = 0,
                        prompt: str = "[ SPACE ] Play again   [ ESC ] Quit",
                        title_color: int = ENEMY) -> None:
        """Standard game-over or victory screen."""
        self.outer_border()
        mr = self.H // 2
        self.center(mr - 2, f'  {title}  ', title_color, bold=True)
        if score_line:
            self.center(mr,     f'  {score_line}  ', GOLD,   bold=True)
        if player_label:
            self.center(mr + 1, f'  {player_label}  ', PLAYER, bold=True)
        if rank is not None:
            self.center(mr + 2, f'  Global rank: #{rank}  ', GOOD, bold=True)
        elif player_label:
            self.center(mr + 2, '  Submitting score...  ', NEUTRAL, dim=True)
        if (tick // 15) % 2 == 0:
            self.center(mr + 4, prompt, NEUTRAL)

    def stars(self, star_list: list[StarDict]) -> None:
        """Draw a parallax star field generated by make_stars()."""
        for s in star_list:
            r, c = int(s['y']), int(s['x'])
            if 0 < r < self.H - 1 and 0 < c < self.W - 1:
                self.text(r, c, s['ch'], s['cp'], dim=True)


# ── Scene ─────────────────────────────────────────────────────────────────────

class Scene:
    """
    One screen of a game (title, gameplay, pause, game over, …).

    Subclass and implement:
        on_enter(self)                     called once when scene activates;
                                           self.payload holds any data passed
                                           from the previous scene
        update(self, inp, tick, dt)        game logic; return either:
                                             - None to stay
                                             - "scene_name" to switch
                                             - ("scene_name", payload) to switch
                                               with data the next scene reads
                                               via self.payload
                                             - "quit" to exit
        draw(self, r, tick)                render via the Renderer

    Access engine, size, and delta-time:
        self.engine.H / self.engine.W      terminal dimensions
        self.engine.switch("gameover")     programmatic scene switch
        self.engine.fade_to("gameover")    scene switch with fade transition
    """

    engine: Engine
    payload: object | None = None

    def on_enter(self) -> None: pass

    def update(self, inp: Input, tick: int, dt: float) -> str | tuple[str, object] | None:
        return None

    def draw(self, r: Renderer, tick: int) -> None: pass


# ── Engine ─────────────────────────────────────────────────────────────────────

_MIN_H = 18
_MIN_W = 50


class Engine:
    """
    The game loop.

    Example
    -------
        engine = (
            Engine("Snake", fps=15)
            .scene("menu",     MenuScene())
            .scene("game",     GameScene())
            .scene("gameover", GameOverScene())
        )
        engine.run("menu")
    """

    def __init__(self, title: str, fps: int = 30, seed: int | None = None) -> None:
        self.title    = title
        self.fps      = fps
        self.H        = 0
        self.W        = 0
        self.dt       = 1.0 / fps   # seconds per frame (updated each tick)
        # Engine-owned RNG. Games should prefer self.engine.rng over the global
        # `random` module so daily challenges and replays can be deterministic.
        self.rng      = random.Random(seed)
        self._seed    = seed
        self._scenes: dict[str, Scene] = {}
        self._current = ""
        self._scr:    curses.window | None = None
        self._tick    = 0
        self._fade:   _FadeState | None = None
        # Profiling: when run(profile=True), accumulates per-frame timings.
        self._profile_data: list[tuple[float, float]] | None = None

    def seed(self, seed: int) -> None:
        """Reseed the engine RNG. Useful for daily challenges."""
        self._seed = seed
        self.rng = random.Random(seed)

    def scene(self, name: str, s: Scene) -> Engine:
        """Register a scene. Returns self for chaining."""
        s.engine = self
        self._scenes[name] = s
        return self

    def switch(self, name: str, payload: object | None = None) -> None:
        """Switch to a named scene immediately. The next scene's `self.payload`
        is set before on_enter() runs. Warns to stderr on unknown name."""
        if name not in self._scenes:
            import sys
            print(
                f'[Engine] Unknown scene {repr(name)}. '
                f'Registered: {list(self._scenes)}',
                file=sys.stderr,
            )
            return
        self._current = name
        self._scenes[name].payload = payload
        self._scenes[name].on_enter()

    def fade_to(self, name: str, ticks: int = 12) -> None:
        """Switch to a scene with a brief fade-out / fade-in transition."""
        self._fade = {'to': name, 'ticks': ticks, 't': 0, 'phase': 'out'}

    def run(self, initial: str, profile: bool = False) -> None:
        """Start the game loop, blocking until the game exits.

        profile=True records per-frame update/draw timings and prints a
        summary on exit. Useful for finding hot paths.

        Any exception inside the loop tears down curses cleanly and prints the
        traceback to stderr so the terminal isn't left in raw mode.
        """
        import sys
        import traceback
        if profile:
            self._profile_data = []
        try:
            curses.wrapper(self._loop, initial)
        except Exception as exc:
            try: curses.endwin()
            except curses.error: pass
            print(f'\n[{self.title} crashed] {exc}', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        if self._profile_data:
            self._print_profile()
        print('\n  [ back in Claude — type anything to chat ]\n')

    def _print_profile(self) -> None:
        """Summarise recorded per-frame timings."""
        data = self._profile_data or []
        if not data:
            return
        n = len(data)
        ups = sorted(u for u, _ in data)
        drs = sorted(d for _, d in data)
        def pct(a: list[float], p: float) -> float:
            return a[min(len(a) - 1, int(len(a) * p))]
        print(f'\n── {self.title} profile ({n} frames at {self.fps} fps) ──')
        print(f'  update  avg={sum(ups)/n*1000:6.2f}ms  '
              f'p50={pct(ups,0.5)*1000:6.2f}ms  '
              f'p95={pct(ups,0.95)*1000:6.2f}ms  '
              f'max={max(ups)*1000:6.2f}ms')
        print(f'  draw    avg={sum(drs)/n*1000:6.2f}ms  '
              f'p50={pct(drs,0.5)*1000:6.2f}ms  '
              f'p95={pct(drs,0.95)*1000:6.2f}ms  '
              f'max={max(drs)*1000:6.2f}ms')
        budget = 1.0 / self.fps * 1000
        over = sum(1 for u, d in data if (u + d) * 1000 > budget)
        print(f'  budget {budget:.1f}ms; {over} frame(s) over ({over * 100 // n}%)')

    def _loop(self, scr: curses.window, initial: str) -> None:
        self._scr = scr
        _init_curses(scr)

        # Read terminal size before calling on_enter so scenes can use it
        self.H, self.W = scr.getmaxyx()
        self._tick = 0
        self.switch(initial)

        nxt  = time.perf_counter()
        prev = time.perf_counter()

        while True:
            now = time.perf_counter()

            # Fixed timestep — skip catch-up if we fall behind
            if now < nxt:
                time.sleep(max(0.0, nxt - now - 0.001))
                continue
            self.dt   = now - prev
            prev      = now
            nxt       = max(nxt + 1.0 / self.fps, now)   # no spiral-of-death
            self._tick += 1
            self.H, self.W = scr.getmaxyx()

            # Minimum terminal size guard
            if self.H < _MIN_H or self.W < _MIN_W:
                scr.erase()
                msg = f' Resize terminal: need {_MIN_W}x{_MIN_H}, have {self.W}x{self.H} '
                try:
                    scr.addstr(0, 0, msg[:self.W])
                except curses.error:
                    pass
                scr.refresh()
                continue

            inp = Input._poll(scr)

            # Handle fade transition
            if self._fade:
                self._tick_fade(scr, inp)
                continue

            current = self._scenes.get(self._current)
            if current is None:
                break

            t0 = time.perf_counter()
            result = current.update(inp, self._tick, self.dt)
            t1 = time.perf_counter()

            # Result may be: None, "name", ("name", payload), or "quit".
            next_name: str | None = None
            payload: object | None = None
            if isinstance(result, tuple) and len(result) == 2:
                next_name, payload = result
            elif isinstance(result, str):
                next_name = result

            if next_name == 'quit':
                break
            if next_name and next_name != self._current:
                self.switch(next_name, payload)

            scr.erase()
            self._scenes[self._current].draw(
                Renderer(scr, self.H, self.W), self._tick
            )
            scr.refresh()
            t2 = time.perf_counter()
            if self._profile_data is not None:
                self._profile_data.append((t1 - t0, t2 - t1))

    def _tick_fade(self, scr: curses.window, inp: Input) -> None:
        f = self._fade
        if f is None:
            return  # _loop() only calls us when _fade is set, but narrow for type checker
        frac  = f['t'] / max(1, f['ticks'])
        chars = '█▓▒░ '
        f['t'] += 1

        current = self._scenes.get(self._current)

        if f['phase'] == 'out':
            # Draw scene underneath, then overlay fade
            scr.erase()
            if current:
                current.draw(Renderer(scr, self.H, self.W), self._tick)
            overlay = chars[min(4, int(frac * 5))]
            for r in range(self.H - 1):
                try:
                    scr.addstr(r, 0, overlay * self.W,
                               curses.color_pair(NEUTRAL) | curses.A_DIM)
                except curses.error:
                    pass
            if f['t'] >= f['ticks']:
                self.switch(f['to'])
                f['t'] = 0
                f['phase'] = 'in'

        else:  # 'in'
            frac2   = 1.0 - frac
            scr.erase()
            current2 = self._scenes.get(self._current)
            if current2:
                current2.draw(Renderer(scr, self.H, self.W), self._tick)
            overlay = chars[min(4, int(frac2 * 5))]
            for r in range(self.H - 1):
                try:
                    scr.addstr(r, 0, overlay * self.W,
                               curses.color_pair(NEUTRAL) | curses.A_DIM)
                except curses.error:
                    pass
            if f['t'] >= f['ticks']:
                self._fade = None

        scr.refresh()


# ── AnimSprite ─────────────────────────────────────────────────────────────────

class AnimSprite:
    """
    Frame-cycling sprite with named states.

    Example
    -------
        states = {
            'idle': [ ['(o)', '|=|', '/ \\'] ],
            'run':  [ ['(_o)', '/|\\', '/ \\'],
                      ['(_o)', '\\|/', '/ \\'] ],
        }
        sprite = AnimSprite(states, ticks_per_frame=6)
        sprite.set_state('run')
        sprite.tick()
        r.sprite(row, col, sprite.current())

    Callbacks
    ---------
        sprite.set_state('die', on_complete=lambda: self.remove())
    """

    def __init__(self, states: dict[str, list[list[str]]], ticks_per_frame: int = 8) -> None:
        self.states          = states
        self.ticks_per_frame = ticks_per_frame
        self.state           = next(iter(states))
        self._frame          = 0
        self._timer          = 0
        self._on_complete:   Callable[[], None] | None = None

    def set_state(self, state: str,
                  on_complete: Callable[[], None] | None = None,
                  force: bool = False) -> None:
        """Switch animation state.

        force=True restarts the animation even if already in this state.
        on_complete fires once when the last frame plays.
        """
        if (state != self.state or force) and state in self.states:
            self.state         = state
            self._frame        = 0
            self._timer        = 0
            self._on_complete  = on_complete

    def tick(self) -> None:
        self._timer += 1
        if self._timer >= self.ticks_per_frame:
            self._timer = 0
            frames      = self.states.get(self.state, [[]])
            n           = max(1, len(frames))
            next_f      = self._frame + 1
            if next_f >= n:
                if self._on_complete:
                    cb = self._on_complete
                    self._on_complete = None
                    cb()
            self._frame = next_f % n

    def current(self) -> list[str]:
        frames = self.states.get(self.state, [[' ']])
        return frames[self._frame % max(1, len(frames))]


# ── Camera ─────────────────────────────────────────────────────────────────────

class Camera:
    """
    Smooth-follow camera for scrolling worlds.

    Coordinate convention: x increases right, y increases DOWN (terminal default).
    Set y_up=True in world_to_screen for platformer games where y=0 is the floor.

    Example
    -------
        cam = Camera()
        cam.set_bounds(0, world_width)
        cam.follow(player.x, player.y)          # each frame
        row, col = cam.world_to_screen(e.x, e.y, W, H)
    """

    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self._min_x = -1e9
        self._max_x =  1e9
        self._min_y = -1e9
        self._max_y =  1e9

    def set_bounds(self, min_x: float, max_x: float,
                   min_y: float = -1e9, max_y: float = 1e9) -> None:
        """Prevent the camera from scrolling outside the world."""
        self._min_x = min_x
        self._max_x = max_x
        self._min_y = min_y
        self._max_y = max_y

    def follow(self, tx: float, ty: float, lerp: float = 0.12) -> None:
        """Lerp toward target. lerp=1.0 is instant, 0.05 is very smooth."""
        self.x = clamp(self.x + (tx - self.x) * lerp, self._min_x, self._max_x)
        self.y = clamp(self.y + (ty - self.y) * lerp, self._min_y, self._max_y)

    def world_to_screen(self, wx: float, wy: float, W: int, H: int,
                        y_up: bool = False) -> tuple[int, int]:
        """Convert world position to terminal (row, col).

        y_up=False  Y increases downward (default terminal — good for top-down games)
        y_up=True   Y increases upward   (platformer convention — Y=0 is the floor)
        """
        col = int(wx - self.x + W // 3)
        if y_up:
            row = int(H - 2 - (wy - self.y))
        else:
            row = int(wy - self.y)
        return row, col

    def on_screen(self, wx: float, wy: float, W: int, H: int,
                  margin: int = 4) -> bool:
        """True if the world position is currently visible."""
        row, col = self.world_to_screen(wx, wy, W, H)
        return -margin <= row < H + margin and -margin <= col < W + margin


# ── Particles ──────────────────────────────────────────────────────────────────

class Particles:
    """
    Moving particle system for explosions, sparks, debris, and effects.

    Example
    -------
        particles = Particles()
        particles.explode(col, row, count=8, color=GOLD)
        particles.spawn(col, row, vx=-0.5, vy=-1.0, char='★', color=PLAYER)
        # each frame:
        particles.update()
        particles.draw(r)
    """

    def __init__(self) -> None:
        self._active: list[_ParticleDict] = []

    def spawn(self, col: float, row: float,
              vx: float = 0.0, vy: float = 0.0,
              char: str = '*', color: int = GOLD,
              life: int = 8) -> None:
        """Spawn one particle with velocity and lifetime."""
        self._active.append(_ParticleDict(
            x=float(col), y=float(row),
            vx=vx, vy=vy,
            life=life, max_life=life,
            char=char, color=color,
        ))

    def explode(self, col: int, row: int,
                count: int = 14, color: int = GOLD,
                chars: str = '✦✧★·∙◆◇*+') -> None:
        """Radial burst of particles in random directions."""
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(0.3, 1.4)
            self.spawn(
                col, row,
                vx=math.cos(angle) * speed * 2,
                vy=math.sin(angle) * speed,
                char=random.choice(chars),
                color=color,
                life=random.randint(4, 12),
            )

    def burst(self, col: int, row: int,
              direction: tuple[float, float],
              spread: float = 0.8, count: int = 5,
              color: int = GOLD) -> None:
        """Directional burst (e.g., exhaust trail, hit sparks)."""
        dx, dy = direction
        angle  = math.atan2(dy, dx)
        for _ in range(count):
            a = angle + random.uniform(-spread, spread)
            s = random.uniform(0.4, 1.2)
            self.spawn(col, row,
                       vx=math.cos(a) * s * 2,
                       vy=math.sin(a) * s,
                       char=random.choice('·∙◦•'),
                       color=color,
                       life=random.randint(3, 8))

    def update(self) -> None:
        """Advance all particles by one frame. Call once per frame."""
        for p in self._active:
            p['x']    += p['vx']
            p['y']    += p['vy']
            p['life'] -= 1
        self._active = [p for p in self._active if p['life'] > 0]

    def draw(self, r: Renderer) -> None:
        """Draw all live particles. Call after update()."""
        for p in self._active:
            fade = p['life'] / max(1, p['max_life'])
            r.text(int(p['y']), int(p['x']),
                   p['char'], p['color'], dim=fade < 0.35)

    @property
    def count(self) -> int:
        return len(self._active)


# ── Timer ─────────────────────────────────────────────────────────────────────

class Timer:
    """
    Countdown timer for game events.

    Example
    -------
        self.invincible = Timer(2.0)     # 2-second invincibility

        # in update():
        if self.invincible.tick(dt):
            self.can_take_damage = True

        # check progress:
        frac = self.invincible.frac      # 0.0 → 1.0
        done = self.invincible.done
    """

    def __init__(self, duration: float, auto_reset: bool = False) -> None:
        self.duration   = duration
        self.auto_reset = auto_reset
        self._elapsed   = 0.0
        self.done       = False

    def tick(self, dt: float) -> bool:
        """Advance the timer. Returns True the frame it completes."""
        if self.done and not self.auto_reset:
            return False
        self._elapsed += dt
        if self._elapsed >= self.duration:
            self._elapsed = 0.0 if self.auto_reset else self.duration
            self.done = not self.auto_reset
            return True
        return False

    def reset(self, duration: float | None = None) -> None:
        """Restart the timer, optionally with a new duration."""
        if duration is not None:
            self.duration = duration
        self._elapsed = 0.0
        self.done     = False

    @property
    def frac(self) -> float:
        """Progress 0.0 → 1.0."""
        return min(1.0, self._elapsed / max(1e-9, self.duration))

    @property
    def remaining(self) -> float:
        return max(0.0, self.duration - self._elapsed)


# ── Audio ─────────────────────────────────────────────────────────────────────

class Audio:
    """
    Minimal audio. Two surfaces:
        beep() / flash()    Always works (curses primitives).
        play(path)          Background a system sound command if available.

    play() degrades gracefully — missing files or unsupported platforms are
    no-ops, never raise. Currently looks for afplay (macOS) / aplay (ALSA) /
    paplay (PulseAudio).

    Example
    -------
        audio = Audio()
        audio.beep()                # short curses bell
        audio.play('assets/hit.wav')  # background; missing file is a no-op
    """

    def __init__(self, enabled: bool = True) -> None:
        import shutil
        import subprocess
        self.enabled = enabled
        self._subprocess = subprocess
        self._cmd: str | None = None
        for candidate in ('afplay', 'paplay', 'aplay'):
            if shutil.which(candidate):
                self._cmd = candidate
                break

    def beep(self) -> None:
        """Short audible bell via curses."""
        if not self.enabled:
            return
        try:
            curses.beep()
        except curses.error:
            pass

    def flash(self) -> None:
        """Visible screen flash via curses (silent alternative to beep)."""
        if not self.enabled:
            return
        try:
            curses.flash()
        except curses.error:
            pass

    def play(self, path: str | Path) -> None:
        """Background-play an audio file. No-op if disabled, no command found,
        or the file doesn't exist. Never blocks the game loop."""
        if not self.enabled or self._cmd is None:
            return
        p = Path(path) if not isinstance(path, Path) else path
        if not p.exists():
            return
        try:
            self._subprocess.Popen(
                [self._cmd, str(p)],
                stdout=self._subprocess.DEVNULL,
                stderr=self._subprocess.DEVNULL,
            )
        except OSError:
            pass


# ── GameSave ───────────────────────────────────────────────────────────────────

class GameSave:
    """
    Simple JSON save file stored in the user's home directory.

    Example
    -------
        save = GameSave('claudemon')
        save.save({'team': [...], 'gold': 500})
        data = save.load()          # returns dict or None
        save.delete()
    """

    def __init__(self, name: str) -> None:
        self._path = Path.home() / f'.claudcade_{name}_save.json'

    def save(self, data: dict[str, object]) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    def load(self) -> dict[str, object] | None:
        if not self._path.exists():
            return None
        try:
            result = json.loads(self._path.read_text())
            return result if isinstance(result, dict) else None
        except (OSError, json.JSONDecodeError):
            return None  # missing / unreadable / corrupt save → caller starts fresh

    def delete(self) -> None:
        self._path.unlink(missing_ok=True)

    @property
    def exists(self) -> bool:
        return self._path.exists()


# ── Stars ─────────────────────────────────────────────────────────────────────

def make_stars(H: int, W: int, count: int = 80,
               deep: int = 0) -> list[StarDict]:
    """Generate a parallax star field. Pass to Renderer.stars() and scroll_stars().

    `deep` adds a small number of slow-scrolling far-background landmarks
    (planets, moons) that give a stronger sense of depth.
    """
    result: list[StarDict] = []
    for _ in range(count):
        spd = random.choice([0.25, 0.5, 1.0, 2.0])
        ch  = '∙' if spd < 0.5 else ('·' if spd < 1.0 else ('+' if spd < 1.8 else '✦'))
        cp  = NEUTRAL if spd < 1.0 else (PLAYER if spd < 1.5 else GOLD)
        result.append(StarDict(
            x=float(random.randint(1, max(1, W - 2))),
            y=float(random.randint(1, max(1, H - 5))),
            spd=spd, ch=ch, cp=cp,
        ))
    # Deep-background landmarks — sparse, very slow, larger glyphs
    for _ in range(deep):
        spd = 0.08
        ch  = random.choice(['O', 'o', '°', '◯'])
        cp  = random.choice([SPECIAL, WATER, NEUTRAL])
        result.append(StarDict(
            x=float(random.randint(1, max(1, W - 2))),
            y=float(random.randint(1, max(1, H - 5))),
            spd=spd, ch=ch, cp=cp,
        ))
    return result


def scroll_stars(stars: list[StarDict], W: int, direction: float = -1.0) -> None:
    """Scroll stars horizontally. Call once per frame. direction=-1 scrolls left."""
    for s in stars:
        s['x'] += s['spd'] * direction
        if s['x'] < 1:
            s['x'] = float(W - 2)
        elif s['x'] >= W - 1:
            s['x'] = 1.0


# ── Math utilities ─────────────────────────────────────────────────────────────

def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_in(t: float) -> float:
    """Quadratic ease-in. t in [0, 1]."""
    return t * t


def ease_out(t: float) -> float:
    """Quadratic ease-out. t in [0, 1]."""
    return 1.0 - (1.0 - t) ** 2


def ease_in_out(t: float) -> float:
    """Smooth step. t in [0, 1]."""
    return t * t * (3.0 - 2.0 * t)


def sign(x: float) -> float:
    return 0.0 if x == 0 else (1.0 if x > 0 else -1.0)


# ── Engine helpers ─────────────────────────────────────────────────────────────

def setup_colors() -> None:
    """Initialise the standard Claudecade color pairs. Call once inside curses.wrapper.

    On 256-color terminals (Ghostty, iTerm2, modern xterm, etc.) we bind to
    fixed indices in the 6x6x6 RGB cube so the arcade palette stays vibrant
    regardless of the user's terminal theme. The named curses colors
    (COLOR_CYAN etc.) are re-mapped by themes — Ghostty's default in
    particular renders them pastel, which is what the arcade is *not*.

    On 8-color terminals we fall back to the named pairs so legacy
    setups still get something readable.
    """
    curses.start_color()
    curses.use_default_colors()

    if curses.COLORS >= 256:
        # 6x6x6 RGB cube: 16 + 36*r + 6*g + b   where r,g,b in 0..5
        # Picked for high saturation against a black background.
        VIBRANT_CYAN    = 51    # #00ffff
        VIBRANT_RED     = 196   # #ff0000
        VIBRANT_GREEN   = 46    # #00ff00
        VIBRANT_YELLOW  = 226   # #ffff00
        VIBRANT_WHITE   = 231   # #ffffff
        VIBRANT_MAGENTA = 201   # #ff00ff
        VIBRANT_BLUE    = 33    # #0087ff (royal — pure 21 #0000ff is too dim)
        VIBRANT_BLACK   = 16    # #000000

        curses.init_pair(CYAN,      VIBRANT_CYAN,    -1)
        curses.init_pair(RED,       VIBRANT_RED,     -1)
        curses.init_pair(GREEN,     VIBRANT_GREEN,   -1)
        curses.init_pair(YELLOW,    VIBRANT_YELLOW,  -1)
        curses.init_pair(WHITE,     VIBRANT_WHITE,   -1)
        curses.init_pair(MAGENTA,   VIBRANT_MAGENTA, -1)
        curses.init_pair(HIGHLIGHT, VIBRANT_BLACK,   VIBRANT_WHITE)
        curses.init_pair(BLUE,      VIBRANT_BLUE,    -1)
    else:
        curses.init_pair(CYAN,      curses.COLOR_CYAN,    -1)
        curses.init_pair(RED,       curses.COLOR_RED,     -1)
        curses.init_pair(GREEN,     curses.COLOR_GREEN,   -1)
        curses.init_pair(YELLOW,    curses.COLOR_YELLOW,  -1)
        curses.init_pair(WHITE,     curses.COLOR_WHITE,   -1)
        curses.init_pair(MAGENTA,   curses.COLOR_MAGENTA, -1)
        curses.init_pair(HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(BLUE,      curses.COLOR_BLUE,    -1)


def _init_curses(scr: curses.window) -> None:
    curses.cbreak()
    curses.noecho()
    scr.keypad(True)
    scr.nodelay(True)
    curses.curs_set(0)
    setup_colors()
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
    except curses.error:
        pass


def draw_how_to_play(scr: curses.window, H: int, W: int, tick: int,
                     goal: list[str],
                     tips: list[str],
                     controls: list[str] | None = None) -> None:
    """Standard HOW TO PLAY screen shared by all games.

    Content is assembled into a single list, vertically centered inside the
    panel, and horizontally centered within the box. The PRESS SPACE prompt
    is reserved its own row at the bottom; the content block above it can
    never overlap the prompt or run off the box (it scales down or clips
    earlier sections if the terminal is too short).
    """
    P  = curses.color_pair
    BW = min(70, W - 4)
    lm = (W - BW) // 2

    def _put(r: int, c: int, s: str, a: int = 0) -> None:
        try:
            if 0 <= r < H - 1 and 0 <= c < W:
                scr.addstr(r, c, s[:max(0, W - c)], a)
        except curses.error:
            pass

    # ── Frame ──────────────────────────────────────────────────────────────
    scr.erase()
    _put(0,   lm, '╔' + '═' * (BW - 2) + '╗', P(NEUTRAL) | curses.A_BOLD)
    _put(H-1, lm, '╚' + '═' * (BW - 2) + '╝', P(NEUTRAL) | curses.A_BOLD)
    for r in range(1, H - 1):
        _put(r, lm,          '║', P(NEUTRAL) | curses.A_BOLD)
        _put(r, lm + BW - 1, '║', P(NEUTRAL) | curses.A_BOLD)

    title = 'H O W   T O   P L A Y'
    hdr   = '▓▒░' + title.center(BW - 8) + '░▒▓'
    _put(1, lm + 1, hdr, P(GOLD) | curses.A_BOLD)
    _put(2, lm, '╠' + '═' * (BW - 2) + '╣', P(NEUTRAL) | curses.A_BOLD)

    # ── Build the content block as (text, color, bold) tuples ──────────────
    Line = tuple[str, int, bool]
    lines: list[Line] = []
    lines.append(('GOAL',  GOLD, True))
    lines.append(('────',  NEUTRAL, False))
    for line in goal:
        lines.append((line, NEUTRAL, False))
    lines.append(('', NEUTRAL, False))

    if controls is not None:
        lines.append(('CONTROLS', GOLD, True))
        lines.append(('────────', NEUTRAL, False))
        for ctrl in controls:
            lines.append((ctrl, NEUTRAL, False))
        lines.append(('', NEUTRAL, False))

    lines.append(('TIPS', GOLD, True))
    lines.append(('────', NEUTRAL, False))
    for line in tips:
        lines.append((line, NEUTRAL, False))

    # Strip trailing blank rows so vertical centering doesn't pad on both
    # sides of empty space at the end.
    while lines and lines[-1][0] == '':
        lines.pop()

    # ── Vertical centering ─────────────────────────────────────────────────
    # Inner panel runs rows 3..H-4 inclusive; H-3 is reserved for the
    # PRESS SPACE prompt. So the content has (H - 3) - 3 = H - 6 rows.
    top_inner = 3
    bot_inner = H - 4         # last writable row above the SPACE prompt
    inner_h   = max(0, bot_inner - top_inner + 1)
    if len(lines) > inner_h:
        # Too tall for this terminal — clip trailing lines so the prompt
        # still has its row. Better than overrunning the box border.
        lines = lines[:inner_h]
    start = top_inner + max(0, (inner_h - len(lines)) // 2)

    # ── Horizontal centering of the whole content column ───────────────────
    # Compute the widest line so the column sits centered as a block, not
    # so each line is independently centered (preserves CONTROLS alignment).
    col_w = max((len(text) for text, _, _ in lines), default=0)
    col_w = min(col_w, BW - 4)
    col_x = lm + (BW - col_w) // 2

    for i, (text, color, bold) in enumerate(lines):
        attr = P(color) | (curses.A_BOLD if bold else 0)
        _put(start + i, col_x, text[:col_w], attr)

    # ── Blinking prompt (always on its reserved row) ───────────────────────
    if (tick // 15) % 2 == 0:
        msg = 'PRESS SPACE TO START'
        _put(H - 3, lm + (BW - len(msg)) // 2, msg, P(GOLD) | curses.A_BOLD)
    scr.refresh()


def run_game(main_fn: Callable[[curses.window], None], game_name: str = '') -> None:
    """Standard curses.wrapper + crash handler. Use at the bottom of every game file.

        from claudcade_engine import run_game
        run_game(main, 'C-TYPE')
    """
    import sys
    import traceback
    try:
        curses.wrapper(main_fn)
    except Exception as e:
        try:
            curses.endwin()
        except curses.error:
            pass
        label = f'[{game_name} crashed] ' if game_name else ''
        print(f'\n{label}{e}', file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    print('\n  [ back in Claude — type anything to chat ]\n')
