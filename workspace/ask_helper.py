#!/usr/bin/env python3
"""
Universal ask helper for copilot-api agent terminals.

Usage:
    python3 ask_helper.py "question" --api-url URL --agent-id ID [--format openai|anthropic] [--api-key KEY]

Maintains per-agent conversation history in /tmp/{agent_id}_history.json.
Prepends the professor/polymath system prompt from /workspace/professor_prompt.txt on every call.
Sends X-Agent-ID header so C1 routes the request to the agent's dedicated backend session.
"""
import json, sys, os, subprocess, argparse, socket

parser = argparse.ArgumentParser(description="Query copilot-api with session isolation")
parser.add_argument("question", help="The question or prompt to send")
parser.add_argument("--api-url", required=True, help="Full API endpoint URL")
parser.add_argument("--agent-id", required=True, help="Unique agent identifier (used for session routing and history)")
parser.add_argument("--format", choices=["openai", "anthropic"], default="openai", help="API format")
parser.add_argument("--api-key", default="sk-ant-not-needed-xxxxxxxxxxxxx", help="API key (Anthropic format only)")
args = parser.parse_args()

history_file = f"/tmp/{args.agent_id}_history.json"
prompt_file = "/workspace/professor_prompt.txt"

# ── Load system prompt ────────────────────────────────────────────────────────
try:
    with open(prompt_file) as f:
        system_prompt = f.read().strip()
except Exception:
    system_prompt = "You are a helpful, rigorous scholar and educator."

# ── Load conversation history (EC2: reset on corruption) ─────────────────────
try:
    with open(history_file) as f:
        history = json.load(f)
    if not isinstance(history, list):
        history = []
except Exception:
    history = []

# Append the new user message to history
history.append({"role": "user", "content": args.question})

# ── Build API payload ─────────────────────────────────────────────────────────
if args.format == "openai":
    # System prompt as first message in messages array
    messages = [{"role": "system", "content": system_prompt}] + history
    payload = json.dumps({"model": "copilot", "messages": messages, "stream": False})
    extra_headers = ["-H", f"X-Agent-ID: {args.agent_id}"]
else:
    # Anthropic format: system is a top-level field
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": history,
    })
    extra_headers = [
        "-H", f"x-api-key: {args.api_key}",
        "-H", "anthropic-version: 2023-06-01",
        "-H", f"X-Agent-ID: {args.agent_id}",
    ]

# ── Pre-flight: check C1 reachability before sending the request ─────────────
def _check_host_port(url: str) -> tuple[bool, str]:
    """Return (reachable, error_msg) for the given URL's host:port."""
    try:
        import urllib.parse
        p = urllib.parse.urlparse(url)
        host = p.hostname or "app"
        port = p.port or (443 if p.scheme == "https" else 8000)
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return True, ""
    except Exception as e:
        return False, str(e)


c1_ok, c1_err = _check_host_port(args.api_url)
if not c1_ok:
    print(f"", file=sys.stderr)
    print(f"  ⚠️  C1 (copilot-api) is NOT reachable at {args.api_url}", file=sys.stderr)
    print(f"     Error: {c1_err}", file=sys.stderr)
    print(f"     → Start C1:  docker compose up app -d", file=sys.stderr)
    print(f"     → Start C3:  docker compose up browser-auth -d  (for fresh cookies)", file=sys.stderr)
    print(f"", file=sys.stderr)
    sys.exit(1)

# ── Call the API ──────────────────────────────────────────────────────────────
cmd = [
    "curl", "-sf", "-X", "POST", args.api_url,
    "-H", "Content-Type: application/json",
] + extra_headers + ["-d", payload]

try:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
except subprocess.TimeoutExpired:
    print("  Error: API call timed out after 120s", file=sys.stderr)
    sys.exit(1)

if result.returncode != 0 or not result.stdout.strip():
    print(f"", file=sys.stderr)
    print(f"  ✗ C1 (copilot-api) returned no response (HTTP error or empty body)", file=sys.stderr)
    if result.stderr:
        print(f"  curl: {result.stderr[:300]}", file=sys.stderr)
    print(f"  → C1 is reachable but Copilot auth may be stale.", file=sys.stderr)
    print(f"  → Refresh cookies: docker compose up browser-auth -d", file=sys.stderr)
    print(f"", file=sys.stderr)
    sys.exit(1)

# ── Parse and display response ────────────────────────────────────────────────
try:
    d = json.loads(result.stdout)
except json.JSONDecodeError as e:
    print(f"  Error: invalid JSON from API — {e}", file=sys.stderr)
    print(f"  Raw: {result.stdout[:300]}", file=sys.stderr)
    sys.exit(1)

# EC4: handle error responses from API
if "error" in d:
    err = d["error"]
    print(f"  API Error: {err.get('message', err)}", file=sys.stderr)
    sys.exit(1)

try:
    if args.format == "openai":
        response_text = d["choices"][0]["message"]["content"]
        usage = d.get("usage", {})
        if not response_text and usage.get("completion_tokens", 1) == 0:
            print(f"", file=sys.stderr)
            print(f"  ⚠️  C1 is up but Copilot returned an empty reply (0 completion tokens).", file=sys.stderr)
            print(f"     Copilot cookies may be expired — refresh with:", file=sys.stderr)
            print(f"     docker compose up browser-auth -d", file=sys.stderr)
            print(f"", file=sys.stderr)
            sys.exit(1)
        print(response_text)
        print()
        print(f"  [tokens: {usage.get('total_tokens', '?')} | model: {d.get('model', '?')} | session: {args.agent_id}]")
    else:
        response_text = ""
        for block in d.get("content", []):
            if block.get("type") == "text":
                response_text = block["text"]
                break
        usage = d.get("usage", {})
        if not response_text and usage.get("output_tokens", 1) == 0:
            print(f"", file=sys.stderr)
            print(f"  ⚠️  C1 is up but Copilot returned an empty reply (0 output tokens).", file=sys.stderr)
            print(f"     Copilot cookies may be expired — refresh with:", file=sys.stderr)
            print(f"     docker compose up browser-auth -d", file=sys.stderr)
            print(f"", file=sys.stderr)
            sys.exit(1)
        print(response_text)
        print()
        print(f"  [in: {usage.get('input_tokens', '?')} | out: {usage.get('output_tokens', '?')} | session: {args.agent_id}]")
except (KeyError, IndexError) as e:
    print(f"  Error extracting response: {e}", file=sys.stderr)
    print(f"  Raw: {result.stdout[:400]}", file=sys.stderr)
    sys.exit(1)

# ── Persist history ───────────────────────────────────────────────────────────
history.append({"role": "assistant", "content": response_text})
try:
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)
except Exception as e:
    print(f"  Warning: could not save history — {e}", file=sys.stderr)
