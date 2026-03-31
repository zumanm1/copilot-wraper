#!/usr/bin/env python3
"""
Universal ask helper for copilot-api agent terminals.
Streams tokens live to stdout using SSE (stream:true).

Usage:
    python3 ask_helper.py "question" --api-url URL --agent-id ID [--format openai|anthropic] [--api-key KEY]

Maintains per-agent conversation history in /workspace/.history (or /tmp fallback).
Prepends the professor/polymath system prompt from /workspace/professor_prompt.txt on every call.
Sends X-Agent-ID header so C1 routes the request to the agent's dedicated backend session.
"""
import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request


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


def _error_text(payload) -> str:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return ""
        try:
            payload = json.loads(text)
        except Exception:
            return text
    if isinstance(payload, dict):
        for key in ("detail", "error", "message"):
            value = payload.get(key)
            if value:
                return _error_text(value)
        return json.dumps(payload)[:400]
    return str(payload)


def _check_host_port(url: str) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "app"
        port = parsed.port or (443 if parsed.scheme == "https" else 8000)
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _print_upstream_failure(status_code: int | None, body_text: str = "", err_text: str = "") -> None:
    print("", file=sys.stderr)
    print("  ✗ C1 (copilot-api) returned no response (HTTP error or empty body)", file=sys.stderr)
    if status_code is not None:
        print(f"  HTTP status: {status_code}", file=sys.stderr)
    if err_text:
        print(f"  error: {err_text[:300]}", file=sys.stderr)
    if body_text:
        print(f"  body: {body_text[:300]}", file=sys.stderr)
    kind = _classify_upstream_error(body_text or err_text)
    if kind == "auth":
        print("  → C1 is reachable, but upstream auth/session is invalid.", file=sys.stderr)
        print("  → Refresh cookies: docker compose up browser-auth -d", file=sys.stderr)
    elif kind == "parse":
        print("  → C1 is reachable, but response envelope/parsing is mismatched.", file=sys.stderr)
    else:
        print("  → C1 is reachable, but upstream returned an error/empty response.", file=sys.stderr)
    print("", file=sys.stderr)


def _load_history(path: str) -> list[dict]:
    try:
        with open(path) as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(path: str, history: list[dict]) -> None:
    with open(path, "w") as handle:
        json.dump(history, handle, indent=2)


def _handle_json_response(raw_body: str) -> tuple[str, list[str], str]:
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        print(f"  Error: invalid JSON from API — {exc}", file=sys.stderr)
        print(f"  Raw: {raw_body[:300]}", file=sys.stderr)
        sys.exit(1)

    if "error" in data:
        err_msg = _error_text(data["error"])
        print(f"  API Error: {err_msg}", file=sys.stderr)
        kind = _classify_upstream_error(err_msg)
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
            choice = data["choices"][0]
            response_text = choice["message"]["content"]
            usage = data.get("usage", {})
            suggestions = choice.get("suggested_responses", [])
            if not response_text and usage.get("completion_tokens", 1) == 0:
                print("", file=sys.stderr)
                print("  ⚠️  C1 is up but Copilot returned an empty reply (0 completion tokens).", file=sys.stderr)
                print("     Copilot cookies may be expired — refresh with:", file=sys.stderr)
                print("     docker compose up browser-auth -d", file=sys.stderr)
                print("", file=sys.stderr)
                sys.exit(1)
            summary = f"  [tokens: {usage.get('total_tokens', '?')} | model: {data.get('model', '?')} | session: {args.agent_id}]"
            return response_text, suggestions, summary

        response_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                response_text = block["text"]
                break
        usage = data.get("usage", {})
        suggestions = data.get("suggested_responses", [])
        if not response_text and usage.get("output_tokens", 1) == 0:
            print("", file=sys.stderr)
            print("  ⚠️  C1 is up but Copilot returned an empty reply (0 output tokens).", file=sys.stderr)
            print("     Copilot cookies may be expired — refresh with:", file=sys.stderr)
            print("     docker compose up browser-auth -d", file=sys.stderr)
            print("", file=sys.stderr)
            sys.exit(1)
        summary = f"  [in: {usage.get('input_tokens', '?')} | out: {usage.get('output_tokens', '?')} | session: {args.agent_id}]"
        return response_text, suggestions, summary
    except (KeyError, IndexError) as exc:
        print(f"  Error extracting response: {exc}", file=sys.stderr)
        print("  Error class: response-envelope-mismatch", file=sys.stderr)
        print("  Action: verify C1 response schema for this provider.", file=sys.stderr)
        print(f"  Raw: {raw_body[:400]}", file=sys.stderr)
        sys.exit(1)


history_dir = "/workspace/.history"
try:
    os.makedirs(history_dir, exist_ok=True)
except OSError:
    history_dir = "/tmp/ask_helper_history"
    os.makedirs(history_dir, exist_ok=True)
history_file = f"{history_dir}/{args.agent_id}_history.json"
prompt_file = "/workspace/professor_prompt.txt"

