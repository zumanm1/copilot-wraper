#!/bin/bash
# ============================================================
# validate_new_agent.sh
#
# Smoke-tests a new AI agent against C1+C3.
# Requires: curl, python3
#
# Usage:
#   bash API-DOCUMENTATION/stubs/validate_new_agent.sh c10-myagent
#   bash API-DOCUMENTATION/stubs/validate_new_agent.sh c10-myagent "Say hello"
#   bash API-DOCUMENTATION/stubs/validate_new_agent.sh c10-myagent "Say hello" web
#
# Arguments:
#   $1  AGENT_ID   (required)  e.g. c10-myagent
#   $2  PROMPT     (optional)  default: "Tell me a joke"
#   $3  CHAT_MODE  (optional)  work | web  default: work
#
# Exit codes:
#   0 = PASS
#   1 = FAIL
# ============================================================

set -euo pipefail

AGENT_ID="${1:-}"
PROMPT="${2:-Tell me a joke}"
CHAT_MODE="${3:-work}"

C1_URL="${C1_URL:-http://localhost:8000}"
C3_URL="${C3_URL:-http://localhost:8001}"
TIMEOUT="${REQUEST_TIMEOUT:-360}"

# ── Colours ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}  [PASS]${NC} $*"; }
fail() { echo -e "${RED}  [FAIL]${NC} $*"; }
info() { echo -e "         $*"; }
warn() { echo -e "${YELLOW}  [WARN]${NC} $*"; }

# ── Usage guard ───────────────────────────────────────────────
if [ -z "$AGENT_ID" ]; then
    echo "Usage: $0 <agent-id> [prompt] [chat_mode]"
    echo "  e.g. $0 c10-myagent"
    echo "  e.g. $0 c10-myagent \"What is 1+1?\" work"
    exit 1
fi

echo ""
echo "============================================================"
echo "  validate_new_agent.sh"
echo "  Agent:     $AGENT_ID"
echo "  Prompt:    $PROMPT"
echo "  Mode:      $CHAT_MODE"
echo "  C1 URL:    $C1_URL"
echo "  C3 URL:    $C3_URL"
echo "============================================================"
echo ""

FAILED=0

# ── Step 1: C1 health ────────────────────────────────────────
echo "[1/4] Checking C1 health..."
C1_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$C1_URL/health" --max-time 10 2>/dev/null || echo "000")
if [ "$C1_STATUS" = "200" ]; then
    pass "C1 copilot-api is healthy (HTTP 200)"
else
    fail "C1 copilot-api unreachable (HTTP $C1_STATUS)"
    info "Start C1: docker compose up app -d"
    FAILED=1
fi

# ── Step 2: C3 health + pool ─────────────────────────────────
echo "[2/4] Checking C3 browser-auth + PagePool..."
C3_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$C3_URL/health" --max-time 10 2>/dev/null || echo "000")
if [ "$C3_STATUS" = "200" ]; then
    pass "C3 browser-auth is healthy (HTTP 200)"
    # Check pool
    POOL_JSON=$(curl -sf "$C3_URL/status" --max-time 10 2>/dev/null || echo "{}")
    POOL_INIT=$(echo "$POOL_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pool_initialized','?'))" 2>/dev/null || echo "?")
    POOL_AVAIL=$(echo "$POOL_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pool_available','?'))" 2>/dev/null || echo "?")
    POOL_SIZE=$(echo "$POOL_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pool_size','?'))" 2>/dev/null || echo "?")
    if [ "$POOL_INIT" = "True" ] || [ "$POOL_INIT" = "true" ]; then
        pass "C3 PagePool initialized (available=$POOL_AVAIL/$POOL_SIZE)"
    else
        warn "C3 PagePool not initialized (pool_initialized=$POOL_INIT)"
        info "Try: curl -X POST $C3_URL/pool-reset"
        FAILED=1
    fi
else
    fail "C3 browser-auth unreachable (HTTP $C3_STATUS)"
    info "Start C3: docker compose up browser-auth -d"
    FAILED=1
