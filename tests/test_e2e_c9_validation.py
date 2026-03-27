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
CHAT_MODE = "work"

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


def _c1_chat(agent_id: str, prompt: str = PROMPT, mode: str = CHAT_MODE) -> dict:
    payload = {
        "model": "copilot",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    req = urllib.request.Request(
        f"{C1_URL}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Agent-ID": agent_id,
            "X-Chat-Mode": mode,
            "User-Agent": "c9-e2e-test/1.0",
        },
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


# ── C9 /api/validate parallel test ───────────────────────────────────────────

class TestC9ParallelValidation:
    """Use C9's /api/validate endpoint — the same run as the UI 'Run All Parallel' button."""

    def test_all_agents_parallel(self):
        t0 = time.monotonic()
        code, body = _http_post(
            f"{C9_URL}/api/validate",
            {"prompt": PROMPT, "chat_mode": CHAT_MODE},
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
            {"prompt": PROMPT, "chat_mode": CHAT_MODE},
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
