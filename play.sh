#!/bin/bash
# Launch the Claudagotchi viewer. Needs a real terminal (curses).
cd "$(dirname "$0")" || exit 1
exec python3 view.py
