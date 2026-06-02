// ============================================================================
//  CLAUDAGOTCHI — round-screen firmware
//  Target: Seeed XIAO ESP32-S3 (or C3) + GC9A01 240x240 round IPS,
//          capacitive touch (CST816S, I2C) + rotary encoder knob.
//
//  The device is a renderer. The Mac brain (pet.py) computes everything from
//  your real token usage; pet_bridge.py streams one slim JSON line over USB
//  whenever state changes. The knob/touch send short "CMD ..." lines back so
//  the brain can spend XP, etc.
//
//  Build (XIAO ESP32-S3):
//    arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 \
//      --build-path ./build ./claudagotchi_round
//    arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3 \
//      -p /dev/cu.usbmodem1101 --input-dir ./build ./claudagotchi_round
//  Libs: "GFX Library for Arduino", "ArduinoJson".
// ============================================================================

#include <Arduino.h>
#include <Wire.h>
#include <Arduino_GFX_Library.h>
#include <ArduinoJson.h>
#include <Adafruit_NeoPixel.h>
#include "FreeMonoBold12pt7b.h"
#include "FreeMonoBold18pt7b.h"
#include "FreeMono9pt7b.h"

// ─────────────────────────────────────────────────────────────────────────────
//  CONFIG — pick your board. Pins are XIAO silkscreen Dn -> GPIO.
// ─────────────────────────────────────────────────────────────────────────────
#define BOARD_CROWPANEL_S3 1  // Elecrow CrowPanel 1.28" HMI ESP32-S3 Rotary Display (default)
#define BOARD_CROWPANEL_C3 0
#define BOARD_XIAO_S3      0
#define BOARD_XIAO_C3      0

#if BOARD_CROWPANEL_S3
  // Elecrow CrowPanel 1.28" HMI ESP32-S3R8 Rotary Display (round, touch, knob).
  #define PIN_SCK   10
  #define PIN_MOSI  11
  #define PIN_DC    3
  #define PIN_CS    9
  #define PIN_RST   14
  #define PIN_BL    46
  #define PIN_SDA   6     // I2C touch (CST816D @0x15)
  #define PIN_SCL   7
  #define PIN_ENC_A 45
  #define PIN_ENC_B 42
  #define PIN_ENC_SW 41
  #define PIN_LCD_PWR 1   // drives the LCD 3.3V rail — MUST be HIGH or the screen is dark
#elif BOARD_CROWPANEL_C3
  // On-board wiring of the Elecrow CrowPanel ESP32-C3 round display.
  #define PIN_SCK   6
  #define PIN_MOSI  7
  #define PIN_DC    8
  #define PIN_CS    9
  #define PIN_RST   10
  #define PIN_BL    11
  #define PIN_SDA   4    // I2C: touch (CST816S) + IO expander @0x43
  #define PIN_SCL   5
  #define PIN_ENC_A 19
  #define PIN_ENC_B 18
  #define PIN_ENC_SW -1  // knob press comes via the IO expander, not a GPIO; tap to select
#elif BOARD_XIAO_S3
  #define PIN_SCK   7    // D8
  #define PIN_MOSI  9    // D10
  #define PIN_DC    4    // D3
  #define PIN_CS    2    // D1
  #define PIN_RST   1    // D0
  #define PIN_BL    3    // D2
  #define PIN_SDA   5    // D4  (touch I2C)
  #define PIN_SCL   6    // D5  (touch I2C)
  #define PIN_ENC_A 8    // D9  (MISO pad — free, GC9A01 is write-only)
  #define PIN_ENC_B 43   // D6  (TX pad)
  #define PIN_ENC_SW 44  // D7  (RX pad)
#elif BOARD_XIAO_C3
  #define PIN_SCK   8    // D8
  #define PIN_MOSI  10   // D10
  #define PIN_DC    5    // D3
  #define PIN_CS    3    // D1
  #define PIN_RST   2    // D0
  #define PIN_BL    4    // D2
  #define PIN_SDA   6    // D4
  #define PIN_SCL   7    // D5
  #define PIN_ENC_A 9    // D9
  #define PIN_ENC_B 21   // D6
  #define PIN_ENC_SW 20  // D7
#endif

#define ENABLE_TOUCH    1
#define ENABLE_ENCODER  1
#define CST816_ADDR     0x15      // common Elecrow/Waveshare round-panel touch IC

// ─────────────────────────────────────────────────────────────────────────────
//  Display
// ─────────────────────────────────────────────────────────────────────────────
Arduino_DataBus *bus = new Arduino_ESP32SPI(PIN_DC, PIN_CS, PIN_SCK, PIN_MOSI, GFX_NOT_DEFINED);
Arduino_GFX     *out = new Arduino_GC9A01(bus, PIN_RST, 0 /*rotation*/, true /*IPS*/);
Arduino_Canvas  *gfx = new Arduino_Canvas(240, 240, out);

#define CX 120
#define CY 120
#define RAD 119

// On-board WS2812 LED strip (bottom of the CrowPanel round display).
#define PIN_LEDS  48
#define NUM_LEDS  5
Adafruit_NeoPixel strip(NUM_LEDS, PIN_LEDS, NEO_GRB + NEO_KHZ800);

// Palette (RGB565)
#define C_BG       0x0000   // true black — eyes/feet & bar segment gaps
#define C_CHAR     0x18E3   // charcoal background  (~#1c1c1c)
#define C_TRACK    0x2945   // empty bar / ring track (~#28282a, lighter than bg)
#define C_FRAME    0x52AA   // bar border / dim gray
#define C_ORANGE   0xFC20   // #ff8700 bright orange (rim / XP)
#define C_ORANGE2  0xD3AA   // EXACT Claude brand terracotta rgb(215,119,87) = #D77757
#define C_DARK     0x2104   // dark ring track
#define C_WHITE    0xFFFF
#define C_CREAM    0xFF38   // warm white
#define C_GREEN    0x07E6   // HP
#define C_PINK     0xFB16   // ATK
#define C_DIM      0x6B4D   // labels / secondary text

