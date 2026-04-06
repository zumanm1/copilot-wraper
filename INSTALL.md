# Installation Guide — Copilot OpenAI-Compatible API Wrapper

> **Fresh-machine setup for Linux and macOS — from GitHub clone to all 13 runtime containers running.**

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Requirements](#2-system-requirements)
3. [Install Prerequisites](#3-install-prerequisites)
4. [Clone the Repository](#4-clone-the-repository)
5. [Configure Environment](#5-configure-environment)
6. [First-Time Build](#6-first-time-build)
7. [Authenticate via C3 (Cookie Flow)](#7-authenticate-via-c3-cookie-flow)
8. [Start the Full Stack](#8-start-the-full-stack)
9. [Verify All Containers](#9-verify-all-containers)
10. [Access the Services](#10-access-the-services)
11. [Using Agent Containers (C2b, C5b, C6b, C7ab/C7bb, C8b)](#11-using-agent-containers)
12. [Updating](#12-updating)
13. [Uninstall](#13-uninstall)
14. [Linux-Specific Notes](#14-linux-specific-notes)
15. [macOS-Specific Notes](#15-macos-specific-notes)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Overview

This project wraps Microsoft Copilot behind a standard OpenAI- and Anthropic-compatible REST API. Thirteen runtime containers handle the full stack:

| # | Container | Port(s) | What it does |
|---|---|---|---|
| C1b | `app` | **8000** (host) | FastAPI server — `/v1/chat/completions` (OpenAI) and `/v1/messages` (Anthropic) |
| C2b | `agent-terminal` | — (internal 8080) | Aider + OpenCode coding agents (interactive terminal) |
| C3b | `browser-auth` | **6080**, **8001** | Headless Chromium + noVNC — cookie extraction and M365 browser proxy |
| C5b | `claude-code-terminal` | — (internal 8080) | Claude Code CLI (routes to C1b) |
| C6b | `kilocode-terminal` | — (internal 8080) | KiloCode CLI (routes to C1b) |
| C7ab | `openclaw-gateway` | **18789** | OpenClaw WebSocket gateway |
| C7bb | `openclaw-cli` | — (internal 8080) | OpenClaw CLI / TUI |
| C8b | `hermes-agent` | — (internal 8080) | Hermes Agent — persistent memory, skills, cron jobs |
| C9b | `c9-jokes` | **6090** (host) | Validation console — chat, agent, multi-agento, logs, tasked, health UI |
| C10b | `c10b-sandbox` | **8310** (host) / 8210 (internal) | Agent workspace sandbox for C9b `/api/agent/*` |
| C11b | `c11b-sandbox` | **8410** (host) / 8200 (internal) | Multi-agent session sandbox for C9b `/api/multi-Agento/*` |
| C12b | `c12b-sandbox` | **8210** (host) | Lean coding/test sandbox for Tasked pipeline `/api/sandbox/exec` |

All containers share the `copilot-net` Docker bridge network. Only the ports listed above are exposed to the host.

---

## 2. System Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 8 GB | 16 GB |
| Disk (free) | 20 GB | 30 GB |
| CPU | 2 cores | 4+ cores |
| OS | macOS 12 / Ubuntu 22.04 | macOS 14 / Ubuntu 24.04 |
| Internet | Required (build-time npm/pip installs) | — |

> **Note:** C3 uses 2 GB of shared memory for Chromium (set via `shm_size: 2g` in docker-compose.yml). C8 clones the Hermes Agent from GitHub during build — a network connection is required for `docker compose build`.

---

## 3. Install Prerequisites

### macOS

```bash
# 1. Install Docker Desktop (includes Docker Compose v2)
#    Download from: https://www.docker.com/products/docker-desktop/
#    Or via Homebrew:
brew install --cask docker

# 2. Install Xcode command-line tools (includes git and curl)
xcode-select --install

# 3. Start Docker Desktop from Applications and wait for the whale icon to appear
open -a Docker

# 4. Verify
docker --version           # Docker version 24.x or later
docker compose version     # Docker Compose version v2.x or later
git --version
curl --version
```

> **Apple Silicon (M1/M2/M3):** All container base images (`python:3.11-slim`, `node:20-alpine`, `ubuntu:22.04`) are multi-arch and run natively on arm64. No Rosetta 2 required.

### Linux (Ubuntu 22.04 / Debian 12)

```bash
# 1. Install Docker Engine and Compose plugin
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

# Add Docker's official GPG key and repo
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 2. Add your user to the docker group (avoids needing sudo)
sudo usermod -aG docker $USER
# Log out and log back in for this to take effect

# 3. Start Docker daemon
sudo systemctl enable --now docker

# 4. Verify
docker --version           # Docker version 24.x or later
docker compose version     # Docker Compose version v2.x or later
git --version
curl --version
```

> **RHEL / Fedora / CentOS Stream:** Replace `apt-get` with `dnf`. Package name is `docker-ce` (from the Docker repo) or `podman-docker` if using Podman (untested).

---

## 4. Clone the Repository

```bash
git clone https://github.com/zumanm1/copilot-wraper.git
cd copilot-wraper/copilot-openai-wrapper
```

You should see these key files:

```
docker-compose.yml          # Full C1-C11 runtime stack definition
.env.example                # Configuration template
Dockerfile                  # C1 (FastAPI server)
Dockerfile.browser          # C3 (Headless Chrome + noVNC)
Dockerfile.agent            # C2 (Aider + OpenCode)
Dockerfile.claude-code      # C5 (Claude Code CLI)
Dockerfile.kilocode         # C6 (KiloCode CLI)
Dockerfile.openclaw-gw      # C7a (OpenClaw gateway)
Dockerfile.openclaw-cli     # C7b (OpenClaw CLI)
Dockerfile.hermes           # C8 (Hermes Agent)
Dockerfile.c9-jokes         # C9 (Validation console)
Dockerfile.c10-sandbox      # C10 (agent sandbox)
Dockerfile.c11-sandbox      # C11 (multi-agent sandbox)
start.sh                    # Automated C1+C3 startup script
cluster-start.sh            # Interactive agent launcher
```

---

## 5. Configure Environment

### 5.1 Create your `.env` file

```bash
cp .env.example .env
```

The `.env` file is auto-populated by C3 during the authentication step. You do not need to edit it manually unless you want to use a manually-extracted cookie value.

Key variables (all have defaults or are auto-set):

```bash
# Auto-populated by C3 after you log in via noVNC
BING_COOKIES=          # Your Copilot session cookie (_U value)
COPILOT_COOKIES=       # Full Copilot cookie string (set by C3 /extract)

# Portal profile — choose one:
COPILOT_PORTAL_PROFILE=consumer    # copilot.microsoft.com (personal)
# COPILOT_PORTAL_PROFILE=m365_hub  # m365.cloud.microsoft (enterprise)

# Optional
COPILOT_STYLE=balanced             # creative | balanced | precise
API_KEY=                           # Optional auth key for C1
```

### 5.2 Linux — Chrome data path

The `app` (C1) service mounts your host Chrome profile read-only so it can access saved cookies. The default path in `docker-compose.yml` is the macOS path. On Linux, you must override it.

**Option A — Edit `.env`:**
```bash
echo 'CHROME_DATA_PATH=${HOME}/.config/google-chrome' >> .env
```

**Option B — Edit `docker-compose.yml`** (under the `app:` service, `volumes:` section):
```yaml
# Replace this line:
- ${HOME}/Library/Application Support/Google/Chrome:/chrome-data:ro
# With:
- ${HOME}/.config/google-chrome:/chrome-data:ro
```

> If you don't have Chrome installed on Linux, you can skip this — C3 handles its own browser session independently. The host Chrome mount is only used for reading pre-existing cookies as a fallback.

### 5.3 Optional — Portal profile for M365 enterprise

If you are using a Microsoft 365 work account (via `m365.cloud.microsoft`), set:

```bash
# In .env:
COPILOT_PORTAL_PROFILE=m365_hub
```

Then in the C3 authentication step (Section 7), log in to `https://m365.cloud.microsoft` instead of `https://copilot.microsoft.com`.

---

## 6. First-Time Build

Docker builds all images from source. This only runs on first install or after a `git pull` that changes a Dockerfile.

```bash
# Build all runtime images
docker compose build

# Expected build time (first run, with internet):
# C1  — ~3 min  (Python + pip packages including uvloop compilation)
# C2  — ~5 min  (Python + Node.js 20 + aider-chat + opencode-ai)
# C3  — ~8 min  (Ubuntu 22.04 + Playwright + Chromium + noVNC)
# C5  — ~2 min  (Node.js 20 + @anthropic-ai/claude-code)
# C6  — ~2 min  (Node.js 20 + @kilocode/cli)
# C7a — ~3 min  (Node.js 22 + openclaw native addons)
# C7b — ~2 min  (Node.js 22 + openclaw)
# C8  — ~6 min  (Python + uv + Hermes git clone v2026.3.17 + submodules)
# C9  — ~1 min  (Python + fastapi + uvicorn + jinja2)
# C10 — ~1 min  (Python sandbox runtime)
# C11 — ~1 min  (Python multi-agent sandbox runtime)
# Total: ~15–35 min depending on internet speed
```

> **Clean build** (if you hit a stale-cache issue):
> ```bash
> docker compose build --no-cache
> ```

---

## 7. Authenticate via C3 (Cookie Flow)

C3 runs a headless Chromium browser with a noVNC remote display. You log into Copilot inside C3's browser, and C3 extracts the session cookies and writes them to `.env` for C1 to use.

### 7.1 Start C1 and C3

```bash
docker compose up app browser-auth -d
docker compose ps
# Both should show "healthy" within ~30 seconds
```

### 7.2 Open the noVNC browser

```bash
open http://localhost:6080          # macOS
xdg-open http://localhost:6080      # Linux (or open manually in browser)
```

You will see a desktop with an Openbox window manager. Open the Chromium browser inside it.

### 7.3 Log in to Microsoft Copilot

**Consumer profile (`consumer`):**
1. In the noVNC window, navigate to `https://copilot.microsoft.com`
2. Sign in with your Microsoft personal account
3. Complete any "Continue" or MFA prompts until you reach the Copilot chat UI

**M365 profile (`m365_hub`):**
1. In the noVNC window, navigate to `https://m365.cloud.microsoft`
2. Sign in with your Microsoft 365 work/school account
3. Complete MFA, then navigate to `https://m365.cloud.microsoft/chat`

### 7.4 Extract cookies

Once signed in (you see the Copilot chat interface):

```bash
curl -X POST http://localhost:8001/extract
# → {"status":"ok","cookies_saved":true}
```

C3 walks through all relevant Microsoft domains, extracts the session cookies, and writes them to `.env`.

### 7.5 Reload C1

```bash
curl -X POST http://localhost:8000/v1/reload-config
# C1 picks up the new cookies without restarting
```

### 7.6 Verify

```bash
curl http://localhost:8000/v1/debug/cookie
# Shows masked cookie values; should not be empty
```

### 7.7 Manual cookie fallback

If the noVNC flow doesn't work (corporate proxy, VPN, etc.):

1. Open `https://copilot.microsoft.com` in your **host** browser and sign in
2. Press F12 → Application → Cookies → `https://copilot.microsoft.com`
3. Copy the `_U` cookie value
4. Edit `.env`: `BING_COOKIES=<paste value here>`
5. Reload: `curl -X POST http://localhost:8000/v1/reload-config`

> Cookies expire every few days. If you get 401 or 403 errors, repeat the extraction flow.

---

## 8. Start the Full Stack

After authentication, start all containers:

```bash
docker compose up -d
docker compose ps
```

You should see all services listed as `running` or `healthy`. The first full start takes ~30–60 seconds for all health checks to pass.

### 8.1 Lean startup (recommended on low-memory machines)

If your machine has limited RAM or CPU, start only the four core containers. Agent containers (C2b, C5b, C7ab, C7bb, C8b) can be started on demand later.

```bash
# Core stack only: C1b (API) + C3b (auth) + C6b (agent) + C9b (console)
docker compose up app browser-auth kilocode-terminal c9-jokes -d

# Add sandbox containers only when needed:
docker compose up c10b-sandbox -d    # C10b — needed for /api/agent/* pages
docker compose up c11b-sandbox -d    # C11b — needed for /api/multi-Agento/* pages
docker compose up c12b-sandbox -d    # C12b — needed for Tasked pipeline sandbox

# Stop agents to free CPU/memory when not in use:
docker stop C2b_agent-terminal C5b_claude-code C7ab_openclaw-gateway C7bb_openclaw-cli C8b_hermes-agent
```

**Minimum viable stack health check:**
```bash
curl http://localhost:8000/health    # C1b
curl http://localhost:8001/health    # C3b
curl http://localhost:6090/api/status  # C9b
```

### 8.2 Quick start: clone → build → auth → run full stack

```bash
# 1. Clone the repo
git clone https://github.com/zumanm1/copilot-wraper.git
cd copilot-wraper/copilot-openai-wrapper

# 2. Create your env file
cp .env.example .env

# 3. Build all images
docker compose build

# 4. Start C1 + C3 first for authentication
docker compose up -d app browser-auth

# 5. Open the C3 noVNC browser and log in
open http://localhost:6080          # macOS
# xdg-open http://localhost:6080    # Linux

# 6. Extract cookies into .env
curl -X POST http://localhost:8001/extract
curl -X POST http://localhost:8000/v1/reload-config

# 7. Start the full runtime stack
docker compose up -d

# 8. Verify C1-C11
docker compose ps
curl http://localhost:8000/health
curl http://localhost:6090/api/status
```

**Using the automated startup script:**
```bash
# Starts C1 + C3, waits for health, validates cookies, runs E2E check
./start.sh
```

---

## 9. Verify All Containers

Run these checks to confirm every container is working:

```bash
# ── C1 — FastAPI API server ─────────────────────────────────
curl http://localhost:8000/health
# → {"status":"ok","service":"copilot-openai-wrapper"}

# Quick chat test (requires valid cookies from Step 7)
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Say hi."}]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"

# ── C3 — Browser auth ────────────────────────────────────────
curl http://localhost:8001/health
# → {"status":"ok"}
curl -sf http://localhost:6080/ | head -3
# → HTML (noVNC page)

# ── C7ab — OpenClaw gateway ──────────────────────────────────
curl http://localhost:18789/healthz
# → {"status":"ok"} or {"alive":true}

# ── C9b — Validation console ─────────────────────────────────
curl http://localhost:6090/api/status
# → JSON dict with keys for each container

# ── C10b / C11b / C12b — Sandboxes (host-exposed in c3btest) ─
curl -sf http://localhost:8310/health   # C10b agent sandbox
curl -sf http://localhost:8410/health   # C11b multi-agent sandbox
curl -sf http://localhost:8210/health   # C12b lean sandbox

# ── Agent containers (health via exec) ───────────────────────
docker compose exec agent-terminal       curl -sf http://localhost:8080/health
docker compose exec claude-code-terminal curl -sf http://localhost:8080/health
docker compose exec kilocode-terminal    curl -sf http://localhost:8080/health
docker compose exec openclaw-cli         curl -sf http://localhost:8080/health
docker compose exec hermes-agent         curl -sf http://localhost:8080/health
```

**Full reference table:**

| Container | Service | Check | Expected |
|---|---|---|---|
| C1b | `app` | `curl http://localhost:8000/health` | `{"status":"ok"}` |
| C2b | `agent-terminal` | `docker compose exec agent-terminal curl -sf http://localhost:8080/health` | `ok` |
| C3b | `browser-auth` | `curl http://localhost:8001/health` | `{"status":"ok"}` |
| C3b noVNC | `browser-auth` | `curl -sf http://localhost:6080/` | HTML |
| C5b | `claude-code-terminal` | `docker compose exec claude-code-terminal curl -sf http://localhost:8080/health` | `ok` |
| C6b | `kilocode-terminal` | `docker compose exec kilocode-terminal curl -sf http://localhost:8080/health` | `ok` |
| C7ab | `openclaw-gateway` | `curl http://localhost:18789/healthz` | `{"status":"ok"}` |
| C7bb | `openclaw-cli` | `docker compose exec openclaw-cli curl -sf http://localhost:8080/health` | `ok` |
| C8b | `hermes-agent` | `docker compose exec hermes-agent curl -sf http://localhost:8080/health` | `ok` |
| C9b | `c9-jokes` | `curl http://localhost:6090/api/status` | JSON dict |
| C10b | `c10b-sandbox` | `curl -sf http://localhost:8310/health` | `200 OK` JSON |
| C11b | `c11b-sandbox` | `curl -sf http://localhost:8410/health` | `200 OK` JSON |
| C12b | `c12b-sandbox` | `curl -sf http://localhost:8210/health` | `200 OK` JSON |

---

## 10. Access the Services

| Service | URL | Notes |
|---|---|---|
| **C1 API** | `http://localhost:8000` | OpenAI + Anthropic compatible API |
| **C1 Swagger UI** | `http://localhost:8000/docs` | Interactive API browser |
| **C3 noVNC** | `http://localhost:6080` | Remote browser for authentication |
| **C3 Setup** | `http://localhost:8001/setup` | Portal profile configuration form |
| **C7a Gateway** | `http://localhost:18789` | OpenClaw WebSocket gateway |
| **C9 Console** | `http://localhost:6090` | Validation dashboard — chat, pairs, logs, health |
| **C10b Sandbox** | `http://localhost:8310` (host) | Agent workspace sandbox — used by C9b `/api/agent/*` APIs |
| **C11b Sandbox** | `http://localhost:8410` (host) | Multi-agent session sandbox — used by C9b `/api/multi-Agento/*` APIs |
| **C12b Sandbox** | `http://localhost:8210` (host) | Lean coding/test sandbox — used by Tasked pipeline `/api/sandbox/exec` |

**C9 Pages:**

| Page | URL | Purpose |
|---|---|---|
| Dashboard | `http://localhost:6090/` | Health overview for all containers |
| Chat | `http://localhost:6090/chat` | Chat with any agent; live token streaming, thinking mode, and file upload |
| Pairs | `http://localhost:6090/pairs` | Batch: one prompt → multiple agents |
| Logs | `http://localhost:6090/logs` | All chat + validation history with timing |
| Health | `http://localhost:6090/health` | Live health snapshots |
| API reference | `http://localhost:6090/api` | C9 REST API reference (`/api/docs` redirects here) |

---

## 11. Using Agent Containers

All agent containers (C2b, C5b, C6b, C7bb, C8b) run in **standby mode** by default. A lightweight health server listens on port 8080 inside the container. You attach interactively with `docker compose exec` or use `cluster-start.sh`.

### Interactive attach

```bash
# C2 — Aider coding agent
docker compose exec agent-terminal bash
# Inside container:
aider                          # Start Aider
opencode                       # Start OpenCode

# C5 — Claude Code CLI
docker compose exec claude-code-terminal bash
# Inside container:
claude                         # Start Claude Code (routes to C1)

# C6 — KiloCode CLI
docker compose exec kilocode-terminal bash
# Inside container:
kilocode                       # Start KiloCode

# C8 — Hermes Agent
docker compose exec hermes-agent bash
# Inside container:
hermes ask "Hello"             # One-shot question
hermes                         # Interactive TUI
```

### cluster-start.sh — quick launcher

```bash
# Usage
./cluster-start.sh [agent-name]

# Start specific agents
./cluster-start.sh aider       # Launches C2 Aider
./cluster-start.sh claude      # Launches C5 Claude Code
./cluster-start.sh kilocode    # Launches C6 KiloCode
./cluster-start.sh openclaw    # Launches C7b OpenClaw CLI

# Check all container health
./cluster-start.sh status
```

### One-shot prompt from host

```bash
# Ask any agent a question via C1 (OpenAI format)
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: c8-hermes" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"What is 2+2?"}]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"
```

---

## 12. Updating

```bash
# Pull latest code
git pull

# Rebuild any changed images
docker compose build

# Restart with new images (zero downtime for unchanged containers)
docker compose up -d

# Check all containers came up healthy
docker compose ps
```

> **Named volumes persist across updates.** The following data is preserved:
> - `copilot-browser-profile` — C3 browser session (cookies, login state)
> - `openclaw-config` — C7a gateway configuration
> - `hermes-config` — C8 Hermes Agent memory, skills, cron jobs
> - `c9-data` — C9 SQLite database (all chat logs, pair results, health history)
> - `c10-workspace` — C10 agent workspace files
> - `c11-workspace` — C11 multi-agent workspace files

---

## 13. Uninstall

```bash
# Stop and remove containers + network
docker compose down

# Also remove persistent volumes (⚠ irreversible — deletes all data)
docker compose down -v

# Remove all built images
docker image rm \
  copilot-api:latest \
  copilot-browser-auth:latest \
  copilot-agent-terminal:latest \
  copilot-claude-code-terminal:latest \
  copilot-kilocode-terminal:latest \
  copilot-openclaw-gateway:latest \
  copilot-openclaw-cli:latest \
  copilot-hermes-agent:latest \
  copilot-c9-jokes:latest \
  copilot-c10-sandbox:latest \
  copilot-c11-sandbox:latest \
  copilot-c12b-sandbox:latest \
  2>/dev/null || true

# Remove the cloned repository
cd ../..
rm -rf copilot-wraper
```

---

## 14. Linux-Specific Notes

### Docker Engine vs Docker Desktop

On Linux, install **Docker Engine** (the daemon) + the **Compose plugin** — not Docker Desktop. Docker Desktop on Linux requires a VM layer and is generally slower. The commands are identical (`docker compose` not `docker-compose`).

### Chrome data path

The `app` (C1) service has a volume bind for the host Chrome profile:

```yaml
# Default (macOS):
- ${HOME}/Library/Application Support/Google/Chrome:/chrome-data:ro

# Linux — change to:
- ${HOME}/.config/google-chrome:/chrome-data:ro
```

If you don't have Chrome installed on Linux, remove this volume line from `docker-compose.yml` entirely. C3 maintains its own browser session and C1 can get cookies from C3 without needing the host Chrome mount.

### Shared memory for C3

C3 requires 2 GB of shared memory for Chromium. This is set in `docker-compose.yml`:

```yaml
browser-auth:
  shm_size: 2g
```

Verify your system has enough `/dev/shm`:

```bash
df -h /dev/shm
# Should show ≥ 2G available
```

If `/dev/shm` is smaller (common in containers-in-containers or restricted environments), increase it:

```bash
sudo mount -o remount,size=2G /dev/shm
```

### Port access

All exposed ports bind to `0.0.0.0` by default in docker-compose.yml, meaning they're accessible on all network interfaces on the Linux machine. If you're on a shared server, restrict to loopback:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

### Firewall

If you're running UFW:

```bash
# Allow ports from localhost only (already default for loopback)
# If you need remote access, add:
sudo ufw allow 8000/tcp
sudo ufw allow 6090/tcp
```

---

## 15. macOS-Specific Notes

### Chrome profile path

The default docker-compose.yml volume bind uses the standard macOS path:

```yaml
- ${HOME}/Library/Application Support/Google/Chrome:/chrome-data:ro
```

This works for Google Chrome. If you use **Chromium** or a different Chrome variant, adjust to match:

```bash
# Chromium:
${HOME}/Library/Application Support/Chromium
# Chrome Canary:
${HOME}/Library/Application Support/Google/Chrome Canary
```

### Docker Desktop memory limit

Docker Desktop allocates a fixed amount of memory to the Docker VM. For the full C1-C11 stack:

1. Open Docker Desktop → Settings (gear icon) → Resources
2. Set **Memory** to at least **8 GB** (12 GB recommended for the full C1-C11 stack)
3. Set **Disk image size** to at least **30 GB**
4. Click "Apply & restart"

### Apple Silicon (M1/M2/M3)

All base images are multi-arch:
- `python:3.11-slim` — arm64 ✅
- `node:20-alpine`, `node:22-alpine` — arm64 ✅
- `ubuntu:22.04` — arm64 ✅
- `mcr.microsoft.com/playwright/python:v1.49.0-jammy` — arm64 ✅

No Rosetta 2, no `--platform linux/amd64` needed.

### macOS `open` command

The guide uses `open http://localhost:...` throughout. This opens the URL in your default browser. If you prefer a specific browser:

```bash
open -a "Google Chrome" http://localhost:6090
open -a Safari http://localhost:8000/docs
```

---

## 16. Troubleshooting

### C1 returns 401 / 403 from Copilot

**Cause:** Cookies are missing, expired, or not loaded.

```bash
# Check if C1 has cookies
curl http://localhost:8000/v1/debug/cookie

# Re-run the full C3 cookie extraction flow
# 1. Open noVNC: http://localhost:6080
# 2. Log in to copilot.microsoft.com (or m365.cloud.microsoft for M365 profile)
# 3. Extract: curl -X POST http://localhost:8001/extract
# 4. Reload: curl -X POST http://localhost:8000/v1/reload-config
```

### C3 noVNC shows a blank or black screen

**Cause:** Chromium crashed due to insufficient shared memory.

```bash
# Check shm_size in docker-compose.yml (should be "2g")
grep shm_size docker-compose.yml

# Restart C3
docker compose restart browser-auth

# Linux — increase /dev/shm if needed
sudo mount -o remount,size=2G /dev/shm
```

### C8 build fails with git clone error

**Cause:** No internet access during build, or GitHub rate limiting.

```bash
# Check internet from the build host
curl -sf https://github.com > /dev/null && echo "OK" || echo "FAIL"

# If behind a proxy, set build args in docker-compose.yml:
# build:
#   args:
#     - HTTP_PROXY=http://proxy.example.com:3128
#     - HTTPS_PROXY=http://proxy.example.com:3128
```

### Port already in use

```bash
# macOS
lsof -i :8000
lsof -i :6090

# Linux
ss -tlnp | grep 8000
ss -tlnp | grep 6090

# Kill the conflicting process or change the port in docker-compose.yml
```

### Container stuck in "starting" / never healthy

```bash
# View logs for a specific container
docker compose logs app --tail 50
docker compose logs browser-auth --tail 50
docker compose logs hermes-agent --tail 50

# Restart a single container
docker compose restart hermes-agent
```

### C9 shows no logs at `/logs`

**Cause:** The SQLite database hasn't been written to yet, or migrations haven't run.

```bash
# Check C9 logs
docker compose logs c9-jokes --tail 30

# Trigger a chat via C9 to seed the database
curl -s -X POST http://localhost:6090/api/chat \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"c1","prompt":"Hello"}'

# Then reload http://localhost:6090/logs
```

### OpenClaw (C7a) fails to start

```bash
# Check the gateway logs
docker compose logs openclaw-gateway --tail 30

# Verify the gateway token matches between C7a and C7b in docker-compose.yml
grep OPENCLAW_GATEWAY_TOKEN docker-compose.yml
# Both should have: OPENCLAW_GATEWAY_TOKEN=copilot-local-gateway-token
```

### Full reset (start from scratch)

```bash
# Stop everything and remove all volumes + images
docker compose down -v
docker system prune -f

# Rebuild from scratch
docker compose build --no-cache
docker compose up -d
```

---

## Dependency Summary

All dependencies are managed inside Docker — you do not need Python, Node.js, or any other runtime on your host machine. The only host requirements are Docker, Git, and curl.

| Container | Base Image | Key Dependencies |
|---|---|---|
| C1 | `python:3.11-slim` | fastapi, uvicorn, websockets, sydney-py, openai, pypdf, python-docx, openpyxl, python-pptx |
| C2 | `python:3.11-slim` | Node.js 20, aider-chat, opencode-ai |
| C3 | `ubuntu:22.04` | playwright 1.44.0, websockify, x11vnc, xvfb, novnc, chromium |
| C5 | `node:20-alpine` | @anthropic-ai/claude-code |
| C6 | `node:20-alpine` | @kilocode/cli |
| C7a | `node:22-alpine` | openclaw@2026.3.13 (native addons) |
| C7b | `node:22-alpine` | openclaw@2026.3.13 |
| C8 | `python:3.11-slim` | uv, hermes-agent v2026.3.17 (git clone), ripgrep, ffmpeg |
| C9 | `python:3.11-slim` | fastapi, uvicorn, httpx, jinja2, python-multipart |
| C10 | `python:3.11-slim` | FastAPI sandbox runtime for single-agent workspace execution |
| C11 | `python:3.11-slim` | FastAPI sandbox runtime for multi-agent session execution |
| C12b | `python:3.11-slim` | FastAPI lean sandbox runtime for Tasked pipeline execution |

---

*For architecture details, API reference, and agent guides — see [README.md](README.md).*