fi

# ── Step 3: M365 session health ───────────────────────────────
echo "[3/4] Checking M365 session health..."
SESSION_JSON=$(curl -sf "$C3_URL/session-health" --max-time 10 2>/dev/null || echo "{}")
SESSION=$(echo "$SESSION_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session','unknown'))" 2>/dev/null || echo "unknown")
if [ "$SESSION" = "active" ]; then
    PROFILE=$(echo "$SESSION_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('profile','?'))" 2>/dev/null || echo "?")
    pass "M365 session active (profile=$PROFILE)"
else
    warn "M365 session: $SESSION"
    info "Sign in via noVNC: http://localhost:6080"
    info "Then retry this script."
    FAILED=1
fi

# ── Step 4: Agent roundtrip ───────────────────────────────────
echo "[4/4] Sending test prompt to agent '$AGENT_ID' via C1..."
info "Prompt: \"$PROMPT\""
info "Timeout: ${TIMEOUT}s (this may take 10–60s on first call)"

PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'model': 'copilot',
    'messages': [{'role': 'user', 'content': sys.argv[1]}],
    'stream': False
}))
" "$PROMPT")

T_START=$(date +%s)
RESPONSE=$(curl -s -X POST "$C1_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "X-Agent-ID: $AGENT_ID" \
    -H "X-Chat-Mode: $CHAT_MODE" \
    --max-time "$TIMEOUT" \
    -w "\n__HTTP_CODE__:%{http_code}" \
    -d "$PAYLOAD" 2>&1)
T_END=$(date +%s)
ELAPSED=$((T_END - T_START))

# Split response body and HTTP code
HTTP_CODE=$(echo "$RESPONSE" | grep -o "__HTTP_CODE__:[0-9]*" | cut -d: -f2 || echo "000")
BODY=$(echo "$RESPONSE" | sed 's/__HTTP_CODE__:[0-9]*$//')

if [ "$HTTP_CODE" = "200" ]; then
    CONTENT=$(echo "$BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception as e:
    print(f'PARSE_ERROR: {e}')
" 2>/dev/null || echo "")
    if [ -n "$CONTENT" ] && [[ "$CONTENT" != PARSE_ERROR* ]]; then
        pass "$AGENT_ID PASSED  (HTTP $HTTP_CODE | ${ELAPSED}s)"
        info "Response preview: ${CONTENT:0:120}..."
    else
        fail "$AGENT_ID: HTTP 200 but empty/invalid content"
        info "Raw body: ${BODY:0:300}"
        FAILED=1
    fi
else
    DETAIL=$(echo "$BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('detail', d.get('error', str(d))))
except Exception:
    print(sys.stdin.read()[:200])
" 2>/dev/null || echo "$BODY")
    fail "$AGENT_ID FAILED  (HTTP $HTTP_CODE | ${ELAPSED}s)"
    info "Error: $DETAIL"
    FAILED=1
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "============================================================"
if [ "$FAILED" -eq 0 ]; then
    echo -e "${GREEN}  RESULT: ALL CHECKS PASSED ✅${NC}"
    echo "  Agent '$AGENT_ID' is correctly wired to C1+C3."
    echo ""
    echo "  Next steps:"
    echo "    • Add to C9 AGENTS list (c9_jokes/app.py) to see in dashboard"
    echo "    • Run full suite: pytest tests/test_e2e_c9_validation.py -v"
    echo "    • Run pairs UI:   cd tests/puppeteer_novnc && node validate_pairs.mjs"
else
    echo -e "${RED}  RESULT: $FAILED CHECK(S) FAILED ❌${NC}"
    echo "  See output above for details."
    echo ""
    echo "  Common fixes:"
    echo "    • Session expired:  sign in at http://localhost:6080"
    echo "    • Pool not ready:   curl -X POST http://localhost:8001/pool-reset"
    echo "    • C1/C3 offline:    docker compose up app browser-auth -d"
fi
echo "============================================================"
echo ""

exit $FAILED
