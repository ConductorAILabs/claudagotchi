#!/usr/bin/env python3
"""Anthropic API proxy that updates the token state file in real time.

Sits between Claude Code and api.anthropic.com. Forwards every request
transparently. While streaming the SSE response back, it sniffs
`message_start` and `message_delta` events and bumps session.input /
session.output in /tmp/claude_token_state.json on every chunk —
giving the on-desk display per-token-ish ticking instead of waiting
for the Stop hook at end of turn.

Run:
    python3 token_proxy.py &
    ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
"""

import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import urllib.error

PORT     = int(os.environ.get("TOKEN_PROXY_PORT", 8787))
UPSTREAM = os.environ.get("TOKEN_PROXY_UPSTREAM", "https://api.anthropic.com")
STATE    = "/tmp/claude_token_state.json"

state_lock = threading.Lock()


def _empty_state():
    return {
        "session": {"input": 0, "output": 0, "requests": 0},
        "hour_tokens": 0, "cache_eff": 0.0, "top_model": "",
        "weekly": {"week_tokens": 0},
        "session_pct": -1.0, "week_pct": -1.0,
        "live": {"output": 0, "cache_read": 0, "cache_write": 0},
    }


def write_state_delta(input_delta=0, output_delta=0, cache_write_delta=0):
    """Bump session.input / session.output / session.cache_write by deltas."""
    if not (input_delta or output_delta or cache_write_delta):
        return
    with state_lock:
        try:
            with open(STATE) as f:
                d = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            d = _empty_state()
        s = d.setdefault("session", {})
        s["input"]       = int(s.get("input", 0))       + int(input_delta)
        s["output"]      = int(s.get("output", 0))      + int(output_delta)
        s["cache_write"] = int(s.get("cache_write", 0)) + int(cache_write_delta)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.rename(tmp, STATE)


def write_live(out=None, cache_read=None, cache_write=None):
    """Update live.output / live.cache_read / live.cache_write atomically.
    Pass None to leave a field untouched. These mirror the chat status line:
    output is what the chat shows ('↑ X tokens'); cache_read/cache_write are
    the hidden cost the chat omits."""
    with state_lock:
        try:
            with open(STATE) as f:
                d = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            d = _empty_state()
        live = d.setdefault("live", {"output": 0, "cache_read": 0, "cache_write": 0})
        if out         is not None: live["output"]      = int(out)
        if cache_read  is not None: live["cache_read"]  = int(cache_read)
        if cache_write is not None: live["cache_write"] = int(cache_write)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.rename(tmp, STATE)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):   self._proxy()
    def do_GET(self):    self._proxy()
    def do_PUT(self):    self._proxy()
    def do_DELETE(self): self._proxy()

    def _proxy(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None

        url = UPSTREAM.rstrip("/") + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        for k, v in self.headers.items():
            # Drop Accept-Encoding so Anthropic returns plain text/event-stream;
            # otherwise we forward gzip bytes correctly but can't sniff them.
            if k.lower() in ("host", "content-length", "connection",
                             "accept-encoding"):
                continue
            req.add_header(k, v)
        req.add_header("Accept-Encoding", "identity")

        # Per-request state for sniffing message_delta running totals
        state = {"baseline_out": 0}

        try:
            with urllib.request.urlopen(req, timeout=600) as up:
                self.send_response(up.status)
                for k, v in up.headers.items():
                    if k.lower() in ("transfer-encoding", "content-length", "connection"):
                        continue
                    self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()

                buf = b""
                while True:
                    chunk = up.read(2048)
                    if not chunk:
                        break
                    # Forward as one HTTP chunk
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                    self.wfile.flush()
                    # Sniff SSE events
                    buf += chunk
                    while b"\n\n" in buf:
                        event, buf = buf.split(b"\n\n", 1)
                        self._sniff(event, state)
                # End-of-chunks
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                if buf:
                    self._sniff(buf, state)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            data = e.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            sys.stderr.write(f"[proxy] error: {e}\n")
            try:
                self.send_response(502)
                msg = json.dumps({"error": str(e)}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            except Exception:
                pass

    def _sniff(self, event_bytes, state):
        """Parse one SSE event block and bump state file on token events."""
        try:
            for line in event_bytes.split(b"\n"):
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == b"[DONE]":
                    continue
                data = json.loads(payload)
                typ = data.get("type")
                if typ == "message_start":
                    usage = (data.get("message") or {}).get("usage") or {}
                    inp = int(usage.get("input_tokens", 0))
                    out = int(usage.get("output_tokens", 0))
                    cw  = int(usage.get("cache_creation_input_tokens", 0))
                    # Bump session cumulatively (cumulative input+output feeds
                    # the pink big-number).
                    write_state_delta(
                        input_delta=inp,
                        output_delta=out,
                        cache_write_delta=cw,
                    )
                    state["baseline_out"] = out
                    # Reset the per-request live numbers (small numbers
                    # under pink). cache_write is fixed at start of request.
                    write_live(out=out, cache_write=cw)
                elif typ == "message_delta":
                    usage = data.get("usage") or {}
                    out = int(usage.get("output_tokens", 0))
                    delta = out - state["baseline_out"]
                    if delta > 0:
                        write_state_delta(output_delta=delta)
                        state["baseline_out"] = out
                    # Mirror the running output into live so green ticks.
                    write_live(out=out)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[proxy] {fmt % args}\n")


def main():
    print(f"[proxy] listening on http://127.0.0.1:{PORT}")
    print(f"[proxy] forwarding to {UPSTREAM}")
    print(f"[proxy] writing token state to {STATE}")
    print(f"[proxy] start a new claude session with:")
    print(f"          ANTHROPIC_BASE_URL=http://127.0.0.1:{PORT} claude")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
