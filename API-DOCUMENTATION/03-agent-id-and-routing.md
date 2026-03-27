# 03 — Agent ID & Routing

## What is an Agent ID?

An **Agent ID** (`X-Agent-ID` HTTP header) is a string tag that tells C1 to route a request to a **dedicated, isolated `CopilotBackend` instance** rather than the shared connection pool.

```http
POST /v1/chat/completions HTTP/1.1
X-Agent-ID: c6-kilocode
X-Chat-Mode: work
Content-Type: application/json

{ "model": "copilot", "messages": [...], "stream": false }
```

Every unique Agent ID gets its own:
- `CopilotBackend` instance (isolated conversation state, style, cache key)
- Sticky PagePool tab in C3 (same browser tab reused for every request from this agent)
- In-flight deduplication future (concurrent identical prompts from the same agent share one upstream call)

---

## How Agent IDs Are Created

Agent IDs are **arbitrary strings** — C1 creates a backend on first sight:

```python
# server.py — _get_or_create_agent_session()
if agent_id not in _agent_sessions:
    backend = CopilotBackend()
    _agent_sessions[agent_id] = backend
```

No registration required. Just send a new `X-Agent-ID` value and C1 creates the session automatically.

### Naming Convention

The existing convention is `cx-toolname`:

| Agent ID | Container | Tool |
|----------|-----------|------|
| `c2-aider` | C2 | Aider coding agent |
| `c5-claude-code` | C5 | Claude Code CLI |
| `c6-kilocode` | C6 | KiloCode CLI |
| `c7-openclaw` | C7a/C7b | OpenClaw |
| `c8-hermes` | C8 | Hermes Agent |
| `c9-jokes` | C9 | Generic validation session |

For a new container C10 running "MyAgent": use `c10-myagent`.

---

## How Agent IDs Are Passed from Containers

Each agent container sets `AGENT_ID` in its environment (from `docker-compose.yml`). The container's launcher script (`start.sh`) reads this and includes it as a header:

```bash
# In start.sh / ask_helper.py
AGENT_ID="${AGENT_ID:-c2-aider}"

curl -X POST http://app:8000/v1/chat/completions \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "X-Chat-Mode: work" \
  ...
```

For tools that natively use an OpenAI client (Aider, KiloCode, Hermes), the `X-Agent-ID` header is injected differently:
- **Aider**: passes `--openai-api-key not-needed` and custom base URL; the start.sh wraps the command
- **KiloCode/Hermes**: these tools send requests that include `AGENT_ID` via a middleware wrapper script

For Python `openai` SDK:
```python
import openai
client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed",
    default_headers={"X-Agent-ID": "c10-myagent", "X-Chat-Mode": "work"},
)
```

---

## Session Registry & Lifecycle

### How sessions are stored (`server.py`)

```python
_agent_sessions: dict[str, CopilotBackend]       # agent_id → backend
_agent_session_last_used: dict[str, float]        # agent_id → last use timestamp
_agent_per_id_locks: dict[str, asyncio.Lock]      # agent_id → per-agent lock
```

### Session TTL

Sessions expire after **30 minutes of idle time** (configurable via `AGENT_SESSION_TTL`):
```
AGENT_SESSION_TTL=1800   # 1800 seconds = 30 minutes
```

A background reaper task checks every 5 minutes. Expired sessions are closed and removed.

### Inspect active sessions

```bash
curl http://localhost:8000/v1/sessions
```
```json
{
  "sessions": {
    "c2-aider": {"connected": true, "idle_seconds": 42},
    "c6-kilocode": {"connected": true, "idle_seconds": 8}
  },
  "total": 2,
  "ttl_seconds": 1800
}
```

---

## Lock Hierarchy (no inter-agent contention)

C1 uses a **two-level lock** to ensure different agents never block each other:

```
Level 1: _agent_registry_lock  (brief — only to fetch/create per-ID lock)
              │
              ▼
Level 2: _agent_per_id_locks["c6-kilocode"]  (held while creating/accessing backend)
```

Two concurrent requests for `c6-kilocode` and `c8-hermes` acquire **different** per-ID locks — they never contend.

---

## PagePool Sticky Tab Assignment (C3)

When C3's `browser_chat()` is called with an `agent_id`:

```python
# cookie_extractor.py — PagePool.acquire()
if agent_id in self._agent_tabs:
    return self._agent_tabs[agent_id]   # return same tab every time

# First call: assign a free tab
tab = self._free_tabs.pop()
self._agent_tabs[agent_id] = tab
return tab
```

After the first call, `c6-kilocode` always gets Tab 3 (for example). This means:
- The tab is already on the right URL (no navigation needed on subsequent calls)
- "New chat" fast reset works (just clicks the button, no full page reload)
- Conversation history from the previous turn is cleared by the fast reset

### What if all tabs are in use?

If `pool_available == 0` and a new agent ID arrives:
- `acquire()` creates an **on-demand tab** (self-healing path, Bug #1 fix)
- This tab is not pre-warmed but functional

---

## Cache Key & Deduplication

The response cache key is:
```python
key = sha256(f"{style}:{agent_id}:{prompt}")
```

Including `agent_id` ensures `c2-aider` and `c6-kilocode` asking the same question get **independent cache entries** — they don't share cached responses.

---

## Without X-Agent-ID (Shared Pool)

If `X-Agent-ID` is absent, C1 uses the shared `ConnectionPool`:
- Backend is acquired from pool on request start
- Released back to pool after response
- No sticky tab assignment in C3 (C3 assigns the least-recently-used free tab)
- Suitable for one-off queries, not for long-running agent sessions

For **all agent containers**, always set `AGENT_ID` and pass it as `X-Agent-ID`.