// ─────────────────────────────────────────────────────────────────────────────
//  State pushed from the Mac (pet_bridge.py)
// ─────────────────────────────────────────────────────────────────────────────
struct Pet {
  char     name[16] = "Claude";
  int      level    = 1;
  float    xp_frac  = 0.0f;    // 0..1 progress through current level
  uint32_t tokens   = 0;       // lifetime tokens eaten
  uint32_t banked   = 0;       // spendable XP
  int      trainRanks[3] = {0,0,0};         // vigor,power,guard
  int      foodFresh[4]  = {9,9,9,9};        // 0..9 per food
  int      skinIdx       = 0;                // currently selected skin
  // cosmetics unlocked by level
  uint16_t body    = 0xD3AA;   // mascot body color (skin, RGB565 for the screen)
  uint32_t ledColor = 0xD77757;// exact 24-bit skin color for the LED strip
  int      actN    = 0;        // token-activity pulse (lights up even on quest)
  uint16_t accent  = 0xFC20;   // accent (rim / XP)
  bool     rainbow = false;    // top skin: cycle hue
  char     skin[12] = "Terracotta";
  int      animTier = 1;       // 1..6, gates ambient effects
  // quest
  bool     questActive = false;
  int      questRem    = 0;    // seconds left
  uint32_t questReward = 0;
  char     questName[14] = "";
  int      offerN = 0;                 // quest offers to choose from (when idle)
  int      offerMin[3] = {0,0,0};      // duration minutes
  uint32_t offerRew[3] = {0,0,0};      // XP reward
};
Pet pet;
bool have_data = false;

// animation latches
float    shown_frac = 0.0f;
unsigned long eat_until = 0, cheer_until = 0, pet_until = 0, flash_until = 0;
unsigned long quest_flash_until = 0;        // quest depart/arrive: flash-all 3x
#define QUEST_FLASH_MS 1200
String   flash_msg;
uint32_t last_tokens = 0;
int      last_level = 1;

// ─────────────────────────────────────────────────────────────────────────────
//  UI: knob-navigated screens
// ─────────────────────────────────────────────────────────────────────────────
// Knob turn ONLY cycles these 4 top screens — it never moves list items.
// Lists/tiles are entirely TOUCH-driven. TRAIN is reached via ACTIONS, not the scroll.
enum Page { PG_HOME = 0, PG_ACTIONS, PG_STYLE, PG_QUEST, PG_COUNT };
int  page = 0;       // current top screen
int  sub  = 0;       // ACTIONS sub-view: 0 tiles, 1 FOOD list, 2 TRAIN list

// 4 foods, 3 trainings (names match pet.py order).
#define NFOOD  4
#define NTRAIN 3
const char* FOOD_NAMES[NFOOD]   = { "Cookie", "Pizza", "Combo", "Feast" };
const char* TRAIN_NAMES[NTRAIN] = { "Vigor", "Power", "Guard" };
const char* TRAIN_DESC[NTRAIN]  = { "+HP", "+ATK", "+DEF" };
const char* TRAIN_KEYS[NTRAIN]  = { "vigor", "power", "guard" };
const int   TRAIN_BASE[NTRAIN]  = { 120, 120, 140 };

// 8 skins for the STYLE grid (colors + unlock level; matches pet.py SKINS order).
#define NSKIN 8
const uint16_t SKIN_COLORS[NSKIN] = { 0xD3AA, 0x4D1F, 0x5E4E, 0xFAF4, 0xFE27, 0x36BC, 0xA3FF, 0xFFFF };
const int      SKIN_UNLOCK[NSKIN] = {   0,      5,     10,     15,     20,     30,     40,     50   };

// Touch/layout geometry. FEED rows sit higher (toward the title); TRAIN rows
// start lower to leave room for the XP line under its title.
#define FEED_TOP   52
#define TRAIN_TOP  74
#define LIST_STEP  30
#define FOOTER_Y   194
bool helpOpen = false;       // ? overlay on an interior list