try:
    with open(prompt_file) as handle:
        system_prompt = handle.read().strip()
except Exception:
    system_prompt = "You are a helpful, rigorous scholar and educator."

history = _load_history(history_file)
history.append({"role": "user", "content": args.question})

if args.format == "openai":
    payload = json.dumps({
        "model": "copilot",
        "messages": [{"role": "system", "content": system_prompt}] + history,
        "stream": True,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Agent-ID": args.agent_id,
    }
else:
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": history,
        "stream": True,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "x-api-key": args.api_key,
        "anthropic-version": "2023-06-01",
        "X-Agent-ID": args.agent_id,
    }

c1_ok, c1_err = _check_host_port(args.api_url)
if not c1_ok:
    print("", file=sys.stderr)
    print(f"  ⚠️  C1 (copilot-api) is NOT reachable at {args.api_url}", file=sys.stderr)
    print(f"     Error: {c1_err}", file=sys.stderr)
    print("     → Start C1:  docker compose up app -d", file=sys.stderr)
    print("     → Start C3:  docker compose up browser-auth -d  (for fresh cookies)", file=sys.stderr)
    print("", file=sys.stderr)
    sys.exit(1)

request = urllib.request.Request(args.api_url, data=payload, headers=headers, method="POST")
response_text = ""
suggestions: list[str] = []
summary = f"  [streaming | session: {args.agent_id}]"
tokens_in = 0
tokens_out = 0
streamed_output = False

try:
    with urllib.request.urlopen(request, timeout=120) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" not in content_type:
            raw_body = response.read().decode("utf-8", errors="replace")
            if not raw_body.strip():
                _print_upstream_failure(response.getcode(), raw_body)
                sys.exit(1)
            response_text, suggestions, summary = _handle_json_response(raw_body)
        else:
            streamed_output = True
            print()
            try:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if "error" in chunk:
                        err_msg = _error_text(chunk["error"])
                        print(f"\n  API Error: {err_msg}", file=sys.stderr)
                        if _classify_upstream_error(err_msg) == "auth":
                            print("  → Refresh cookies: docker compose up browser-auth -d", file=sys.stderr)
                        sys.exit(1)

                    if args.format == "openai":
                        for choice in chunk.get("choices", []):
                            token = (choice.get("delta") or {}).get("content") or ""
                            if token:
                                print(token, end="", flush=True)
                                response_text += token
                            if choice.get("finish_reason"):
                                suggestions = choice.get("suggested_responses", []) or suggestions
                    else:
                        event_type = chunk.get("type", "")
                        if event_type == "content_block_delta":
                            token = chunk.get("delta", {}).get("text") or ""
                            if token:
                                print(token, end="", flush=True)
                                response_text += token
                        elif event_type == "message_start":
                            usage = chunk.get("message", {}).get("usage", {})
                            tokens_in = usage.get("input_tokens", 0)
                        elif event_type == "message_delta":
                            tokens_out = chunk.get("usage", {}).get("output_tokens", 0)
            except KeyboardInterrupt:
                print("\n  [interrupted]", file=sys.stderr)
                sys.exit(1)
            print()
            print()
            if args.format == "anthropic":
                summary = f"  [in: {tokens_in} | out: {tokens_out} | session: {args.agent_id}]"
except urllib.error.HTTPError as exc:
    body_text = exc.read().decode("utf-8", errors="replace")
    _print_upstream_failure(exc.code, body_text)
    sys.exit(1)
except urllib.error.URLError as exc:
    _print_upstream_failure(None, err_text=str(exc))
    sys.exit(1)
except (TimeoutError, socket.timeout):
    print("  Error: API call timed out after 120s", file=sys.stderr)
    sys.exit(1)

if not response_text:
    print("", file=sys.stderr)
    print("  ⚠️  C1 is up but returned an empty reply.", file=sys.stderr)
    print("     Copilot cookies may be expired — refresh with:", file=sys.stderr)
    print("     docker compose up browser-auth -d", file=sys.stderr)
    print("", file=sys.stderr)
    sys.exit(1)

if not streamed_output:
    print(response_text)
    print()
print(summary)

if suggestions:
    print("\n  💡 Suggested follow-ups:")
    for idx, suggestion in enumerate(suggestions, 1):
        print(f"    {idx}. {suggestion}")
    print("    Enter number to select or press Enter to skip.")
    try:
        if sys.stdin.isatty():
            choice_idx = input("  > ").strip()
            if choice_idx.isdigit() and 1 <= int(choice_idx) <= len(suggestions):
                selected = suggestions[int(choice_idx) - 1]
                print(f"  Selected: {selected}")
                history.append({"role": "assistant", "content": response_text})
                _save_history(history_file, history)
                os.execvp("python3", ["python3", sys.argv[0], selected] + sys.argv[2:])
    except EOFError:
        pass

history.append({"role": "assistant", "content": response_text})
try:
    _save_history(history_file, history)
except Exception as exc:
    print(f"  Warning: could not save history — {exc}", file=sys.stderr)
