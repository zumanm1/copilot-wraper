#!/bin/bash
# ============================================================
# Container 3 entrypoint
# Starts: Xvfb → x11vnc → noVNC → FastAPI
# ============================================================
set -e

DISPLAY_NUM=99
SCREEN_RES="${VNC_RESOLUTION:-1280x1024x24}"
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

# 1b. Start lightweight window manager (openbox) so Chrome can maximize properly.
# Without a WM, --start-maximized has no effect and Chrome leaves a black strip.
# The openbox-rc.xml config auto-maximizes all windows with no decorations.
OPENBOX_RC="/browser-auth/openbox-rc.xml"
if [ -f "$OPENBOX_RC" ]; then
    openbox --config-file "$OPENBOX_RC" &
else
    openbox --config-file /dev/null &
fi
sleep 0.5
echo "[browser-auth] openbox window manager started"

# 2. Start VNC server (x11vnc, no password for local-only use)
# Note: x11vnc uses -rfbport (not -port) to set the listening port.
# Do NOT use -bg: wait until the RFB port accepts connections before websockify starts.
# Use 127.0.0.1 (not "localhost") so IPv6 ::1 cannot bypass a v4-only listener.
# -clip both: forward RFB cut-text (clipboard) in both directions (host ↔ guest).
x11vnc -display :${DISPLAY_NUM} -nopw -rfbport ${VNC_PORT} \
       -xkb -forever -shared -noxrecord -clip both -o /tmp/x11vnc.log \
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

# 2b. Start clipboard synchronization daemon AFTER x11vnc is confirmed up.
# autocutsel syncs X11 PRIMARY selection ↔ CLIPBOARD so Ctrl+C in Chrome propagates
# to the RFB cut-text buffer that x11vnc -clip both forwards to the noVNC client.
# Must run after x11vnc so the XFIXES extension event source exists.
if command -v autocutsel >/dev/null 2>&1; then
    DISPLAY=:${DISPLAY_NUM} autocutsel &
    DISPLAY=:${DISPLAY_NUM} autocutsel -selection PRIMARY &
    echo "[browser-auth] autocutsel started (clipboard sync enabled)"
else
    echo "[browser-auth] WARNING: autocutsel not found — install it in Dockerfile.browser for clipboard sync"
fi

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
# Idempotent: skip if already injected (sentinel = autoconnect script present)
SENTINEL = "autoconnect=true"
if SENTINEL not in text:
    if "<head>" not in text:
        print("[browser-auth] WARNING: noVNC index.html missing <head> — skipping inject")
    else:
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
        print("[browser-auth] noVNC autoconnect inject applied")
else:
    print("[browser-auth] noVNC autoconnect inject already present — skipping")
PY
    fi
    # Ensure index.html exists (Ubuntu 22.04 novnc has vnc.html but not vnc_auto.html/index.html).
    if [ ! -f "$NOVNC_SERVE/index.html" ] && [ -f "$NOVNC_SERVE/vnc.html" ]; then
        cp "$NOVNC_SERVE/vnc.html" "$NOVNC_SERVE/index.html"
        echo "[browser-auth] Created index.html from vnc.html (vnc_auto.html not present)"
    fi
    # Inject bidirectional clipboard sync into the inline RFB module script in index.html.
    # vnc.html (used as index.html on Ubuntu 22.04) is a minimal client with no ui.js —
    # clipboard sync must be injected directly after the rfb event listeners are set up.
    python3 <<'CLIPHTML'
from pathlib import Path
p = Path("/tmp/novnc-web/index.html")
if p.exists():
    text = p.read_text(encoding="utf-8")
    SENTINEL = "CLIPBOARD-AUTOSYNC"
    if SENTINEL not in text:
        # Anchor: the last rfb property set before the IIFE closes
        anchor = '            rfb.resizeSession = WebUtil.getConfigVar(\'resize\', false);'
        inject = (
            anchor + "\n"
            "            // CLIPBOARD-AUTOSYNC: VNC→host — when VNC clipboard changes, write to host\n"
            "            rfb.addEventListener('clipboard', function(e) {\n"
            "                if (e.detail && e.detail.text && navigator.clipboard) {\n"
            "                    navigator.clipboard.writeText(e.detail.text).catch(function() {});\n"
            "                }\n"
            "            });\n"
            "            // CLIPBOARD-AUTOSYNC: host→VNC — on window focus, push host clipboard to VNC\n"
            "            window.addEventListener('focus', function() {\n"
            "                if (navigator.clipboard && navigator.clipboard.readText) {\n"
            "                    navigator.clipboard.readText().then(function(t) {\n"
            "                        if (t) rfb.clipboardPasteFrom(t);\n"
            "                    }).catch(function() {});\n"
            "                }\n"
            "            });\n"
            "            // CLIPBOARD-AUTOSYNC: host→VNC — intercept paste events\n"
            "            document.addEventListener('paste', function(e) {\n"
            "                var cbd = e.clipboardData || window.clipboardData;\n"
            "                var t = cbd ? cbd.getData('text') : '';\n"
            "                if (t) rfb.clipboardPasteFrom(t);\n"
            "            });"
        )
        if anchor in text:
            text = text.replace(anchor, inject, 1)
            p.write_text(text, encoding="utf-8")
            print("[browser-auth] noVNC index.html clipboard autosync injected")
        else:
            print("[browser-auth] WARNING: rfb.resizeSession anchor not found — clipboard inject skipped")
    else:
        print("[browser-auth] noVNC index.html clipboard autosync already present — skipping")