// Whimsical "thinking" words (like Claude Code's loading verbs) — shown while
// he's away on a quest instead of a countdown timer.
const char* THINK_WORDS[] = {
  "Flibbertigibbeting", "Pondering", "Percolating", "Noodling", "Ruminating",
  "Cogitating", "Conjuring", "Tinkering", "Marinating", "Vibing",
  "Schlepping", "Bamboozling", "Discombobulating", "Finagling", "Wrangling",
  "Hornswoggling", "Galumphing", "Lollygagging", "Dawdling", "Mulling",
  "Brewing", "Simmering", "Stewing", "Churning", "Whirring",
  "Buffering", "Computing", "Calculating", "Deliberating", "Contemplating",
  "Scheming", "Plotting", "Devising", "Concocting", "Brainstorming",
  "Wondering", "Musing", "Reflecting", "Speculating", "Theorizing",
  "Hypothesizing", "Postulating", "Surmising", "Deducing", "Inferring",
  "Reasoning", "Analyzing", "Synthesizing", "Processing", "Crunching",
  "Untangling", "Unraveling", "Decoding", "Deciphering", "Parsing",
  "Wrestling", "Grappling", "Tussling", "Juggling", "Balancing",
  "Fiddling", "Fussing", "Puttering", "Tweaking", "Tuning",
  "Adjusting", "Calibrating", "Fine-tuning", "Optimizing", "Polishing",
  "Refining", "Honing", "Sharpening", "Sculpting", "Crafting",
  "Forging", "Weaving", "Knitting", "Stitching", "Assembling",
  "Constructing", "Building", "Engineering", "Architecting", "Designing",
  "Sketching", "Drafting", "Outlining", "Mapping", "Charting",
  "Plotting", "Navigating", "Exploring", "Foraging", "Scavenging",
  "Hunting", "Seeking", "Searching", "Scouring", "Combing",
  "Digging", "Excavating", "Unearthing", "Mining", "Prospecting",
  "Gathering", "Collecting", "Harvesting", "Reaping", "Accumulating",
  "Hoarding", "Stockpiling", "Amassing", "Compiling", "Aggregating",
  "Bundling", "Packaging", "Wrapping", "Folding", "Crumpling",
  "Squishing", "Smooshing", "Mashing", "Blending", "Whisking",
  "Frothing", "Foaming", "Bubbling", "Fizzing", "Sizzling",
  "Crackling", "Popping", "Snapping", "Zapping", "Buzzing",
  "Humming", "Droning", "Whistling", "Twiddling", "Diddling",
  "Doodling", "Scribbling", "Scrawling", "Jotting", "Notating",
  "Annotating", "Footnoting", "Cross-referencing", "Indexing", "Cataloging",
  "Sorting", "Shuffling", "Reshuffling", "Rearranging", "Reorganizing",
  "Tidying", "Straightening", "Smoothing", "Ironing", "Pressing",
  "Steaming", "Boiling", "Roasting", "Toasting", "Baking",
  "Kneading", "Proofing", "Glazing", "Frosting", "Garnishing",
  "Seasoning", "Spicing", "Sprinkling", "Dusting", "Drizzling",
  "Macerating", "Fermenting", "Pickling", "Curing", "Aging",
  "Maturing", "Ripening", "Blossoming", "Flourishing", "Germinating",
  "Sprouting", "Budding", "Blooming", "Unfurling", "Stretching",
  "Yawning", "Blinking", "Squinting", "Pondering some more", "Almost there",
  "Hold tight", "One sec", "Cooking", "Manifesting", "Wizarding",
  "Sorcerizing", "Alchemizing", "Transmuting", "Levitating", "Teleporting",
  "Procrastinating", "Daydreaming", "Doomscrolling", "Overthinking", "Caffeinating",
};
#define THINK_N (sizeof(THINK_WORDS) / sizeof(THINK_WORDS[0]))
const char* thinkWord() { return THINK_WORDS[(millis() / 120000UL) % THINK_N]; }  // one every 2 min

// ─────────────────────────────────────────────────────────────────────────────
//  Helpers
// ─────────────────────────────────────────────────────────────────────────────
String kfmt(uint32_t n) {
  char b[16];
  if (n >= 1000000UL) snprintf(b, sizeof(b), "%.1fM", n / 1e6);
  else if (n >= 1000UL) snprintf(b, sizeof(b), "%.0fK", n / 1e3);
  else snprintf(b, sizeof(b), "%lu", (unsigned long)n);
  return b;
}
void textC(const char* s, int cx, int by, const GFXfont* f, uint16_t col) {
  gfx->setFont(f); gfx->setTextColor(col);
  int16_t x1, y1; uint16_t w, h;
  gfx->getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  gfx->setCursor(cx - w / 2 - x1, by); gfx->print(s);
}
void textL(const char* s, int x, int by, const GFXfont* f, uint16_t col) {
  gfx->setFont(f); gfx->setTextColor(col);
  gfx->setCursor(x, by); gfx->print(s);
}
void textR(const char* s, int xr, int by, const GFXfont* f, uint16_t col) {
  gfx->setFont(f); gfx->setTextColor(col);
  int16_t x1, y1; uint16_t w, h;
  gfx->getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  gfx->setCursor(xr - w - x1, by); gfx->print(s);
}

// A real RPG bar: bordered frame, charcoal track, colored fill, segment ticks.
// Caller draws its own label/value text around it.
void rpgBar(int x, int y, int w, int h, float frac, uint16_t fill) {
  if (frac < 0) frac = 0; if (frac > 1) frac = 1;
  gfx->fillRoundRect(x, y, w, h, 3, C_TRACK);            // track
  int fw = (int)((w - 4) * frac + 0.5f);
  if (fw > 0) gfx->fillRoundRect(x + 2, y + 2, fw, h - 4, 2, fill);  // fill
  int seg = (w - 4) / 8;                                 // segment gaps
  for (int sx = x + 2 + seg; sx < x + w - 3; sx += seg)
    gfx->drawFastVLine(sx, y + 2, h - 4, C_CHAR);
  gfx->drawRoundRect(x, y, w, h, 3, C_FRAME);            // border on top
}
uint16_t hsv565(float h, float s, float v) {
  h = fmodf(h, 360); if (h < 0) h += 360;
  float c = v * s, x = c * (1 - fabsf(fmodf(h / 60, 2) - 1)), m = v - c, r, g, b;
  if (h < 60){r=c;g=x;b=0;} else if(h<120){r=x;g=c;b=0;} else if(h<180){r=0;g=c;b=x;}
  else if(h<240){r=0;g=x;b=c;} else if(h<300){r=x;g=0;b=c;} else {r=c;g=0;b=x;}
  uint8_t R=(r+m)*255, G=(g+m)*255, B=(b+m)*255;
  return ((R&0xF8)<<8)|((G&0xFC)<<3)|(B>>3);
}

