<div align="center">

# CONDUCTOR LABS TOKEN TRACKER

### A desk-sized window into Claude Code, in real time.

`pink × purple × black` &nbsp;·&nbsp; `monospace` &nbsp;·&nbsp; `~800 LOC` &nbsp;·&nbsp; `$15 board`

</div>

---

```
    ╭──────────────────────────────────────╮     splash
    │                                      │     ╭──────────────────────╮
    │     CONDUCTOR LABS TOKEN TRACKER     │     │  CONDUCTOR AI LABS   │
    │                                      │     │                      │
    │              413,142                 │     │       TOKEN          │
    │       +2,654          +215           │     │      TRACKER         │
    │                                      │     ╰──────────────────────╯
    │         ╭─────╮      ╭─────╮         │
    │         │ 15% │      │ 53% │         │
    │         ╰─────╯      ╰─────╯         │
    │           5 HR        WEEK           │
    │                                      │
    ╰──────────────────────────────────────╯
              1.47" ST7789 · 320×172
```

Pink is *today*. Green is *what just happened* — the per-request output count that matches the `↑ X tokens` indicator in your chat status. Blue is *what you didn't see happen* — the cache_write Anthropic charged you for and your chat counter never showed. The WS2812 LED swirls a 2.5-second rainbow on every tick at 65% brightness, calibrated so each token spend is unmistakable from across the room.

---

## At a glance

| | |
| --- | --- |
| **Board** | Waveshare ESP32-S3-LCD-1.47 (8 MB PSRAM, 16 MB flash) |
| **Display** | 172×320 ST7789, SPI, landscape |
| **Accent** | Onboard WS2812 RGB LED (GPIO 38) |
| **Host** | macOS daemons (~150 LOC bridge + ~150 LOC API proxy) |
| **Firmware** | Arduino-CLI, Adafruit GFX, ArduinoJson |
| **Update paths** | (a) Claude Code hooks → JSON → USB-CDC<br>(b) Anthropic SSE proxy → JSON → USB-CDC<br>(c) 5-second truth-checker reconciles JSONL truth ↔ state file |
| **Latency** | per-chunk streaming via proxy<br>~1.2 s push cooldown bunches rapid mini-ticks into one meaningful jump |
| **Boot time** | < 1 second from power-on to splash |
| **Total LOC** | ~1,200 lines (firmware + host + reconciler + truth-test) |

---

## The itch

I use Claude Code all day. Every prompt, every tool call, every "just one more turn" — tokens. The status line in the terminal is honest enough, but it's *in* the terminal, which is also where I'm trying to work. I wanted the number somewhere I could glance at without context-switching: a tiny screen on the desk, glowing pink, ticking up the moment I press enter.

So I built one.

> **The best ambient displays don't compete for attention. They wait.**

---

## What it is

A 1.47" ST7789 LCD, driven by an ESP32-S3, that shows five things:

- The live total of tokens Claude has spent for me today (big pink number)
- A green per-request `+output` that ticks during a streaming response and matches the chat's `↑ X tokens`
- A blue per-request `+cache_write` that reveals the bill the chat status line never tells you about
- A 5-hour-window ring (current Anthropic session limit)
- A weekly ring (current Anthropic weekly limit)

The screen palette is pink, purple, lavender, and black — calm, monospace, no chrome. The two small numbers fade away after four seconds of no change, so between bursts the screen settles back to *just* the big pink and the rings. The actual *animation* — a rainbow swirl during each value change — happens on the onboard WS2812 LED, off to the side. The screen itself never twitches.

That separation matters:

> The screen is a **status surface**. The LED is an **event**.

I don't want my peripheral vision working overtime. I want it to *react* when something happens, then settle.

The cache_write number is the one that surprised me most. On a long Claude Code session with prompt caching working hard, the chat's `↑ X tokens` reads four digits while the blue number rolls past six. Most users never realize how much of their bill that is until the screen forces them to.

---

## Architecture, end to end

Two independent producers feed the same JSON state file. Either or both can run.

