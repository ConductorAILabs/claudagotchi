#!/bin/bash
# Run on the Pi to start the token display server.
# Can also be installed as a systemd service.

cd /home/pi/chromasound/v8/token_display
LG_WD=/tmp python3 pi_display.py
