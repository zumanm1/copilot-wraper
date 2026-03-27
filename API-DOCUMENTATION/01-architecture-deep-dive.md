# 01 — Architecture Deep-Dive

## Container Map

| Container | Name | Port(s) | Role |
|-----------|------|---------|------|
| C1 | `C1_copilot-api` | 8000 | FastAPI OpenAI/Anthropic-compatible API — the single inference gateway |
| C2 | `C2_agent-terminal` | 8080 (health) | Aider / OpenCode interactive terminal agent |
| C3 | `C3_browser-auth` | 6080 (noVNC), 8001 (API) | Playwright headless Chrome + cookie extractor + M365 chat proxy |
| C5 | `C5_claude-code` | 8080 (health) | Claude Code CLI (Anthropic format via C1 `/v1/messages`) |
| C6 | `C6_kilocode` | 8080 (health) | KiloCode CLI (OpenAI format via C1 `/v1/chat/completions`) |
| C7a | `C7a_openclaw-gateway` | 18789 | OpenClaw gateway (routes to C1) |
| C7b | `C7b_openclaw-cli` | 8080 (health) | OpenClaw CLI / TUI |
| C8 | `C8_hermes-agent` | 8080 (health) | Hermes persistent-memory agent |
| C9 | `C9_jokes` | 6090 | Read-only validation console + SQLite results DB |
| CT | `CT_tests` | — | Playwright automated test runner (ephemeral) |

All containers share the `copilot-net` Docker bridge network. Internal DNS uses service names (e.g. `http://app:8000`).

---

## C1: copilot-api (the Gateway)

**File:** `server.py`, `copilot_backend.py`, `config.py`, `models.py`

### What it does
C1 is a **FastAPI application** that presents two standard AI API surfaces:
- **OpenAI format** — `POST /v1/chat/completions` (used by C2, C6, C7, C8)
- **Anthropic format** — `POST /v1/messages` (used by C5 Claude Code)

Behind both surfaces, every request ultimately calls `CopilotBackend.chat_completion()` or `chat_completion_stream()`, which routes to one of two providers:

| Provider | Route | When used |
|----------|-------|-----------|
| `m365` | C1 → C3 → Playwright → M365 Copilot | `COPILOT_PROVIDER=m365` or `COPILOT_PORTAL_PROFILE=m365_hub` |
| `copilot` | C1 → WebSocket → copilot.microsoft.com | `COPILOT_PROVIDER=copilot` or `COPILOT_PORTAL_PROFILE=consumer` |

### Key internal components

#### Per-agent session registry (`server.py` lines 28–88)
```
X-Agent-ID: c2-aider  →  _get_or_create_agent_session("c2-aider")
                       →  dedicated CopilotBackend instance
                       →  lives for 30 min idle (AGENT_SESSION_TTL)
```
Each agent ID gets its **own** `CopilotBackend`. They never share state. A two-level lock (registry lock → per-ID lock) ensures different agents never block each other.

#### Connection pool (`copilot_backend.py`)
For requests **without** `X-Agent-ID`, C1 uses a shared pool of `CopilotBackend` instances (pre-warmed on startup via `POOL_WARM_COUNT`).

#### Response cache + in-flight dedup
- `TTLCache(maxsize=1000, ttl=300)` — identical `(style, agent_id, prompt)` triplets return cached responses within 5 minutes.
- In-flight dedup — concurrent identical requests share a single `asyncio.Future`; only one HTTP/WS call is made.

#### Circuit breaker (`circuit_breaker.py`)
Wraps every `_raw_copilot_call`. Opens after `CIRCUIT_BREAKER_THRESHOLD` failures, resets after `CIRCUIT_BREAKER_TIMEOUT` seconds.

#### Message format conversion
- OpenAI `messages[]` → flat prompt string via `extract_user_prompt()`
- Anthropic `messages[]` + `system` → flat prompt via `_anthropic_messages_to_prompt()` (system truncated to 500 chars to avoid C3 timeouts)

### C1 request flow (M365 path)

```
POST /v1/chat/completions
  X-Agent-ID: c6-kilocode
  X-Chat-Mode: work
  body: { model, messages, stream }
          │
          ▼
  extract_user_prompt(messages) → flat prompt string
          │
          ▼
  _get_or_create_agent_session("c6-kilocode")
  → dedicated CopilotBackend
          │
          ▼
  CopilotBackend.chat_completion(prompt, agent_id, chat_mode)
  → TTLCache check → in-flight dedup
          │
          ▼
  _do_chat_completion() → circuit_breaker.call(_raw_copilot_call)
          │
          ▼
  _raw_copilot_call() → provider == "m365"
          │
          ▼
  _c3_proxy_call()
  POST http://browser-auth:8001/chat
  { "prompt": "...", "agent_id": "c6-kilocode", "mode": "work" }
          │
          ▼
  C3 returns { "success": true, "text": "..." }
          │
          ▼
  JSON response: { choices[0].message.content = text }
```