// ─────────────────────────────────────────────────────────────────────────────
//  The creature — drawn from primitives so it scales on the circle
// ─────────────────────────────────────────────────────────────────────────────
// The Claude mascot as a 14x6 block grid drawn as filled square blocks.
//   '#' = terracotta body, ' ' = empty. The two interior gaps on row 1 are the
// eyes. He always looks the same — only an occasional blink.
static const char* CLAWD[] = {
  " ############ ",   // 0
  " ## ###### ## ",   // 1   gaps at col 3 & 10 = eyes
  "##############",   // 2   widest
  " ############ ",   // 3
  "  # #    # #  ",   // 4   legs centered under body (cols 2,4,9,11)
  "              ",   // 5
};
#define CLAWD_W 14
#define CLAWD_H 6
#define SX 9                  // sub-pixel width  (14*9  = 126)
#define SY 15                 // sub-pixel height (6*15 = 90) — tall, like a terminal

// Draw Clawd centered at (ccx,ccy) with sub-pixel size (sx,sy).
void drawCreatureAt(int tick, int ccx, int ccy, int sx, int sy) {
  int ox = ccx - (CLAWD_W * sx) / 2, oy = ccy - (CLAWD_H * sy) / 2;
  bool blink = ((tick % 130) < 5);
  uint16_t body = pet.rainbow ? hsv565(tick * 2.0f, 0.75f, 0.95f) : pet.body;

  for (int r = 0; r < CLAWD_H; r++)
    for (int c = 0; c < CLAWD_W; c++)
      if (CLAWD[r][c] == '#')
        gfx->fillRect(ox + c * sx, oy + r * sy, sx, sy, body);

  int eyR = oy + 1 * sy, e1 = ox + 3 * sx, e2 = ox + 10 * sx;   // eyes (gaps on row1)
  if (blink) {
    gfx->fillRect(e1, eyR + sy / 2 - 1, sx, 3, C_BG);
    gfx->fillRect(e2, eyR + sy / 2 - 1, sx, 3, C_BG);
  } else {
    gfx->fillRect(e1, eyR, sx, sy, C_BG);
    gfx->fillRect(e2, eyR, sx, sy, C_BG);
  }

  if (millis() < pet_until) {                      // pet hearts (float above him)
    for (int i = 0; i < 3; i++) {
      int hx = ccx - 26 + i * 26, hy = oy - 12 - (int)(millis() % 400) / 20;
      gfx->fillCircle(hx - 3, hy, 3, C_PINK);
      gfx->fillCircle(hx + 3, hy, 3, C_PINK);
      gfx->fillTriangle(hx - 6, hy + 1, hx + 6, hy + 1, hx, hy + 8, C_PINK);
    }
  }
}

// HOME creature: full size, centered, nudged down.
void drawCreature(int m, int tick) { (void)m; drawCreatureAt(tick, CX, CY + 16, SX, SY); }

// XP arc around the bezel (12 o'clock, clockwise)
void drawRimXP(float frac, int tick, bool celebrate) {
  gfx->fillArc(CX, CY, RAD, RAD - 8, 0, 360, C_DARK);
  if (celebrate) {                            // rainbow sweep on level up
    for (int a = 0; a < 360; a += 6)
      gfx->fillArc(CX, CY, RAD, RAD - 8, a, a + 6, hsv565(a + tick * 6, 1, 1));
    return;
  }
  if (frac > 0) gfx->fillArc(CX, CY, RAD, RAD - 8, 0, frac * 360.0f, pet.accent);
}

// ─────────────────────────────────────────────────────────────────────────────
//  Screens
// ─────────────────────────────────────────────────────────────────────────────
// ── SCREEN 1: HOME — smaller Claude, level (from 0) + XP bar at the number ────
void drawHome(int tick) {
  drawCreature(0, tick);

  char buf[16];
  snprintf(buf, sizeof(buf), "LV %d", pet.level);
  textC(buf, CX, 32, &FreeMonoBold12pt7b, C_WHITE);
  int bw = 116, bx = CX - bw / 2;
  rpgBar(bx, 40, bw, 10, shown_frac, pet.accent);     // his level bar, right here

  if (pet.questActive) {
    char q[28]; snprintf(q, sizeof(q), "%s...", thinkWord());
    textC(q, CX, 200, &FreeMono9pt7b, pet.accent);
  } else if (millis() < flash_until) {
    textC(flash_msg.c_str(), CX, 208, &FreeMonoBold12pt7b, C_ORANGE);
  } else {
    char t[24]; snprintf(t, sizeof(t), "%s tok", kfmt(pet.tokens).c_str());
    textC(t, CX, 208, &FreeMono9pt7b, C_DIM);
  }
}

// Footer: a bare return arrow (left) + a [?] button (right of it). No box on arrow.
void drawFooter() {
  int ax = CX - 18, ay = FOOTER_Y + 12;                                  // arrow
  gfx->fillTriangle(ax - 11, ay, ax - 1, ay - 7, ax - 1, ay + 7, C_CREAM);
  gfx->fillRect(ax - 1, ay - 2, 14, 5, C_CREAM);
  gfx->fillRoundRect(CX + 6, FOOTER_Y, 28, 24, 6, C_TRACK);              // ? button
  gfx->drawRoundRect(CX + 6, FOOTER_Y, 28, 24, 6, C_FRAME);
  textC("?", CX + 20, FOOTER_Y + 17, &FreeMonoBold12pt7b, C_ORANGE);
}

// Touchable item rows (caller draws the title) + footer.
void drawList(const char* const* items, const char* const* subs, int n, int top) {
  for (int i = 0; i < n; i++) {
    int y = top + i * LIST_STEP;
    gfx->fillRoundRect(CX - 84, y, 168, LIST_STEP - 5, 6, C_TRACK);
    textL(items[i], CX - 76, y + LIST_STEP / 2 + 2, &FreeMono9pt7b, C_CREAM);
    if (subs && subs[i] && subs[i][0])
      textR(subs[i], CX + 76, y + LIST_STEP / 2 + 2, &FreeMono9pt7b, C_DIM);
  }
  drawFooter();
}

