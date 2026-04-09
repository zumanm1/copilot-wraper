#!/usr/bin/env python3
"""
E2E validation: C9 /api/validate pipeline — all 6 agent pairs via C1+C3+M365.

Requirements:
  - All containers running: docker compose up -d
  - M365 session active in C3 browser (sign in via http://localhost:6080)
  - C3 PagePool initialized (check http://localhost:8001/status)

Run:
  pytest tests/test_e2e_c9_validation.py -v
  pytest tests/test_e2e_c9_validation.py -v -k "parallel"   # parallel only
  pytest tests/test_e2e_c9_validation.py -v -k "sequential" # sequential only
  python tests/test_e2e_c9_validation.py                     # standalone
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Override the autouse conftest fixture that imports agent_manager (unit-test only).
# This E2E test makes only HTTP calls and has no local module dependencies.
@pytest.fixture(autouse=True)
def _reset_agent_registry_between_tests():
    yield


C1_URL = "http://localhost:8000"
C3_URL = "http://localhost:8001"
C9_URL = "http://localhost:6090"
TIMEOUT_AGENT = 360          # seconds per individual agent call
TIMEOUT_PARALLEL = 600       # seconds for full parallel run
PROMPT = "Tell me a joke"
WORK_MODE = "work"           # M365 context scope: "work" | "web"  (X-Work-Mode header)
THINK_MODE = "auto"          # Thinking depth: "auto" | "quick" | "deep" (X-Chat-Mode header)

AGENTS = [
    {"id": "c2-aider",       "label": "C2 Aider (OpenAI)",         "path": "C1→OpenAI"},
    {"id": "c5-claude-code", "label": "C5 Claude Code (Anthropic)", "path": "C1→C3→M365"},
    {"id": "c6-kilocode",    "label": "C6 KiloCode (OpenAI)",       "path": "C1→C3→M365"},
    {"id": "c7-openclaw",    "label": "C7b OpenClaw",               "path": "C1→C3→M365"},
    {"id": "c8-hermes",      "label": "C8 Hermes Agent",            "path": "C1→C3→M365"},
    {"id": "c9-jokes",       "label": "C9 (generic session)",       "path": "C1→C3→M365"},
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _http_post(url: str, payload: dict, timeout: int = 60) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "c9-e2e-test/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_get(url: str, timeout: int = 10) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "c9-e2e-test/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.status
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        # Some endpoints return HTML (e.g. C9 /health serves a page)
        return code, {"_raw": body[:120]}


def _c1_chat(agent_id: str, prompt: str = PROMPT, work_mode: str = WORK_MODE, think_mode: str = THINK_MODE) -> dict:
    payload = {
        "model": "copilot",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Agent-ID": agent_id,
        "X-Work-Mode": work_mode,    # M365 Work/Web toggle → forwarded to C3
        "X-Chat-Mode": think_mode,   # Thinking depth → sets backend.style
        "User-Agent": "c9-e2e-test/1.0",
    }
    req = urllib.request.Request(
        f"{C1_URL}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_AGENT) as resp:
            elapsed = time.monotonic() - t0
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"ok": bool(text.strip()), "text": text, "elapsed": elapsed, "error": None}
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - t0
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail") or raw
        except Exception:
            detail = raw
        return {"ok": False, "text": "", "elapsed": elapsed, "error": detail}
    except Exception as exc:
        return {"ok": False, "text": "", "elapsed": time.monotonic() - t0, "error": str(exc)}


# ── pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def require_containers():
    """Skip all tests if C1 or C9 are not reachable."""
    for name, url in [("C1", f"{C1_URL}/health"), ("C9", f"{C9_URL}/health")]:
        try:
            code, body = _http_get(url, timeout=5)
            assert code == 200, f"{name} health returned {code}"
        except Exception as exc:
            pytest.skip(f"{name} not reachable ({exc}) — start containers first")


@pytest.fixture(scope="session")
def pool_status():
    try:
        _, data = _http_get(f"{C3_URL}/status", timeout=5)
        return data
    except Exception:
        return {}


# ── infrastructure tests ──────────────────────────────────────────────────────

class TestInfrastructure:
    def test_c1_health(self):
        code, body = _http_get(f"{C1_URL}/health")
        assert code == 200
        assert body.get("status") == "ok"

    def test_c3_health(self):
        code, body = _http_get(f"{C3_URL}/health")
        assert code == 200
        assert body.get("status") == "ok"

    def test_c9_health(self):
        # C9 /health may return HTML (200 is sufficient)
        code, _ = _http_get(f"{C9_URL}/health", timeout=5)
        assert code == 200

    def test_c3_pool_initialized(self, pool_status):
        assert pool_status.get("pool_initialized") is True, (
            f"C3 PagePool not initialized: {pool_status}. "
            "Wait for C3 to finish startup or POST http://localhost:8001/pool-reset"
        )

    def test_c3_pool_has_available_tabs(self, pool_status):
        avail = pool_status.get("pool_available", 0)
        size = pool_status.get("pool_size", 0)
        assert size > 0, "C3 pool_size is 0"
        # Available may be 0 if agents are actively using tabs; warn but don't fail
        if avail == 0:
            pytest.xfail(f"pool_available=0 (all {size} tabs in use — run may still pass)")

    def test_c3_session_health(self):
        code, body = _http_get(f"{C3_URL}/session-health")
        assert code == 200
        assert body.get("session") == "active", (
            f"M365 session not active: {body}. "
            "Sign in via noVNC at http://localhost:6080"
        )


# ── per-agent sequential tests ────────────────────────────────────────────────

class TestAgentPipelinesSequential:
    """Test each agent pipeline sequentially via direct C1 POST."""

    @pytest.mark.parametrize("agent", AGENTS, ids=[a["id"] for a in AGENTS])
    def test_agent_c1_roundtrip(self, agent):
        result = _c1_chat(agent["id"])
        assert result["ok"], (
            f"{agent['id']} ({agent['path']}) failed after {result['elapsed']:.1f}s: "
            f"{result['error']}"
        )
        assert len(result["text"].strip()) > 10, (
            f"{agent['id']} returned empty/short response: {result['text']!r}"
        )
        print(f"\n  [{agent['id']}] {result['elapsed']:.2f}s | {result['text'][:80]}")

    @pytest.mark.parametrize("think", ["auto", "quick", "deep"],
                              ids=["think=auto", "think=quick", "think=deep"])
    def test_c9_jokes_thinking_modes(self, think):
        """c9-jokes session (generic) must return a joke under each thinking mode."""
        result = _c1_chat("c9-jokes", prompt="Tell me a short joke", think_mode=think)
        assert result["ok"], (
            f"c9-jokes think_mode={think} failed: {result['error']}"
        )
        assert len(result["text"].strip()) > 5, (
            f"c9-jokes think_mode={think} returned empty: {result['text']!r}"
        )
        print(f"\n  [c9-jokes think={think}] {result['elapsed']:.2f}s | {result['text'][:80]}")

    @pytest.mark.parametrize("work", ["work", "web"],
                              ids=["work_mode=work", "work_mode=web"])
    def test_c9_jokes_work_web_modes(self, work):
        """c9-jokes session must respond under both Work and Web M365 scopes."""
        result = _c1_chat("c9-jokes", prompt="Tell me a short joke", work_mode=work)
        assert result["ok"], (
            f"c9-jokes work_mode={work} failed: {result['error']}"
        )
        print(f"\n  [c9-jokes work={work}] {result['elapsed']:.2f}s | {result['text'][:80]}")


# ── C9 /api/validate parallel test ───────────────────────────────────────────

class TestC9ParallelValidation:
    """Use C9's /api/validate endpoint — the same run as the UI 'Run All Parallel' button."""

    def test_all_agents_parallel(self):
        t0 = time.monotonic()
        code, body = _http_post(
            f"{C9_URL}/api/validate",
            {"prompt": PROMPT, "chat_mode": THINK_MODE, "work_mode": WORK_MODE},
            timeout=TIMEOUT_PARALLEL,
        )
        wall = time.monotonic() - t0

        assert code == 200, f"/api/validate returned HTTP {code}"
        assert body.get("total") == len(AGENTS), (
            f"Expected {len(AGENTS)} agents, got {body.get('total')}: {body}"
        )

        passed = body.get("passed", 0)
        failed = body.get("failed", 0)
        results = body.get("results", [])

        print(f"\n  Run #{body.get('run_id')} | {passed}/{body['total']} passed | {wall:.1f}s wall")
        for r in results:
            mark = "PASS" if r["ok"] else "FAIL"
            err = f" ERR={r['error']}" if not r["ok"] else ""
            print(f"  [{mark}] {r['agent_id']:20} {(r['elapsed_ms'] or 0)/1000:.2f}s{err}")

        assert failed == 0, (
            f"{failed}/{body['total']} agents failed:\n" +
            "\n".join(
                f"  FAIL {r['agent_id']}: {r.get('error') or r.get('text','')[:120]}"
                for r in results if not r["ok"]
            )
        )

    def test_c9_pairs_page_loads(self):
        req = urllib.request.Request(
            f"{C9_URL}/pairs", headers={"User-Agent": "c9-e2e-test/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8", errors="replace")
        assert "run-parallel" in html, "Run All Parallel button not found in /pairs HTML"
        for agent in AGENTS:
            assert agent["id"] in html, f"Agent row for {agent['id']} missing from /pairs"

    def test_c9_chat_page_has_thinking_dropdown(self):
        """C9 chat page must contain the thinking-mode pill and all three options."""
        req = urllib.request.Request(
            f"{C9_URL}/chat", headers={"User-Agent": "c9-e2e-test/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8", errors="replace")
        assert "thinking-pill" in html, "Thinking mode pill button missing from /chat"
        assert "data-mode=\"auto\"" in html, "Auto thinking option missing"
        assert "data-mode=\"quick\"" in html, "Quick Response option missing"
        assert "data-mode=\"deep\"" in html, "Think Deeper option missing"
        assert "thinkingMode" in html, "localStorage thinkingMode key missing"

    def test_c9_chat_page_has_work_web_toggle(self):
        """C9 chat page must contain the Work/Web segmented toggle."""
        req = urllib.request.Request(
            f"{C9_URL}/chat", headers={"User-Agent": "c9-e2e-test/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8", errors="replace")
        assert "work-web-toggle" in html, "Work/Web toggle missing from /chat"
        assert "data-mode=\"work\"" in html, "Work button missing"
        assert "data-mode=\"web\"" in html, "Web button missing"
        assert "workMode" in html, "localStorage workMode key missing"

    def test_c9_chat_page_has_file_upload_button(self):
        """C9 chat page must contain the + file upload button and file input."""
        req = urllib.request.Request(
            f"{C9_URL}/chat", headers={"User-Agent": "c9-e2e-test/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8", errors="replace")
        assert "attach-plus" in html, "File upload + button missing from /chat"
        assert "file-input" in html, "File input missing from /chat"
        assert "Upload files" in html, "Upload files option missing"
        assert "/api/upload" in html, "/api/upload endpoint reference missing"

    def test_c9_dashboard_page_loads(self):
        """C9 dashboard (/) must load and contain container health cards."""
        req = urllib.request.Request(
            f"{C9_URL}/", headers={"User-Agent": "c9-e2e-test/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8", errors="replace")
        # Dashboard shows the health-card grid and at least one stable target label.
        assert "card-grid" in html, "Dashboard card grid missing"
        assert "copilot-api" in html, "copilot-api label missing from dashboard"
        assert "browser-auth" in html, "browser-auth label missing from dashboard"

    def test_c9_api_chat_sends_chat_mode(self):
        """POST /api/chat with chat_mode=deep must accept request (no error)."""
        code, body = _http_post(
            f"{C9_URL}/api/chat",
            {"agent_id": "c9-jokes", "prompt": "Tell me a short joke", "chat_mode": "deep", "work_mode": "work"},
            timeout=TIMEOUT_AGENT,
        )
        # We assert HTTP 200 and a non-empty response — the joke backend must respond
        assert code == 200, f"POST /api/chat returned {code}"
        assert body.get("ok") is True, f"chat failed: {body.get('error')}"
        assert len((body.get("text") or "").strip()) > 5, "Empty joke response"
        print(f"\n  [c9 chat_mode=deep] {body.get('text','')[:80]}")


# ── standalone runner ─────────────────────────────────────────────────────────

def _standalone():
    print("=" * 70)
    print("C9 E2E Validation — All Agent Pipelines")
    print("=" * 70)

    # Infra checks
    print("\n[1/3] Infrastructure health checks")
    for name, url in [
        ("C1 /health", f"{C1_URL}/health"),
        ("C3 /health", f"{C3_URL}/health"),
        ("C9 /health (HTML ok)", f"{C9_URL}/health"),
        ("C3 /session-health", f"{C3_URL}/session-health"),
        ("C3 /status", f"{C3_URL}/status"),
    ]:
        try:
            code, body = _http_get(url, timeout=8)
            mark = "OK " if code == 200 else "ERR"
            display = str(body)[:80] if "_raw" not in body else f"HTML ({code})"
            print(f"  [{mark}] {name:30} HTTP {code} | {display}")
        except Exception as exc:
            print(f"  [ERR] {name:30} {exc}")

    # Sequential per-agent
    print(f"\n[2/3] Sequential per-agent C1 roundtrips (prompt={PROMPT!r})")
    seq_results = []
    for agent in AGENTS:
        r = _c1_chat(agent["id"])
        mark = "PASS" if r["ok"] else "FAIL"
        err = f" | {r['error'][:80]}" if not r["ok"] else ""
        print(f"  [{mark}] {agent['id']:20} {r['elapsed']:.2f}s{err}")
        if r["ok"]:
            print(f"         → {r['text'][:80]}")
        seq_results.append(r)

    # Parallel via C9
    print(f"\n[3/3] C9 /api/validate parallel run (prompt={PROMPT!r})")
    try:
        t0 = time.monotonic()
        code, body = _http_post(
            f"{C9_URL}/api/validate",
            {"prompt": PROMPT, "chat_mode": THINK_MODE, "work_mode": WORK_MODE},
            timeout=TIMEOUT_PARALLEL,
        )
        wall = time.monotonic() - t0
        passed = body.get("passed", 0)
        total = body.get("total", 0)
        mark = "PASS" if passed == total else "FAIL"
        print(f"  [{mark}] Run #{body.get('run_id')} | {passed}/{total} passed | {wall:.1f}s wall")
        for r in body.get("results", []):
            m = "PASS" if r["ok"] else "FAIL"
            err = f" | {r['error']}" if not r["ok"] else ""
            print(f"        [{m}] {r['agent_id']:20} {(r['elapsed_ms'] or 0)/1000:.2f}s{err}")
    except Exception as exc:
        print(f"  [ERR] {exc}")

    # Summary
    print("\n" + "=" * 70)
    seq_pass = sum(1 for r in seq_results if r["ok"])
    print(f"Sequential: {seq_pass}/{len(AGENTS)} passed")
    return 0 if seq_pass == len(AGENTS) else 1


if __name__ == "__main__":
    sys.exit(_standalone())
