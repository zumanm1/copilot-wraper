# 05 — New Agent Integration Steps

This guide shows exactly how to connect any new AI agent to the stack with **zero changes to C1 or C3**.

---

## Prerequisites

- All core containers running: `docker compose up -d`
- C3 PagePool initialized: `curl http://localhost:8001/status` → `pool_initialized: true`
- M365 session active: `curl http://localhost:8001/session-health` → `session: "active"`
- If session expired: sign in via noVNC at `http://localhost:6080`

---

## Step 1 — Choose Your API Format

Determine which API format your agent tool uses:

| Format | Endpoint | Header needed | Examples |
|--------|----------|--------------|---------|
| **OpenAI** | `POST /v1/chat/completions` | `X-Agent-ID` | Aider, KiloCode, Hermes, most LLM tools |
| **Anthropic** | `POST /v1/messages` | `X-Agent-ID` | Claude Code CLI |

Both formats are fully supported by C1. Use whichever your tool requires natively.

---

## Step 2 — Pick a Container Number and Agent ID

Choose the next available Cx slot. Current containers: C1–C9.

```
New container: C10
Container name: C10_myagent
Service name in docker-compose: myagent
AGENT_ID: c10-myagent
```

**Agent ID rules:**
- Lowercase letters, digits, hyphens
- Convention: `cx-toolname` (e.g. `c10-aicoder`)
- Must be unique across all containers

---

## Step 3 — Create the Dockerfile

Copy the stub:
```bash
cp API-DOCUMENTATION/stubs/Dockerfile.cx-stub Dockerfile.c10-myagent
```

Edit 3 lines (marked with `# CHANGE:` in the stub):

```dockerfile
# CHANGE 1: Install your tool
RUN pip install --no-cache-dir my-ai-tool==1.0.0
# or: RUN npm install -g my-ai-tool

# CHANGE 2: Set default env vars for your tool
ENV OPENAI_API_BASE=http://app:8000/v1
ENV OPENAI_API_KEY=not-needed
ENV MY_TOOL_MODEL=copilot

# CHANGE 3: Set default AGENT_ID (overridden by docker-compose)
ENV AGENT_ID=c10-myagent
```

The standby health server and `ask` command are already included in the stub's `start.sh`.

---

## Step 4 — Add the docker-compose Service Block

Open `docker-compose.yml` and add the new service. Use the snippet from `API-DOCUMENTATION/stubs/docker-compose-cx-snippet.yml`.

**Minimum required fields:**

```yaml
myagent:                                  # service name
  build:
    context: .
    dockerfile: Dockerfile.c10-myagent
  container_name: C10_myagent
  depends_on:
    app:
      condition: service_healthy          # wait for C1
    browser-auth:
      condition: service_healthy          # wait for C3
  environment:
    - OPENAI_API_BASE=http://app:8000/v1  # C1 internal URL
    - OPENAI_API_KEY=not-needed
    - AGENT_ID=c10-myagent               # MUST be unique
  volumes:
    - ./workspace:/workspace
  networks:
    - copilot-net
  command: ["standby"]
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-sf", "http://localhost:8080/health"]
    interval: 15s
    timeout: 5s
    start_period: 10s
    retries: 3
```

**For Anthropic-format tools** (like Claude Code), use instead:
```yaml
    - ANTHROPIC_BASE_URL=http://app:8000   # no /v1 suffix
    - ANTHROPIC_API_KEY=sk-ant-not-needed-xxxxxxxxxxxxx
    - AGENT_ID=c10-myagent
```

---

## Step 5 — Configure Your Tool to Point at C1

Your tool must send requests to C1. Set the base URL in your tool's config:

### OpenAI SDK (Python)
```python
import openai
client = openai.OpenAI(
    base_url="http://localhost:8000/v1",   # or http://app:8000/v1 inside Docker
    api_key="not-needed",
    default_headers={
        "X-Agent-ID": "c10-myagent",
        "X-Chat-Mode": "work",
    },
)
response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### OpenAI SDK (Node.js)
```javascript
import OpenAI from "openai";
const client = new OpenAI({
  baseURL: "http://localhost:8000/v1",
  apiKey: "not-needed",
  defaultHeaders: {
    "X-Agent-ID": "c10-myagent",
    "X-Chat-Mode": "work",
  },
});
```

### Anthropic SDK (Python)
```python
import anthropic
client = anthropic.Anthropic(
    base_url="http://localhost:8000",
    api_key="sk-ant-not-needed-xxxxxxxxxxxxx",
    default_headers={"X-Agent-ID": "c10-myagent"},
)
```

### Environment variables (most CLI tools)
```bash
OPENAI_API_BASE=http://app:8000/v1
OPENAI_API_KEY=not-needed
# Tool-specific model var (check your tool's docs):
MY_TOOL_MODEL=copilot
```

---

## Step 6 — Build and Start

```bash
# Build the new container image
docker compose build myagent

# Start it (C1 and C3 must already be running)
docker compose up myagent -d

# Verify it's healthy
docker compose ps myagent
docker logs C10_myagent --tail 20
```

---

## Step 7 — Run the Validation Script

```bash
bash API-DOCUMENTATION/stubs/validate_new_agent.sh c10-myagent
```

Expected output:
```
[1/3] Checking C1 health...  OK (HTTP 200)
[2/3] Checking C3 pool...    OK (pool_initialized=true, available=5)
[3/3] Sending test prompt to c10-myagent via C1...
      HTTP 200 | 12.3s
      Response: Sure! Here's a joke...
      [PASS] c10-myagent PASSED
```

Exit code `0` = pass, `1` = fail.

---

## Step 8 (Optional) — Add to C9 Dashboard

To see your new agent in the C9 validation console at `http://localhost:6090/pairs`:

Edit `c9_jokes/app.py`, add one line to the `AGENTS` list:

```python
AGENTS = [
    {"id": "c2-aider",       "label": "C2 Aider (OpenAI)"},
    # ... existing agents ...
    {"id": "c10-myagent",    "label": "C10 MyAgent"},   # ← add this
]
```

Restart C9:
```bash
docker compose restart c9-jokes
```

Now your agent appears in the pairs table and is included in "Run All Parallel".

---

## Checklist Summary

```
[ ] 1. API format decided: OpenAI or Anthropic
[ ] 2. Container number + AGENT_ID chosen (unique)
[ ] 3. Dockerfile.c10-myagent created from stub
[ ] 4. docker-compose.yml updated with new service block
[ ] 5. Tool configured to use http://app:8000/v1 (or /v1/messages)
[ ] 6. docker compose build + up successful, container healthy
[ ] 7. validate_new_agent.sh passes (exit code 0)
[ ] 8. (Optional) Added to c9_jokes/app.py AGENTS list
```

---

## Common Mistakes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| `AGENT_ID` missing | Requests use shared pool, no sticky tab | Add `AGENT_ID=c10-myagent` env var and pass as `X-Agent-ID` header |
| Wrong base URL | `Connection refused` | Use `http://app:8000/v1` inside Docker, `http://localhost:8000/v1` from host |
| Anthropic URL has `/v1` | `404 Not Found` on `/v1/v1/messages` | Use `http://app:8000` (no `/v1`) for Anthropic format |
| M365 session expired | `HTTP 500` — auth dialog | Sign in via noVNC at `http://localhost:6080` |
| Pool exhausted | All 6 tabs busy, request queues | `curl -X POST http://localhost:8001/pool-reset` or restart C3 |
| Timeout cascade | `HTTP 500` after 180s | C3 tab may have stale state; `pool-reset` or wait for tab reassignment |