// Help overlay text — short lines (<=15 chars) so nothing clips the round bezel.
const char* HELP_FOOD[] = {
  "FOOD",
  "Claude eats the",
  "tokens you use,",
  "automatically.",
  "Tap a food for",
  "a bonus treat.",
  "Repeats fade,",
  "then recover.",
};
const char* HELP_TRAIN[] = {
  "TRAIN",
  "Spend banked XP",
  "(from tokens,",
  "1 XP / 1000).",
  "Each rank costs",
  "more each time.",
  "Stats power",
  "battles.",
};
const char* HELP_PET[] = {
  "PET",
  "Tap his head,",
  "back or belly",
  "for bonus XP.",
  "Same spot = less.",
  "Mix all three",
  "for a COMBO!",
  "",
};
void drawHelp(const char* const* lines, int n) {
  gfx->fillCircle(CX, CY, RAD - 2, C_CHAR);                 // cover the list, stay in-bezel
  gfx->drawCircle(CX, CY, RAD - 6, C_ORANGE2);
  textC(lines[0], CX, 56, &FreeMonoBold12pt7b, C_ORANGE);  // title
  for (int i = 1; i < n; i++)
    textC(lines[i], CX, 78 + (i - 1) * 16, &FreeMono9pt7b, C_CREAM);
  textC("tap to close", CX, 200, &FreeMono9pt7b, C_FRAME);
}

void drawTrainList();   // fwd
void drawPetScreen(int tick);  // fwd

// ── SCREEN 2: ACTIONS (touch-driven) ──────────────────────────────────────────
void drawActions(int tick) {
  if (sub == 0) {                                     // 2x2 tiles — tap to open
    textC("ACTIONS", CX, 40, &FreeMonoBold12pt7b, C_ORANGE);
    const char* A[4] = { "FEED", "TRAIN", "QUEST", "PET" };
    for (int i = 0; i < 4; i++) {
      int cx = CX - 64 + (i % 2) * 66, cy = 66 + (i / 2) * 58;
      gfx->fillRoundRect(cx, cy, 58, 48, 8, C_TRACK);
      gfx->drawRoundRect(cx, cy, 58, 48, 8, C_FRAME);
      textC(A[i], cx + 29, cy + 30, &FreeMono9pt7b, C_CREAM);
    }
    textC("tap a button", CX, 210, &FreeMono9pt7b, C_DIM);
  } else if (sub == 1) {                              // FOOD list (tap a food)
    textC("FEED", CX, 36, &FreeMonoBold12pt7b, C_ORANGE);
    static char fs[NFOOD][6];
    const char* subs2[NFOOD];
    for (int i = 0; i < NFOOD; i++) {
      snprintf(fs[i], sizeof(fs[i]), "%d/9", pet.foodFresh[i]); subs2[i] = fs[i];
    }
    drawList(FOOD_NAMES, subs2, NFOOD, FEED_TOP);
    if (helpOpen) drawHelp(HELP_FOOD, 8);
  } else if (sub == 2) {                              // TRAIN list
    drawTrainList();
    if (helpOpen) drawHelp(HELP_TRAIN, 8);
  } else {                                            // PET screen
    drawPetScreen(tick);
    if (helpOpen) drawHelp(HELP_PET, 8);
  }
}

// PET: tap his head / back / belly for bonus XP (diminishing on repeats, combo
// for mixing). Body drawn center; the three zones are stacked top-to-bottom.
const char* SPOT_LBL[3] = { "HEAD", "BACK", "BELLY" };
void drawPetScreen(int tick) {
  textC("PET", CX, 28, &FreeMonoBold12pt7b, C_ORANGE);
  drawCreatureAt(tick, 60, 116, 5, 9);                // small, on the left
  for (int i = 0; i < 3; i++) {                       // clear buttons on the right
    int by = 50 + i * 40;
    gfx->fillRoundRect(108, by, 96, 32, 8, C_ORANGE2);
    gfx->drawRoundRect(108, by, 96, 32, 8, C_FRAME);
    textC(SPOT_LBL[i], 156, by + 21, &FreeMonoBold12pt7b, C_WHITE);
  }
  if (millis() < flash_until)
    textC(flash_msg.c_str(), CX, 178, &FreeMono9pt7b, C_PINK);
  drawFooter();
}

// ── SCREEN 3: STYLE — skin grid ───────────────────────────────────────────────
void drawStyle(int tick) {
  textC("STYLE", CX, 30, &FreeMonoBold12pt7b, C_ORANGE);
  // 8 swatches, 2 rows x 4 cols. Unlocked = colored; locked = grey + "Lv N".
  for (int i = 0; i < NSKIN; i++) {
    int col = i % 4, row = i / 4;
    int cx = CX - 66 + col * 44;
    int sy = 50 + row * 48;
    bool unlocked = pet.level >= SKIN_UNLOCK[i];
    if (i == 7 && unlocked) {                          // rainbow swatch
      for (int a = 0; a < 360; a += 20)
        gfx->fillArc(cx, sy + 17, 17, 0, a, a + 20, hsv565(a + tick * 4, 0.85f, 0.95f));
    } else {
      gfx->fillRoundRect(cx - 17, sy, 34, 34, 6, unlocked ? SKIN_COLORS[i] : C_TRACK);
    }
    if (i == pet.skinIdx)                              // current = white border
      gfx->drawRoundRect(cx - 18, sy - 1, 36, 36, 6, C_WHITE);
    if (!unlocked) {                                   // locked: show unlock level
      char l[6]; snprintf(l, sizeof(l), "Lv%d", SKIN_UNLOCK[i]);
      textC(l, cx, sy + 21, &FreeMono9pt7b, C_DIM);
    }
  }
  textC(pet.skin, CX, 168, &FreeMonoBold12pt7b, C_WHITE);
  textC("tap a color", CX, 192, &FreeMono9pt7b, C_FRAME);
}

