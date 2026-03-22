#!/bin/bash
# ============================================================
# Container 3 entrypoint
# Starts: Xvfb → x11vnc → noVNC → FastAPI
# ============================================================
set -e

DISPLAY_NUM=99
SCREEN_RES="${VNC_RESOLUTION:-1280x900x24}"
VNC_PORT=5900
NOVNC_PORT=6080
API_PORT=8001

# Remove stale Chrome profile lock files (left by previous container runs).
# Chrome uses dangling symlinks for locking, so we must use [ -L ] (symlink test)
# NOT [ -e ] (existence test) — [ -e ] returns false for dangling symlinks.
PROFILE_DIR="${BROWSER_PROFILE_DIR:-/browser-profile}"
for lock in SingletonLock SingletonCookie SingletonSocket; do
    if [ -L "$PROFILE_DIR/$lock" ] || [ -f "$PROFILE_DIR/$lock" ]; then
        rm -f "$PROFILE_DIR/$lock"
        echo "[browser-auth] Removed stale Chrome lock: $lock"
    fi
done

echo "[browser-auth] Starting display server..."

# Remove stale X11 lock files left by machine restarts or container crashes
rm -f /tmp/.X${DISPLAY_NUM}-lock /tmp/.X11-unix/X${DISPLAY_NUM} 2>/dev/null || true

# 1. Start virtual display (Xvfb)
Xvfb :${DISPLAY_NUM} -screen 0 ${SCREEN_RES} -ac +extension GLX +render -noreset &
XVFB_PID=$!
export DISPLAY=:${DISPLAY_NUM}
sleep 2

# Verify Xvfb started
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "[browser-auth] ERROR: Xvfb failed to start"
    exit 1
fi
echo "[browser-auth] Xvfb running on :${DISPLAY_NUM}"

# 2. Start VNC server (x11vnc, no password for local-only use)
# Note: x11vnc uses -rfbport (not -port) to set the listening port
x11vnc -display :${DISPLAY_NUM} -nopw -rfbport ${VNC_PORT} \
       -xkb -forever -shared -bg -o /tmp/x11vnc.log
sleep 1
echo "[browser-auth] x11vnc running on port ${VNC_PORT}"

# 3. Start noVNC (find the correct web root path)
NOVNC_WEB=""
for path in /usr/share/novnc /usr/share/novnc/utils /opt/novnc; do
    if [ -f "$path/vnc.html" ] || [ -f "$path/index.html" ]; then
        NOVNC_WEB="$path"
        break
    fi
done

if [ -z "$NOVNC_WEB" ]; then
    # Fallback: find it
    NOVNC_WEB=$(find /usr -name "vnc.html" -exec dirname {} \; 2>/dev/null | head -1)
fi

if [ -n "$NOVNC_WEB" ]; then
    # Enable auto-scaling so the full VNC canvas fits the user's browser window.
    # Without this, the bottom of the canvas (where Copilot's input box lives) is clipped.
    if [ -f "$NOVNC_WEB/vnc_auto.html" ]; then
        sed -i "s/getConfigVar('scale', false)/getConfigVar('scale', true)/" "$NOVNC_WEB/vnc_auto.html"
    fi
    websockify --web "$NOVNC_WEB" ${NOVNC_PORT} localhost:${VNC_PORT} &
    echo "[browser-auth] noVNC web UI at http://localhost:${NOVNC_PORT}/vnc.html"
else
    # websockify without web (VNC-only, no browser UI)
    websockify ${NOVNC_PORT} localhost:${VNC_PORT} &
    echo "[browser-auth] websockify running (no noVNC web UI found, raw VNC on port ${NOVNC_PORT})"
fi
sleep 1

# 4. Start FastAPI cookie extractor server
echo "[browser-auth] Starting cookie extractor API on port ${API_PORT}..."
echo "[browser-auth] Open http://localhost:${NOVNC_PORT}/vnc.html to see the browser"
echo "[browser-auth] Trigger: curl -X POST http://localhost:${API_PORT}/extract"
exec uvicorn server:app --host 0.0.0.0 --port ${API_PORT} --log-level info
