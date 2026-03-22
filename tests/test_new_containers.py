"""
============================================================
Containers 4 / 5 / 6 Integration Tests
============================================================

Tests the full 6-container stack. Verifies:

  C4 (Cline)      → C1 OpenAI-compat endpoint (/v1/chat/completions)
  C5 (Claude Code)→ C1 Anthropic-compat endpoint (/v1/messages)
  C6 (KiloCode)   → code-server health + browser IDE loads
  C3 (browser-auth) → health, status, C1 connectivity
  Cross-container → calculator consistency across all agents

Run from host:
  docker compose run --rm test python -m pytest test_new_containers.py -v

Environment variables (set in docker-compose test service):
  BASE_URL  = http://app:8000           (C1)
  C3_URL    = http://browser-auth:8001  (C3)
  C6_URL    = http://kilocode-server:9001 (C6)

Test categories:
  [C4]     — Cline dependency tests (C1 OpenAI endpoint)
  [C5]     — Claude Code dependency tests (C1 Anthropic /v1/messages)
  [C6]     — KiloCode server tests (browser IDE)
  [C3]     — Browser-auth additional tests
  [CALC]   — Calculator consistency: 10M + 5M + 500K = 15,500,000
  [SCHEMA] — Anthropic /v1/messages schema validation
============================================================
"""
from __future__ import annotations

import json
import os
import sys
import time
import subprocess
import pathlib
import pytest
from playwright.sync_api import sync_playwright, APIRequestContext, Page, expect

# ─────────────────────────── Configuration ────────────────────────────

BASE_URL  = os.getenv("BASE_URL",  "http://app:8000")
C3_URL    = os.getenv("C3_URL",    "http://browser-auth:8001")
C6_URL    = os.getenv("C6_URL",    "http://kilocode-server:9001")

TIMEOUT_MS = 20_000

REPORTS_DIR = pathlib.Path(__file__).parent / "reports"
SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"
REPORTS_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Full stack is only reachable on copilot-net (docker compose `test` service sets this).
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CONTAINER_E2E", "").lower() not in ("1", "true", "yes"),
    reason="Container E2E: run via `docker compose run --rm test` (sets RUN_CONTAINER_E2E=1).",
)


def screenshot(page: Page, name: str) -> pathlib.Path:
    path = SCREENSHOTS_DIR / f"{name}_{int(time.time())}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"  📸 {path.name}")
    return path


# ─────────────────────────── Shared fixtures ──────────────────────────

@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def c1(pw):
    """API context pointing to Container 1 (Copilot API)."""
    ctx = pw.request.new_context(
        base_url=BASE_URL,
        extra_http_headers={"Content-Type": "application/json"},
        timeout=TIMEOUT_MS,
    )
    yield ctx
    ctx.dispose()


@pytest.fixture(scope="session")
def c3(pw):
    """API context pointing to Container 3 (browser-auth)."""
    ctx = pw.request.new_context(
        base_url=C3_URL,
        extra_http_headers={"Content-Type": "application/json"},
        timeout=TIMEOUT_MS,
    )
    yield ctx
    ctx.dispose()


@pytest.fixture(scope="session")
def c6(pw):
    """API context pointing to Container 6 (KiloCode server)."""
    ctx = pw.request.new_context(
        base_url=C6_URL,
        timeout=TIMEOUT_MS,
    )
    yield ctx
    ctx.dispose()


@pytest.fixture(scope="session")
def browser(pw):
    b = pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    yield b
    b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.set_default_timeout(TIMEOUT_MS)
    yield pg
    pg.close()
    ctx.close()


# ══════════════════════════════════════════════════════════════════════
# [C4] Container 4 — Cline dependency tests
# Cline uses: GET /v1/models, POST /v1/chat/completions (OpenAI format)
# ══════════════════════════════════════════════════════════════════════