// TRAIN list (reached via the ACTIONS -> TRAIN tile; touch a stat to train).
void drawTrainList() {
  textC("TRAIN", CX, 36, &FreeMonoBold12pt7b, C_ORANGE);
  char xp[20]; snprintf(xp, sizeof(xp), "%s XP", kfmt(pet.banked).c_str());
  textC(xp, CX, 56, &FreeMono9pt7b, C_ORANGE2);          // XP under the title
  static char tl[NTRAIN][16], ts[NTRAIN][9];
  const char* items[NTRAIN]; const char* subs[NTRAIN];
  for (int i = 0; i < NTRAIN; i++) {
    snprintf(tl[i], sizeof(tl[i]), "%s %s", TRAIN_NAMES[i], TRAIN_DESC[i]);
    int cost = TRAIN_BASE[i] * (pet.trainRanks[i] + 1);
    snprintf(ts[i], sizeof(ts[i]), "%d", cost);
    items[i] = tl[i]; subs[i] = ts[i];
  }
  drawList(items, subs, NTRAIN, TRAIN_TOP);
}

// ── SCREEN 5: QUEST INFO ──────────────────────────────────────────────────────
#define QUEST_TOP 78
#define QUEST_STEP 42
void drawQuest(int tick) {
  (void)tick;
  textC("QUEST", CX, 30, &FreeMonoBold12pt7b, C_ORANGE);          // page title
  if (pet.questActive) {
    textC(pet.questName, CX, 96, &FreeMonoBold12pt7b, C_WHITE);
    textC("away - not eating", CX, 118, &FreeMono9pt7b, C_DIM);
    char tw[24]; snprintf(tw, sizeof(tw), "%s...", thinkWord());
    textC(tw, CX, 152, &FreeMonoBold12pt7b, pet.accent);
    char r[22]; snprintf(r, sizeof(r), "reward %s", kfmt(pet.questReward).c_str());
    textC(r, CX, 184, &FreeMono9pt7b, C_ORANGE2);
  } else {
    textC("CHOOSE YOUR QUEST", CX, 52, &FreeMono9pt7b, C_CREAM);  // headline
    for (int i = 0; i < pet.offerN; i++) {                        // random time/XP offers
      int by = QUEST_TOP + i * QUEST_STEP;
      gfx->fillRoundRect(CX - 84, by, 168, QUEST_STEP - 8, 7, C_TRACK);
      char t[10]; snprintf(t, sizeof(t), "%dm", pet.offerMin[i]);
      textL(t, CX - 74, by + (QUEST_STEP - 8) / 2 + 4, &FreeMonoBold12pt7b, C_WHITE);
      char rw[14]; snprintf(rw, sizeof(rw), "%s XP", kfmt(pet.offerRew[i]).c_str());
      textR(rw, CX + 76, by + (QUEST_STEP - 8) / 2 + 4, &FreeMono9pt7b, C_ORANGE);
    }
  }
}

void render(int tick) {
  gfx->fillScreen(C_CHAR);                            // charcoal background
  if (!have_data) { textC("CLAUDAGOTCHI", CX, 110, &FreeMonoBold12pt7b, C_ORANGE);
                    textC("waiting for Mac...", CX, 140, &FreeMono9pt7b, C_DIM); gfx->flush(); return; }
  switch (page) {
    case PG_HOME:    drawHome(tick);    break;
    case PG_ACTIONS: drawActions(tick); break;
    case PG_STYLE:   drawStyle(tick);   break;
    case PG_QUEST:   drawQuest(tick);   break;
  }
  // page dots (hidden inside an ACTIONS sub-list, to keep it clean)
  if (!(page == PG_ACTIONS && sub != 0)) {
    int dx0 = CX - (PG_COUNT - 1) * 6;
    for (int i = 0; i < PG_COUNT; i++)
      gfx->fillCircle(dx0 + i * 12, 220, 2, i == page ? pet.accent : C_DIM);
  }
  gfx->flush();
}

// ─────────────────────────────────────────────────────────────────────────────
//  Input: rotary encoder (quadrature poll) + button + touch
// ─────────────────────────────────────────────────────────────────────────────
void sendCmd(const String& c) { Serial.print("CMD "); Serial.println(c); }

void petHim() { pet_until = millis() + 1200; flash_msg = "<3";
                flash_until = millis() + 1000; sendCmd("PET"); }

// KNOB TURN: only cycles the 4 top screens. Never moves list items. Always
// exits any ACTIONS sub-list back to the tiles flow.
void onTurn(int dir) {
  page = (page + dir + PG_COUNT) % PG_COUNT;
  sub = 0;
}

// KNOB PRESS: convenience action; selection is touch.
void onPress() {
  if (helpOpen) { helpOpen = false; return; }
  switch (page) {
    case PG_HOME:  petHim(); break;
    case PG_STYLE: sendCmd("SKIN"); break;
    case PG_QUEST: sendCmd("QUEST"); break;
    case PG_ACTIONS: if (sub != 0) sub = 0; break;   // back to tiles
    default: break;
  }
}