---

## C3: browser-auth (the M365 Proxy)

**Files:** `browser_auth/server.py`, `browser_auth/cookie_extractor.py`

### What it does
C3 runs a **Playwright headless Chromium** browser with a visible display (via Xvfb + noVNC on port 6080). It maintains a **PagePool** of 6 pre-navigated M365 Copilot Chat tabs and uses them to proxy chat requests through the real M365 UI.

### PagePool
```
PagePool(size=6)
  ├── Tab 1: https://m365.cloud.microsoft/chat?auth=1  → assigned to c2-aider
  ├── Tab 2: https://m365.cloud.microsoft/chat?auth=1  → assigned to c5-claude-code
  ├── Tab 3: ...                                        → assigned to c6-kilocode
  ├── Tab 4: ...                                        → assigned to c7-openclaw
  ├── Tab 5: ...                                        → assigned to c8-hermes
  └── Tab 6: ...                                        → assigned to c9-jokes
```

On C3 startup (`browser_auth/server.py` lifespan), 6 tabs are opened and navigated to M365 Copilot Chat. Each tab is then **sticky-assigned** to one agent ID on first use — `acquire(agent_id)` returns the same tab every time for that agent.

### C3 `/chat` endpoint flow
```
POST /chat  { prompt, agent_id, mode }
     │
     ▼
PagePool.acquire(agent_id)   → get/create sticky tab
     │
     ▼
_browser_chat_on_page(page, context, prompt, mode)
     │
     ├── Health check: is page still on m365 URL?
     ├── Auth dialog check: is M365 asking to sign in?
     │     └─ if yes: click Continue, wait 8s, re-check
     ├── Fast reset: click "New chat" button (reuse tab)
     │     └─ fallback: full page.goto() teardown + reload
     ├── Attach WebSocket listener (BEFORE navigation)
     │     └─ intercept SignalR frames from substrate.office.com
     ├── Type prompt into composer [role="textbox"]
     ├── Press Enter to submit (React synthetic event)
     ├── Wait for SignalR type=2 (bot response) or DOM fallback
     └─ Extract text from WS frame or DOM
     │
     ▼
Return { success: true, text: "..." }
```

### M365 SignalR protocol
M365 Copilot Chat communicates via SignalR WebSocket at:
```
wss://substrate.office.com/m365Copilot/Chathub/
```
Frames use the `\x1e` (record separator) delimiter. C3 intercepts these frames in the browser's WebSocket stream, splits on `\x1e`, parses JSON, and extracts the bot message from `type=2` frames.

### C3 endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness |
| `/status` | GET | Pool size, available tabs, browser state |
| `/session-health` | GET | M365 session validity, pool warnings |
| `/chat` | POST | Send a prompt through a PagePool tab |
| `/extract` | POST | Trigger cookie extraction |
| `/navigate` | POST | Navigate browser to a URL |
| `/pool-reset` | POST | Reinitialize PagePool (recovery after DNS failure) |
| `/setup` | GET/POST | Configure portal profile + URL overrides |

---

## C9: Validation Console

**Files:** `c9_jokes/app.py`, `c9_jokes/templates/`

C9 is a **read-only** FastAPI + Jinja2 web app that:
- Probes all container health endpoints
- Sends test prompts to all agents via C1's `/v1/chat/completions` with the correct `X-Agent-ID`
- Persists every run to SQLite (`/app/data/c9.db`)
- Displays results on `/pairs` with a "Run All Parallel" button

C9 never modifies C1, C3, or any agent config. It only reads.

### AGENTS list in C9 (`c9_jokes/app.py` line 49)
```python
AGENTS = [
    {"id": "c2-aider",       "label": "C2 Aider (OpenAI)"},
    {"id": "c5-claude-code", "label": "C5 Claude Code (Anthropic)"},
    {"id": "c6-kilocode",    "label": "C6 KiloCode (OpenAI)"},
    {"id": "c7-openclaw",    "label": "C7b OpenClaw"},
    {"id": "c8-hermes",      "label": "C8 Hermes Agent"},
    {"id": "c9-jokes",       "label": "C9 (generic session)"},
]
```
To add a new agent to the C9 dashboard, add one dict here and restart C9.

---

## Internal Docker Networking

All services join `copilot-net` (bridge). Inter-container DNS:

| From any container | Reaches |
|--------------------|---------|
| `http://app:8000` | C1 copilot-api |
| `http://browser-auth:8001` | C3 browser-auth API |
| `http://agent-terminal:8080` | C2 health server |
| `http://claude-code-terminal:8080` | C5 health server |
| `http://kilocode-terminal:8080` | C6 health server |
| `http://openclaw-gateway:18789` | C7a gateway |
| `http://openclaw-cli:8080` | C7b health server |
| `http://hermes-agent:8080` | C8 health server |

From the **host machine**, use `localhost` with mapped ports (8000, 8001, 6080, 6090, 18789).
