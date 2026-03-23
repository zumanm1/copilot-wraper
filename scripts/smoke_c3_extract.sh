#!/usr/bin/env bash
# smoke_c3_extract.sh — validate C3 POST /extract JSON after manual noVNC sign-in.
#
# Does NOT log into Microsoft (no CI secrets). You complete login in noVNC first.
#
# Usage:
#   ./scripts/smoke_c3_extract.sh
#   BROWSER_AUTH_URL=http://127.0.0.1:8001 C1_URL=http://127.0.0.1:8000 ./scripts/smoke_c3_extract.sh
#
# Optional: one C1 chat probe (may 403/500 if Copilot rejects session):
#   WITH_CHAT=1 ./scripts/smoke_c3_extract.sh
#
set -euo pipefail

BROWSER_AUTH_URL="${BROWSER_AUTH_URL:-http://127.0.0.1:8001}"
C1_URL="${C1_URL:-http://127.0.0.1:8000}"
WITH_CHAT="${WITH_CHAT:-0}"

base="${BROWSER_AUTH_URL%/}"

echo "[1/3] GET $base/health"
curl -sf "$base/health" | grep -q 'browser-auth' || {
  echo "FAIL: C3 health missing or not browser-auth"
  exit 1
}
echo "      OK"

echo "[2/3] POST $base/extract"
RESP=$(curl -sf -X POST "$base/extract") || {
  echo "FAIL: POST /extract HTTP error"
  exit 1
}

echo "$RESP" | python3 -c '
import json, sys
j = json.load(sys.stdin)
st = j.get("status")
if st != "ok":
    print("FAIL: extract status=%r body=%s" % (st, j))
    sys.exit(1)
names = set(j.get("cookie_names") or [])
print("      status=ok authenticated=%s count=%s" % (j.get("authenticated"), len(names)))
print("      cookie_names:", ", ".join(sorted(names)[:20]) + ("..." if len(names) > 20 else ""))
if j.get("reload_warning"):
    print("      WARN reload:", j["reload_warning"][:200])
# Heuristic: merged extract should include MUID; authed sessions usually include one of:
auth_markers = ("__Host-copilot-anon", "_C_ETH", "_U", "SRCHHPGUSR")
if j.get("authenticated") and not any(m in names for m in auth_markers):
    print("      WARN: authenticated but none of", auth_markers, "in cookie_names — check noVNC login")
'

echo "[3/3] extract JSON OK"

if [[ "$WITH_CHAT" == "1" ]]; then
  echo "[extra] POST $C1_URL/v1/chat/completions (WITH_CHAT=1)"
  code=$(curl -sS -o /tmp/smoke_c1_body.json -w "%{http_code}" -X POST "$C1_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "X-Agent-ID: smoke-c3-extract" \
    -d '{"model":"copilot","messages":[{"role":"user","content":"Reply with exactly: SMOKE_OK"}]}') || true
  echo "      HTTP $code"
  head -c 400 /tmp/smoke_c1_body.json 2>/dev/null || true
  echo ""
  if [[ "$code" == "200" ]] && grep -q "SMOKE_OK" /tmp/smoke_c1_body.json 2>/dev/null; then
    echo "      C1 chat probe: OK"
  else
    echo "      C1 chat probe: not OK (expected without valid Copilot session); re-check noVNC + /extract"
    exit 0
  fi
fi

echo "smoke_c3_extract.sh: all checks passed"
