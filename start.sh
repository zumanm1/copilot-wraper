#!/usr/bin/env bash
# ==============================================================================
# Copilot OpenAI-Compatible API Wrapper — Startup & Coordination Script
# 
# Usage: ./start.sh
# 
# This script robustly guarantees that:
#   1. C1_copilot-api and C3_browser-auth are started.
#   2. C1 and C3 are healthy and listening.
#   3. A valid Microsoft Copilot cookie is present (extracts via C3 if needed).
#   4. End-to-end WebSocket connection to Copilot is fully functional.
# 
# Only after all validation passes will it allow other containers (C2, C5, CT) 
# to run against the API.
# ==============================================================================

set -euo pipefail

cd "$(dirname "$0")"

# Check dependencies
if ! command -v curl &> /dev/null; then
    echo "❌ Error: 'curl' is required but not installed."
    exit 1
fi
if ! command -v docker &> /dev/null; then
    echo "❌ Error: 'docker' is required but not installed."
    exit 1
fi

echo "============================================================"
echo "🚀 Copilot Wrapper Startup Coordination"
echo "============================================================"

# 1. Bring up core services
echo "▶ Starting C1_copilot-api and C3_browser-auth..."
docker compose up app browser-auth -d

# 2. Wait for local HTTP endpoints
echo "▶ Waiting for C1 and C3 to be healthy (up to 30s)..."
timeout 45 bash -c '
while ! curl -s -f http://localhost:8000/health >/dev/null || ! curl -s -f http://localhost:8001/health >/dev/null; do
    sleep 2
done
' || { echo "❌ Services failed to become healthy. Check docker compose logs."; exit 1; }
echo "✅ Core endpoints are listening."

# 3. Check for valid cookies
echo "▶ Verifying Copilot cookies..."
# We check the /v1/debug/cookie endpoint. If it says cookie_present=true, we proceed.
cookie_status=$(curl -s http://localhost:8000/v1/debug/cookie)
has_cookie=$(echo "$cookie_status" | grep -o '"cookie_present":\s*true' || echo "false")

if [[ "$has_cookie" == "false" ]]; then
    echo "⚠️  No cookies found! Triggering C3_browser-auth extraction..."
    echo "   (If you are not logged in, please open http://localhost:6080 and authenticate within 5 minutes)"
    extract_result=$(curl -s -X POST http://localhost:8001/extract)
    if echo "$extract_result" | grep -q '"status":"ok"'; then
        echo "✅ Cookies successfully extracted and loaded."
    else
        echo "❌ Failed to extract cookies. Ensure you have unlocked Chrome Safe Storage."
        # Print a redacted snippet of the failure
        echo "$extract_result" | cut -c 1-150
        exit 1
    fi
else
    echo "✅ Cookies are present."
fi

# 4. End-to-End API Validation
echo "▶ Validating End-to-End Copilot WebSocket connection..."
# Send a simple ping. If the cookie is expired or auth fails, this will return 500 or 503 instead of 200.
response_status=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "copilot", "messages": [{"role": "user", "content": "ping"}], "stream": false}')

if [[ "$response_status" == "200" ]]; then
    echo "✅ Copilot connection validated successfully!"
else
    echo "❌ API validation failed with HTTP $response_status."
    echo "   This usually means your cookies are expired or invalid."
    echo "   Attempting to force a fresh cookie extraction via C3..."
    extract_result=$(curl -s -X POST http://localhost:8001/extract)
    
    # Retry validation
    sleep 2
    response_status_retry=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d '{"model": "copilot", "messages": [{"role": "user", "content": "ping"}], "stream": false}')
      
    if [[ "$response_status_retry" == "200" ]]; then
        echo "✅ Copilot connection validated successfully after fresh extraction!"
    else
        echo "❌ Copilot connection STILL failing (HTTP $response_status_retry)."
        echo "Please check 'docker logs C1_copilot-api' and ensure you are fully logged in via http://localhost:6080."
        exit 1
    fi
fi

echo "============================================================"
echo "🎉 System Ready!"
echo "You can now safely run agent terminals or tests:"
echo "   docker compose run --rm test"
echo "   docker compose run --rm agent-terminal"
echo "   docker compose run --rm claude-code-terminal"
echo "============================================================"