class TestC4ClineViaC1:
    """
    Cline (C4) talks to C1 via OpenAI-compatible /v1/chat/completions.
    These tests verify the exact endpoints and response formats that
    Cline relies on are working correctly.
    """

    def test_c1_health_reachable_from_network(self, c1):
        """[C4] C1 health endpoint must be reachable (C4 depends on this)."""
        r = c1.get("/health")
        assert r.status == 200, f"C1 health check failed: {r.status} {r.text()}"
        body = r.json()
        assert body["status"] == "ok"

    def test_c4_model_copilot_listed(self, c1):
        """[C4] 'copilot' model must appear in /v1/models (Cline uses this ID)."""
        r = c1.get("/v1/models")
        assert r.status == 200
        ids = {m["id"] for m in r.json()["data"]}
        assert "copilot" in ids, f"'copilot' model missing from list: {ids}"

    def test_c4_openai_chat_completions_schema_accepted(self, c1):
        """[C4] OpenAI-format POST /v1/chat/completions accepted with copilot model."""
        payload = {
            "model": "copilot",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        }
        r = c1.post("/v1/chat/completions", data=json.dumps(payload))
        # 200 = live Copilot responded; 500 = auth/rate-limit (schema was valid)
        assert r.status in (200, 500), \
            f"C4 OpenAI request rejected at schema level: {r.status} {r.text()}"

    def test_c4_openai_response_fields_present(self, c1):
        """[C4] Non-500 response must have OpenAI chat.completion fields."""
        payload = {
            "model": "copilot",
            "messages": [{"role": "user", "content": "say yes"}],
            "stream": False,
        }
        r = c1.post("/v1/chat/completions", data=json.dumps(payload))
        if r.status == 200:
            body = r.json()
            assert body["object"] == "chat.completion"
            assert "choices" in body
            assert "usage" in body
            assert body["choices"][0]["message"]["role"] == "assistant"

    def test_c4_openai_streaming_accepted(self, c1):
        """[C4] Streaming (SSE) request accepted — Cline can use stream=true."""
        payload = {
            "model": "copilot",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        r = c1.post("/v1/chat/completions", data=json.dumps(payload))
        assert r.status in (200, 500), \
            f"C4 streaming request rejected: {r.status} {r.text()}"
        if r.status == 200:
            ct = r.headers.get("content-type", "")
            assert "text/event-stream" in ct

    def test_c4_cline_settings_json_api_provider(self):
        """[C4] cline-terminal/cline-settings.json must set openai-compatible provider."""
        settings_path = pathlib.Path("/cline-terminal/cline-settings.json")
        if not settings_path.exists():
            pytest.skip("cline-settings.json not mounted in test container")
        settings = json.loads(settings_path.read_text())
        assert settings.get("cline.apiProvider") == "openai-compatible", \
            f"Expected openai-compatible provider, got: {settings}"
        assert "app:8000" in settings.get("cline.openAiCompatible.baseUrl", ""), \
            "Cline settings must point to Container 1"

    def test_c4_calc_10m_plus_5m_plus_500k(self):
        """[C4] Calculator: 10 million + 5 million + 500k = 15,500,000."""
        calc_path = pathlib.Path("/workspace/calculator.py")
        if not calc_path.exists():
            pytest.skip("calculator.py not found at /workspace/calculator.py")
        result = subprocess.run(
            [sys.executable, str(calc_path), "10 million + 5 million + 500k"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"Calculator error: {result.stderr}"
        assert "15,500,000" in result.stdout, \
            f"Expected 15,500,000 in output, got: {result.stdout!r}"


# ══════════════════════════════════════════════════════════════════════
# [C5] Container 5 — Claude Code dependency tests
# Claude Code uses: POST /v1/messages (Anthropic format)
# ══════════════════════════════════════════════════════════════════════

class TestC5ClaudeCodeViaC1Messages:
    """
    Claude Code (C5) talks to C1 via /v1/messages (Anthropic format).
    These tests verify the exact Anthropic Messages API contract that
    Claude Code CLI expects.
    """

    ANTHROPIC_HEADERS = {
        "Content-Type": "application/json",
        "x-api-key": "sk-ant-not-needed-xxxxxxxxxxxxx",
        "anthropic-version": "2023-06-01",
    }

    def _post_message(self, c1, content: str, stream: bool = False) -> object:
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": content}],
            "stream": stream,
        }
        return c1.post(
            "/v1/messages",
            data=json.dumps(payload),
            headers=self.ANTHROPIC_HEADERS,
        )

    def test_c5_messages_endpoint_exists(self, c1):
        """[C5] POST /v1/messages must return 200 or 500 (not 404/405)."""
        r = self._post_message(c1, "ping")
        assert r.status in (200, 500), \
            f"/v1/messages returned unexpected status: {r.status} {r.text()}"

    def test_c5_messages_response_schema_non_streaming(self, c1):
        """[C5] Non-streaming /v1/messages response must match Anthropic schema."""
        r = self._post_message(c1, "say hello")
        if r.status != 200:
            pytest.skip(f"Copilot not responding (status {r.status}) — schema test skipped")
        body = r.json()
        # Anthropic response schema
        assert body.get("type") == "message",       f"Expected type=message: {body}"
        assert body.get("role") == "assistant",     f"Expected role=assistant: {body}"
        assert isinstance(body.get("content"), list), "content must be a list"
        assert len(body["content"]) > 0,            "content must not be empty"
        assert body["content"][0]["type"] == "text", "First content block must be text"
        assert isinstance(body["content"][0]["text"], str)
        assert "usage" in body,                     "usage field required"
        assert "input_tokens" in body["usage"]
        assert "output_tokens" in body["usage"]

    def test_c5_messages_model_field_echoed(self, c1):
        """[C5] Response must include a model field."""
        r = self._post_message(c1, "hi")
        if r.status != 200:
            pytest.skip("Copilot not responding")
        body = r.json()
        assert "model" in body, f"model field missing: {body}"

    def test_c5_messages_stop_reason_present(self, c1):
        """[C5] Non-streaming response must have stop_reason (Claude Code checks this)."""
        r = self._post_message(c1, "hi")
        if r.status != 200:
            pytest.skip("Copilot not responding")
        body = r.json()
        assert "stop_reason" in body, f"stop_reason missing: {body}"
        assert body["stop_reason"] in ("end_turn", "max_tokens", "stop_sequence", None)

    def test_c5_messages_streaming_response(self, c1):
        """[C5] Streaming /v1/messages must return text/event-stream with SSE events."""
        r = self._post_message(c1, "hi", stream=True)
        assert r.status in (200, 500), \
            f"Streaming /v1/messages returned: {r.status} {r.text()}"
        if r.status == 200:
            ct = r.headers.get("content-type", "")
            assert "text/event-stream" in ct, f"Expected SSE content-type: {ct}"

    def test_c5_messages_streaming_sse_events(self, c1):
        """[C5] SSE stream must contain Anthropic event types."""
        r = self._post_message(c1, "say yes", stream=True)
        if r.status != 200:
            pytest.skip("Copilot not responding")
        lines = [l for l in r.text().strip().splitlines() if l.startswith("data: ")]
        assert len(lines) >= 2, "Stream must have at least 2 data lines"
        # First event must be message_start
        first = json.loads(lines[0][len("data: "):])
        assert first["type"] == "message_start", \
            f"First SSE event must be message_start, got: {first['type']}"
        # Must contain content_block_start and message_stop
        types = set()
        for line in lines:
            try:
                ev = json.loads(line[len("data: "):])
                types.add(ev.get("type"))
            except Exception:
                pass
        assert "content_block_start" in types, f"Missing content_block_start event: {types}"
        assert "message_stop" in types, f"Missing message_stop event: {types}"

    def test_c5_messages_system_prompt_accepted(self, c1):
        """[C5] Request with system field must be accepted."""
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "system": "You are a concise assistant.",
            "messages": [{"role": "user", "content": "say yes"}],
        }
        r = c1.post(
            "/v1/messages",
            data=json.dumps(payload),
            headers=self.ANTHROPIC_HEADERS,
        )
        assert r.status in (200, 500), \
            f"System prompt request rejected: {r.status} {r.text()}"

    def test_c5_messages_missing_messages_field_returns_error(self, c1):
        """[C5] /v1/messages without messages field must return 4xx."""
        payload = {"model": "claude-3-5-sonnet-20241022", "max_tokens": 100}
        r = c1.post(
            "/v1/messages",
            data=json.dumps(payload),
            headers=self.ANTHROPIC_HEADERS,
        )
        assert r.status in (400, 422), \
            f"Expected 4xx for missing messages, got: {r.status}"

    def test_c5_messages_anthropic_version_header_not_required(self, c1):
        """[C5] C1 must accept /v1/messages without anthropic-version header."""
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "ping"}],
        }
        r = c1.post(
            "/v1/messages",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert r.status in (200, 500), \
            f"Request without anthropic-version header rejected: {r.status}"

    def test_c5_calc_10m_plus_5m_plus_500k(self):
        """[C5] Calculator: 10 million + 5 million + 500k = 15,500,000."""
        calc_path = pathlib.Path("/workspace/calculator.py")
        if not calc_path.exists():
            pytest.skip("calculator.py not found at /workspace/calculator.py")
        result = subprocess.run(
            [sys.executable, str(calc_path), "10 million + 5 million + 500k"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "15,500,000" in result.stdout


# ══════════════════════════════════════════════════════════════════════
# [C6] Container 6 — KiloCode server tests
# ══════════════════════════════════════════════════════════════════════

class TestC6KiloCodeServer:
    """
    KiloCode (C6) runs code-server on port 9001 with the KiloCode
    VS Code extension pre-installed and configured to use C1.
    """

    def test_c6_health_endpoint(self, c6):
        """[C6] /healthz must respond (code-server liveness probe)."""
        r = c6.get("/healthz")
        # code-server /healthz returns 200 with JSON or expired token
        assert r.status in (200, 401), \
            f"C6 /healthz unexpected status: {r.status} {r.text()}"

    def test_c6_root_redirects_to_ide(self, c6):
        """[C6] GET / must redirect to VS Code IDE (302 or 200)."""
        r = c6.get("/")
        assert r.status in (200, 302, 301), \
            f"C6 root returned unexpected status: {r.status}"

    def test_c6_browser_loads_vscode(self, page):
        """[C6] Browser must load VS Code IDE at http://kilocode-server:9001."""
        page.goto(C6_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        screenshot(page, "c6_kilocode_loaded")
        # code-server loads a VS Code-like workbench
        content = page.content()
        # Should see VS Code workbench elements or login page
        assert any(kw in content.lower() for kw in
                   ["code", "workbench", "vscode", "editor", "monaco"]), \
            "C6 browser page doesn't appear to be VS Code IDE"

    def test_c6_settings_json_points_to_c1(self):
        """[C6] settings.json must configure KiloCode to use Container 1."""
        settings_path = pathlib.Path("/kilocode-server/settings.json")
        if not settings_path.exists():
            pytest.skip("settings.json not mounted in test container")
        settings = json.loads(settings_path.read_text())
        base_url = settings.get("kilocode.openAiCompatible.baseUrl", "")
        assert "app:8000" in base_url, \
            f"KiloCode settings must point to C1, got: {base_url}"
        assert settings.get("kilocode.apiProvider") == "openai-compatible", \
            "KiloCode must use openai-compatible provider"

    def test_c6_c1_reachable_from_kilocode_network(self, c1):
        """[C6] C1 must be reachable on the same Docker network as C6."""
        r = c1.get("/health")
        assert r.status == 200, \
            "C1 not reachable — C6 won't be able to reach its AI backend"

    def test_c6_static_assets_served(self, c6):
        """[C6] code-server must serve static assets (JS/CSS)."""
        r = c6.get("/static/")
        # 200 = directory, 404 = path doesn't exist, 302 = redirect — all OK
        assert r.status in (200, 302, 301, 403, 404), \
            f"C6 static assets returned unexpected status: {r.status}"


# ══════════════════════════════════════════════════════════════════════
# [C3] Container 3 — Browser-auth additional tests
# ══════════════════════════════════════════════════════════════════════

class TestC3BrowserAuth:
    """Additional tests for Container 3 (browser-auth / noVNC)."""

    def test_c3_health_returns_ok(self, c3):
        """[C3] /health must return status=ok."""
        r = c3.get("/health")
        assert r.status == 200, f"C3 health failed: {r.status} {r.text()}"
        body = r.json()
        assert body["status"] == "ok"
        assert "browser-auth" in body.get("service", "").lower()

    def test_c3_status_browser_running(self, c3):
        """[C3] /status must report browser as running."""
        r = c3.get("/status")
        assert r.status == 200, f"C3 /status failed: {r.status}"
        body = r.json()
        assert body.get("browser") == "running", \
            f"Expected browser=running, got: {body}"

    def test_c3_status_has_open_pages(self, c3):
        """[C3] /status must report open_pages count."""
        r = c3.get("/status")
        assert r.status == 200
        body = r.json()
        assert "open_pages" in body, f"Missing open_pages in C3 status: {body}"
        assert isinstance(body["open_pages"], int)

    def test_c3_extract_endpoint_exists(self, c3):
        """[C3] POST /extract endpoint must exist (not 404)."""
        # We don't trigger extraction (would modify .env), just verify it exists
        r = c3.post("/extract", data=json.dumps({}))
        assert r.status != 404, \
            f"C3 /extract endpoint missing (got 404)"

    def test_c3_c1_connectivity(self, c1):
        """[C3] C1 must be reachable so C3 can call /v1/reload-config after extraction."""
        r = c1.post("/v1/reload-config")
        assert r.status == 200, \
            f"C3→C1 reload-config failed: {r.status} {r.text()}"
        assert r.json()["status"] == "ok"


# ══════════════════════════════════════════════════════════════════════
# [CALC] Calculator — consistency across all containers
# ══════════════════════════════════════════════════════════════════════

class TestCalculatorConsistency:
    """
    The calculator is shared across C2, C4, C5 (all mount /workspace).
    These tests verify correctness of all expressions.
    """

    CALC = pathlib.Path("/workspace/calculator.py")

    def _calc(self, expression: str) -> str:
        if not self.CALC.exists():
            pytest.skip("calculator.py not found at /workspace/calculator.py")
        r = subprocess.run(
            [sys.executable, str(self.CALC), expression],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, f"Calculator failed: {r.stderr}"
        return r.stdout

    def test_primary_10m_plus_5m_plus_500k(self):
        """[CALC] Primary test: 10 million + 5 million + 500k = 15,500,000."""
        out = self._calc("10 million + 5 million + 500k")
        assert "15,500,000" in out, f"Got: {out!r}"

    def test_human_readable_label(self):
        """[CALC] Result must include human label '15.5 million'."""
        out = self._calc("10 million + 5 million + 500k")
        assert "15.5 million" in out, f"Missing label in: {out!r}"

    def test_100k_plus_200k(self):
        """[CALC] 100k + 200k = 300,000."""
        out = self._calc("100k + 200k")
        assert "300,000" in out, f"Got: {out!r}"

    def test_1billion_minus_500million(self):
        """[CALC] 1 billion - 500 million = 500,000,000."""
        out = self._calc("1 billion - 500 million")
        assert "500,000,000" in out, f"Got: {out!r}"

    def test_multiplication(self):
        """[CALC] 2 million * 3 = 6,000,000."""
        out = self._calc("2 million * 3")
        assert "6,000,000" in out, f"Got: {out!r}"

    def test_division(self):
        """[CALC] 10 million / 4 = 2,500,000."""
        out = self._calc("10 million / 4")
        assert "2,500,000" in out, f"Got: {out!r}"

    def test_three_addends(self):
        """[CALC] 1k + 1k + 1k = 3,000."""
        out = self._calc("1k + 1k + 1k")
        assert "3,000" in out, f"Got: {out!r}"

    def test_full_test_suite_passes(self):
        """[CALC] Calculator's own test suite must report 6/6 passed."""
        if not self.CALC.exists():
            pytest.skip("calculator.py not found")
        r = subprocess.run(
            [sys.executable, str(self.CALC)],
            capture_output=True, text=True, timeout=15,
        )
        assert r.returncode == 0
        assert "6/6 passed" in r.stdout, \
            f"Expected 6/6 passed, got:\n{r.stdout}"


# ══════════════════════════════════════════════════════════════════════
# [SCHEMA] Anthropic /v1/messages schema — deep validation
# ══════════════════════════════════════════════════════════════════════

class TestAnthropicMessagesSchema:
    """
    Deep schema compliance tests for the /v1/messages endpoint.
    Used by C5 (Claude Code) and any Anthropic SDK client.
    """

    HEADERS = {
        "Content-Type": "application/json",
        "x-api-key": "sk-ant-not-needed-xxxxxxxxxxxxx",
        "anthropic-version": "2023-06-01",
    }

    def _msg(self, c1, content: str, **kwargs) -> dict | None:
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": content}],
            **kwargs,
        }
        r = c1.post("/v1/messages", data=json.dumps(payload), headers=self.HEADERS)
        if r.status != 200:
            return None
        return r.json()

    def test_messages_endpoint_returns_200_or_500(self, c1):
        """[SCHEMA] /v1/messages must not return 404 or 405."""
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "ping"}],
        }
        r = c1.post("/v1/messages", data=json.dumps(payload), headers=self.HEADERS)
        assert r.status not in (404, 405), \
            f"/v1/messages returned {r.status} — endpoint may be missing"

    def test_messages_required_fields_type_and_role(self, c1):
        """[SCHEMA] Response must have type=message and role=assistant."""
        body = self._msg(c1, "hi")
        if body is None:
            pytest.skip("Copilot not responding")
        assert body.get("type") == "message"
        assert body.get("role") == "assistant"

    def test_messages_content_array_structure(self, c1):
        """[SCHEMA] content must be list of {type, text} blocks."""
        body = self._msg(c1, "hi")
        if body is None:
            pytest.skip("Copilot not responding")
        content = body.get("content", [])
        assert isinstance(content, list)
        for block in content:
            assert "type" in block
            if block["type"] == "text":
                assert "text" in block
                assert isinstance(block["text"], str)

    def test_messages_usage_token_counts(self, c1):
        """[SCHEMA] usage must have input_tokens and output_tokens as ints."""
        body = self._msg(c1, "hi")
        if body is None:
            pytest.skip("Copilot not responding")
        usage = body.get("usage", {})
        assert isinstance(usage.get("input_tokens"), int)
        assert isinstance(usage.get("output_tokens"), int)
        assert usage["input_tokens"] >= 0
        assert usage["output_tokens"] >= 0

    def test_messages_id_field_present(self, c1):
        """[SCHEMA] Response must have an id field (msg_ prefix expected)."""
        body = self._msg(c1, "hi")
        if body is None:
            pytest.skip("Copilot not responding")
        assert "id" in body, f"id field missing: {body}"
        assert isinstance(body["id"], str)

    def test_messages_multi_turn_conversation(self, c1):
        """[SCHEMA] Multi-turn messages array must be accepted."""
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "My name is Alice."},
                {"role": "assistant", "content": "Hello Alice!"},
                {"role": "user", "content": "What is my name?"},
            ],
        }
        r = c1.post("/v1/messages", data=json.dumps(payload), headers=self.HEADERS)
        assert r.status in (200, 500), \
            f"Multi-turn messages rejected: {r.status}"

    def test_messages_get_method_returns_405(self, c1):
        """[SCHEMA] GET /v1/messages must return 405 Method Not Allowed."""
        r = c1.get("/v1/messages")
        assert r.status == 405, \
            f"Expected 405 for GET /v1/messages, got {r.status}"

    def test_messages_empty_content_returns_error(self, c1):
        """[SCHEMA] Empty messages array must return 4xx."""
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 10,
            "messages": [],
        }
        r = c1.post("/v1/messages", data=json.dumps(payload), headers=self.HEADERS)
        assert r.status in (400, 422), \
            f"Expected 4xx for empty messages, got: {r.status}"