```
   ┌──────────────────────┐                       ┌──────────────────────┐
   │  Claude Code session │                       │  Claude Code session │
   │  (hook events)       │                       │  (streaming SSE)     │
   └──────────┬───────────┘                       └──────────┬───────────┘
              │ Stop · PostToolUse · Prompt                  │ ANTHROPIC_BASE_URL=
              ▼                                              ▼ http://127.0.0.1:8787
   ┌──────────────────────┐                       ┌──────────────────────┐
   │  hook.sh /           │                       │  token_proxy.py      │
   │  prompt_hook.sh      │                       │  (SSE sniffer)       │
   └──────────┬───────────┘                       └──────────┬───────────┘
              │ atomic write                                 │ chunk-by-chunk
              │                                              │
              └────────────────────┬─────────────────────────┘
                                   ▼
                  ┌──────────────────────────────────────┐
                  │  /tmp/claude_token_state.json        │
                  └──────────────────┬───────────────────┘
                                     │  poll mtime · 100 ms
                                     ▼
                  ┌──────────────────────────────────────┐
                  │  display_bridge.py  (macOS daemon)   │
                  │  cooldown 200 ms, dedupes payloads   │
                  └──────────────────┬───────────────────┘
                                     │  shell-redirect to tty
                                     ▼
                  ┌──────────────────────────────────────┐
                  │  /dev/cu.usbmodem101    (USB-CDC)    │
                  └──────────────────┬───────────────────┘
                                     ▼
                  ┌──────────────────────────────────────┐
                  │  ESP32-S3 firmware                   │
                  │    ├── ST7789 render (1.2 s ease)    │
                  │    ├── pink today / green +out /     │
                  │    │   blue +cache_write (4 s hide)  │
                  │    └── WS2812 rainbow (2.5 s, 65%)   │
                  └──────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │  truth_checker.py  (every 5 s)                               │
   │    JSONL truth  vs  state.json  →  snap up on any drift,     │
   │    snap down only past 100K (lets the proxy lead mid-stream) │
   └──────────────────────────────────────────────────────────────┘
```

Five small pieces, each doing one job:

**1. Claude Code hooks** (`Stop`, `PostToolUse`, `UserPromptSubmit`) write a JSON state file. The first two run an analyzer that scans every session JSONL in `~/.claude/projects/` and sums tokens for today.

The third is the trick: when I press enter, the prompt hook bumps the cached number by `prompt_len / 4` *before* Claude has spent a single token. So the screen reacts instantly to a keystroke instead of waiting for the first tool call.

**2. A macOS daemon** watches the state file's mtime every 100 ms. When it changes, it shells the JSON out to the USB-CDC tty. Just `cat > /dev/cu.usbmodem1101`. (More on *why* shell-redirect in a second.)

**3. The firmware** parses the JSON, captures the previous value as `prev`, sets the new one as `tgt`, and runs a 1.2-second ease-out tween. During the tween the WS2812 rainbow-spins; when the tween finishes the LED goes dark. The number on screen lerps from `prev` to `tgt`.

---

## The hard parts

### USB-CDC on macOS is haunted

I tried `pyserial` first. Worked for a minute, then started silently dropping writes — the bridge log said "pushed 216 bytes," the screen disagreed. I tried opening the tty with raw `os.write` on a held-open file descriptor. Same problem.

The only thing that reliably gets bytes to the chip on macOS, every single time, is forking a shell and redirecting:

```bash
bash -c 'echo "$payload" > /dev/cu.usbmodem1101'
```

It's ugly. It's the only thing that works. The bridge is built around it.

### Two USB ports, only one shows up sometimes

The S3 enumerates as **both** a USB-JTAG port (for `esptool`) and a USB-CDC port (for `Serial`). After a flash, sometimes only the JTAG port comes back. Solution: unplug, replug. Annoying, but consistent.

### Defining "today"

"Today's tokens" sounds simple. But Claude Code writes one JSONL per session, and a single day might span ten sessions across five projects. The analyzer walks every JSONL modified today, parses ISO-8601 timestamps line-by-line, and sums anything since local midnight.

Switching projects doesn't reset the count. That was the right call — a per-session counter would be a step backwards from the status line that already exists.

### 21 billion phantom tokens

A user glanced at the JSON state file one afternoon and saw this:

```
WED 2026-05-06   20,243,176,014 tokens
TOTAL WEEK       20,924,180,816 tokens
```

Twenty billion tokens. In a day. By one person. On a single Max plan.

The screen *looked* fine — the percentage rings come from `/tmp/claude_rate_limits.json` (real Anthropic limits), so the bug hid behind the rings. But the underlying weekly aggregator was producing nonsense.

The cause was a classic logging-pattern trap. The old `weekly.py` read `~/.claude/token_analysis.jsonl` — an append-only log written every time a hook fires — and summed `last.input + last.output` per record:

```python
# the bug
tok = int(last["input"]) + int(last["output"])
by_date[d] = by_date.get(d, 0) + tok
```

But `last` is a **cumulative session snapshot**, not a delta. Every hook fire writes the running session total. With ~1000 hook fires per day, each carrying a multi-million-token cumulative count, you sum a triangle: `1 + 2 + 3 + ... + n` quietly explodes into the billions.

The fix doesn't try to dedupe the snapshots. It throws the whole log out and uses the same source of truth `hook.sh` already trusts: the project JSONLs in `~/.claude/projects/`. Each line there is one API request, so token counts are per-request deltas — safe to sum.

