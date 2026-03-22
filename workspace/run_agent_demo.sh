#!/usr/bin/env bash
# Container 2 → Container 1 agent demo
# Task: create test.txt with result of 343434 * 24324

set -euo pipefail

TASK="Create a file called test.txt in the current directory. Calculate 343434 * 24324 and write ONLY the result of that calculation into test.txt. Nothing else, just the number."

echo "=============================================="
echo " Container 2 Agent Demo"
echo " LLM Backend: http://app:8000 (Container 1)"
echo "=============================================="
echo ""

# Step 1 — trigger cookie extraction on Container 1
echo "[1/3] Requesting Container 1 to extract fresh cookies..."
EXTRACT=$(curl -s -X POST http://app:8000/v1/cookies/extract)
echo "      $EXTRACT"
echo ""

# Step 2 — verify Container 1 is ready
echo "[2/3] Verifying Container 1 health..."
HEALTH=$(curl -s http://app:8000/health)
COOKIE_STATE=$(curl -s http://app:8000/v1/debug/cookie)
echo "      Health: $HEALTH"
echo "      Cookie: $(echo $COOKIE_STATE | python3 -c 'import sys,json; d=json.load(sys.stdin); print("_U present:", d["key_cookies_present"]["_U"])')"
echo ""

# Step 3 — run aider non-interactively with the task
echo "[3/3] Launching Aider agent with task..."
echo "      Task: $TASK"
echo ""

cd /workspace

# Use aider in message mode (non-interactive, single task)
aider \
  --model openai/copilot \
  --openai-api-base http://app:8000/v1 \
  --openai-api-key not-needed \
  --no-auto-commits \
  --no-git \
  --yes \
  --message "$TASK" \
  2>&1

echo ""
echo "=============================================="
echo " Result: contents of test.txt"
echo "=============================================="
if [ -f /workspace/test.txt ]; then
    cat /workspace/test.txt
else
    echo "(test.txt not created yet)"
fi