else:
    print("[browser-auth] WARNING: index.html not found — clipboard inject skipped")
CLIPHTML
    # Patch app/ui.js for seamless clipboard sync (both directions):
    #   VNC→host: clipboardReceive writes to navigator.clipboard automatically
    #   host→VNC: focus event + paste event push host clipboard to VNC session
    python3 <<'CLIPPY'
from pathlib import Path
ui = Path("/tmp/novnc-web/app/ui.js")
if ui.exists():
    text = ui.read_text(encoding="utf-8")
    SENTINEL = "AUTO-SYNC-CLIPBOARD"
    if SENTINEL not in text:
        # Patch 1: VNC → host clipboard (in clipboardReceive)
        old1 = "        document.getElementById('noVNC_clipboard_text').value = e.detail.text;"
        new1 = (
            "        document.getElementById('noVNC_clipboard_text').value = e.detail.text;\n"
            "        // AUTO-SYNC-CLIPBOARD: push VNC clipboard to host browser clipboard\n"
            "        if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {\n"
            "            navigator.clipboard.writeText(e.detail.text).catch(function() {});\n"
            "        }"
        )
        # Patch 2: host → VNC (focus + paste event listeners in addClipboardHandlers)
        old2 = (
            "        document.getElementById(\"noVNC_clipboard_clear_button\")\n"
            "            .addEventListener('click', UI.clipboardClear);\n"
            "    },"
        )
        new2 = (
            "        document.getElementById(\"noVNC_clipboard_clear_button\")\n"
            "            .addEventListener('click', UI.clipboardClear);\n"
            "        // AUTO-SYNC-CLIPBOARD: host browser clipboard → VNC on window focus\n"
            "        window.addEventListener('focus', function() {\n"
            "            if (!UI.rfb) return;\n"
            "            if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.readText) {\n"
            "                navigator.clipboard.readText().then(function(text) {\n"
            "                    if (text) {\n"
            "                        document.getElementById('noVNC_clipboard_text').value = text;\n"
            "                        UI.rfb.clipboardPasteFrom(text);\n"
            "                    }\n"
            "                }).catch(function() {});\n"
            "            }\n"
            "        });\n"
            "        // AUTO-SYNC-CLIPBOARD: paste event from host browser → VNC\n"
            "        document.addEventListener('paste', function(e) {\n"
            "            if (!UI.rfb) return;\n"
            "            var cbd = e.clipboardData || window.clipboardData;\n"
            "            var txt = cbd ? cbd.getData('text') : '';\n"
            "            if (txt) {\n"
            "                document.getElementById('noVNC_clipboard_text').value = txt;\n"
            "                UI.rfb.clipboardPasteFrom(txt);\n"
            "            }\n"
            "        });\n"
            "    },"
        )
        p1 = old1 in text
        p2 = old2 in text
        if p1:
            text = text.replace(old1, new1, 1)
        if p2:
            text = text.replace(old2, new2, 1)
        if p1 or p2:
            ui.write_text(text, encoding="utf-8")
            print(f"[browser-auth] noVNC clipboard auto-sync patched (VNC→host={p1}, host→VNC={p2})")
        else:
            print("[browser-auth] WARNING: clipboard patch anchors not found in ui.js — skipping")
    else:
        print("[browser-auth] noVNC clipboard auto-sync already patched — skipping")
else:
    print("[browser-auth] WARNING: /tmp/novnc-web/app/ui.js not found — clipboard patch skipped")
CLIPPY
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
    echo "[browser-auth] WARNING: websockify not ready on port ${NOVNC_PORT} after timeout — continuing anyway"
    return 0
}
wait_novnc

# 4. Start FastAPI cookie extractor server
# IMPORTANT: Do NOT use 'exec' here. If uvicorn is exec'd as PID 1, a hot-reload
# (triggered by __pycache__ writes or .log file changes in the bind-mounted /browser-auth)
# will SIGTERM the process, which kills the entire container — taking x11vnc and
# websockify with it and causing noVNC "connection closed" errors.
# Run uvicorn in the background and use a wait loop so the container stays alive.
echo "[browser-auth] Starting cookie extractor API on port ${API_PORT}..."
echo "[browser-auth] Portal setup: http://localhost:${API_PORT}/setup"
echo "[browser-auth] Open http://localhost:${NOVNC_PORT}/ (or vnc_auto.html) to see the browser"
echo "[browser-auth] Trigger: curl -X POST http://localhost:${API_PORT}/extract"
uvicorn server:app --host 0.0.0.0 --port ${API_PORT} --log-level info --reload --reload-dir /browser-auth &
UVICORN_PID=$!
echo "[browser-auth] uvicorn pid ${UVICORN_PID}"

# Keep the container alive; if any critical process exits, log it and exit cleanly.
wait_procs() {
    while true; do
        if ! kill -0 "$XVFB_PID" 2>/dev/null; then
            echo "[browser-auth] ERROR: Xvfb (pid $XVFB_PID) exited — restarting container"
            exit 1
        fi
        if ! kill -0 "$X11VNC_PID" 2>/dev/null; then
            echo "[browser-auth] ERROR: x11vnc (pid $X11VNC_PID) exited — restarting container"
            exit 1
        fi
        sleep 5
    done
}
wait_procs
