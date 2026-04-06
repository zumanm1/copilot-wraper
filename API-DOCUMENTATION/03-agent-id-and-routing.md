# 03 — Agent ID & Session Routing

> **Last updated: 2026-03-27**
> How X-Agent-ID provides session isolation, the per-agent session registry, lock hierarchy, TTL, and the PagePool sticky-tab mechanism.

---

## Table of Contents

- [What is an Agent ID?](#what-is-an-agent-id)
- [Registered Agent IDs](#registered-agent-ids)
- [Session Registry Architecture](#session-registry-architecture)
- [Lock Hierarchy](#lock-hierarchy)
- [Session Lifecycle](#session-lifecycle)
- [PagePool Sticky-Tab Assignment (M365)](#pagepool-sticky-tab-assignment-m365)
- [Cache Key Deduplication](#cache-key-deduplication)
- [Routing Without X-Agent-ID](#routing-without-x-agent-id)
- [Using X-Agent-ID in Practice](#using-x-agent-id-in-practice)

---

## What is an Agent ID?

An **Agent ID** (`X-Agent-ID` HTTP header) is a string tag that tells C1 to route a request to a **dedicated, isolated `CopilotBackend` instance** rather than the shared connection pool.

```
Without X-Agent-ID:  → shared CopilotBackend pool (stateless, round-robin)
With X-Agent-ID:     → dedicated CopilotBackend instance (sticky, stateful)
```

Benefits of a dedicated instance:
- **Isolated conversation history** — each agent's context never mixes with another
- **Isolated session** — separate Copilot conversation IDs per agent
- **Isolated cache** — cache key includes agent_id, so identical prompts from different agents produce separate Copilot calls
- **Isolated M365 tab** — in M365 mode, each agent ID gets its own sticky PagePool tab in C3

---

## Registered Agent IDs

These are the canonical agent IDs used throughout the stack:

| Container | Agent ID | API format | Connects via |
|---|---|---|---|
| C2 Aider | `c2-aider` | OpenAI `/v1/chat/completions` | `OPENAI_API_BASE=http://app:8000/v1` |
| C5 Claude Code | `c5-claude-code` | Anthropic `/v1/messages` | `ANTHROPIC_BASE_URL=http://app:8000` |
| C6 KiloCode | `c6-kilocode` | OpenAI `/v1/chat/completions` | `OPENAI_API_BASE=http://app:8000/v1` |
| C7a/C7b OpenClaw | `c7-openclaw` | OpenAI `/v1/chat/completions` | via C7a gateway |
| C8 Hermes | `c8-hermes` | OpenAI `/v1/chat/completions` | `OPENAI_BASE_URL=http://app:8000/v1` |
| C9 generic | `c9-jokes` | OpenAI `/v1/chat/completions` | `C1_URL=http://app:8000` |

Agent IDs are **arbitrary strings** — you can create custom IDs for any purpose:
```bash
# Custom agent ID for your own client
curl -H "X-Agent-ID: my-custom-bot" ...
```

---

## Session Registry Architecture

C1 (`server.py`) maintains three dictionaries:

```python
_agent_sessions: dict[str, CopilotBackend] = {}
# Maps agent_id → dedicated CopilotBackend instance

_agent_session_last_used: dict[str, float] = {}
# Maps agent_id → unix timestamp of last request

_agent_per_id_locks: dict[str, asyncio.Lock] = {}
# Maps agent_id → asyncio.Lock (prevents concurrent access to same backend)

_agent_registry_lock: asyncio.Lock
# Single lock protecting the dictionaries themselves (brief hold only)
```

### _get_or_create_agent_session(agent_id) logic

```python
async def _get_or_create_agent_session(agent_id: str) -> CopilotBackend:
    # Step 1: acquire registry lock briefly to get or create per-ID lock
    async with _agent_registry_lock:
        if agent_id not in _agent_per_id_locks:
            _agent_per_id_locks[agent_id] = asyncio.Lock()
        lock = _agent_per_id_locks[agent_id]

    # Step 2: hold per-ID lock while checking / creating backend
    # Different agents never block each other here
    async with lock:
        if agent_id not in _agent_sessions:
            _agent_sessions[agent_id] = CopilotBackend()
        _agent_session_last_used[agent_id] = time.time()
        return _agent_sessions[agent_id]
```

This two-level approach means:
- C2 and C5 can be creating sessions simultaneously — they use different per-ID locks
- Only one request per agent_id runs at a time within that backend (no race conditions)
- Registry operations are minimal — the brief lock on step 1 does not block step 2

---

## Lock Hierarchy

```
_agent_registry_lock  (brief hold — read/write dictionaries only)
    └── _agent_per_id_locks["c2-aider"]      (held during full backend call)
    └── _agent_per_id_locks["c5-claude-code"] (independent — no contention)
    └── _agent_per_id_locks["c8-hermes"]      (independent — no contention)
```

**Rule:** Never acquire `_agent_registry_lock` while holding a per-ID lock. Never acquire two per-ID locks simultaneously.

---

## Session Lifecycle

```
State machine per agent_id:

[not in registry]
     │
     │ first request with X-Agent-ID
     ▼
[active]
  _agent_sessions[id] = CopilotBackend()
  _agent_session_last_used[id] = now()
     │
     │ each subsequent request
     │ → update _agent_session_last_used[id] = now()
     │
     │ AGENT_SESSION_TTL seconds pass with no requests (default: 1800s)
     ▼
[reaped by _session_reaper()]
  del _agent_sessions[id]
  del _agent_session_last_used[id]
  del _agent_per_id_locks[id]
     │
     │ new request arrives → creates fresh session
     ▼
[active again]
```

### _session_reaper()

A background asyncio task runs every 60 seconds:
```python
async def _session_reaper():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        stale = [
            aid for aid, last in _agent_session_last_used.items()
            if now - last > AGENT_SESSION_TTL
        ]
        for aid in stale:
            del _agent_sessions[aid]
            del _agent_session_last_used[aid]
            # per-ID lock left in place (cheap, avoids race if reaper and new request collide)
```

Configuration:
```
AGENT_SESSION_TTL=1800   (default: 30 minutes)
```

---

## PagePool Sticky-Tab Assignment (M365)

In M365 profile mode, each `X-Agent-ID` is also assigned a **sticky Playwright tab** in C3's PagePool:

```python
class PagePool:
    size: int = 6  # C3_CHAT_TAB_POOL_SIZE env var
    _tabs: list[Page]
    _agent_tab_map: dict[str, int] = {}  # agent_id → tab index

    async def acquire(self, agent_id: str) -> Page:
        if agent_id in _agent_tab_map:
            return _tabs[_agent_tab_map[agent_id]]  # sticky: same tab every time
        else:
            # Assign next available tab
            idx = _next_available_idx()
            _agent_tab_map[agent_id] = idx
            return _tabs[idx]
```

Result: Each agent always gets the same browser tab → separate M365 conversation thread.

```
Tab 1 → assigned "c2-aider"       on first C2 request
Tab 2 → assigned "c5-claude-code" on first C5 request
Tab 3 → assigned "c6-kilocode"
Tab 4 → assigned "c7-openclaw"
Tab 5 → assigned "c8-hermes"
Tab 6 → assigned "c9-jokes"
```

If the pool is exhausted (all 6 tabs assigned and a 7th agent appears), the request waits until a tab is free.

**Pool reset:** `POST http://localhost:8001/pool-reset` reinitializes all tabs (clears `_agent_tab_map`). Use this after a Playwright crash or DNS failure.

---

## Cache Key Deduplication

The response cache key includes `agent_id`:

```python
cache_key = sha256(f"{style}:{agent_id}:{prompt}".encode()).hexdigest()
```

This means:
- `c2-aider` asking "Hello" and `c8-hermes` asking "Hello" → **two separate cache entries**
- The same agent asking the same question twice within 300 seconds → **cache hit**
- Two requests for the same agent with the same prompt arriving simultaneously → **in-flight dedup** (one Copilot call, result shared)

---

## Routing Without X-Agent-ID

Requests without `X-Agent-ID` use the **shared connection pool**:

```python
POOL_WARM_COUNT = int(os.getenv("POOL_WARM_COUNT", "2"))
# Pre-create N CopilotBackend instances on startup

class CopilotConnectionPool:
    _pool: list[CopilotBackend]
    _semaphore: asyncio.Semaphore  # limits concurrency

    async def get(self) -> CopilotBackend:
        async with _semaphore:
            return _pool[_round_robin_idx]
```

Use cases for the shared pool:
- Quick one-off API calls
- External clients that don't need session isolation
- Health checks and testing

**The pool does not maintain conversation history** across requests.

---

## Using X-Agent-ID in Practice

### Direct API call

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c2-aider" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello"}]}'
```

### OpenAI SDK

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed",
    default_headers={"X-Agent-ID": "c2-aider"}
)

response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### Anthropic SDK (C5 format)

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8000",
    api_key="not-needed"
)

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    extra_headers={"X-Agent-ID": "c5-claude-code"},
    messages=[{"role": "user", "content": "Hello"}]
)
```

### Combined with thinking mode and work mode

```bash
# Deep reasoning, M365 work scope, isolated to hermes session
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "X-Agent-ID: c8-hermes" \
  -H "X-Chat-Mode: deep" \
  -H "X-Work-Mode: work" \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Summarise my recent Teams messages"}]}'
```

### Viewing active sessions

```bash
curl http://localhost:8000/v1/sessions
```
Returns a list of all active agent session IDs and their last-used timestamps.

### Adding a new agent to C9 dashboard

In `c9_jokes/app.py`, add to the `AGENTS` list:
```python
AGENTS = [
    ...
    {"id": "my-new-agent", "label": "My New Agent (OpenAI)"},
]
```
Restart C9: `docker compose restart c9-jokes`. The new agent appears in the Chat dropdown and Pairs grid.
