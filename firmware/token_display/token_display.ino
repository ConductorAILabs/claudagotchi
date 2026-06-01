// ESP32-S3-LCD-1.47 token display.
// Reads one JSON line per update over USB-CDC; renders a big total + three
// ring graphs (session/week/cache limits) on a 320x172 ST7789 panel.
// Look: black bg, hot-pink + purple, monospace.

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <ArduinoJson.h>
#include "FreeMonoBold24pt7b.h"
#include "FreeMonoBold12pt7b.h"
#include "FreeMonoBold18pt7b.h"
#include "FreeMono9pt7b.h"

// Waveshare ESP32-S3-LCD-1.47 pin map
#define PIN_LCD_DC   41
#define PIN_LCD_CS   42
#define PIN_LCD_SCK  40
#define PIN_LCD_MOSI 45
#define PIN_LCD_RST  39
#define PIN_LCD_BL   48
#define PIN_RGB_LED  38   // onboard WS2812

// ST7789 visible window inside its 240x320 frame at 172x320
#define COL_OFFSET 34
#define ROW_OFFSET 0

Arduino_DataBus *bus = new Arduino_ESP32SPI(
    PIN_LCD_DC, PIN_LCD_CS, PIN_LCD_SCK, PIN_LCD_MOSI, GFX_NOT_DEFINED, HSPI);

// rotation 3 = landscape (320x172), IPS true
Arduino_GFX *output = new Arduino_ST7789(
    bus, PIN_LCD_RST, 3, true, 172, 320,
    COL_OFFSET, ROW_OFFSET, COL_OFFSET, ROW_OFFSET);

// Double-buffered canvas to avoid flicker on redraw
Arduino_Canvas *gfx = new Arduino_Canvas(320, 172, output);

#define SCR_W 320
#define SCR_H 172

// Palette: black bg, pink/purple, monospace
#define C_BG          0x0000   // black
#define C_PINK        0xF81F   // bright magenta-pink (RGB 255, 0, 255)
#define C_HOTPINK     0xF8D2   // hot pink (RGB ~255, 27, 150)
#define C_PURPLE      0x801F   // mid purple
#define C_DEEP        0x180A   // very dark purple (ring track)
#define C_LAVENDER    0xC59F   // light lavender (labels)
#define C_DIMPURPLE   0x4015   // dim purple (subtitle)
#define C_WHITE       0xFFFF
#define C_GREEN       0x07E0   // pure green — matches chat status "↑ X tokens"
#define C_BLUE        0x051F   // sky blue — hidden cache cost the chat omits

const uint32_t SESSION_LIMIT = 1930000UL;
const uint32_t WEEK_LIMIT    = 13000000UL;

struct Vals {
  uint32_t sess_in         = 0;   // cumulative input_tokens today (pink half)
  uint32_t sess_out        = 0;   // cumulative output_tokens today (pink half)
  float    session_pct     = -1.0f;
  float    week_pct        = -1.0f;
  // Live per-request numbers from token_proxy.py — small numbers that
  // reset at each message_start. The bridge bunches rapid ticks so
  // visible jumps are more meaningful (one +200 instead of five +40s).
  uint32_t live_output      = 0;
  uint32_t live_cache_write = 0;
};

Vals prev, cur, tgt;
String        model     = "--";
bool          have_data = false;

// Animation
bool          animating   = false;
unsigned long anim_start  = 0;
const unsigned long ANIM_MS  = 1200;   // number tween
const unsigned long LED_MS   = 2500;   // rainbow lingers past tween for visibility
const unsigned long DELTA_MS = 2000;   // "+X" popup duration

// Delta popup (set in parseAndUpdate when tokens go up)
int32_t       delta_value     = 0;
unsigned long delta_until     = 0;

// Live (green/blue) idle hide — both numbers vanish if neither has changed
// recently. Reset whenever a new payload reports a different value.
unsigned long last_live_change_ms = 0;
bool          live_was_active     = false;
const unsigned long LIVE_IDLE_MS  = 4000;