// TOUCH: drives tiles + list rows + footer (? / Back) + help dismiss.
void onTouch(int x, int y) {
  if (helpOpen) { helpOpen = false; return; }
  switch (page) {
    case PG_HOME:  petHim(); return;
    case PG_STYLE: {                                   // tap a swatch in the grid
      // pick the swatch whose center is closest to the tap (forgiving — every
      // swatch incl. the top-left orange is reliably reachable)
      int best = 0; long bd = 0x7FFFFFFF;
      for (int i = 0; i < NSKIN; i++) {
        int sx = CX - 66 + (i % 4) * 44;
        int sy = 50 + (i / 4) * 48 + 17;          // swatch center (matches drawStyle)
        long dx = x - sx, dy = y - sy, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
      }
      sendCmd(String("SKIN ") + best);
      return;
    }
    case PG_QUEST:                                     // tap an offer to choose it
      if (!pet.questActive && pet.offerN > 0) {
        int row = (y - QUEST_TOP) / QUEST_STEP;
        if (row >= 0 && row < pet.offerN) {
          sendCmd(String("QUEST ") + row);
          flash_msg = "off you go!"; flash_until = millis() + 1500;
        }
      }
      return;
    case PG_ACTIONS:
      if (sub == 0) {                                  // tap a tile quadrant
        int t = (x > CX ? 1 : 0) + (y > CY ? 2 : 0);   // FEED0 TRAIN1 QUEST2 PET3
        if (t == 0) sub = 1;
        else if (t == 1) sub = 2;
        else if (t == 2) { page = PG_QUEST; sub = 0; }
        else sub = 3;                                  // PET screen
        return;
      }
      if (y >= FOOTER_Y) {                             // footer (all sub-screens)
        if (x < CX) sub = 0;                           // [<- back] on the left
        else helpOpen = true;                          // [?] on the right
        return;
      }
      if (sub == 3) {                                  // PET: head / back / belly buttons
        int spot = (y < 86) ? 0 : (y < 126) ? 1 : 2;
        sendCmd(String("PET ") + spot);
        pet_until = millis() + 1000;                   // hearts; +XP flash arrives from bridge
        return;
      }
      {                                                // FEED / TRAIN item row
        int top = (sub == 1) ? FEED_TOP : TRAIN_TOP;
        int n   = (sub == 1) ? NFOOD : NTRAIN;
        int row = (y - top) / LIST_STEP;
        if (row >= 0 && row < n) {
          if (sub == 1) { sendCmd(String("FEED ") + row);
                          flash_msg = "yum!"; flash_until = millis() + 1200; }
          else          { sendCmd(String("BUY ") + TRAIN_KEYS[row]);
                          flash_msg = "training..."; flash_until = millis() + 1400; }
        }
      }
      return;
  }
}

#if ENABLE_ENCODER
unsigned long lastBtn = 0; bool btnPrev = true;
// The encoder is read on pin-change INTERRUPTS so no detent is ever missed,
// even during a slow screen redraw. The ISR decodes quarter-steps with a
// transition table; one detent (4 quarter-steps) = one pending step.
volatile int32_t encSteps = 0;
volatile int8_t  encAcc   = 0;
volatile uint8_t encPrev  = 0;
void IRAM_ATTR encISR() {
  static const int8_t TBL[16] = { 0,-1,1,0, 1,0,0,-1, -1,0,0,1, 0,1,-1,0 };
  uint8_t cur = (digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B);
  encAcc += TBL[(encPrev << 2) | cur];
  encPrev = cur;
  if (encAcc >= 4)       { encSteps++; encAcc = 0; }
  else if (encAcc <= -4) { encSteps--; encAcc = 0; }
}
void pollEncoder() {
  noInterrupts(); int32_t s = encSteps; encSteps = 0; interrupts();
  for (; s > 0; s--) onTurn(+1);
  for (; s < 0; s++) onTurn(-1);
#if (PIN_ENC_SW) >= 0
  bool btn = digitalRead(PIN_ENC_SW);
  if (!btn && btnPrev && millis() - lastBtn > 180) { onPress(); lastBtn = millis(); }
  btnPrev = btn;
#endif
}
#endif

#if ENABLE_TOUCH
bool touchPrev = false;
void pollTouch() {
  Wire.beginTransmission(CST816_ADDR); Wire.write(0x02);
  if (Wire.endTransmission(false) != 0) return;
  if (Wire.requestFrom(CST816_ADDR, 5) != 5) return;
  uint8_t n  = Wire.read();
  uint8_t xh = Wire.read(), xl = Wire.read(), yh = Wire.read(), yl = Wire.read();
  bool down = (n & 0x0F) > 0;
  if (down && !touchPrev) {
    int x = ((xh & 0x0F) << 8) | xl;
    int y = ((yh & 0x0F) << 8) | yl;
    onTouch(x, y);
  }
  touchPrev = down;
}
#endif