```python
# the fix — sum per-request deltas, not cumulative snapshots
for line in jsonl:
    line_tok = sum(int(v) for k, v in PAT_TOK.findall(line))
    by_date[d] += line_tok
```

Result: **20,924,180,816 → 36,782,103.** Six orders of magnitude saner. The ring percentages didn't move (they were never wrong), but the underlying data is now coherent and ready for whatever v3 view comes next.

> Append-only logs whose records carry running totals are a footgun. Sum the deltas, not the snapshots.

### The hooks-aren't-real-time problem

The first version drove the screen entirely from Claude Code's hook events: `UserPromptSubmit` on Enter, `PostToolUse` after each tool call, `Stop` at end of turn. For tool-heavy work this *felt* like real-time — five tools in a row, five ticks. But for plain text replies, no hook fires during the 5–30 seconds Claude is streaming a response. The screen sat frozen. Then `Stop` fired and it jumped by thousands of tokens at once.

The honest answer was that the architecture didn't actually have a hook to attach to. So I built one.

### The token proxy

`token_proxy.py` is a ~150-line stdlib HTTP server that sits between Claude Code and `api.anthropic.com`. Claude Code points at it via `ANTHROPIC_BASE_URL=http://127.0.0.1:8787`. The proxy forwards every request transparently, but as the SSE response streams back, it sniffs the chunks:

```
event: message_start          → bump session.input by usage.input_tokens
event: content_block_delta    → (forward through, no count)
event: message_delta          → bump session.output by usage.output_tokens delta
event: message_stop           → reset per-message baseline
```

Because `message_delta` events fire every few hundred output tokens, the screen now ticks **during** the response, not just at the end. The hook path still runs in parallel as a backup and for the moments the proxy isn't in the loop (anything that doesn't go through the API — prompt submission, tool calls).

The core trick is that the proxy doesn't care what the request *is*. It forwards bytes, sniffs SSE for two specific event types, writes deltas to the state file. Anthropic could ship new event types tomorrow and the proxy would still pass traffic correctly; it just might miss a few token counts until the sniffer learns them.

> The "real-time" was never going to come from the hooks. It came from owning the wire.

### The gzip wall

When I rebuilt the proxy to also surface per-request `live.output` and `live.cache_write`, the screen stayed on splash. The proxy log showed `POST /v1/messages 200` — bytes were flowing — but no SSE events were being parsed. Zero. Adding a debug print on the first chunk made it obvious:

```
[proxy] first chunk first 100 bytes: b'\x1f\x8b\x08\x00\x00\x00\x00\x00...'
[proxy] response Content-Encoding: gzip
```

The proxy was forwarding the raw gzip bytes correctly to Claude Code (which decompressed them), but trying to parse them as plain SSE text on its own. The `\n\n` event boundaries don't exist in compressed streams, so `_sniff` was never called, and every JSON parse silently failed.

The fix is one line:

```python
# strip Accept-Encoding so Anthropic returns plain text/event-stream
req.add_header("Accept-Encoding", "identity")
```

The bandwidth cost is invisible at SSE sizes; the readability win is total. It's the kind of bug that's nearly impossible to find without dumping bytes — and instantly obvious once you do.

### The 280-byte cliff

The slim payload that gets shelled to the chip has a hard ceiling. Push more than ~280 bytes through USB-CDC during a render frame and the firmware starts logging `ERR IncompleteInput` — the JSON arrives truncated mid-parse because the chip's buffer overflows while it's busy drawing. The original slim was tight at 263 bytes. Adding the `live` block pushed it over.

The fix wasn't to make USB-CDC faster. It was to *send less*. Anything the firmware doesn't render gets dropped from the slim:

```python
# only fields the firmware actually renders end up in the payload
slim = {
    "session":     {"input": ..., "output": ...},
    "session_pct": ..., "week_pct": ...,
    "live":        {"output": ..., "cache_write": ...},
}
```

The minimum-viable payload is now ~135 bytes — half the cliff height. Plenty of headroom for the next field that earns its keep.

> The cheapest way past a buffer limit is to need less buffer.

### Bunching the rapid ticks

Once the proxy was sniffing every `message_delta` event, the screen got *too* responsive. A long streaming response would fire 10–15 deltas, each ~40 tokens, each triggering a 1.2-second tween animation that got immediately interrupted by the next one. The number on screen never settled. It just *churned*.

The fix is in the bridge, not the firmware: raise the push cooldown from 200 ms to 1200 ms. Multiple proxy ticks within the cooldown window collapse — only the latest cumulative value gets pushed. Five rapid `+40`s become one `+200` jump that animates fully before the next one starts.

```python
COOLDOWN_MS = 1200   # bunches rapid proxy ticks into one meaningful jump
                     # rather than a flurry of small animations
```

