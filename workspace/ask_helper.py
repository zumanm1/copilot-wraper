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


def _classify_upstream_error(message: str) -> str:
    msg = (message or "").lower()
    if any(x in msg for x in ("unauthorized", "401", "403", "handshake", "verification required", "challenge", "cookie")):
        return "auth"
    if any(x in msg for x in ("json", "choices", "content", "extracting response", "invalid")):
        return "parse"
    return "unknown"

history_dir = "/workspace/.history"
history_file = f"{history_dir}/{args.agent_id}_history.json"
prompt_file = "/workspace/professor_prompt.txt"

# ── Load system prompt ────────────────────────────────────────────────────────
try:
    with open(prompt_file) as f:
        system_prompt = f.read().strip()
except Exception:
    system_prompt = "You are a helpful, rigorous scholar and educator."

# ── Load conversation history (EC2: reset on corruption) ─────────────────────
try:
    if not os.path.exists(history_dir):
        os.makedirs(history_dir, exist_ok=True)
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
    "curl", "-sS", "-X", "POST", args.api_url,
    "-H", "Content-Type: application/json",
] + extra_headers + ["-d", payload, "-w", "\n__ASK_HELPER_HTTP_STATUS__:%{http_code}\n"]

try:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
except subprocess.TimeoutExpired:
    print("  Error: API call timed out after 120s", file=sys.stderr)
    sys.exit(1)

raw_stdout = result.stdout or ""
status_code = None
if "__ASK_HELPER_HTTP_STATUS__:" in raw_stdout:
    body, _, tail = raw_stdout.rpartition("__ASK_HELPER_HTTP_STATUS__:")
    raw_stdout = body.rstrip("\n")
    try:
        status_code = int(tail.strip().splitlines()[0].strip())
    except Exception:
        status_code = None

if result.returncode != 0 or (status_code is not None and status_code >= 400) or not raw_stdout.strip():
    print(f"", file=sys.stderr)
    print(f"  ✗ C1 (copilot-api) returned no response (HTTP error or empty body)", file=sys.stderr)
    if status_code is not None:
        print(f"  HTTP status: {status_code}", file=sys.stderr)
    if result.stderr:
        print(f"  curl: {result.stderr[:300]}", file=sys.stderr)
    kind = _classify_upstream_error(result.stderr)
    if raw_stdout.strip():
        kind = _classify_upstream_error(raw_stdout)
        print(f"  body: {raw_stdout[:300]}", file=sys.stderr)
    if kind == "auth":
        print(f"  → C1 is reachable, but upstream auth/session is invalid.", file=sys.stderr)
        print(f"  → Refresh cookies: docker compose up browser-auth -d", file=sys.stderr)
    elif kind == "parse":
        print(f"  → C1 is reachable, but response envelope/parsing is mismatched.", file=sys.stderr)
    else:
        print(f"  → C1 is reachable, but upstream returned an error/empty response.", file=sys.stderr)
    print(f"", file=sys.stderr)
    sys.exit(1)

# ── Parse and display response ────────────────────────────────────────────────
try:
    d = json.loads(raw_stdout)
except json.JSONDecodeError as e:
    print(f"  Error: invalid JSON from API — {e}", file=sys.stderr)
    print(f"  Raw: {raw_stdout[:300]}", file=sys.stderr)
    sys.exit(1)

# EC4: handle error responses from API
if "error" in d:
    err = d["error"]
    err_msg = err.get("message", err) if isinstance(err, dict) else str(err)
    print(f"  API Error: {err_msg}", file=sys.stderr)
    kind = _classify_upstream_error(str(err_msg))
    if kind == "auth":
        print("  Error class: upstream-auth-invalid", file=sys.stderr)
        print("  Action: sign in (provider-matching portal), extract cookies, reload C1.", file=sys.stderr)
    elif kind == "parse":
        print("  Error class: response-envelope-mismatch", file=sys.stderr)
        print("  Action: inspect C1 response schema and ask_helper parser assumptions.", file=sys.stderr)
    else:
        print("  Error class: upstream-unknown", file=sys.stderr)
    sys.exit(1)

try:
    if args.format == "openai":
        choice = d["choices"][0]
        response_text = choice["message"]["content"]
        usage = d.get("usage", {})
        suggestions = choice.get("suggested_responses", [])
        
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
        
        # Display suggestions
        if suggestions:
            print(f"\n  💡 Suggested follow-ups:")
            for i, s in enumerate(suggestions, 1):
                print(f"    {i}. {s}")
            print(f"    Enter number to select or press Enter to skip.")
            try:
                # Wait for user input if in interactive terminal
                if sys.stdin.isatty():
                    choice_idx = input("  > ").strip()
                    if choice_idx.isdigit() and 1 <= int(choice_idx) <= len(suggestions):
                        selected = suggestions[int(choice_idx)-1]
                        print(f"  Selected: {selected}")
                        # Re-run ask_helper with the selected suggestion
                        # This works because the script persists history below
                        history.append({"role": "assistant", "content": response_text})
                        with open(history_file, "w") as f:
                            json.dump(history, f, indent=2)
                        
                        os.execvp("python3", ["python3", sys.argv[0], selected] + sys.argv[2:])
            except EOFError:
                pass

    else:
        response_text = ""
        for block in d.get("content", []):
            if block.get("type") == "text":
                response_text = block["text"]
                break
        usage = d.get("usage", {})
        suggestions = d.get("suggested_responses", [])
        
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
        
        # Display suggestions
        if suggestions:
            print(f"\n  💡 Suggested follow-ups:")
            for i, s in enumerate(suggestions, 1):
                print(f"    {i}. {s}")
            print(f"    Enter number to select or press Enter to skip.")
            try:
                if sys.stdin.isatty():
                    choice_idx = input("  > ").strip()
                    if choice_idx.isdigit() and 1 <= int(choice_idx) <= len(suggestions):
                        selected = suggestions[int(choice_idx)-1]
                        print(f"  Selected: {selected}")
                        history.append({"role": "assistant", "content": response_text})
                        with open(history_file, "w") as f:
                            json.dump(history, f, indent=2)
                        os.execvp("python3", ["python3", sys.argv[0], selected] + sys.argv[2:])
            except EOFError:
                pass

except (KeyError, IndexError) as e:
    print(f"  Error extracting response: {e}", file=sys.stderr)
    print("  Error class: response-envelope-mismatch", file=sys.stderr)
    print("  Action: verify C1 response schema for this provider.", file=sys.stderr)
    print(f"  Raw: {raw_stdout[:400]}", file=sys.stderr)
    sys.exit(1)

# ── Persist history ───────────────────────────────────────────────────────────
history.append({"role": "assistant", "content": response_text})
try:
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)
except Exception as e:
    print(f"  Warning: could not save history — {e}", file=sys.stderr)
