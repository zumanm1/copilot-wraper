"""
Unit tests for c9_jokes/app.py — no live containers required.

Strategy:
- FastAPI TestClient for C9's app
- httpx calls to C1/C3 are intercepted by monkeypatching _get_http() to return
  a mock AsyncClient that returns canned JSON responses.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os
import sqlite3

# ── Make c9_jokes importable ─────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "c9_jokes"))


# Override the autouse conftest fixture (imports agent_manager, irrelevant here)
@pytest.fixture(autouse=True)
def _reset_agent_registry_between_tests():
    yield


# ── Shared fake C1 response ───────────────────────────────────────────────────

def _make_c1_ok(text: str = "Why don't scientists trust atoms? Because they make up everything.") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "copilot",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 15, "total_tokens": 20},
    }


def _make_mock_http(response_json: dict, status: int = 200):
    """Return a mock httpx.AsyncClient that intercepts POST /v1/chat/completions."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json = MagicMock(return_value=response_json)
    mock_resp.text = json.dumps(response_json)

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.get  = AsyncMock(return_value=mock_resp)
    mock_client.is_closed = False
    return mock_client


def _json_response(payload: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/json"}
    resp.json = MagicMock(return_value=payload)
    resp.text = json.dumps(payload)
    return resp


class _FakeStreamResponse:
    def __init__(self, *, status_code: int = 200, lines: list[str] | None = None, body: str = ""):
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._body.encode("utf-8")


def _make_c1_sse_lines(tokens: list[str]) -> list[str]:
    chat_id = "chatcmpl-stream"
    created = 1700000000
    base = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": "copilot"}
    lines = [
        "data: " + json.dumps({
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        })
    ]
    for token in tokens:
        lines.append(
            "data: " + json.dumps({
                **base,
                "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
            })
        )
    lines.append(
        "data: " + json.dumps({
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
    )
    lines.append("data: [DONE]")
    return lines


def _parse_c9_sse(raw_sse: str) -> list[dict]:
    events: list[dict] = []
    for line in raw_sse.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):].strip()
        if not payload or payload == "[DONE]":
            continue
        events.append(json.loads(payload))
    return events


# ── TestClient fixture ────────────────────────────────────────────────────────

@pytest.fixture
def c9_app(tmp_path):
    """C9 FastAPI app with mocked C1 HTTP client and temp SQLite DB."""
    db_path = str(tmp_path / "test_c9.db")
    with patch.dict(os.environ, {
        "C1_URL": "http://fake-c1:8000",
        "DATABASE_PATH": db_path,
    }):
        import importlib
        import c9_jokes.app as c9_mod
        importlib.reload(c9_mod)          # fresh module with patched env
        from fastapi.testclient import TestClient

        mock_http = _make_mock_http(_make_c1_ok())
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            with patch.object(c9_mod, "_http", mock_http):
                yield TestClient(c9_mod.app, raise_server_exceptions=False)


# ── Page route tests ──────────────────────────────────────────────────────────

class TestC9PageRoutes:
    def test_chat_page_returns_200(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200

    def test_chat_page_has_thinking_dropdown(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        html = r.text
        assert "thinking-pill" in html
        assert 'data-mode="auto"' in html
        assert 'data-mode="quick"' in html
        assert 'data-mode="deep"' in html
        assert "thinkingMode" in html

    def test_chat_page_has_work_web_toggle(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        html = r.text
        assert "work-web-toggle" in html
        assert 'data-mode="work"' in html
        assert 'data-mode="web"' in html
        assert "workMode" in html

    def test_chat_page_has_file_upload(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        html = r.text
        assert "attach-plus" in html
        assert "file-input" in html
        assert "Upload files" in html
        assert "/api/upload" in html

    def test_pairs_page_returns_200(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        for agent_id in ("c2-aider", "c5-claude-code", "c6-kilocode", "c8-hermes", "c9-jokes"):
            assert agent_id in r.text

    def test_pairs_page_has_thinking_dropdown(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        html = r.text
        assert "thinking-pill" in html
        assert 'data-mode="auto"' in html
        assert 'data-mode="quick"' in html
        assert 'data-mode="deep"' in html
        assert "pairsThinkingMode" in html

    def test_pairs_page_has_work_web_toggle(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        html = r.text
        assert "mode-work" in html
        assert "mode-web" in html
        assert "activeWorkMode" in html

    def test_pairs_page_has_file_upload(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        html = r.text
        assert "attach-plus" in html
        assert "file-input" in html
        assert "Upload files" in html
        assert "/api/upload" in html

    def test_api_reference_page_returns_200(self, c9_app):
        r = c9_app.get("/api")
        assert r.status_code == 200

    def test_logs_page_returns_200(self, c9_app):
        r = c9_app.get("/logs")
        assert r.status_code == 200

    def test_health_page_returns_200(self, c9_app):
        r = c9_app.get("/health")
        assert r.status_code == 200

    def test_c3_auth_page_returns_200(self, c9_app):
        r = c9_app.get("/c3-auth")
        assert r.status_code == 200
        assert "C3 Tab 1 Auth Progress" in r.text
        assert "/api/c3-auth-progress" in r.text
        assert "Run Tab 1 Validation" in r.text
        assert "25-Step Checklist" in r.text
        assert "Pool Monitor" in r.text
        assert "Min / Avg / Max" not in r.text  # rendered dynamically in JS

    def test_tasked_page_returns_200(self, c9_app):
        r = c9_app.get("/tasked")
        assert r.status_code == 200
        assert "Tasked Orchestrator" in r.text
        assert "/api/tasks" in r.text
        assert "/api/task-runs" in r.text
        assert "/api/task-templates" in r.text
        assert "Load 4 DB examples" in r.text
        assert "Select editable template" in r.text
        assert "Load selected template" in r.text
        assert "Save current as template" in r.text
        assert "Clone selected template" in r.text
        assert "Archive selected template" in r.text
        assert "Start Task" in r.text
        assert "Repeat" in r.text
        assert "Clone" in r.text
        assert "Pause" in r.text
        assert "Resume" in r.text
        assert "Restart" in r.text
        assert "Archive" in r.text
        assert "Workflow steps" in r.text
        assert "Traceability" in r.text
        assert "Live / continuous monitor" in r.text

    def test_tasked_page_supports_task_id_query_param(self, c9_app):
        r = c9_app.get("/tasked?task_id=task_123")
        assert r.status_code == 200
        assert "task_id" in r.text

    def test_tasked_page_supports_sandbox_builder(self, c9_app):
        r = c9_app.get("/tasked")
        assert r.status_code == 200
        assert "Sandbox (C12b)" in r.text
        assert "Validation command" in r.text
        assert "Test command" in r.text
        assert "Enable AIO sandbox assist" in r.text
        assert "Sandbox Runtime" in r.text
        assert "C12b Lean Sandbox" in r.text
        assert "C12 AIO Sandbox" not in r.text

    def test_task_legacy_redirects_to_tasked(self, c9_app):
        r = c9_app.get("/task", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "/tasked"

    def test_alerts_page_returns_200(self, c9_app):
        r = c9_app.get("/alerts")
        assert r.status_code == 200
        assert "Alerts" in r.text
        assert "/api/alerts" in r.text
        assert "Open Tasked" in r.text
        assert "Open Pipeline" in r.text
        assert "Open Completed" in r.text
        assert "Tasked alerts auto-refresh every 15 seconds" in r.text
        assert "Snooze 30m" in r.text

    def test_task_completed_page_returns_200(self, c9_app):
        r = c9_app.get("/task-completed")
        assert r.status_code == 200
        assert "TaskCompleted" in r.text
        assert "/api/task-completed" in r.text
        assert "Redo" in r.text
        assert "Clone Task" in r.text

    def test_piplinetask_page_returns_200(self, c9_app):
        r = c9_app.get("/piplinetask")
        assert r.status_code == 200
        assert "piplinetask" in r.text
        assert "/api/task-pipelines" in r.text
        assert "Open Tasked" in r.text
        assert "Open Alerts" in r.text
        assert "TaskCompleted" in r.text
        assert "Active runs refresh every 5 seconds" in r.text

    def test_agent_page_supports_tasked_launch_context(self, c9_app):
        r = c9_app.get("/agent?task=Build+app&task_id=task_123&task_run_id=trun_456&source=tasked")
        assert r.status_code == 200
        assert "⚡ Agent Workspace" in r.text
        assert "/api/agent/run" in r.text
        assert "Tasked launch context" in r.text
        assert "task_123" in r.text
        assert "trun_456" in r.text

    def test_multi_agento_page_supports_tasked_launch_context(self, c9_app):
        r = c9_app.get("/multi-Agento?task=Ship+feature&task_id=task_abc&task_run_id=trun_def&source=tasked")
        assert r.status_code == 200
        assert "Multi-Agento" in r.text
        assert "/api/ma/run" in r.text
        assert "Tasked launch context" in r.text
        assert "task_abc" in r.text
        assert "trun_def" in r.text

    def test_multi_agento_page_does_not_render_duplicate_shell_panels(self, c9_app):
        r = c9_app.get("/multi-Agento")
        assert r.status_code == 200
        assert r.text.count('id="rpanel-shell"') == 1
        assert r.text.count('id="mao-shell-out"') == 1

    def test_pages_include_runtime_status_badge_polling(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        assert "runtime-badge" in r.text
        assert "/api/runtime-status" in r.text


# ── /api/chat tests ───────────────────────────────────────────────────────────

class TestC9ApiChat:
    def _post_chat(self, c9_app, payload: dict):
        return c9_app.post("/api/chat", json=payload)

    def test_basic_joke_returns_ok(self, c9_app):
        r = self._post_chat(c9_app, {"agent_id": "c9-jokes", "prompt": "Tell me a joke"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "everything" in body["text"]  # canned response contains "make up everything"

    def test_empty_prompt_returns_400(self, c9_app):
        r = self._post_chat(c9_app, {"agent_id": "c9-jokes", "prompt": ""})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_missing_prompt_returns_400(self, c9_app):
        r = self._post_chat(c9_app, {"agent_id": "c9-jokes"})
        assert r.status_code == 400

    @pytest.mark.parametrize("think", ["auto", "quick", "deep"])
    def test_thinking_mode_accepted(self, c9_app, think):
        """chat_mode (thinking) must be forwarded without error for all three modes."""
        r = self._post_chat(c9_app, {
            "agent_id": "c9-jokes",
            "prompt": "Tell me a short joke",
            "chat_mode": think,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    @pytest.mark.parametrize("work", ["work", "web"])
    def test_work_web_mode_accepted(self, c9_app, work):
        """work_mode (Work/Web) must be accepted without error."""
        r = self._post_chat(c9_app, {
            "agent_id": "c9-jokes",
            "prompt": "Tell me a joke",
            "work_mode": work,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_x_chat_mode_header_sent_to_c1(self, c9_app):
        """Verify X-Chat-Mode: deep is forwarded to C1."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Joke",
                "chat_mode": "deep",
            })
        assert r.status_code == 200
        assert captured_headers.get("X-Chat-Mode") == "deep"

    def test_x_work_mode_header_sent_to_c1(self, c9_app):
        """Verify X-Work-Mode: web is forwarded to C1."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Joke",
                "work_mode": "web",
            })
        assert r.status_code == 200
        assert captured_headers.get("X-Work-Mode") == "web"

    def test_both_headers_sent_together(self, c9_app):
        """X-Chat-Mode and X-Work-Mode can both be sent in the same request."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Joke",
                "chat_mode": "quick",
                "work_mode": "work",
            })
        assert r.status_code == 200
        assert captured_headers.get("X-Chat-Mode") == "quick"
        assert captured_headers.get("X-Work-Mode") == "work"

    def test_c9_jokes_agent_uses_correct_agent_id(self, c9_app):
        """Default agent_id fallback is 'c9-jokes'."""
        import c9_jokes.app as c9_mod
        captured_headers = {}

        async def capture_post(url, *, headers=None, json=None, timeout=None, **kw):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            # Don't pass agent_id — should default to c9-jokes
            r = c9_app.post("/api/chat", json={"prompt": "Joke"})
        assert r.status_code == 200
        assert captured_headers.get("X-Agent-ID") == "c9-jokes"

    def test_streaming_chat_returns_sse_and_persists_session(self, c9_app):
        import c9_jokes.app as c9_mod

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.stream = MagicMock(return_value=_FakeStreamResponse(
            lines=_make_c1_sse_lines(["Why ", "streaming ", "works."])
        ))

        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Tell me a joke",
                "stream": True,
            })

        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        events = _parse_c9_sse(r.text)
        assert [ev["type"] for ev in events[:-1]] == ["token", "token", "token"]
        done = events[-1]
        assert done["type"] == "done"
        assert done["text"] == "Why streaming works."
        assert done["session_id"].startswith("cs_")
        assert done["token_estimate"] > 0

        r_sess = c9_app.get(f"/api/chat/session/{done['session_id']}")
        assert r_sess.status_code == 200
        msgs = r_sess.json()["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Tell me a joke"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Why streaming works."

    def test_streaming_chat_forwards_messages_and_attachments(self, c9_app):
        import c9_jokes.app as c9_mod

        captured = {}

        def capture_stream(method, url, *, headers=None, json=None, timeout=None, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["json"] = json or {}
            captured["timeout"] = timeout
            return _FakeStreamResponse(lines=_make_c1_sse_lines(["Attached reply"]))

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.stream = capture_stream
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Summarise the attachment",
                "chat_mode": "deep",
                "work_mode": "web",
                "stream": True,
                "messages": [{"role": "user", "content": "Summarise the attachment"}],
                "attachments": [{"file_id": "fid_xyz", "filename": "doc.txt"}],
            })

        assert r.status_code == 200
        assert captured["method"] == "POST"
        assert captured["headers"]["X-Agent-ID"] == "c9-jokes"
        assert captured["headers"]["X-Chat-Mode"] == "deep"
        assert captured["headers"]["X-Work-Mode"] == "web"
        assert captured["json"]["stream"] is True
        content = captured["json"]["messages"][0]["content"]
        assert isinstance(content, list)
        types = [part["type"] for part in content]
        assert "text" in types
        assert "file_ref" in types
        file_ref = next(part for part in content if part["type"] == "file_ref")
        assert file_ref["file_id"] == "fid_xyz"
        assert file_ref["filename"] == "doc.txt"

    def test_streaming_chat_emits_error_without_persisting_messages(self, c9_app):
        import c9_jokes.app as c9_mod

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.stream = MagicMock(return_value=_FakeStreamResponse(
            status_code=503,
            body=json.dumps({"detail": "Upstream timeout from Copilot"}),
        ))

        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = self._post_chat(c9_app, {
                "agent_id": "c9-jokes",
                "prompt": "Will fail",
                "stream": True,
            })

        assert r.status_code == 200
        events = _parse_c9_sse(r.text)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "Upstream timeout" in events[0]["message"]

        r_sessions = c9_app.get("/api/chat/sessions?limit=10")
        assert r_sessions.status_code == 200
        assert r_sessions.json() == []

        r_logs = c9_app.get("/api/logs")
        assert r_logs.status_code == 200
        latest = r_logs.json()["rows"][0]
        assert latest["source"] == "chat-stream"
        assert "Upstream timeout" in (latest["response_excerpt"] or "")


class TestC9Tasks:
    def test_task_db_tables_exist(self, c9_app):
        import c9_jokes.app as c9_mod

        c9_mod._ensure_db()
        with sqlite3.connect(c9_mod.DEFAULT_DB) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('task_definitions','task_runs','task_events','task_alerts','task_templates','task_run_claims')"
            ).fetchall()

        assert {row[0] for row in rows} == {
            "task_definitions", "task_runs", "task_events", "task_alerts", "task_templates", "task_run_claims"
        }

    def test_api_tasks_lists_templates(self, c9_app):
        r = c9_app.get("/api/tasks")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["tasks"] == []
        template_keys = {tpl["key"] for tpl in body["templates"]}
        assert "weather-dublin" in template_keys
        assert "outlook-sharepoint-linked" in template_keys

    def test_task_template_create_clone_and_archive(self, c9_app):
        r_create = c9_app.post("/api/task-templates", json={
            "name": "User Workflow Template",
            "mode": "chat",
            "schedule_kind": "recurring",
            "interval_minutes": 15,
            "tabs_required": 2,
            "planner_prompt": "Plan the workflow",
            "executor_prompt": "Execute the workflow",
            "context_handoff": "Copy findings into tab 2",
            "trigger_mode": "contains",
            "trigger_text": "workflow ready",
            "notes": "User-created template",
            "source": "user",
        })
        assert r_create.status_code == 200
        created = r_create.json()["template"]
        assert created["source"] == "user"
        assert created["active"] is True

        r_clone = c9_app.post(f"/api/task-templates/{created['key']}/clone")
        assert r_clone.status_code == 200
        cloned = r_clone.json()["template"]
        assert cloned["key"] != created["key"]
        assert cloned["name"].endswith("(Clone)")

        r_archive = c9_app.post(f"/api/task-templates/{created['key']}/archive")
        assert r_archive.status_code == 200
        assert r_archive.json()["template"]["active"] is False

        r_list = c9_app.get("/api/task-templates?include_archived=true")
        assert r_list.status_code == 200
        templates = {item["key"]: item for item in r_list.json()["templates"]}
        assert created["key"] in templates
        assert templates[created["key"]]["active"] is False
        assert cloned["key"] in templates

    def test_manual_chat_task_run_creates_alert(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Dublin Weather",
            "mode": "chat",
            "schedule_kind": "manual",
            "tabs_required": 2,
            "template_key": "weather-dublin",
            "trigger_mode": "contains",
            "trigger_text": "dublin weather message",
            "executor_prompt": "Check Dublin weather and alert when above 10C",
        })
        assert r_save.status_code == 200
        task = r_save.json()["task"]

        fake_chat = AsyncMock(return_value={
            "ok": True,
            "text": "Dublin weather message: 12C and above threshold.",
        })
        with patch.object(c9_mod, "_chat_one", fake_chat):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        run_body = r_run.json()
        assert run_body["ok"] is True
        assert run_body["status"] == "completed"
        assert run_body["alert_id"] is not None

        r_alerts = c9_app.get("/api/alerts")
        assert r_alerts.status_code == 200
        alerts = r_alerts.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["task_name"] == "Dublin Weather"
        assert alerts[0]["task_mode"] == "chat"
        assert alerts[0]["template_key"] == "weather-dublin"
        assert alerts[0]["schedule_kind"] == "manual"
        assert alerts[0]["interval_minutes"] == 0
        assert alerts[0]["tabs_required"] == 2
        assert alerts[0]["active"] is True
        assert alerts[0]["task_url"] == f"/tasked?task_id={task['id']}"
        assert alerts[0]["pipeline_url"] == f"/piplinetask?task_id={task['id']}"
        assert "Dublin weather message" in alerts[0]["summary"]

        r_runs = c9_app.get("/api/task-runs")
        assert r_runs.status_code == 200
        assert r_runs.json()["runs"][0]["status"] == "completed"

    def test_task_pipeline_api_returns_task_run_alert_timeline(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Pipeline Weather",
            "mode": "chat",
            "schedule_kind": "manual",
            "tabs_required": 2,
            "template_key": "weather-dublin",
            "trigger_mode": "contains",
            "trigger_text": "dublin weather message",
            "executor_prompt": "Check Dublin weather and alert when above 10C",
        })
        assert r_save.status_code == 200
        task = r_save.json()["task"]

        fake_chat = AsyncMock(return_value={
            "ok": True,
            "text": "Dublin weather message: 14C and above threshold.",
        })
        with patch.object(c9_mod, "_chat_one", fake_chat):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        run_body = r_run.json()
        assert run_body["ok"] is True
        assert run_body["alert_id"] is not None

        r_ack = c9_app.post(f"/api/alerts/{run_body['alert_id']}/ack")
        assert r_ack.status_code == 200

        r_pipeline = c9_app.get(f"/api/task-pipelines?task_id={task['id']}")
        assert r_pipeline.status_code == 200
        body = r_pipeline.json()
        assert body["ok"] is True
        assert len(body["pipelines"]) == 1

        pipeline = body["pipelines"][0]
        assert pipeline["task"]["id"] == task["id"]
        assert pipeline["task"]["task_url"] == f"/tasked?task_id={task['id']}"
        assert pipeline["task"]["alerts_url"] == "/alerts"
        assert pipeline["summary"]["runs_total"] == 1
        assert pipeline["summary"]["alerts_total"] == 1
        assert pipeline["summary"]["open_alerts"] == 0

        kinds = [event["kind"] for event in pipeline["events"]]
        assert "task-created" in kinds
        assert "run-started" in kinds
        assert "run-finished" in kinds
        assert "alert-created" in kinds
        assert "alert-acknowledged" in kinds

    def test_seed_examples_populates_tasked_pipeline_and_alerts(self, c9_app):
        r_seed = c9_app.post("/api/tasks/seed-examples")
        assert r_seed.status_code == 200
        seed_body = r_seed.json()
        assert seed_body["ok"] is True
        assert seed_body["seeded_count"] == 4

        r_tasks = c9_app.get("/api/tasks")
        assert r_tasks.status_code == 200
        task_ids = {task["id"] for task in r_tasks.json()["tasks"]}
        assert {
            "task_example_jhb_nvidia",
            "task_example_gmail_sender",
            "task_example_sharepoint_file",
            "task_example_outlook_sharepoint",
        }.issubset(task_ids)

        r_alerts = c9_app.get("/api/alerts")
        assert r_alerts.status_code == 200
        alerts = r_alerts.json()["alerts"]
        alert_task_ids = {alert["task_id"] for alert in alerts}
        assert {
            "task_example_jhb_nvidia",
            "task_example_gmail_sender",
            "task_example_sharepoint_file",
            "task_example_outlook_sharepoint",
        }.issubset(alert_task_ids)

        r_pipelines = c9_app.get("/api/task-pipelines")
        assert r_pipelines.status_code == 200
        pipelines = {item["task"]["id"]: item for item in r_pipelines.json()["pipelines"]}
        assert "task_example_jhb_nvidia" in pipelines
        assert "task_example_gmail_sender" in pipelines
        assert "task_example_outlook_sharepoint" in pipelines
        example_pipeline = pipelines["task_example_outlook_sharepoint"]
        kinds = [event["kind"] for event in example_pipeline["events"]]
        assert "task-created" in kinds
        assert "run-started" in kinds
        assert "run-finished" in kinds
        assert "alert-created" in kinds
        assert "alert-acknowledged" in kinds

    def test_non_chat_task_returns_launch_url(self, c9_app):
        r_save = c9_app.post("/api/tasks", json={
            "name": "Build Calculator",
            "mode": "agent",
            "schedule_kind": "manual",
            "executor_prompt": "Create a calculator app",
        })
        assert r_save.status_code == 200
        task = r_save.json()["task"]

        r_run = c9_app.post(f"/api/tasks/{task['id']}/run")
        assert r_run.status_code == 200
        body = r_run.json()
        assert body["ok"] is True
        assert body["status"] == "launch-pending"
        assert body["background_supported"] is False
        assert body["launch_url"].startswith("/agent?task=")
        assert "task_id=" in body["launch_url"]
        assert "task_run_id=" in body["launch_url"]

    def test_task_supports_continuous_schedule(self, c9_app):
        r = c9_app.post("/api/tasks", json={
            "name": "Live Weather Monitor",
            "mode": "chat",
            "schedule_kind": "continuous",
            "interval_minutes": 10,
            "executor_prompt": "Monitor weather continuously",
        })
        assert r.status_code == 200
        task = r.json()["task"]
        assert task["schedule_kind"] == "continuous"
        assert task["schedule_label"].startswith("Live / every 10m")
        assert task["lifecycle_state"] == "live"

    def test_task_clone_pause_resume_and_traceability(self, c9_app):
        r_save = c9_app.post("/api/tasks", json={
            "name": "Traceable Task",
            "mode": "chat",
            "schedule_kind": "recurring",
            "interval_minutes": 10,
            "executor_prompt": "Reply with READY only.",
        })
        task = r_save.json()["task"]

        r_pause = c9_app.post(f"/api/tasks/{task['id']}/pause")
        assert r_pause.status_code == 200
        paused = r_pause.json()["task"]
        assert paused["active"] is False
        assert paused["lifecycle_state"] == "paused"

        r_resume = c9_app.post(f"/api/tasks/{task['id']}/resume")
        assert r_resume.status_code == 200
        resumed = r_resume.json()["task"]
        assert resumed["active"] is True
        assert resumed["lifecycle_state"] == "scheduled"
        assert resumed["next_run_at"]
        assert resumed["trace"]["trace_id"] == task["id"]

        r_clone = c9_app.post(f"/api/tasks/{task['id']}/clone")
        assert r_clone.status_code == 200
        cloned = r_clone.json()["task"]
        assert cloned["id"] != task["id"]
        assert cloned["name"].endswith("(Clone)")
        assert cloned["trace"]["trace_id"] == cloned["id"]

        r_pipeline = c9_app.get(f"/api/task-pipelines?task_id={task['id']}")
        assert r_pipeline.status_code == 200
        kinds = [event["kind"] for event in r_pipeline.json()["pipelines"][0]["events"]]
        assert "task-paused" in kinds
        assert "task-resumed" in kinds

    def test_task_start_repeat_restart_actions_create_runs(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Lifecycle Task",
            "mode": "chat",
            "schedule_kind": "manual",
            "trigger_mode": "always",
            "executor_prompt": "Reply with READY only.",
        })
        task_id = r_save.json()["task"]["id"]

        fake_chat = AsyncMock(return_value={"ok": True, "text": "READY"})
        with patch.object(c9_mod, "_chat_one", fake_chat):
            r_start = c9_app.post(f"/api/tasks/{task_id}/start")
            r_repeat = c9_app.post(f"/api/tasks/{task_id}/repeat")
            r_restart = c9_app.post(f"/api/tasks/{task_id}/restart")

        assert r_start.status_code == 200
        assert r_repeat.status_code == 200
        assert r_restart.status_code == 200
        assert r_start.json()["status"] == "completed"
        assert r_repeat.json()["status"] == "completed"
        assert r_restart.json()["status"] == "completed"

        r_runs = c9_app.get(f"/api/task-runs?task_id={task_id}")
        runs = r_runs.json()["runs"]
        assert len(runs) == 3
        assert all(run["duration_label"] != "—" for run in runs)

    def test_manual_sandbox_task_run_executes_validate_and_test(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Sandbox Smoke",
            "mode": "sandbox",
            "schedule_kind": "manual",
            "executor_target": "c12b",
            "workspace_dir": "/workspace",
            "trigger_mode": "always",
            "executor_prompt": "python3 build.py",
            "validation_command": "python3 -m py_compile build.py",
            "test_command": "python3 -m pytest -q",
        })
        assert r_save.status_code == 200
        task = r_save.json()["task"]
        assert task["executor_target"] == "c12b"
        assert task["background_supported"] is True

        async def fake_c12b_exec(command, timeout=30, cwd=".", session_id=""):
            if command == "python3 build.py":
                return {"stdout": "build ok", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": "sess_c12b"}
            if command == "python3 -m py_compile build.py":
                return {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": session_id or "sess_c12b"}
            if command == "python3 -m pytest -q":
                return {"stdout": "1 passed", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": session_id or "sess_c12b"}
            raise AssertionError(f"unexpected command {command}")

        with patch.object(c9_mod, "_c12b_exec", AsyncMock(side_effect=fake_c12b_exec)):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        body = r_run.json()
        assert body["ok"] is True
        assert body["status"] == "completed"
        assert body["alert_id"] is not None
        assert "Sandbox target: C12b Lean Sandbox" in body["text"]

        r_runs = c9_app.get(f"/api/task-runs?task_id={task['id']}")
        runs = r_runs.json()["runs"]
        assert runs[0]["executor_target"] == "c12b"
        assert runs[0]["sandbox_session_id"] == "sess_c12b"
        assert runs[0]["validation_status"] == "completed"
        assert runs[0]["test_status"] == "completed"

        r_alerts = c9_app.get("/api/alerts")
        alerts = r_alerts.json()["alerts"]
        assert alerts[0]["task_id"] == task["id"]
        assert alerts[0]["executor_target"] == "c12b"
        assert alerts[0]["workspace_dir"] == "/workspace"

        r_pipeline = c9_app.get(f"/api/task-pipelines?task_id={task['id']}")
        pipeline = r_pipeline.json()["pipelines"][0]
        event_kinds = [event["kind"] for event in pipeline["events"]]
        assert "sandbox-exec" in event_kinds
        assert "sandbox-validate" in event_kinds
        assert "sandbox-test" in event_kinds

    def test_chat_task_can_use_aio_sandbox_assist(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Chat With Sandbox Assist",
            "mode": "chat",
            "schedule_kind": "manual",
            "trigger_mode": "always",
            "executor_prompt": "Summarize the prepared data.",
            "sandbox_assist": True,
            "sandbox_assist_target": "c12b",
            "sandbox_assist_workspace_dir": "/workspace",
            "sandbox_assist_command": "python3 prepare.py",
            "sandbox_assist_validation_command": "python3 -m py_compile prepare.py",
            "sandbox_assist_test_command": "python3 -m pytest -q",
        })
        assert r_save.status_code == 200
        task = r_save.json()["task"]
        assert task["sandbox_assist"] is True
        assert task["sandbox_assist_target"] == "c12b"

        async def fake_c12b_exec(command, timeout=30, cwd=".", session_id=""):
            if command == "python3 prepare.py":
                return {"stdout": "prepared", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": "assist_123"}
            if command == "python3 -m py_compile prepare.py":
                return {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": session_id or "assist_123"}
            if command == "python3 -m pytest -q":
                return {"stdout": "2 passed", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": session_id or "assist_123"}
            raise AssertionError(f"unexpected command {command}")

        with patch.object(c9_mod, "_c12b_exec", AsyncMock(side_effect=fake_c12b_exec)):
            with patch.object(c9_mod, "_chat_one", AsyncMock(return_value={"ok": True, "text": "Prepared summary complete."})):
                r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        body = r_run.json()
        assert body["ok"] is True
        assert body["status"] == "completed"
        assert body["alert_id"] is not None

        r_runs = c9_app.get(f"/api/task-runs?task_id={task['id']}")
        runs = r_runs.json()["runs"]
        assert runs[0]["executor_target"] == "c12b"
        assert runs[0]["sandbox_session_id"] == "assist_123"
        assert runs[0]["validation_status"] == "completed"
        assert runs[0]["test_status"] == "completed"

        r_alerts = c9_app.get("/api/alerts")
        alerts = r_alerts.json()["alerts"]
        assert alerts[0]["task_id"] == task["id"]
        assert alerts[0]["sandbox_assist"] is True
        assert alerts[0]["sandbox_assist_target"] == "c12b"

        r_pipeline = c9_app.get(f"/api/task-pipelines?task_id={task['id']}")
        pipeline = r_pipeline.json()["pipelines"][0]
        event_kinds = [event["kind"] for event in pipeline["events"]]
        assert "sandbox-assist-exec" in event_kinds
        assert "sandbox-assist-validate" in event_kinds
        assert "sandbox-assist-test" in event_kinds
        assert "task-run-finished" in event_kinds

    def test_launch_task_can_use_aio_sandbox_assist_before_launch(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Agent With Sandbox Assist",
            "mode": "agent",
            "schedule_kind": "manual",
            "executor_prompt": "Implement the feature.",
            "sandbox_assist": True,
            "sandbox_assist_target": "c12b",
            "sandbox_assist_workspace_dir": "/workspace",
            "sandbox_assist_command": "python3 bootstrap.py",
            "sandbox_assist_validation_command": "python3 -m py_compile bootstrap.py",
        })
        assert r_save.status_code == 200
        task = r_save.json()["task"]

        async def fake_c12b_exec(command, timeout=30, cwd=".", session_id=""):
            if command == "python3 bootstrap.py":
                return {"stdout": "bootstrap ok", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": "assist_launch"}
            if command == "python3 -m py_compile bootstrap.py":
                return {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False, "session_id": session_id or "assist_launch"}
            raise AssertionError(f"unexpected command {command}")

        with patch.object(c9_mod, "_c12b_exec", AsyncMock(side_effect=fake_c12b_exec)):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        body = r_run.json()
        assert body["ok"] is True
        assert body["status"] == "launch-pending"
        assert body["launch_url"].startswith("/agent?task=")
        assert "assist" in body["text"].lower()

        r_runs = c9_app.get(f"/api/task-runs?task_id={task['id']}")
        runs = r_runs.json()["runs"]
        assert runs[0]["executor_target"] == "c12b"
        assert runs[0]["sandbox_session_id"] == "assist_launch"
        assert runs[0]["validation_status"] == "completed"

        r_pipeline = c9_app.get(f"/api/task-pipelines?task_id={task['id']}")
        pipeline = r_pipeline.json()["pipelines"][0]
        event_kinds = [event["kind"] for event in pipeline["events"]]
        assert "sandbox-assist-exec" in event_kinds
        assert "sandbox-assist-validate" in event_kinds

    def test_alert_acknowledge_updates_status(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Email Alert",
            "mode": "chat",
            "schedule_kind": "manual",
            "trigger_mode": "always",
            "executor_prompt": "Check for a critical email",
        })
        task_id = r_save.json()["task"]["id"]
        with patch.object(c9_mod, "_chat_one", AsyncMock(return_value={"ok": True, "text": "Critical email found."})):
            r_run = c9_app.post(f"/api/tasks/{task_id}/run")
        alert_id = r_run.json()["alert_id"]
        assert alert_id is not None

        r_ack = c9_app.post(f"/api/alerts/{alert_id}/ack")
        assert r_ack.status_code == 200
        assert r_ack.json()["alert"]["status"] == "acknowledged"

        r_alerts = c9_app.get("/api/alerts")
        alerts = r_alerts.json()["alerts"]
        assert alerts[0]["status"] == "acknowledged"

    def test_alert_status_resolve_snooze_and_reopen(self, c9_app):
        import c9_jokes.app as c9_mod

        r_save = c9_app.post("/api/tasks", json={
            "name": "Escalation Alert",
            "mode": "chat",
            "schedule_kind": "manual",
            "trigger_mode": "always",
            "executor_prompt": "Raise an escalation alert",
        })
        task_id = r_save.json()["task"]["id"]
        with patch.object(c9_mod, "_chat_one", AsyncMock(return_value={"ok": True, "text": "Escalation found."})):
            r_run = c9_app.post(f"/api/tasks/{task_id}/run")
        alert_id = r_run.json()["alert_id"]
        assert alert_id is not None

        r_snooze = c9_app.post(f"/api/alerts/{alert_id}/status", json={"status": "snoozed", "snooze_minutes": 30})
        assert r_snooze.status_code == 200
        assert r_snooze.json()["alert"]["status"] == "snoozed"
        assert r_snooze.json()["alert"]["snoozed_until"]

        r_resolve = c9_app.post(f"/api/alerts/{alert_id}/status", json={"status": "resolved"})
        assert r_resolve.status_code == 200
        assert r_resolve.json()["alert"]["status"] == "resolved"
        assert r_resolve.json()["alert"]["resolved_at"]

        r_reopen = c9_app.post(f"/api/alerts/{alert_id}/status", json={"status": "open"})
        assert r_reopen.status_code == 200
        assert r_reopen.json()["alert"]["status"] == "open"

        r_missing = c9_app.post("/api/alerts/999999/ack")
        assert r_missing.status_code == 404


# ── /api/validate tests ───────────────────────────────────────────────────────

class TestC9ApiValidate:
    def test_validate_all_agents_ok(self, c9_app):
        r = c9_app.post("/api/validate", json={"prompt": "Tell me a joke"})
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] == body["total"]
        assert body["failed"] == 0
        assert len(body["results"]) > 0

    def test_validate_with_thinking_mode(self, c9_app):
        r = c9_app.post("/api/validate", json={
            "prompt": "Tell me a joke",
            "chat_mode": "deep",
            "work_mode": "work",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] == body["total"]

    def test_validate_with_web_mode(self, c9_app):
        r = c9_app.post("/api/validate", json={
            "prompt": "Tell me a joke",
            "work_mode": "web",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] == body["total"]

    def test_validate_no_matching_agents_returns_400(self, c9_app):
        r = c9_app.post("/api/validate", json={
            "prompt": "Joke",
            "agent_ids": ["nonexistent-agent"],
        })
        assert r.status_code == 400

    def test_validate_mode_parallel(self, c9_app):
        r = c9_app.post("/api/validate", json={"prompt": "Joke", "parallel": True})
        assert r.json()["mode"] == "parallel"

    def test_validate_mode_sequential(self, c9_app):
        r = c9_app.post("/api/validate", json={"prompt": "Joke", "parallel": False})
        assert r.json()["mode"] == "sequential"

    def test_validate_calls_appear_in_logs(self, c9_app):
        """Validation runs must be visible in /logs (source='validate')."""
        # Run a single-agent validate
        c9_app.post("/api/validate", json={
            "prompt": "Validate log test",
            "agent_ids": ["c9-jokes"],
        })
        r = c9_app.get("/logs")
        assert r.status_code == 200
        assert "validate" in r.text  # source badge visible

    def test_chat_logs_include_elapsed_ms(self, c9_app):
        """chat_logs must store elapsed_ms so /logs can display response time."""
        c9_app.post("/api/chat", json={"agent_id": "c9-jokes", "prompt": "Timing test"})
        r = c9_app.get("/api/logs")
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert len(rows) > 0
        # elapsed_ms should be present (may be 0 in test but not absent)
        assert "elapsed_ms" in rows[0]

    def test_failed_chat_logs_error_text(self, c9_app):
        """When C1 returns an error, response_excerpt must contain the error, not be blank."""
        import c9_jokes.app as c9_mod
        error_resp = {"detail": "Upstream timeout from Copilot"}
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.json = MagicMock(return_value=error_resp)
        mock_resp.text = json.dumps(error_resp)
        mock_http = _make_mock_http(error_resp, status=503)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            c9_app.post("/api/chat", json={"agent_id": "c9-jokes", "prompt": "Will fail"})
        r = c9_app.get("/api/logs")
        rows = r.json()["rows"]
        assert len(rows) > 0
        latest = rows[0]
        assert latest["http_status"] == 503
        assert "Upstream timeout" in (latest["response_excerpt"] or "")


# ── /pairs page: header correctness tests ────────────────────────────────────

class TestC9PairsValidate:
    """Verify pairs page sends chat_mode (thinking) and work_mode correctly."""

    def _capture_validate(self, c9_app, payload: dict):
        """Send /api/validate and capture the headers forwarded to C1."""
        import c9_jokes.app as c9_mod
        captured = {}

        async def capture_post(url, **kwargs):
            captured["headers"] = dict(kwargs.get("headers", {}))
            captured["json"] = kwargs.get("json", {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            mock_resp.text = ""
            return mock_resp

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post("/api/validate", json=payload)
        return r, captured

    def test_validate_thinking_deep_sends_x_chat_mode(self, c9_app):
        """Selecting Think Deeper must send X-Chat-Mode: deep, not X-Work-Mode."""
        r, captured = self._capture_validate(c9_app, {
            "prompt": "Joke",
            "agent_ids": ["c9-jokes"],
            "chat_mode": "deep",
            "work_mode": "work",
        })
        assert r.status_code == 200
        assert captured["headers"].get("X-Chat-Mode") == "deep"
        assert captured["headers"].get("X-Work-Mode") == "work"

    def test_validate_work_mode_web_sends_x_work_mode(self, c9_app):
        """Work/Web toggle (web) must send X-Work-Mode: web on its own header."""
        r, captured = self._capture_validate(c9_app, {
            "prompt": "Joke",
            "agent_ids": ["c9-jokes"],
            "chat_mode": "auto",
            "work_mode": "web",
        })
        assert r.status_code == 200
        assert captured["headers"].get("X-Work-Mode") == "web"
        # Thinking mode should NOT bleed into X-Work-Mode
        assert captured["headers"].get("X-Chat-Mode") == "auto"

    def test_validate_work_mode_is_not_sent_as_chat_mode(self, c9_app):
        """Bug regression: 'work' must never appear as X-Chat-Mode value."""
        r, captured = self._capture_validate(c9_app, {
            "prompt": "Joke",
            "agent_ids": ["c9-jokes"],
            "chat_mode": "quick",
            "work_mode": "work",
        })
        assert r.status_code == 200
        # X-Chat-Mode must be the thinking key, not 'work'
        assert captured["headers"].get("X-Chat-Mode") == "quick"
        assert captured["headers"].get("X-Chat-Mode") != "work"

    def test_validate_forwards_attachments(self, c9_app):
        """Attachments array must be forwarded as file_ref content parts to C1."""
        import c9_jokes.app as c9_mod
        captured_json = {}

        async def capture_post(url, **kwargs):
            captured_json.update(kwargs.get("json", {}))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=_make_c1_ok())
            mock_resp.text = ""
            return mock_resp

        # Pre-load a fake file_id into the C1 _file_store via upload mock
        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = capture_post
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post("/api/validate", json={
                "prompt": "Summarise this",
                "agent_ids": ["c9-jokes"],
                "attachments": [{"file_id": "fid_xyz", "filename": "doc.txt"}],
            })
        assert r.status_code == 200
        # The message content should be a list with text + file_ref parts
        messages = captured_json.get("messages", [])
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        types = [p["type"] for p in content]
        assert "text" in types
        assert "file_ref" in types
        fref = next(p for p in content if p["type"] == "file_ref")
        assert fref["file_id"] == "fid_xyz"


# ── /api/status tests ─────────────────────────────────────────────────────────

class TestC9ApiStatus:
    def test_api_status_returns_200(self, c9_app):
        r = c9_app.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        # /api/status returns a dict keyed by agent ID plus a "ts" timestamp key
        assert isinstance(body, dict)
        assert "ts" in body
        agent_probes = {k: v for k, v in body.items() if k != "ts"}
        assert len(agent_probes) > 0
        for _agent_id, probe in agent_probes.items():
            assert "http_status" in probe
            assert "name" in probe

    def test_runtime_status_classifies_c3_pool_saturation(self, c9_app):
        import c9_jokes.app as c9_mod

        async def fake_get(url, timeout=None, **kwargs):
            if url.endswith("/session-health"):
                return _json_response({"session": "active", "profile": "m365_hub", "chat_mode": "work"})
            if url.endswith("/v1/docs"):
                return _json_response({"ok": True})
            if url.endswith("/status"):
                return _json_response({"status": "ok", "pool_size": 6, "pool_available": 0, "pool_initialized": True})
            if url.endswith("/health"):
                return _json_response({"status": "ok"})
            raise AssertionError(f"unexpected GET {url}")

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.get = AsyncMock(side_effect=fake_get)
        c9_mod._runtime_cache["data"] = None
        c9_mod._runtime_cache["captured_monotonic"] = 0.0
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.get("/api/runtime-status?force=true")

        assert r.status_code == 200
        body = r.json()
        assert body["level"] == "warn"
        assert body["components"]["c3_pool"]["state"] == "saturated"
        assert "c12b" in body["components"]
        assert "c12" not in body["components"]
        assert body["components"]["c12b"]["ok"] is True
        assert body["components"]["c12b"]["state"] == "ok"
        assert body["components"]["c3_pool"]["message"]


class TestC9SandboxExec:
    def test_sandbox_exec_supports_c12b(self, c9_app):
        import c9_jokes.app as c9_mod

        with patch.object(
            c9_mod,
            "_c12b_exec",
            AsyncMock(return_value={"stdout": "Python 3.11\nv20.0.0\nuv 0.1", "stderr": "", "exit_code": 0, "timed_out": False}),
        ):
            r = c9_app.post(
                "/api/sandbox/exec",
                json={"command": "python3 --version && node --version && uv --version", "sandbox": "c12b", "timeout": 10},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["exit_code"] == 0
        assert "Python 3.11" in body["stdout"]

    def test_chat_timeout_is_classified_when_runtime_is_otherwise_healthy(self, c9_app):
        import httpx
        import c9_jokes.app as c9_mod

        async def fake_get(url, timeout=None, **kwargs):
            if url.endswith("/session-health"):
                return _json_response({"session": "active", "profile": "m365_hub", "chat_mode": "work"})
            if url.endswith("/status"):
                return _json_response({"status": "ok", "pool_size": 6, "pool_available": 4, "pool_initialized": True})
            if url.endswith("/health"):
                return _json_response({"status": "ok"})
            raise AssertionError(f"unexpected GET {url}")

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.get = AsyncMock(side_effect=fake_get)
        mock_http.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        c9_mod._runtime_cache["data"] = None
        c9_mod._runtime_cache["captured_monotonic"] = 0.0
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post("/api/chat", json={"agent_id": "c9-jokes", "prompt": "Will time out"})

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "M365 Copilot slow or not responding" in (body.get("error") or "")

    def test_api_c3_auth_progress_proxies_snapshot(self, c9_app):
        import c9_jokes.app as c9_mod

        async def fake_get(url, timeout=None, **kwargs):
            if url.endswith("/auth-progress"):
                return _json_response({
                    "run_id": "auth-123",
                    "active": True,
                    "current_step_id": "hello_submit",
                    "steps": [{
                        "id": "hello_submit",
                        "label": "First prompt submitted",
                        "status": "running",
                        "detail": "Submitting hello",
                        "stats": {"runs": 2, "min_ms": 1100, "avg_ms": 1450.5, "max_ms": 1801},
                    }],
                    "pool_monitor": {
                        "phase": "expanding",
                        "requested_target": 12,
                        "target_size": 12,
                        "pool_size": 6,
                        "pool_available": 4,
                        "pool_initialized": True,
                        "agent_tabs": 2,
                        "last_added": 2,
                        "last_reloaded": 0,
                        "detail": "Expanding pool to 12",
                    },
                })
            if url.endswith("/session-health"):
                return _json_response({"session": "active", "profile": "m365_hub", "chat_mode": "work"})
            if url.endswith("/status"):
                return _json_response({"status": "ok", "pool_size": 4, "pool_available": 2, "pool_initialized": True, "agent_tabs": 1})
            if url.endswith("/health"):
                return _json_response({"status": "ok"})
            raise AssertionError(f"unexpected GET {url}")

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.get = AsyncMock(side_effect=fake_get)
        c9_mod._runtime_cache["data"] = None
        c9_mod._runtime_cache["captured_monotonic"] = 0.0
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.get("/api/c3-auth-progress")

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["progress"]["run_id"] == "auth-123"
        assert body["progress"]["current_step_id"] == "hello_submit"
        assert body["progress"]["pool_monitor"]["target_size"] == 12
        assert body["progress"]["steps"][0]["stats"]["avg_ms"] == 1450.5
        assert body["session"]["session"] == "active"
        assert body["c3_status"]["pool_initialized"] is True

    def test_api_c3_auth_progress_run_proxies_validate_auth(self, c9_app):
        import c9_jokes.app as c9_mod

        async def fake_post(url, timeout=None, **kwargs):
            if url.endswith("/validate-auth"):
                return _json_response({"validated": True, "pool_tabs_reloaded": 4, "pool_tabs_added": 2})
            raise AssertionError(f"unexpected POST {url}")

        mock_http = _make_mock_http(_make_c1_ok())
        mock_http.post = AsyncMock(side_effect=fake_post)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post("/api/c3-auth-progress/run")

        assert r.status_code == 200
        assert r.json()["validated"] is True
        assert r.json()["pool_tabs_reloaded"] == 4
        assert r.json()["pool_tabs_added"] == 2


# ── /api/upload tests ─────────────────────────────────────────────────────────

class TestC9ApiUpload:
    """Test that C9's /api/upload correctly proxies to C1 /v1/files."""

    def _make_upload_response(self, file_type="text", file_id="abc123", preview="Hello world"):
        return {
            "ok": True,
            "file_id": file_id,
            "type": file_type,
            "filename": "test.txt",
            "size": 11,
            "preview": preview,
        }

    def test_upload_txt_returns_ok(self, c9_app):
        import c9_jokes.app as c9_mod
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=self._make_upload_response())
        mock_http = _make_mock_http(self._make_upload_response())
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("note.txt", b"Hello world", "text/plain")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["file_id"] == "abc123"
        assert body["type"] == "text"

    def test_upload_png_returns_image_type(self, c9_app):
        import c9_jokes.app as c9_mod
        img_response = self._make_upload_response(file_type="image", preview=None)
        img_response["filename"] = "photo.png"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=img_response)
        mock_http = _make_mock_http(img_response)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["type"] == "image"

    def test_upload_c1_error_propagated(self, c9_app):
        """When C1 returns 400, C9 should propagate the error status."""
        import c9_jokes.app as c9_mod
        error_body = {"detail": "Unsupported file type"}
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json = MagicMock(return_value=error_body)
        mock_resp.text = '{"detail": "Unsupported file type"}'
        mock_http = _make_mock_http(error_body)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("virus.exe", b"\x00\x01", "application/octet-stream")},
            )
        assert r.status_code == 400
        body = r.json()
        assert body["ok"] is False
        assert "Unsupported" in (body.get("error") or "")

    def test_upload_then_chat_with_attachment(self, c9_app):
        """Full flow: upload a file, then reference it in a chat message."""
        import c9_jokes.app as c9_mod

        upload_response = self._make_upload_response(file_id="file999", preview="My secret note")
        chat_response = _make_c1_ok("The file says: My secret note")

        call_count = {"n": 0}

        async def fake_post(url, *, headers=None, json=None, files=None, timeout=None, **kw):
            call_count["n"] += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if files is not None:
                # This is the upload call
                mock_resp.json = MagicMock(return_value=upload_response)
            else:
                # This is the chat call
                mock_resp.json = MagicMock(return_value=chat_response)
            return mock_resp

        mock_http = MagicMock()
        mock_http.post = fake_post
        mock_http.get  = AsyncMock(return_value=MagicMock(status_code=200, json=MagicMock(return_value={})))
        mock_http.is_closed = False

        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            # Step 1: Upload
            r_upload = c9_app.post(
                "/api/upload",
                files={"file": ("note.txt", b"My secret note", "text/plain")},
            )
            assert r_upload.status_code == 200
            assert r_upload.json()["file_id"] == "file999"

            # Step 2: Chat with the attachment
            r_chat = c9_app.post("/api/chat", json={
                "agent_id": "c9-jokes",
                "prompt": "What does the file say?",
                "attachments": [{"file_id": "file999", "filename": "note.txt"}],
            })
            assert r_chat.status_code == 200
            assert r_chat.json()["ok"] is True

        assert call_count["n"] == 2, f"Expected 2 HTTP calls (upload + chat), got {call_count['n']}"

    def test_upload_pdf_shows_preview(self, c9_app):
        """PDF upload should return a text preview from C1."""
        import c9_jokes.app as c9_mod
        pdf_response = self._make_upload_response(
            file_type="text", file_id="pdf001",
            preview="Revenue Q1: $1M  Revenue Q2: $2M"
        )
        pdf_response["filename"] = "report.pdf"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=pdf_response)
        mock_http = _make_mock_http(pdf_response)
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch.object(c9_mod, "_get_http", return_value=mock_http):
            r = c9_app.post(
                "/api/upload",
                files={"file": ("report.pdf", b"%PDF-1.4", "application/pdf")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "preview" in body
        assert body["preview"] is not None
