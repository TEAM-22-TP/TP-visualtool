#!/bin/bash
set -e

# Start virtual framebuffer
Xvfb :99 -screen 0 1920x1080x24 &
sleep 1

# Start VNC server on display :99
x11vnc -display :99 -forever -nopw -listen 0.0.0.0 -rfbport 5900 &

# Start noVNC (web-based VNC client) on port 6080
/opt/noVNC/utils/novnc_proxy --vnc localhost:5900 --listen 6080 &

# Run the PyQt5 application
cd /app
exec python3 main.py