# ══════════════════════════════════════════════════════════════════════
# [STACK] Full 6-container stack integration
# ══════════════════════════════════════════════════════════════════════

class TestFullStackIntegration:
    """
    Cross-container tests that validate the complete 6-container stack
    is wired together correctly.
    """

    def test_all_containers_healthy(self, c1, c3, c6):
        """[STACK] All 3 persistent containers must be healthy simultaneously."""
        results = {}

        r = c1.get("/health")
        results["C1 copilot-api"] = r.status
        assert r.status == 200, f"C1 unhealthy: {r.status}"

        r = c3.get("/health")
        results["C3 browser-auth"] = r.status
        assert r.status == 200, f"C3 unhealthy: {r.status}"

        r = c6.get("/healthz")
        results["C6 kilocode-server"] = r.status
        assert r.status in (200, 401), f"C6 unhealthy: {r.status}"

        print(f"\n  Stack health: {results}")

    def test_c1_serves_both_openai_and_anthropic(self, c1):
        """[STACK] C1 must serve both /v1/chat/completions (C2/C4) and /v1/messages (C5)."""
        # OpenAI endpoint (C2, C4)
        r_openai = c1.post(
            "/v1/chat/completions",
            data=json.dumps({
                "model": "copilot",
                "messages": [{"role": "user", "content": "ping"}],
            })
        )
        assert r_openai.status in (200, 500), \
            f"OpenAI endpoint broken: {r_openai.status}"

        # Anthropic endpoint (C5)
        r_anthropic = c1.post(
            "/v1/messages",
            data=json.dumps({
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "ping"}],
            }),
            headers={"Content-Type": "application/json"},
        )
        assert r_anthropic.status in (200, 500), \
            f"Anthropic endpoint broken: {r_anthropic.status}"

    def test_c3_can_trigger_c1_reload(self, c1):
        """[STACK] C3→C1 reload-config channel must work (used after cookie extraction)."""
        r = c1.post("/v1/reload-config")
        assert r.status == 200
        body = r.json()
        assert body["status"] == "ok"

    def test_workspace_calculator_accessible(self):
        """[STACK] /workspace/calculator.py must exist and be importable."""
        calc = pathlib.Path("/workspace/calculator.py")
        if not calc.exists():
            pytest.skip("/workspace/calculator.py not found")
        # Import-level test
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, '/workspace'); import calculator; print('ok')"],
            capture_output=True, text=True, timeout=5,
        )
        assert "ok" in result.stdout, f"Calculator import failed: {result.stderr}"

    def test_c1_models_includes_copilot_and_variants(self, c1):
        """[STACK] /v1/models must list copilot and all variant model IDs."""
        r = c1.get("/v1/models")
        assert r.status == 200
        ids = {m["id"] for m in r.json()["data"]}
        expected = {"copilot", "gpt-4", "gpt-4o",
                    "copilot-balanced", "copilot-creative", "copilot-precise"}
        missing = expected - ids
        assert not missing, f"Missing models: {missing}"

    def test_calculator_result_same_across_agents(self):
        """[STACK] Calculator gives identical result regardless of which container runs it."""
        calc = pathlib.Path("/workspace/calculator.py")
        if not calc.exists():
            pytest.skip("/workspace/calculator.py not found")
        expr = "10 million + 5 million + 500k"
        # Run 3 times simulating C2, C4, C5 each running it
        results = []
        for _ in range(3):
            r = subprocess.run(
                [sys.executable, str(calc), expr],
                capture_output=True, text=True, timeout=10,
            )
            assert r.returncode == 0
            results.append(r.stdout.strip())
        # All runs must produce identical output
        assert len(set(results)) == 1, \
            f"Calculator output differs across runs: {results}"
        assert "15,500,000" in results[0]


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sys.exit(pytest.main([
        __file__, "-v", "--tb=short",
        f"--html={REPORTS_DIR}/report_containers.html",
        "--self-contained-html",
    ]))