// ── helpers ───────────────────────────────────────────────────────────────
String withCommas(uint32_t n) {
  char b[16]; snprintf(b, sizeof(b), "%lu", (unsigned long)n);
  String s = b, out;
  int len = s.length();
  for (int i = 0; i < len; i++) {
    if (i > 0 && (len - i) % 3 == 0) out += ',';
    out += s[i];
  }
  return out;
}

String tk(uint32_t n) {
  char b[16];
  if (n >= 1000000UL) { snprintf(b, sizeof(b), "%.1fM", n / 1000000.0); return b; }
  if (n >= 1000UL)    { snprintf(b, sizeof(b), "%.0fK", n / 1000.0);    return b; }
  snprintf(b, sizeof(b), "%lu", (unsigned long)n);
  return b;
}

String modelAbbrev(const String& raw) {
  String r = raw; r.toLowerCase();
  if (r.indexOf("opus")   >= 0) return "OPUS";
  if (r.indexOf("sonnet") >= 0) return "SONNET";
  if (r.indexOf("haiku")  >= 0) return "HAIKU";
  return "--";
}

// Print text centered around (cx, baselineY)
void printCentered(const char* s, int cx, int baselineY, const GFXfont* font, uint16_t color) {
  gfx->setFont(font);
  gfx->setTextColor(color);
  int16_t x1, y1; uint16_t w, h;
  gfx->getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  gfx->setCursor(cx - w / 2 - x1, baselineY);
  gfx->print(s);
}

// HSV (h:[0,360), s,v:[0,1]) -> 8-bit RGB
void hsv_to_rgb8(float h, float s, float v, uint8_t* r8, uint8_t* g8, uint8_t* b8) {
  h = fmodf(h, 360.0f);
  if (h < 0) h += 360.0f;
  float c = v * s;
  float x = c * (1.0f - fabsf(fmodf(h / 60.0f, 2.0f) - 1.0f));
  float m = v - c;
  float r, g, b;
  if      (h <  60) { r = c; g = x; b = 0; }
  else if (h < 120) { r = x; g = c; b = 0; }
  else if (h < 180) { r = 0; g = c; b = x; }
  else if (h < 240) { r = 0; g = x; b = c; }
  else if (h < 300) { r = x; g = 0; b = c; }
  else              { r = c; g = 0; b = x; }
  *r8 = (uint8_t)((r + m) * 255.0f);
  *g8 = (uint8_t)((g + m) * 255.0f);
  *b8 = (uint8_t)((b + m) * 255.0f);
}

void ledOff() {
  neopixelWrite(PIN_RGB_LED, 0, 0, 0);
}

void ledRainbow(float t01) {
  // Two full hue rotations across the animation
  float h = fmodf(t01 * 720.0f, 360.0f);
  uint8_t r, g, b;
  hsv_to_rgb8(h, 1.0f, 0.65f, &r, &g, &b);   // 65% brightness — clearly visible per tick
  neopixelWrite(PIN_RGB_LED, r, g, b);
}

// HSV (h:[0,360), s,v:[0,1]) -> RGB565
uint16_t hsv565(float h, float s, float v) {
  h = fmodf(h, 360.0f);
  if (h < 0) h += 360.0f;
  float c = v * s;
  float x = c * (1.0f - fabsf(fmodf(h / 60.0f, 2.0f) - 1.0f));
  float m = v - c;
  float r, g, b;
  if      (h <  60) { r = c; g = x; b = 0; }
  else if (h < 120) { r = x; g = c; b = 0; }
  else if (h < 180) { r = 0; g = c; b = x; }
  else if (h < 240) { r = 0; g = x; b = c; }
  else if (h < 300) { r = x; g = 0; b = c; }
  else              { r = c; g = 0; b = x; }
  uint8_t R = (uint8_t)((r + m) * 255.0f);
  uint8_t G = (uint8_t)((g + m) * 255.0f);
  uint8_t B = (uint8_t)((b + m) * 255.0f);
  return ((R & 0xF8) << 8) | ((G & 0xFC) << 3) | (B >> 3);
}

