#!/bin/bash
# ============================================================
# Container 3 entrypoint
# Starts: Xvfb → x11vnc → noVNC → FastAPI
# ============================================================
set -e

DISPLAY_NUM=99
SCREEN_RES="${VNC_RESOLUTION:-1280x800x24}"

# 1. Start virtual display (Xvfb)
echo "[entrypoint] Starting Xvfb on :${DISPLAY_NUM}"
Xvfb :${DISPLAY_NUM} -screen 0 ${SCREEN_RES} &
export DISPLAY=:${DISPLAY_NUM}
sleep 1

# 2. Start VNC server (x11vnc, no password for local use)
echo "[entrypoint] Starting x11vnc..."
x11vnc -display :${DISPLAY_NUM} -nopw -listen localhost -xkb -forever &
sleep 1

# 3. Start noVNC web UI (websockify proxy to VNC)
echo "[entrypoint] Starting noVNC on port 6080..."
websockify --web /usr/share/novnc 6080 localhost:5900 &
sleep 1

# 4. Start FastAPI cookie extractor server
echo "[entrypoint] Starting browser-auth API on port 8001..."
exec uvicorn server:app --host 0.0.0.0 --port 8001 --log-level info