// ─────────────────────────────────────────────────────────────────────────────
//  Serial intake from pet_bridge.py
// ─────────────────────────────────────────────────────────────────────────────
String inbuf;
void parse(const String& line) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) return;
  last_tokens = pet.tokens; last_level = pet.level;

  strlcpy(pet.name, doc["n"] | "Claude", sizeof(pet.name));
  pet.level   = doc["lv"] | 1;
  pet.xp_frac = doc["xf"] | 0.0f;
  pet.tokens  = doc["tok"] | 0u;
  pet.banked  = doc["bk"]  | 0u;
  JsonArrayConst tr = doc["tr"]; for (int i = 0; i < NTRAIN; i++) pet.trainRanks[i] = (i < (int)tr.size()) ? (tr[i] | 0) : 0;
  JsonArrayConst ff = doc["ff"]; for (int i = 0; i < NFOOD;  i++) pet.foodFresh[i]  = (i < (int)ff.size()) ? (ff[i] | 9) : 9;
  pet.skinIdx = doc["si"] | 0;
  // cosmetics
  pet.body     = doc["sb"] | 0xD3AA;
  pet.ledColor = doc["lc"] | 0xD77757;
  pet.actN     = doc["an"] | 0;
  pet.accent  = doc["sa"] | 0xFC20;
  pet.rainbow = (int)(doc["sr"] | 0) != 0;
  strlcpy(pet.skin, doc["sn"] | "Terracotta", sizeof(pet.skin));
  pet.animTier      = doc["at"] | 1;
  // quest
  bool wasQuest = pet.questActive;
  pet.questActive = (int)(doc["qa"] | 0) != 0;
  pet.questRem    = doc["qr"] | 0;
  pet.questReward = doc["qw"] | 0u;
  strlcpy(pet.questName, doc["qn"] | "", sizeof(pet.questName));
  JsonArrayConst qod = doc["qod"]; JsonArrayConst qor = doc["qor"];
  pet.offerN = qod.size() < 3 ? qod.size() : 3;
  for (int i = 0; i < pet.offerN; i++) { pet.offerMin[i] = qod[i] | 0; pet.offerRew[i] = qor[i] | 0u; }

  static int last_actN = 0;
  if (have_data) {
    if (pet.questActive != wasQuest)               // quest departs OR arrives
      quest_flash_until = millis() + QUEST_FLASH_MS;
    if (pet.actN != last_actN)                     // token activity -> LED lights up
      eat_until = millis() + 2500;                 // (fires even while on a quest)
    if (pet.tokens > last_tokens) {                // credited eating -> "+X" flash
      flash_msg = "+" + kfmt(pet.tokens - last_tokens);
      flash_until = millis() + 2200;
    }
    if (pet.level > last_level) cheer_until = millis() + 2800;
  }
  last_actN = pet.actN;
  have_data = true;
  Serial.println("OK");
}

// LED behavior (color comes from pet.ledColor — the EXACT 24-bit skin color,
// gamma-corrected so it reads true on the WS2812s instead of washed/blue):
//  - quest depart/arrive: ALL LEDs flash 3x in the skin color
//  - XP/token gain:       spinning comet in the skin color (fires even on a quest)
//  - level up:            spinning RAINBOW ring
//  - on a quest (idle):   slow breathing glow in the skin color
//  - otherwise:           off
void updateLeds() {
  unsigned long now = millis();
  static int lastMode = -1;
  // gamma-correct the exact skin color once
  uint8_t sr = Adafruit_NeoPixel::gamma8((pet.ledColor >> 16) & 0xFF);
  uint8_t sg = Adafruit_NeoPixel::gamma8((pet.ledColor >> 8)  & 0xFF);
  uint8_t sb = Adafruit_NeoPixel::gamma8( pet.ledColor        & 0xFF);

  if (now < quest_flash_until) {                     // flash-all 3x (depart/arrive)
    unsigned long elapsed = QUEST_FLASH_MS - (quest_flash_until - now);
    bool on = ((elapsed / 200) % 2) == 0;
    strip.fill(on ? strip.Color(sr, sg, sb) : 0);
    strip.show(); lastMode = 4; return;
  }
  int mode = (now < eat_until) ? 1 : (now < cheer_until) ? 2 : (pet.questActive) ? 3 : 0;
  if (mode == 0) {
    if (lastMode != 0) { strip.clear(); strip.show(); }
    lastMode = 0; return;
  }
  if (mode == 3) {                                   // slow breathing glow while away
    float p = 0.20f + 0.22f * sinf(now * 0.003f);
    strip.fill(strip.Color(sr * p, sg * p, sb * p));
    strip.show(); lastMode = 3; return;
  }
  float head = fmodf(now / 60.0f, NUM_LEDS);
  for (int i = 0; i < NUM_LEDS; i++) {
    float d = fabsf(i - head); d = fminf(d, NUM_LEDS - d);
    if (mode == 2) {                                 // spinning rainbow (level up)
      uint16_t hue = (uint16_t)(((i - head) / (float)NUM_LEDS) * 65536.0f);
      strip.setPixelColor(i, strip.gamma32(strip.ColorHSV(hue, 255, 255)));
    } else {                                         // spinning skin-color comet (XP gain)
      float br = fmaxf(0.06f, 1.0f - d * 0.55f);
      strip.setPixelColor(i, strip.Color(sr * br, sg * br, sb * br));
    }
  }
  strip.show();
  lastMode = mode;
}

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.setRxBufferSize(2048);   // default 256 truncates larger state payloads
  Serial.begin(115200);
  strip.begin(); strip.setBrightness(140); strip.clear(); strip.show();
#ifdef PIN_LCD_PWR
  pinMode(PIN_LCD_PWR, OUTPUT); digitalWrite(PIN_LCD_PWR, HIGH);  // power the LCD rail
  delay(50);
#endif
  pinMode(PIN_BL, OUTPUT); digitalWrite(PIN_BL, HIGH);
#if ENABLE_ENCODER
  pinMode(PIN_ENC_A, INPUT_PULLUP); pinMode(PIN_ENC_B, INPUT_PULLUP);
  encPrev = (digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_B), encISR, CHANGE);
  #if (PIN_ENC_SW) >= 0
  pinMode(PIN_ENC_SW, INPUT_PULLUP);
  #endif
#endif
#if ENABLE_TOUCH
  Wire.begin(PIN_SDA, PIN_SCL);
#endif
  gfx->begin();
  gfx->fillScreen(C_BG);
  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { if (inbuf.length()) parse(inbuf); inbuf = ""; }
    else if (c != '\r' && inbuf.length() < 1024) inbuf += c;
  }
#if ENABLE_ENCODER
  pollEncoder();
#endif
#if ENABLE_TOUCH
  static unsigned long tT = 0;
  if (millis() - tT > 40) { pollTouch(); tT = millis(); }
#endif

  // ease the XP bar toward the real value
  shown_frac += (pet.xp_frac - shown_frac) * 0.15f;

  updateLeds();

  static unsigned long tR = 0; static int tick = 0;
  if (millis() - tR > 33) { render(tick++); tR = millis(); }   // ~30 fps
}