That's the one tunable that's most worth tuning. Faster cooldowns feel jittery; slower ones feel laggy. 1200 ms is the value where every visible jump *means* something.

### Calibrating to /usage

For a while the screen's 5-hour and weekly rings were exactly 1% ahead of `/usage`. Both pull from Anthropic's rate-limit endpoint, but `/usage` caches its result on dialog-open while the screen captures fresh values from `statusLine` on every redraw. Sampling races.

I tried gating screen refresh to Stop events only — that helped, but didn't fully close the gap. The cleanest solution turned out to be a literal +1 calibration in the bridge slim:

```python
if _spct >= 0: _spct = min(100.0, _spct + 1.0)
if _wpct >= 0: _wpct = min(100.0, _wpct + 1.0)
```

Honest about what it is — not a model of the rate limit, just a constant offset chosen empirically so the rings always agree with the dialog the user actually sees.

> Sometimes the right fix is the boring one. A literal +1.

### Snap on any drift

The original `truth_checker.py` allowed up to 2,000 tokens of drift between the JSONL and the state file before snapping. That tolerance was there to protect the proxy's mid-stream lead — when it ticks `session.output` per chunk before the JSONL line is written, state legitimately leads truth.

But "drift up to 2,000" meant the screen was sometimes *quietly* a few hundred tokens off. For a tool whose entire pitch is *honesty*, that's a tax I didn't want to keep paying.

The fix is asymmetric:

```python
SNAP_UP_DRIFT  = 0       # state behind truth — exact match always
SNAP_DOWN_HIGH = 100000  # state ahead by >this — proxy double-counted

if drift < -SNAP_UP_DRIFT or drift > SNAP_DOWN_HIGH:
    s["input"]  = truth_in
    s["output"] = truth_out
```

State behind truth is always wrong, so snap immediately. State ahead is fine — that's just the proxy doing its job. Only an absurd lead (~100K) signals something has actually gone wrong.

The end-to-end truth test now reports zero drift across `JSONL → state.json → bridge slim` for ordinary work, and recovers to zero within 5 seconds of any drift. The exact-match guarantee was always the goal; tightening this one constant was what made it real.

### What counts as a token

A short reply that *looks* like 900 tokens often shows up on the screen as `+3,000`. The gap is **extended thinking**: internal reasoning tokens that Anthropic counts in `output_tokens` but never sends to the client as visible text. They're the same units that count against your rate limit, so showing them is the honest thing to do — but it does mean the popup is bigger than your eyeballs expect.

If you want to subtract them, the proxy has the data: thinking arrives as `content_block_delta` events with `delta.type == "thinking_delta"`. We just choose to count everything because the rate-limit number is what actually matters when you're watching a budget.

---

## The history

```
   v1 ─────────────────────────────►  v2
   ─────                              ─────
   Raspberry Pi                       ESP32-S3-LCD-1.47
   Flask server                       Arduino-CLI firmware
   ~10 s boot                         < 1 s boot
   ~3 W                               ~80 mA
   $40+                               $15
```

V1 was a Pi running Flask with the same JSON contract. It worked, but a Pi is overkill for a number on a screen, and the boot time was annoying when I unplugged it. The migration kept the bridge daemon and the hook scripts unchanged — only the wire between the JSON file and the screen got swapped out.

That's the part I'm proudest of architecturally: the **JSON state file** is the contract. Anything that can read JSON and drive a display can be the next v3.

---

## What I'd do differently

- **Launchd-ify all three daemons.** Right now I `nohup` the bridge, the proxy, and the truth-checker manually. Three launchd agents would survive reboots without thinking about it.
- **Back up the stock firmware properly.** I dumped 320 KB of app0 and the bootloader before I started, but didn't dump FFAT. Can't fully restore the factory demo.
- **HTTPS on the proxy.** Currently it accepts HTTP on localhost. Fine for one machine, but if I ever wanted to run the proxy on a separate box, I'd want TLS with a self-signed cert.
- **Subtract thinking tokens optionally.** Add a flag so the screen can show visible-output-only when I want a number that matches what I read.
- **Health LED.** A brief red blink on the WS2812 if any of the three daemons stops, so a stale screen doesn't quietly mislead me.

---

## What I learned

The best ambient displays don't compete for attention. They wait.

The screen here is calm on purpose — pink number, two rings, two labels. The *event* (a token spend) is a tiny rainbow, off to the side. Glance at it, get the number, look away. That's it.

The whole project is maybe 800 lines of code and a $15 board — and it changes how token usage *feels* during a long Claude Code session.

> Sometimes the smallest tools are the ones you reach for most.

---

<div align="center">

`built with claude code` &nbsp;·&nbsp; `flashed with arduino-cli` &nbsp;·&nbsp; `lives on my desk`

</div>