// Ring at (cx, cy) with outer/inner radius, fill from 12 o'clock CW based on pct.
void drawRing(int cx, int cy, int rOuter, int rInner, float pct,
              uint16_t fillColor, uint16_t trackColor) {
  gfx->fillArc(cx, cy, rOuter, rInner, 0, 360, trackColor);
  if (pct > 0) {
    if (pct > 100) pct = 100;
    float endAngle = pct * 3.6f;
    gfx->fillArc(cx, cy, rOuter, rInner, 0, endAngle, fillColor);
  }
}

// ── animation helpers ─────────────────────────────────────────────────────
static inline float easeOutCubic(float t) {
  float f = 1.0f - t;
  return 1.0f - f * f * f;
}
static inline uint32_t lerpU32(uint32_t a, uint32_t b, float t) {
  if (b >= a) return a + (uint32_t)((b - a) * t + 0.5f);
  return a - (uint32_t)((a - b) * t + 0.5f);
}
static inline float lerpF(float a, float b, float t) { return a + (b - a) * t; }

// ── render full screen ────────────────────────────────────────────────────
void renderTokens() {
  gfx->fillScreen(C_BG);

  uint32_t sess_tok = cur.sess_in + cur.sess_out;

  // ── Header ─────────────────────────────────────────────────────────────
  printCentered("CONDUCTOR LABS TOKEN TRACKER", SCR_W / 2, 14, &FreeMono9pt7b, C_LAVENDER);

  // ── Big pink number = today's input + output total ─────────────────────
  String big = withCommas(sess_tok);
  printCentered(big.c_str(), SCR_W / 2, 58, &FreeMonoBold24pt7b, C_PINK);

  // ── Under it: green = this request's output, blue = its cache_write ────
  // Per-request small numbers; hide when no change for LIVE_IDLE_MS.
  unsigned long live_age = millis() - last_live_change_ms;
  bool live_active = (last_live_change_ms > 0) && (live_age < LIVE_IDLE_MS);
  if (live_active) {
    String out_s   = "+" + withCommas(cur.live_output);
    String cache_s = "+" + withCommas(cur.live_cache_write);
    printCentered(out_s.c_str(),    80, 82, &FreeMonoBold12pt7b, C_GREEN);
    printCentered(cache_s.c_str(), 240, 82, &FreeMonoBold12pt7b, C_BLUE);
  }

  // ── 2 rings ─ real Anthropic limit %s from statusLine ─────────────────
  // No fallback math any more: if rate_limits aren't in the slim payload,
  // the ring is just empty rather than estimated from local totals.
  float sess_pct = cur.session_pct >= 0.0f ? cur.session_pct : 0.0f;
  float week_pct = cur.week_pct    >= 0.0f ? cur.week_pct    : 0.0f;

  const int ring_cy   = 124;
  const int ring_rOut = 36;
  const int ring_rIn  = 27;
  const int cx_a = 90, cx_b = 230;     // two rings, centered around 160

  drawRing(cx_a, ring_cy, ring_rOut, ring_rIn, sess_pct, C_PINK,    C_DEEP);
  drawRing(cx_b, ring_cy, ring_rOut, ring_rIn, week_pct, C_HOTPINK, C_DEEP);

  // Percent inside rings
  char pbuf[8];
  snprintf(pbuf, sizeof(pbuf), "%d%%", (int)(sess_pct + 0.5));
  printCentered(pbuf, cx_a, ring_cy + 6, &FreeMonoBold18pt7b, C_WHITE);
  snprintf(pbuf, sizeof(pbuf), "%d%%", (int)(week_pct + 0.5));
  printCentered(pbuf, cx_b, ring_cy + 6, &FreeMonoBold18pt7b, C_WHITE);

  // Labels under rings
  printCentered("5 HR",  cx_a, 168, &FreeMono9pt7b, C_LAVENDER);
  printCentered("WEEK",  cx_b, 168, &FreeMono9pt7b, C_LAVENDER);

  gfx->flush();
}

