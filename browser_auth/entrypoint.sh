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
# Prefer pip-installed websockify (/usr/local/bin) over distro 0.10.x (fewer RFB handshake drops).
WEBSOCKIFY_BIN="websockify"
[ -x /usr/local/bin/websockify ] && WEBSOCKIFY_BIN="/usr/local/bin/websockify"

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
sleep 3

# Verify Xvfb started
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "[browser-auth] ERROR: Xvfb failed to start"
    exit 1
fi
echo "[browser-auth] Xvfb running on :${DISPLAY_NUM}"

# 2. Start VNC server (x11vnc, no password for local-only use)
# Note: x11vnc uses -rfbport (not -port) to set the listening port.
# Do NOT use -bg: wait until the RFB port accepts connections before websockify starts.
# Use 127.0.0.1 (not "localhost") so IPv6 ::1 cannot bypass a v4-only listener.
x11vnc -display :${DISPLAY_NUM} -nopw -rfbport ${VNC_PORT} \
       -xkb -forever -shared -noxrecord -o /tmp/x11vnc.log \
       2>/tmp/x11vnc.stderr.log &
X11VNC_PID=$!
echo "[browser-auth] x11vnc pid ${X11VNC_PID}"

wait_rfb() {
    local i=0
    while [ "$i" -lt 60 ]; do
        if (echo >/dev/tcp/127.0.0.1/${VNC_PORT}) 2>/dev/null; then
            return 0
        fi
        if ! kill -0 "$X11VNC_PID" 2>/dev/null; then
            echo "[browser-auth] ERROR: x11vnc process exited early"
            echo "---- /tmp/x11vnc.log ----"; cat /tmp/x11vnc.log 2>/dev/null || true
            echo "---- /tmp/x11vnc.stderr.log ----"; cat /tmp/x11vnc.stderr.log 2>/dev/null || true
            exit 1
        fi
        i=$((i + 1))
        sleep 0.25
    done
    echo "[browser-auth] ERROR: x11vnc never listened on 127.0.0.1:${VNC_PORT}"
    echo "---- /tmp/x11vnc.log ----"; cat /tmp/x11vnc.log 2>/dev/null || true
    echo "---- /tmp/x11vnc.stderr.log ----"; cat /tmp/x11vnc.stderr.log 2>/dev/null || true
    exit 1
}
wait_rfb
echo "[browser-auth] x11vnc listening on 127.0.0.1:${VNC_PORT}"

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
    # Serve from a writable copy (/usr/share/novnc may be read-only in some images).
    NOVNC_SERVE="/tmp/novnc-web"
    rm -rf "$NOVNC_SERVE"
    mkdir -p "$NOVNC_SERVE"
    cp -a "$NOVNC_WEB/." "$NOVNC_SERVE/"
    # Enable auto-scaling so the full VNC canvas fits the user's browser window.
    if [ -f "$NOVNC_SERVE/vnc_auto.html" ]; then
        sed -i "s/getConfigVar('scale', false)/getConfigVar('scale', true)/" "$NOVNC_SERVE/vnc_auto.html"
    fi
    # Root URL: serve the same client as vnc_auto.html; if the query string is empty, jump once to
    # /?resize=scale&autoconnect=true so the address bar stays on "/" (not /vnc_auto.html).
    if [ -f "$NOVNC_SERVE/vnc_auto.html" ]; then
        cp -a "$NOVNC_SERVE/vnc_auto.html" "$NOVNC_SERVE/index.html"
        python3 <<'PY'
from pathlib import Path
p = Path("/tmp/novnc-web/index.html")
text = p.read_text(encoding="utf-8")
if "<head>" not in text:
    raise SystemExit("noVNC index.html: missing <head>")
inject = (
    "<head>\n"
    "  <script>\n"
    "  (function(){\n"
    '    if (!window.location.search) {\n'
    '      window.location.replace(window.location.pathname + "?resize=scale&autoconnect=true");\n'
    "    }\n"
    "  })();\n"
    "  </script>"
)
text = text.replace("<head>", inject, 1)
p.write_text(text, encoding="utf-8")
PY
    fi
    "${WEBSOCKIFY_BIN}" --web "$NOVNC_SERVE" ${NOVNC_PORT} 127.0.0.1:${VNC_PORT} &
    echo "[browser-auth] noVNC web UI at http://localhost:${NOVNC_PORT}/ (same client as vnc_auto; / adds query once if needed)"
else
    # websockify without web (VNC-only, no browser UI)
    "${WEBSOCKIFY_BIN}" ${NOVNC_PORT} 127.0.0.1:${VNC_PORT} &
    echo "[browser-auth] websockify running (no noVNC web UI found, raw VNC on port ${NOVNC_PORT})"
fi

# Wait until the noVNC HTTP server accepts traffic. Without this, uvicorn can become PID 1 while
# websockify is still binding — health checks on :8001 pass but :6080 is not ready yet, and the
# WebSocket/RFB handshake fails with "connection closed" in the browser.
wait_novnc() {
    local i=0
    while [ "$i" -lt 120 ]; do
        if curl -sf "http://127.0.0.1:${NOVNC_PORT}/" >/dev/null 2>&1; then
            echo "[browser-auth] websockify ready on http://127.0.0.1:${NOVNC_PORT}/"
            return 0
        fi
        i=$((i + 1))
        sleep 0.25
    done
    echo "[browser-auth] ERROR: websockify did not become ready on port ${NOVNC_PORT}"
    exit 1
}
wait_novnc

# 4. Start FastAPI cookie extractor server
echo "[browser-auth] Starting cookie extractor API on port ${API_PORT}..."
echo "[browser-auth] Portal setup: http://localhost:${API_PORT}/setup"
echo "[browser-auth] Open http://localhost:${NOVNC_PORT}/ (or vnc_auto.html) to see the browser"
echo "[browser-auth] Trigger: curl -X POST http://localhost:${API_PORT}/extract"
exec uvicorn server:app --host 0.0.0.0 --port ${API_PORT} --log-level info
