#!/usr/bin/env bash
# cookie_manager/setup.sh
# =======================
# One-time setup: installs Python deps, registers the macOS LaunchAgent so
# cookies are refreshed every 15 minutes starting on every login.
#
# Usage:
#   cd /path/to/copilot-openai-wrapper
#   bash cookie_manager/setup.sh
#
# To uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.copilot-wrapper.cookies.plist
#   rm ~/Library/LaunchAgents/com.copilot-wrapper.cookies.plist

set -euo pipefail

# ── Resolve project root (parent of this script's directory) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_NAME="com.copilot-wrapper.cookies.plist"
PLIST_TEMPLATE="$SCRIPT_DIR/$PLIST_NAME.template"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "======================================================"
echo " Copilot Wrapper — Cookie Manager Setup"
echo "======================================================"
echo " Project dir : $PROJECT_DIR"
echo " LaunchAgent : $PLIST_DEST"
echo ""

# ── 1. Install Python dependencies ───────────────────────────────────────────
echo "[1/4] Installing Python dependencies…"
pip3 install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "      Done."

# ── 2. Expand template → plist ───────────────────────────────────────────────
echo "[2/4] Creating LaunchAgent plist…"
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_TEMPLATE" > "$PLIST_DEST"
echo "      Written to $PLIST_DEST"

# ── 3. Unload any existing version (idempotent) ──────────────────────────────
echo "[3/4] Registering LaunchAgent…"
if launchctl list | grep -q "com.copilot-wrapper.cookies" 2>/dev/null; then
    echo "      Unloading existing agent…"
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi
launchctl load "$PLIST_DEST"
echo "      LaunchAgent loaded — will run every 15 min and on every login."

# ── 4. Initial cookie extraction ─────────────────────────────────────────────
echo "[4/4] Running initial cookie extraction…"
echo ""
echo "  NOTE: macOS may display an 'Allow python3 to access Keychain?'"
echo "  dialog. Click 'Always Allow' to permit automatic Chrome cookie"
echo "  decryption without future prompts."
echo ""
cd "$PROJECT_DIR"
python3 cookie_manager/service.py --once 2>&1 | head -40 || \
    python3 -c "
import sys
sys.path.insert(0, 'cookie_manager')
from extractor import extract_all
from updater import update_and_reload
cookies = extract_all()
result = update_and_reload('.env', cookies)
print('Env changed:', result['env_changed'])
print('Reload OK  :', result['reload_ok'])
"

echo ""
echo "======================================================"
echo " Setup complete!"
echo ""
echo " Next steps:"
echo "  1. Check .env now contains your real cookie values:"
echo "     grep BING_COOKIES .env"
echo ""
echo "  2. Start the Docker app:"
echo "     docker compose up app -d"
echo ""
echo "  3. Verify the cookie manager is running:"
echo "     launchctl list | grep copilot-wrapper"
echo ""
echo "  4. View logs:"
echo "     tail -f cookie_manager/cookie_manager.log"
echo "======================================================"