void renderSplash() {
  gfx->fillScreen(C_BG);
  printCentered("CONDUCTOR AI LABS", SCR_W / 2,  30, &FreeMonoBold12pt7b, C_LAVENDER);
  printCentered("TOKEN",             SCR_W / 2,  90, &FreeMonoBold24pt7b, C_PINK);
  printCentered("TRACKER",           SCR_W / 2, 145, &FreeMonoBold24pt7b, C_PINK);
  gfx->flush();
}

void parseAndUpdate(const String& line) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) { Serial.print("ERR "); Serial.println(err.c_str()); return; }

  // New target: snapshot whatever we're showing right now as the starting point.
  prev = cur;

  tgt.sess_in          = doc["session"]["input"]       | 0u;
  tgt.sess_out         = doc["session"]["output"]      | 0u;
  tgt.session_pct      = doc["session_pct"]            | -1.0f;
  tgt.week_pct         = doc["week_pct"]               | -1.0f;
  tgt.live_output      = doc["live"]["output"]         | 0u;
  tgt.live_cache_write = doc["live"]["cache_write"]    | 0u;

  // Reset live-hide timer when either small number changed. Heartbeats
  // re-send identical values and must not keep them on screen.
  if (tgt.live_output      != prev.live_output ||
      tgt.live_cache_write != prev.live_cache_write) {
    last_live_change_ms = millis();
  }

  // LED rainbow trigger: any positive tick on the live output.
  if (tgt.live_output > prev.live_output && have_data) {
    delta_value = (int32_t)(tgt.live_output - prev.live_output);
    delta_until = millis() + DELTA_MS;
  }

  have_data = true;
  animating = true;
  anim_start = millis();
  Serial.println("OK");
}

String inbuf;

void setup() {
  Serial.begin(115200);
  delay(100);

  pinMode(PIN_LCD_BL, OUTPUT);
  digitalWrite(PIN_LCD_BL, HIGH);

  ledOff();

  gfx->begin();
  renderSplash();

  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (inbuf.length()) parseAndUpdate(inbuf);
      inbuf = "";
    } else if (c != '\r' && inbuf.length() < 4096) {
      inbuf += c;
    }
  }

  unsigned long now = millis();
  bool led_active   = (now - anim_start) < LED_MS && have_data;
  bool popup_active = delta_value > 0 && now < delta_until;

  // Live numbers expired? Redraw once so they actually disappear.
  bool live_active_now = (last_live_change_ms > 0) &&
                         ((now - last_live_change_ms) < LIVE_IDLE_MS);
  bool live_state_flip = (live_active_now != live_was_active);
  if (live_state_flip) live_was_active = live_active_now;

  bool need_render  = animating || popup_active || live_state_flip;

  if (animating) {
    float t = (now - anim_start) / (float)ANIM_MS;
    if (t >= 1.0f) { t = 1.0f; animating = false; }
    float k = easeOutCubic(t);

    cur.sess_in          = lerpU32(prev.sess_in,          tgt.sess_in,          k);
    cur.sess_out         = lerpU32(prev.sess_out,         tgt.sess_out,         k);
    cur.live_output      = lerpU32(prev.live_output,      tgt.live_output,      k);
    cur.live_cache_write = lerpU32(prev.live_cache_write, tgt.live_cache_write, k);
    // For pcts: if either prev or tgt is missing (-1), don't lerp — use tgt
    cur.session_pct = (prev.session_pct < 0 || tgt.session_pct < 0)
                        ? tgt.session_pct : lerpF(prev.session_pct, tgt.session_pct, k);
    cur.week_pct    = (prev.week_pct < 0 || tgt.week_pct < 0)
                        ? tgt.week_pct    : lerpF(prev.week_pct,    tgt.week_pct,    k);
  }

  if (need_render) renderTokens();

  if (led_active) {
    float lt = (now - anim_start) / (float)LED_MS;
    if (lt > 1.0f) lt = 1.0f;
    ledRainbow(lt);
  } else {
    ledOff();
  }
}
