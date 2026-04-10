"""
Unit tests for c9_jokes/app.py — no live containers required.

Strategy:
- FastAPI TestClient for C9's app
- httpx calls to C1/C3 are intercepted by monkeypatching _get_http() to return
  a mock AsyncClient that returns canned JSON responses.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

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
    resp.json = MagicMock(return_value=payload)
    resp.text = json.dumps(payload)
    return resp


class _FakeHttpxAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aclose(self):
        return None

    async def get(self, url, *args, **kwargs):
        if url.endswith("/health"):
            return _json_response({"ok": True})
        if url.endswith("/session-health"):
            return _json_response({"session": "active"})
        return _json_response({"ok": True})

    async def post(self, url, *args, **kwargs):
        return _json_response(_make_c1_ok("Preflight ok"))


def _make_fake_post_with_heartbeats(responses):
    response_iter = iter(responses)

    async def _fake(*args, **kwargs):
        yield {"kind": "response", "response": next(response_iter)}

    return _fake


async def _no_sleep(*args, **kwargs):
    return None


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


def _create_tasked_task(c9_app, *, name: str, executor_prompt: str, trigger_mode: str = "json", **extra):
    payload = {
        "name": name,
        "mode": "chat",
        "schedule_kind": "manual",
        "interval_minutes": 0,
        "tabs_required": 1,
        "active": False,
        "planner_prompt": "Run the task once and record the result.",
        "executor_prompt": executor_prompt,
        "trigger_mode": trigger_mode,
        "trigger_text": "",
    }
    payload.update(extra)
    r = c9_app.post("/api/tasks", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    return body["task"]


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
    def test_dashboard_page_returns_200(self, c9_app):
        r = c9_app.get("/")
        assert r.status_code == 200

    def test_dashboard_filters_hidden_alias_targets(self, c9_app):
        r = c9_app.get("/")
        assert r.status_code == 200
        html = r.text
        assert "C3 browser-auth runtime" in html
        assert html.count("C10b agent sandbox") == 1
        assert html.count("C11b multi-agent sandbox") == 1

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

    def test_api_reference_page_documents_current_multi_agent_routes(self, c9_app):
        r = c9_app.get("/api")
        assert r.status_code == 200
        html = r.text
        assert "/api/agent/stop" in html
        assert "/api/multi-agent/pause/{session_id}" in html
        assert "/api/multi-agent/inject/{session_id}/{pane_id}" in html
        assert "/api/ma/run" in html
        assert "/api/ma/stop/{session_id}" in html

    def test_docuz_tasked_page_returns_200_and_documents_tasked_surfaces(self, c9_app):
        r = c9_app.get("/docuz-tasked")
        assert r.status_code == 200
        html = r.text
        assert "Docuz-tasked" in html
        assert "Tasked operations manual" in html
        assert "Tasked - Builder and Orchestrator" in html
        assert "Alerts - Operational Signal Board" in html
        assert "Pipeline - Run Trace Monitor" in html
        assert "Completed - Terminal Run Review" in html
        assert "Live Docs - Seeded Regression Lab" in html
        assert "/api/tasks/draft-from-text" in html
        assert "/api/task-pipelines" in html
        assert "/api/task-completed" in html
        assert "/api/tasked-live-doc/traces" in html
        assert "task_definitions" in html
        assert "task_alerts" in html
        assert "task_step_results" in html
        assert "http://localhost:6080" in html

    def test_base_nav_links_docuz_tasked_next_to_api(self, c9_app):
        r = c9_app.get("/api")
        assert r.status_code == 200
        html = r.text
        api_idx = html.index('data-path="/api">API</a>')
        docuz_idx = html.index('data-path="/docuz-tasked">Docuz-tasked</a>')
        assert api_idx < docuz_idx

    def test_agent_page_stop_button_calls_backend_stop(self, c9_app):
        r = c9_app.get("/agent")
        assert r.status_code == 200
        assert 'body class="workspace-page"' in r.text
        assert '<main class="workspace-main">' in r.text
        assert "/api/agent/stop" in r.text
        assert "refreshHistory()" in r.text
        assert "margin: -1.25rem;" not in r.text

    def test_multi_agent_page_uses_full_bleed_workspace_shell(self, c9_app):
        r = c9_app.get("/multi-agent")
        assert r.status_code == 200
        assert 'body class="workspace-page"' in r.text
        assert '<main class="workspace-main">' in r.text
        assert "margin: -1.25rem;" not in r.text
        assert "background: linear-gradient(180deg, var(--bg) 0%, #0a0a0a 100%);" in r.text

    def test_multi_agento_page_stop_button_calls_backend_stop(self, c9_app):
        r = c9_app.get("/multi-Agento")
        assert r.status_code == 200
        assert 'body class="workspace-page"' in r.text
        assert '<main class="workspace-main">' in r.text
        assert "/api/ma/stop/" in r.text
        assert "multi-Agento session cancelled by user." in r.text
        assert "margin: -1.25rem;" not in r.text

    def test_logs_page_returns_200(self, c9_app):
        r = c9_app.get("/logs")
        assert r.status_code == 200

    def test_logs_page_uses_http_status_data_attributes_for_filtering(self, c9_app):
        c9_app.post("/api/chat", json={"agent_id": "c9-jokes", "prompt": "status row"})
        r = c9_app.get("/logs")
        assert r.status_code == 200
        assert 'data-http-status="' in r.text
        assert "tr.dataset.httpStatus" in r.text

    def test_health_page_returns_200(self, c9_app):
        r = c9_app.get("/health")
        assert r.status_code == 200

    def test_health_page_filters_hidden_alias_targets_and_shows_c3_status(self, c9_app):
        r = c9_app.get("/health")
        assert r.status_code == 200
        html = r.text
        assert html.count("C10b agent sandbox") == 1
        assert html.count("C11b multi-agent sandbox") == 1
        assert "C3 /status" in html

    def test_health_history_returns_elapsed_ms_after_status_probe(self, c9_app):
        r_status = c9_app.get("/api/status")
        assert r_status.status_code == 200
        r_hist = c9_app.get("/api/health-history?target=c1&limit=1")
        assert r_hist.status_code == 200
        rows = r_hist.json()
        assert rows
        assert "elapsed_ms" in rows[0]

    def test_tasked_page_has_workflow_diagram_anchor(self, c9_app):
        r = c9_app.get("/tasked")
        assert r.status_code == 200
        assert 'id="tasked-workflow-diagram"' in r.text
        assert "renderWorkflowDiagram" in r.text
        assert "Run Again keeps the same Trace ID and creates a new execution number." in r.text
        assert "Clone / Edit" in r.text
        assert 'data-row-action="rerun"' in r.text
        assert 'data-row-action="clone-edit"' in r.text

    def test_tasked_page_exposes_distance_and_combo_template_controls(self, c9_app):
        r = c9_app.get("/tasked")
        assert r.status_code == 200
        html = r.text
        assert "Refine with AI agent first" in html
        assert 'id="task-distance-from-location"' in html
        assert 'id="task-distance-to-location"' in html
        assert 'id="task-distance-comparator"' in html
        assert 'id="task-distance-threshold"' in html
        assert "Use combo / multiple templates" in html
        assert 'id="task-chain-operator"' in html
        assert 'id="task-chain-execution-mode"' in html
        assert 'id="task-chain-condition-strategy"' in html
        assert 'id="task-chain-add-template"' in html

    def test_pipeline_page_returns_200(self, c9_app):
        r = c9_app.get("/piplinetask")
        assert r.status_code == 200
        assert "/api/task-pipelines" in r.text

    def test_task_completed_page_returns_200(self, c9_app):
        r = c9_app.get("/task-completed")
        assert r.status_code == 200
        assert "/api/task-completed" in r.text

    def test_live_docs_page_returns_200(self, c9_app):
        r = c9_app.get("/tasked-live-doc")
        assert r.status_code == 200
        assert "Tasked Live Documentation" in r.text
        assert "Run All" in r.text
        assert "Validate All" in r.text
        assert "TRACE-200" in r.text
        assert "TRACE-206" in r.text
        assert "TRACE-300" in r.text
        assert "TRACE-301" in r.text
        assert "TRACE-400" in r.text
        assert "TRACE-401" in r.text
        assert "TRACE-500" in r.text
        assert "TRACE-501" in r.text
        assert "TRACE-801" in r.text
        assert "TRACE-802" in r.text
        assert "Editable Template Traces (TRACE-200 / TRACE-300 / TRACE-400 / TRACE-500 / TRACE-800 Series)" in r.text
        assert "These fifteen traces are DB-backed mirrors" in r.text
        assert "custom TRACE-8xx aggregate workflow" in r.text
        assert "Dublin, Ireland" in r.text
        assert "above 50C" in r.text
        assert "below 1000 km" in r.text
        assert "above 100 km" in r.text
        assert "positive AND chain" in r.text
        assert "negative AND chain" in r.text
        assert "positive NOR chain" in r.text
        assert "negative NOR chain" in r.text

    def test_live_docs_api_returns_template_trace_series(self, c9_app):
        r = c9_app.get("/api/tasked-live-doc/traces")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        traces = body["traces"]
        assert [item["trace"] for item in traces] == [
            "TRACE-200",
            "TRACE-201",
            "TRACE-202",
            "TRACE-203",
            "TRACE-204",
            "TRACE-205",
            "TRACE-206",
            "TRACE-300",
            "TRACE-301",
            "TRACE-400",
            "TRACE-401",
            "TRACE-500",
            "TRACE-501",
            "TRACE-801",
            "TRACE-802",
        ]
        by_trace = {item["trace"]: item for item in traces}
        assert by_trace["TRACE-200"]["task_id"] == "task_trace_200"
        assert by_trace["TRACE-200"]["expect_alert"] == "required"
        assert by_trace["TRACE-200"]["template_data"]["weather_location"] == "Dublin, Ireland"
        assert by_trace["TRACE-200"]["template_data"]["temperature_threshold_c"] == 0.0
        assert by_trace["TRACE-301"]["task_id"] == "task_trace_301"
        assert by_trace["TRACE-301"]["template_key"] == "distance-between-cities"
        assert by_trace["TRACE-301"]["template_data"]["distance_threshold_km"] == 100.0
        assert by_trace["TRACE-301"]["template_data"]["distance_comparator"] == "lt"
        assert by_trace["TRACE-400"]["template_key"] == "template-chain"
        assert by_trace["TRACE-400"]["expect_alert"] == "required"
        assert by_trace["TRACE-400"]["template_data"]["chain_operator"] == "AND"
        assert [item["template_key"] for item in by_trace["TRACE-400"]["template_data"]["chain_items"]] == [
            "distance-between-cities",
            "weather-dublin",
        ]
        assert by_trace["TRACE-401"]["template_key"] == "template-chain"
        assert by_trace["TRACE-401"]["template_data"]["chain_operator"] == "AND"
        assert by_trace["TRACE-500"]["template_key"] == "template-chain"
        assert by_trace["TRACE-500"]["expect_alert"] == "required"
        assert by_trace["TRACE-500"]["template_data"]["chain_operator"] == "NOR"
        assert [item["template_key"] for item in by_trace["TRACE-500"]["template_data"]["chain_items"]] == [
            "distance-between-cities",
            "weather-dublin",
        ]
        assert by_trace["TRACE-501"]["template_key"] == "template-chain"
        assert by_trace["TRACE-501"]["expect_alert"] == "none"
        assert by_trace["TRACE-501"]["template_data"]["chain_operator"] == "NOR"
        assert by_trace["TRACE-801"]["template_key"] == "template-chain"
        assert by_trace["TRACE-801"]["expect_alert"] == "required"
        assert by_trace["TRACE-801"]["template_data"]["execution_mode"] == "parallel"
        assert by_trace["TRACE-801"]["template_data"]["condition_strategy"] == "aggregate-only"
        assert [item["template_key"] for item in by_trace["TRACE-801"]["template_data"]["chain_items"]] == [
            "weather-dublin",
            "weather-dublin",
            "distance-between-cities",
            "distance-between-cities",
            "custom-step",
        ]
        assert by_trace["TRACE-801"]["template_data"]["chain_items"][-1]["condition_role"] == "aggregate"
        assert by_trace["TRACE-802"]["expect_alert"] == "none"
        assert "average_distance_km is less than 100" in by_trace["TRACE-802"]["template_data"]["chain_items"][-1]["executor_prompt"]

    def test_trace8xx_reference_runs_seed_completed_pipeline_alerts_and_completed(self, c9_app):
        traces = c9_app.get("/api/tasked-live-doc/traces").json()["traces"]
        by_trace = {item["trace"]: item for item in traces}
        assert by_trace["TRACE-801"]["run_id"] == "trun_trace_801_ref"
        assert by_trace["TRACE-801"]["alert_id"]
        assert by_trace["TRACE-802"]["run_id"] == "trun_trace_802_ref"
        assert by_trace["TRACE-802"]["alert_id"] is None

        for task_id, expect_alert in (("task_trace_801", True), ("task_trace_802", False)):
            pipe_resp = c9_app.get(f"/api/task-pipelines?task_id={task_id}")
            assert pipe_resp.status_code == 200
            pipeline = pipe_resp.json()["pipelines"][0]
            assert pipeline["run"]["status"] == "completed"
            assert pipeline["summary"]["steps_total"] == 8
            assert len(pipeline["steps"]) == 8

            chain_steps = [step for step in pipeline["steps"] if step["step_id"].startswith(f"{task_id}_chain_")]
            assert len(chain_steps) == 5
            assert all(step["status"] == "completed" for step in chain_steps)

            aggregate_step = next(step for step in pipeline["steps"] if step["step_id"] == f"{task_id}_chain_5")
            aggregate = aggregate_step["output"]["parsed"]
            assert aggregate["details"]["average_temperature_c"] == 13.8
            assert aggregate["details"]["average_distance_km"] == 2252.0
            assert aggregate["details"]["min_temperature_c"] == 11.4
            assert aggregate["details"]["max_temperature_c"] == 16.2

            complete_step = next(step for step in pipeline["steps"] if step["step_id"] == f"{task_id}_complete")
            assert complete_step["status"] == "completed"
            assert complete_step["output"]["result"]["details"]["average_distance_km"] == 2252.0

            alert_step = next(step for step in pipeline["steps"] if step["step_id"] == f"{task_id}_alert")
            if expect_alert:
                assert pipeline["summary"]["alerts_total"] == 1
                assert alert_step["status"] == "completed"
                assert alert_step["output"]["alert_id"]
            else:
                assert pipeline["summary"]["alerts_total"] == 0
                assert alert_step["status"] == "skipped"
                assert alert_step["output"]["result"]["triggered"] is False

            completed_resp = c9_app.get(f"/api/task-completed?task_id={task_id}")
            assert completed_resp.status_code == 200
            completed_item = completed_resp.json()["items"][0]
            assert completed_item["run"]["status"] == "completed"
            assert len(completed_item["steps"]) == 8

        alerts = c9_app.get("/api/alerts?limit=500").json()["alerts"]
        assert any(alert["task_id"] == "task_trace_801" for alert in alerts)
        assert all(alert["task_id"] != "task_trace_802" for alert in alerts)

    def test_task_templates_expose_live_doc_trace_metadata(self, c9_app):
        r = c9_app.get("/api/task-templates?include_archived=true")
        assert r.status_code == 200
        templates = r.json()["templates"]
        weather = next(item for item in templates if item["key"] == "weather-dublin")
        distance = next(item for item in templates if item["key"] == "distance-between-cities")
        combo = next(item for item in templates if item["key"] == "template-chain")
        sandbox = next(item for item in templates if item["key"] == "sandbox-python-validate")
        assert weather["live_doc_trace"] == "TRACE-200"
        assert weather["live_doc_order"] == 200
        assert distance["live_doc_trace"] == "TRACE-300"
        assert distance["live_doc_order"] == 300
        assert combo["live_doc_trace"] == "TRACE-400"
        assert combo["live_doc_order"] == 400
        assert sandbox["live_doc_trace"] == "TRACE-205"
        assert sandbox["live_doc_order"] == 205

    def test_live_doc_seeded_template_tasks_exist(self, c9_app):
        r = c9_app.get("/api/tasks?include_archived=false")
        assert r.status_code == 200
        tasks = r.json()["tasks"]
        task_ids = {item["id"] for item in tasks}
        assert "task_trace_200" in task_ids
        assert "task_trace_205" in task_ids
        assert "task_trace_206" in task_ids
        assert "task_trace_300" in task_ids
        assert "task_trace_301" in task_ids
        assert "task_trace_400" in task_ids
        assert "task_trace_401" in task_ids
        assert "task_trace_500" in task_ids
        assert "task_trace_501" in task_ids
        assert "task_trace_801" in task_ids
        assert "task_trace_802" in task_ids
        positive = next(item for item in tasks if item["id"] == "task_trace_200")
        negative = next(item for item in tasks if item["id"] == "task_trace_206")
        distance_positive = next(item for item in tasks if item["id"] == "task_trace_300")
        distance_negative = next(item for item in tasks if item["id"] == "task_trace_301")
        combo_positive = next(item for item in tasks if item["id"] == "task_trace_400")
        combo_negative = next(item for item in tasks if item["id"] == "task_trace_401")
        combo_nor_positive = next(item for item in tasks if item["id"] == "task_trace_500")
        combo_nor_negative = next(item for item in tasks if item["id"] == "task_trace_501")
        custom_aggregate_positive = next(item for item in tasks if item["id"] == "task_trace_801")
        custom_aggregate_negative = next(item for item in tasks if item["id"] == "task_trace_802")
        assert positive["template_data"]["temperature_threshold_c"] == 0.0
        assert negative["template_data"]["temperature_threshold_c"] == 50.0
        assert "above 0" in positive["executor_prompt"]
        assert "above 50" in negative["executor_prompt"]
        assert distance_positive["template_data"]["distance_threshold_km"] == 1000.0
        assert distance_negative["template_data"]["distance_threshold_km"] == 100.0
        assert "less than 1000" in distance_positive["executor_prompt"]
        assert "less than 100" in distance_negative["executor_prompt"]
        assert combo_positive["template_data"]["chain_operator"] == "AND"
        assert combo_negative["template_data"]["chain_operator"] == "AND"
        assert [item["template_key"] for item in combo_positive["template_data"]["chain_items"]] == [
            "distance-between-cities",
            "weather-dublin",
        ]
        assert "operator AND" in combo_positive["executor_prompt"]
        assert combo_nor_positive["template_data"]["chain_operator"] == "NOR"
        assert combo_nor_negative["template_data"]["chain_operator"] == "NOR"
        assert [item["template_key"] for item in combo_nor_positive["template_data"]["chain_items"]] == [
            "distance-between-cities",
            "weather-dublin",
        ]
        assert "operator NOR" in combo_nor_positive["executor_prompt"]
        assert custom_aggregate_positive["template_data"]["execution_mode"] == "parallel"
        assert custom_aggregate_positive["template_data"]["condition_strategy"] == "aggregate-only"
        assert custom_aggregate_positive["template_data"]["chain_items"][-1]["template_key"] == "custom-step"
        assert custom_aggregate_positive["template_data"]["chain_items"][-1]["include_context"] is True
        assert "average_temperature_c" in custom_aggregate_positive["executor_prompt"]
        assert "average_distance_km is less than 100" in custom_aggregate_negative["executor_prompt"]

    def test_pairs_multi_agent_launcher_uses_main_prompt_input(self, c9_app):
        r = c9_app.get("/pairs")
        assert r.status_code == 200
        assert "document.getElementById('val-prompt')" in r.text
        assert ".pair-prompt" not in r.text

    def test_chat_resume_restores_saved_agent_selection(self, c9_app):
        c9_app.post("/api/chat", json={"agent_id": "c6-kilocode", "prompt": "remember me"})
        r = c9_app.get("/chat")
        assert r.status_code == 200
        assert "session.agent_id" in r.text
        assert "agentSelect.value = session.agent_id" in r.text

    def test_pages_include_runtime_status_badge_polling(self, c9_app):
        r = c9_app.get("/chat")
        assert r.status_code == 200
        assert "runtime-badge" in r.text
        assert "/api/runtime-status" in r.text


# ── Tasked authoring tests ────────────────────────────────────────────────────

class TestTaskedAuthoring:
    def test_draft_from_text_matches_distance_template(self, c9_app):
        prompt = "what is km distance between Dublin and Cork if the distance is less than 100 km, create the alert, run once."
        r = c9_app.post("/api/tasks/draft-from-text", json={
            "prompt": prompt,
            "strategy": "auto",
            "mode_hint": "chat",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["strategy_used"] == "existing-template"
        assert body["source"] == "heuristic-template"
        assert body["authoring_engine"] == "local"
        assert body["draft"]["template_key"] == "distance-between-cities"
        assert body["draft"]["template_data"]["template_kind"] == "distance-threshold"
        assert body["draft"]["template_data"]["from_location"] == "Dublin, Ireland"
        assert body["draft"]["template_data"]["to_location"] == "Cork, Ireland"
        assert body["draft"]["template_data"]["distance_threshold_km"] == 100.0
        assert body["draft"]["template_data"]["distance_comparator"] == "lt"

    def test_draft_from_text_can_use_agent_refinement_for_distance_template(self, c9_app):
        import c9_jokes.app as c9_mod

        prompt = "what is km distance between Dublin and Cork if the distance is less than 100 km, create the alert, run once."
        llm_payload = {
            "strategy": "existing-template",
            "template_key": "distance-between-cities",
            "template_data": {
                "template_kind": "distance-threshold",
                "from_location": "Dublin, Ireland",
                "to_location": "Cork, Ireland",
                "distance_threshold_km": 100.0,
                "distance_comparator": "lt",
            },
            "explanation": "Matched the request to the closest editable distance template.",
        }
        mock_chat = AsyncMock(return_value={
            "ok": True,
            "http_status": 200,
            "text": json.dumps(llm_payload),
            "error": None,
        })
        with patch.object(c9_mod, "_chat_one", mock_chat):
            r = c9_app.post("/api/tasks/draft-from-text", json={
                "prompt": prompt,
                "strategy": "auto",
                "mode_hint": "chat",
                "refine_with_agent": True,
            })

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["source"] == "llm"
        assert body["authoring_engine"] == "m365-agent"
        assert body["draft"]["template_key"] == "distance-between-cities"
        assert body["draft"]["template_data"]["from_location"] == "Dublin, Ireland"
        assert body["draft"]["template_data"]["to_location"] == "Cork, Ireland"
        assert body["draft"]["template_data"]["distance_threshold_km"] == 100.0
        sent_prompt = mock_chat.call_args.args[1]
        assert "DOCUZ-TASKED FULL PAGE TEMPLATE" in sent_prompt
        assert "Tasked app reference material" in sent_prompt
        assert "prompt1 is the user's raw request" in sent_prompt
        assert prompt in sent_prompt

    def test_draft_from_text_builds_combo_chain_for_distance_and_weather(self, c9_app):
        prompt = (
            "what is km distance between Dublin and Cork if the distance is less than 100 km, create the alert, run once. "
            "and also the temperature in dublin is above 5 degrees"
        )
        r = c9_app.post("/api/tasks/draft-from-text", json={
            "prompt": prompt,
            "strategy": "auto",
            "mode_hint": "chat",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["source"] == "heuristic-chain"
        assert body["draft"]["template_key"] == "template-chain"
        assert body["draft"]["template_data"]["template_kind"] == "template-chain"
        assert body["draft"]["template_data"]["chain_operator"] == "AND"
        assert [item["template_key"] for item in body["draft"]["template_data"]["chain_items"]] == [
            "distance-between-cities",
            "weather-dublin",
        ]
        assert body["draft"]["template_data"]["chain_items"][0]["template_data"]["distance_threshold_km"] == 100.0
        assert body["draft"]["template_data"]["chain_items"][1]["template_data"]["temperature_threshold_c"] == 5.0
        assert "operator AND" in body["draft"]["executor_prompt"]

    def test_draft_from_text_builds_or_combo_chain_for_distance_or_weather(self, c9_app):
        prompt = (
            "what is km distance between Dublin and Cork if the distance is less than 100 km, create the alert, run once, "
            "or the temperature in dublin is above 5 degrees"
        )
        r = c9_app.post("/api/tasks/draft-from-text", json={
            "prompt": prompt,
            "strategy": "auto",
            "mode_hint": "chat",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["source"] == "heuristic-chain"
        assert body["draft"]["template_key"] == "template-chain"
        assert body["draft"]["template_data"]["chain_operator"] == "OR"
        assert [item["template_key"] for item in body["draft"]["template_data"]["chain_items"]] == [
            "distance-between-cities",
            "weather-dublin",
        ]
        assert "operator OR" in body["draft"]["executor_prompt"]

    def test_draft_from_text_builds_custom_aggregate_chain_in_prompt_order(self, c9_app):
        prompt = (
            "check current weather in newyork, current weather in London, "
            "distance between LA to Sanfrancisco. and distance LA to Manhattan. "
            "provide average distance and average temperature, also min average and max average temperature."
        )
        r = c9_app.post("/api/tasks/draft-from-text", json={
            "prompt": prompt,
            "strategy": "auto",
            "mode_hint": "chat",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["source"] == "heuristic-chain"
        draft = body["draft"]
        chain_data = draft["template_data"]
        assert draft["template_key"] == "template-chain"
        assert chain_data["execution_mode"] == "parallel"
        assert chain_data["condition_strategy"] == "aggregate-only"
        assert [item["template_key"] for item in chain_data["chain_items"]] == [
            "weather-dublin",
            "weather-dublin",
            "distance-between-cities",
            "distance-between-cities",
            "custom-step",
        ]
        assert chain_data["chain_items"][0]["template_data"]["weather_location"] == "New York, United States"
        assert chain_data["chain_items"][1]["template_data"]["weather_location"] == "London, United Kingdom"
        assert chain_data["chain_items"][2]["template_data"]["from_location"] == "Los Angeles, United States"
        assert chain_data["chain_items"][2]["template_data"]["to_location"] == "San Francisco, United States"
        assert chain_data["chain_items"][3]["template_data"]["to_location"] == "Manhattan, New York, United States"
        aggregate = chain_data["chain_items"][-1]
        assert aggregate["condition_role"] == "aggregate"
        assert aggregate["include_context"] is True
        assert "average_temperature_c" in aggregate["executor_prompt"]
        assert "average_distance_km" in aggregate["executor_prompt"]
        condition_step = next(step for step in draft["steps"] if step["kind"] == "condition")
        assert condition_step["config"]["condition_strategy"] == "aggregate-only"
        assert condition_step["config"]["rules"] == [{
            "source": "task_draft_chain_5",
            "field": "parsed.triggered",
            "comparator": "eq",
            "value": True,
        }]

    def test_draft_from_text_can_use_agent_refinement_for_combo_nor_chain(self, c9_app):
        import c9_jokes.app as c9_mod

        prompt = "Alert only when neither the Dublin to Cork distance is less than 100 km nor the temperature in Dublin is above 50 degrees."
        llm_payload = {
            "strategy": "freehand",
            "template_key": "template-chain",
            "template_data": {
                "template_kind": "template-chain",
                "chain_operator": "NOR",
                "chain_items": [
                    {
                        "template_key": "distance-between-cities",
                        "template_data": {
                            "template_kind": "distance-threshold",
                            "from_location": "Dublin, Ireland",
                            "to_location": "Cork, Ireland",
                            "distance_threshold_km": 100.0,
                            "distance_comparator": "lt",
                        },
                    },
                    {
                        "template_key": "weather-dublin",
                        "template_data": {
                            "template_kind": "weather-threshold",
                            "weather_location": "Dublin, Ireland",
                            "temperature_threshold_c": 50.0,
                        },
                    },
                ],
                "source_request": prompt,
                "refined_request": "Run the Dublin-to-Cork distance template, run the Dublin weather template, then apply NOR so the alert fires only when both component conditions are false.",
            },
            "explanation": "Expanded the request into a NOR chain built from two existing editable templates.",
        }
        with patch.object(
            c9_mod,
            "_chat_one",
            AsyncMock(return_value={
                "ok": True,
                "http_status": 200,
                "text": json.dumps(llm_payload),
                "error": None,
            }),
        ):
            r = c9_app.post("/api/tasks/draft-from-text", json={
                "prompt": prompt,
                "strategy": "auto",
                "mode_hint": "chat",
                "refine_with_agent": True,
            })

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["source"] == "llm"
        assert body["authoring_engine"] == "m365-agent"
        assert body["draft"]["template_key"] == "template-chain"
        assert body["draft"]["template_data"]["chain_operator"] == "NOR"
        assert [item["template_key"] for item in body["draft"]["template_data"]["chain_items"]] == [
            "distance-between-cities",
            "weather-dublin",
        ]
        assert "operator NOR" in body["draft"]["executor_prompt"]


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

    def test_validate_sequential_runs_one_agent_at_a_time(self, c9_app):
        import c9_jokes.app as c9_mod

        active = 0
        max_active = 0
        order = []

        async def fake_chat_one(agent_id, prompt, c1, chat_mode="", work_mode="", attachments=None):
            nonlocal active, max_active
            order.append(agent_id)
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return {
                "ok": True,
                "text": f"ok:{agent_id}",
                "http_status": 200,
                "elapsed_ms": 1,
            }

        agent_ids = ["c2-aider", "c6-kilocode", "c8-hermes"]
        with patch.object(c9_mod, "_chat_one", side_effect=fake_chat_one):
            r = c9_app.post("/api/validate", json={
                "prompt": "Joke",
                "parallel": False,
                "agent_ids": agent_ids,
            })

        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "sequential"
        assert order == agent_ids
        assert max_active == 1

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


class TestC9TaskExecutionIdentity:
    def test_rerun_keeps_trace_id_and_increments_execution_number(self, c9_app):
        import c9_jokes.app as c9_mod

        task = _create_tasked_task(
            c9_app,
            name="Execution identity task",
            executor_prompt='{"triggered": false, "summary": "ok"}',
        )

        chat_result = {
            "ok": True,
            "http_status": 200,
            "text": json.dumps({
                "triggered": False,
                "trigger": "Execution identity",
                "title": "Execution identity",
                "summary": "Stable trace, new execution",
                "details": {"trace": task["id"]},
            }),
            "error": None,
        }
        with patch.object(c9_mod, "_chat_one", AsyncMock(return_value=chat_result)):
            first = c9_app.post(f"/api/tasks/{task['id']}/run")
            assert first.status_code == 200
            assert first.json()["ok"] is True

            second = c9_app.post(f"/api/tasks/{task['id']}/redo")
            assert second.status_code == 200
            second_body = second.json()
            assert second_body["ok"] is True
            assert second_body["task_id"] == task["id"]
            assert second_body["run_id"] != first.json()["run_id"]

        task_rows = c9_app.get("/api/tasks?include_archived=false").json()["tasks"]
        latest = next(item for item in task_rows if item["id"] == task["id"])
        assert latest["trace"]["trace_id"] == task["id"]
        assert latest["latest_run"]["execution_number"] == 2
        assert latest["latest_run"]["execution_label"] == "Execution #2"

        runs = c9_app.get(f"/api/task-runs?task_id={task['id']}&limit=10").json()["runs"]
        assert [item["execution_number"] for item in runs] == [2, 1]
        assert runs[0]["trace_id"] == task["id"]

    def test_clone_creates_new_trace_id_for_editing(self, c9_app):
        task = _create_tasked_task(
            c9_app,
            name="Clone identity task",
            executor_prompt='{"triggered": false, "summary": "ok"}',
        )

        r = c9_app.post(f"/api/tasks/{task['id']}/clone")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        cloned = body["task"]
        assert cloned["id"] != task["id"]
        assert cloned["trace"]["trace_id"] == cloned["id"]


class TestC9AgentAndAgento:
    def test_agent_stop_endpoint_updates_session_status(self, c9_app):
        import c9_jokes.app as c9_mod

        c9_mod._ensure_db()
        with sqlite3.connect(c9_mod.DEFAULT_DB) as conn:
            conn.execute(
                "INSERT INTO agent_sessions (id, created_at, updated_at, task, agent_id, chat_mode, work_mode, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("sess-stop", "2026-04-08T00:00:00Z", "2026-04-08T00:00:00Z", "stop me", "c9-jokes", "auto", "work", "running"),
            )

        r = c9_app.post("/api/agent/stop", json={"session_id": "sess-stop"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

        rows = c9_app.get("/api/agent/sessions").json()
        stopped = next(row for row in rows if row["id"] == "sess-stop")
        assert stopped["status"] == "cancelled"

    def test_agent_run_stream_completes_with_stubbed_tools(self, c9_app):
        import c9_jokes.app as c9_mod

        responses = [
            _json_response(_make_c1_ok('FILE: hello.py\n```python\nprint("hi")\n```\nRUN: python3 hello.py')),
            _json_response(_make_c1_ok("DONE: Built hello.py and ran it successfully.")),
        ]

        async def fake_execute_tool(tool):
            if tool["tool"] == "write_file":
                return "File written", {"ok": True, "path": tool["path"], "size": len(tool.get("content", ""))}
            if tool["tool"] == "exec":
                return "STDOUT:\nhi\nSTDERR:\n\nEXIT_CODE: 0", {"exit_code": 0}
            return "ok", {}

        with patch.object(c9_mod.httpx, "AsyncClient", _FakeHttpxAsyncClient), \
             patch.object(c9_mod, "_post_with_heartbeats", new=_make_fake_post_with_heartbeats(responses)), \
             patch.object(c9_mod, "_execute_tool", side_effect=fake_execute_tool), \
             patch.object(c9_mod, "_notes_init", new=AsyncMock(return_value=None)), \
             patch.object(c9_mod, "_notes_read", new=AsyncMock(return_value="")), \
             patch.object(c9_mod, "_notes_append", new=AsyncMock(return_value=None)), \
             patch.object(c9_mod.asyncio, "sleep", new=AsyncMock(side_effect=_no_sleep)):
            r = c9_app.get("/api/agent/run?task=build+hello.py&agent_id=c9-jokes")

        assert r.status_code == 200
        assert "event: session" in r.text
        assert "event: tool_call" in r.text
        assert "event: final" in r.text

        rows = c9_app.get("/api/agent/sessions").json()
        assert any(row["status"] == "completed" for row in rows)

    def test_multi_agento_stop_endpoint_updates_session_status(self, c9_app):
        import c9_jokes.app as c9_mod

        c9_mod._ensure_db()
        with sqlite3.connect(c9_mod.DEFAULT_DB) as conn:
            conn.execute(
                "INSERT INTO ma_sessions (id, created_at, updated_at, task, roles, status) VALUES (?,?,?,?,?,?)",
                ("ma-stop", "2026-04-08T00:00:00Z", "2026-04-08T00:00:00Z", "stop us", json.dumps(["builder"]), "running"),
            )

        r = c9_app.post("/api/ma/stop/ma-stop")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

        rows = c9_app.get("/api/ma/sessions").json()
        stopped = next(row for row in rows if row["id"] == "ma-stop")
        assert stopped["status"] == "cancelled"

    def test_multi_agento_run_stream_completes_with_stubbed_roles(self, c9_app):
        import c9_jokes.app as c9_mod

        supervisor_response = _json_response(_make_c1_ok("builder: Build the feature\ntester: Validate the feature"))

        async def fake_role_loop(*, pane_id, role, queue, **kwargs):
            queue.put_nowait(
                "event: pane_done\ndata: "
                + json.dumps({"pane_id": pane_id, "role": role, "step": 1, "summary": f"{role} done", "files": [f"{role}.md"]})
                + "\n\n"
            )
            return {"role": role, "pane_id": pane_id, "done": True, "summary": f"{role} done", "files": [f"{role}.md"], "steps": 1}

        with patch.object(c9_mod.httpx, "AsyncClient", _FakeHttpxAsyncClient), \
             patch.object(c9_mod, "_post_with_heartbeats", new=_make_fake_post_with_heartbeats([supervisor_response])), \
             patch.object(c9_mod, "_ma_role_loop_c11", side_effect=fake_role_loop), \
             patch.object(c9_mod.asyncio, "sleep", new=AsyncMock(side_effect=_no_sleep)):
            r = c9_app.get("/api/ma/run?task=ship+it&roles=builder,tester&agent_id=c6-kilocode")

        assert r.status_code == 200
        assert "event: session" in r.text
        assert "event: pane_init" in r.text
        assert "event: pane_done" in r.text
        assert "event: final" in r.text

        rows = c9_app.get("/api/ma/sessions").json()
        assert any(row["status"] == "completed" for row in rows)


class TestC9TaskedWorkflow:
    def test_tasked_save_syncs_builder_prompt_into_chat_step(self, c9_app):
        payload = {
            "name": "Weather sync save",
            "mode": "chat",
            "schedule_kind": "manual",
            "interval_minutes": 0,
            "tabs_required": 1,
            "active": False,
            "planner_prompt": "Check weather",
            "executor_prompt": "Weather threshold above 7",
            "trigger_mode": "json",
            "trigger_text": "",
            "steps": [
                {
                    "id": "task_draft_step_1",
                    "name": "Execute prompt",
                    "kind": "chat",
                    "config": {"prompt": "Weather threshold above 10", "agent_id": "c6-kilocode"},
                    "active": True,
                },
                {
                    "id": "task_draft_step_2",
                    "name": "Create alert",
                    "kind": "alert",
                    "config": {"trigger_text": "old-trigger", "severity": "warning", "repeat_every_minutes": 5},
                    "active": True,
                },
                {
                    "id": "task_draft_step_3",
                    "name": "Complete",
                    "kind": "complete",
                    "config": {},
                    "active": True,
                },
            ],
            "alert_policy": {"repeat_every_minutes": 0, "dedupe_key_template": "", "severity": "info", "while_condition_true": False},
        }

        r = c9_app.post("/api/tasks", json=payload)
        assert r.status_code == 200
        task = r.json()["task"]
        chat_step = next(step for step in task["steps"] if step["kind"] == "chat")
        alert_step = next(step for step in task["steps"] if step["kind"] == "alert")

        assert chat_step["config"]["prompt"] == "Weather threshold above 7"
        assert alert_step["config"]["trigger_text"] == ""
        assert alert_step["config"]["severity"] == "info"

    def test_task_run_repairs_drifted_chat_step_before_execution(self, c9_app):
        import c9_jokes.app as c9_mod

        task = _create_tasked_task(
            c9_app,
            name="Weather drift repair",
            executor_prompt="Weather threshold above 7",
        )

        with c9_mod._db() as conn:
            row = conn.execute(
                "SELECT id, config_json FROM task_workflow_steps WHERE task_id=? AND kind='chat' ORDER BY position ASC LIMIT 1",
                (task["id"],),
            ).fetchone()
            cfg = json.loads(row["config_json"])
            cfg["prompt"] = "Weather threshold above 10"
            conn.execute(
                "UPDATE task_workflow_steps SET config_json=? WHERE id=?",
                (json.dumps(cfg), row["id"]),
            )

        async def fake_chat_one(agent_id, prompt, *args, **kwargs):
            assert prompt == "Weather threshold above 7"
            return {
                "ok": True,
                "http_status": 200,
                "text": json.dumps({
                    "triggered": True,
                    "trigger": "Dublin weather",
                    "title": "Current weather in Dublin",
                    "summary": "Threshold passed",
                    "details": {"location": "Dublin", "temperature_c": 8.0, "condition": "Cloudy"},
                }),
                "error": None,
            }

        with patch.object(c9_mod, "_chat_one", AsyncMock(side_effect=fake_chat_one)):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        assert r_run.json()["status"] == "completed"

        r_tasks = c9_app.get("/api/tasks?include_archived=false")
        repaired = next(item for item in r_tasks.json()["tasks"] if item["id"] == task["id"])
        repaired_chat_step = next(step for step in repaired["steps"] if step["kind"] == "chat")
        assert repaired_chat_step["config"]["prompt"] == "Weather threshold above 7"

    def test_weather_template_exposes_editable_location_and_threshold(self, c9_app):
        r = c9_app.get("/api/task-templates")
        assert r.status_code == 200
        weather = next(item for item in r.json()["templates"] if item["key"] == "weather-dublin")
        assert weather["template_data"]["template_kind"] == "weather-threshold"
        assert weather["template_data"]["weather_location"] == "Dublin, Ireland"
        assert weather["template_data"]["temperature_threshold_c"] == 10.0
        assert "Dublin, Ireland" in weather["executor_prompt"]
        assert "above 10" in weather["executor_prompt"]

        page = c9_app.get("/tasked")
        assert page.status_code == 200
        assert "Weather town / city" in page.text
        assert "Trigger above temperature" in page.text

    def test_weather_task_save_syncs_location_and_threshold_into_prompt(self, c9_app):
        r = c9_app.post("/api/tasks", json={
            "name": "Weather in Cork",
            "mode": "chat",
            "schedule_kind": "manual",
            "interval_minutes": 0,
            "tabs_required": 1,
            "active": False,
            "template_key": "weather-dublin",
            "template_data": {
                "weather_location": "Cork, Ireland",
                "temperature_threshold_c": 12,
            },
            "planner_prompt": "Run the weather task once.",
            "executor_prompt": "stale prompt that should be replaced",
            "trigger_mode": "json",
            "trigger_text": "",
        })
        assert r.status_code == 200
        task = r.json()["task"]
        chat_step = next(step for step in task["steps"] if step["kind"] == "chat")

        assert task["template_data"]["weather_location"] == "Cork, Ireland"
        assert task["template_data"]["temperature_threshold_c"] == 12.0
        assert "Cork, Ireland" in task["executor_prompt"]
        assert "above 12" in task["executor_prompt"]
        assert "Cork, Ireland" in chat_step["config"]["prompt"]
        assert "above 12" in chat_step["config"]["prompt"]

    def test_weather_task_runtime_uses_saved_location_and_threshold(self, c9_app):
        import c9_jokes.app as c9_mod

        task = _create_tasked_task(
            c9_app,
            name="Weather in Galway",
            executor_prompt="stale weather prompt",
            template_key="weather-dublin",
            template_data={
                "weather_location": "Galway, Ireland",
                "temperature_threshold_c": 9,
            },
        )

        async def fake_chat_one(agent_id, prompt, *args, **kwargs):
            assert "Galway, Ireland" in prompt
            assert "above 9" in prompt
            return {
                "ok": True,
                "http_status": 200,
                "text": json.dumps({
                    "triggered": True,
                    "trigger": "Galway weather",
                    "title": "Current weather in Galway",
                    "summary": "Threshold passed",
                    "details": {"location": "Galway, Ireland", "temperature_c": 10.0, "condition": "Cloudy"},
                }),
                "error": None,
            }

        with patch.object(c9_mod, "_chat_one", AsyncMock(side_effect=fake_chat_one)):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        assert r_run.json()["status"] == "completed"

    def test_tasked_chat_refusal_fails_run_and_does_not_create_alert(self, c9_app):
        import c9_jokes.app as c9_mod

        task = _create_tasked_task(
            c9_app,
            name="Weather refusal regression",
            executor_prompt="Check weather and return JSON",
        )

        refusal = "Sorry, it looks like I can’t respond to this. Let’s try a different topic"
        with patch.object(
            c9_mod,
            "_chat_one",
            AsyncMock(return_value={"ok": True, "http_status": 200, "text": refusal, "error": None}),
        ):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 400
        assert r_run.json()["status"] == "failed"

        r_runs = c9_app.get(f"/api/task-runs?task_id={task['id']}")
        latest_run = r_runs.json()["runs"][0]
        assert latest_run["status"] == "failed"
        assert latest_run["terminal_reason"] == "chat-failed"

        r_alerts = c9_app.get("/api/alerts?limit=200")
        alerts = [a for a in r_alerts.json()["alerts"] if a["task_id"] == task["id"]]
        assert alerts == []

        r_completed = c9_app.get(f"/api/task-completed?task_id={task['id']}")
        items = r_completed.json()["items"]
        assert len(items) == 1
        assert items[0]["run"]["status"] == "failed"
        assert items[0]["latest_alert"] is None

    def test_tasked_json_trigger_false_skips_alert_and_still_completes(self, c9_app):
        import c9_jokes.app as c9_mod

        task = _create_tasked_task(
            c9_app,
            name="Weather below threshold",
            executor_prompt="Check weather and return JSON",
        )

        with patch.object(
            c9_mod,
            "_chat_one",
            AsyncMock(return_value={
                "ok": True,
                "http_status": 200,
                "text": json.dumps({
                    "triggered": False,
                    "trigger": "Dublin weather",
                    "title": "Weather below threshold",
                    "summary": "Temperature is 8C",
                    "details": {"location": "Dublin", "temperature_c": 8.0, "condition": "Cloudy"},
                }),
                "error": None,
            }),
        ):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        assert r_run.json()["status"] == "completed"

        r_alerts = c9_app.get("/api/alerts?limit=200")
        alerts = [a for a in r_alerts.json()["alerts"] if a["task_id"] == task["id"]]
        assert alerts == []

        r_pipe = c9_app.get(f"/api/task-pipelines?task_id={task['id']}")
        pipe = r_pipe.json()["pipelines"][0]
        step_status = {step["step_kind"]: step["status"] for step in pipe["steps"]}
        assert step_status["chat"] == "completed"
        assert step_status["alert"] == "skipped"
        assert pipe["run"]["status"] == "completed"

        r_completed = c9_app.get(f"/api/task-completed?task_id={task['id']}")
        items = r_completed.json()["items"]
        assert len(items) == 1
        assert items[0]["run"]["status"] == "completed"
        assert items[0]["latest_alert"] is None

    def test_task_stop_releases_claim_and_allows_rerun(self, c9_app):
        import c9_jokes.app as c9_mod

        task = _create_tasked_task(
            c9_app,
            name="Weather stop and rerun",
            executor_prompt="Check weather and return JSON",
        )

        running_run_id = "trun_busy123"
        now = "2026-04-09T18:00:00Z"
        with c9_mod._db() as conn:
            conn.execute(
                "INSERT INTO task_runs (id, task_id, created_at, started_at, source, status, mode, trigger_snapshot_json) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    running_run_id,
                    task["id"],
                    now,
                    now,
                    "manual",
                    "running",
                    "chat",
                    json.dumps({"source": "manual"}),
                ),
            )
            conn.execute(
                "INSERT INTO task_run_claims (task_id, run_id, owner_id, source, claimed_at, expires_at) VALUES (?,?,?,?,?,?)",
                (
                    task["id"],
                    running_run_id,
                    "pytest",
                    "manual",
                    now,
                    "2026-04-09T18:15:00Z",
                ),
            )
        c9_mod._task_runner_ids.add(task["id"])

        r_stop = c9_app.post(f"/api/tasks/{task['id']}/stop")
        assert r_stop.status_code == 200
        assert r_stop.json()["status"] == "cancelled"
        assert task["id"] not in c9_mod._task_runner_ids

        with c9_mod._db() as conn:
            claim_rows = conn.execute("SELECT * FROM task_run_claims WHERE task_id=?", (task["id"],)).fetchall()
        assert claim_rows == []

        with patch.object(
            c9_mod,
            "_chat_one",
            AsyncMock(return_value={
                "ok": True,
                "http_status": 200,
                "text": json.dumps({
                    "triggered": False,
                    "trigger": "Dublin weather",
                    "title": "Weather below threshold",
                    "summary": "Temperature is 8C",
                    "details": {"location": "Dublin", "temperature_c": 8.0, "condition": "Cloudy"},
                }),
                "error": None,
            }),
        ):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        assert r_run.json()["status"] == "completed"

    def test_tasked_sandbox_success_skips_alert_with_failure_only_trigger(self, c9_app):
        import c9_jokes.app as c9_mod

        payload = {
            "name": "Sandbox success regression",
            "mode": "sandbox",
            "schedule_kind": "manual",
            "interval_minutes": 0,
            "tabs_required": 1,
            "active": False,
            "executor_target": "c12b",
            "workspace_dir": "/workspace",
            "planner_prompt": "Run the sandbox flow once and record the result.",
            "executor_prompt": "python3 smoke.py",
            "validation_command": "python3 -m py_compile smoke.py",
            "test_command": "python3 smoke.py",
            "trigger_mode": "contains",
            "trigger_text": "",
        }
        r_task = c9_app.post("/api/tasks", json=payload)
        assert r_task.status_code == 200
        task = r_task.json()["task"]

        stage_results = [
            {"stdout": "5\n", "stderr": "", "exit_code": 0, "session_id": "sess_1"},
            {"stdout": "", "stderr": "", "exit_code": 0, "session_id": "sess_1"},
            {"stdout": "5\n", "stderr": "", "exit_code": 0, "session_id": "sess_1"},
        ]
        with patch.object(c9_mod, "_c12b_exec", AsyncMock(side_effect=stage_results)):
            r_run = c9_app.post(f"/api/tasks/{task['id']}/run")

        assert r_run.status_code == 200
        assert r_run.json()["status"] == "completed"

        r_alerts = c9_app.get("/api/alerts?limit=200")
        alerts = [a for a in r_alerts.json()["alerts"] if a["task_id"] == task["id"]]
        assert alerts == []

        r_completed = c9_app.get(f"/api/task-completed?task_id={task['id']}")
        items = r_completed.json()["items"]
        assert len(items) == 1
        assert items[0]["run"]["status"] == "completed"
        assert items[0]["latest_alert"] is None

    def test_builtin_weather_template_migrates_legacy_prompt(self, c9_app):
        import c9_jokes.app as c9_mod

        c9_mod._ensure_db()
        with sqlite3.connect(c9_mod.DEFAULT_DB) as conn:
            conn.execute(
                "UPDATE task_templates SET executor_prompt=? WHERE key='weather-dublin' AND source='builtin'",
                (c9_mod.WEATHER_DUBLIN_LEGACY_PROMPT,),
            )

        c9_mod._ensure_task_templates_seeded()

        r = c9_app.get("/api/task-templates")
        assert r.status_code == 200
        weather = next(t for t in r.json()["templates"] if t["key"] == "weather-dublin")
        assert weather["executor_prompt"] == c9_mod.WEATHER_DUBLIN_PROMPT

    def test_builtin_portal_templates_migrate_legacy_prompts(self, c9_app):
        import c9_jokes.app as c9_mod

        c9_mod._ensure_db()
        with sqlite3.connect(c9_mod.DEFAULT_DB) as conn:
            conn.execute(
                "UPDATE task_templates SET executor_prompt=? WHERE key='gmail-sender' AND source='builtin'",
                (c9_mod.GMAIL_SENDER_LEGACY_PROMPT,),
            )
            conn.execute(
                "UPDATE task_templates SET executor_prompt=? WHERE key='sharepoint-new-file' AND source='builtin'",
                (c9_mod.SHAREPOINT_NEW_FILE_LEGACY_PROMPT,),
            )
            conn.execute(
                "UPDATE task_templates SET executor_prompt=? WHERE key='m365-outlook-alert' AND source='builtin'",
                (c9_mod.M365_OUTLOOK_ALERT_LEGACY_PROMPT,),
            )
            conn.execute(
                "UPDATE task_templates SET executor_prompt=? WHERE key='outlook-sharepoint-linked' AND source='builtin'",
                (c9_mod.OUTLOOK_SHAREPOINT_LINKED_LEGACY_PROMPT,),
            )
            conn.execute(
                "UPDATE task_templates SET trigger_mode=?, trigger_text=? WHERE key='sandbox-python-validate' AND source='builtin'",
                (c9_mod.SANDBOX_VALIDATE_LEGACY_TRIGGER_MODE, c9_mod.SANDBOX_VALIDATE_LEGACY_TRIGGER_TEXT),
            )

        c9_mod._ensure_task_templates_seeded()

        r = c9_app.get("/api/task-templates")
        assert r.status_code == 200
        templates = {t["key"]: t for t in r.json()["templates"]}
        assert templates["gmail-sender"]["executor_prompt"] == c9_mod.GMAIL_SENDER_PROMPT
        assert templates["sharepoint-new-file"]["executor_prompt"] == c9_mod.SHAREPOINT_NEW_FILE_PROMPT
        assert templates["m365-outlook-alert"]["executor_prompt"] == c9_mod.M365_OUTLOOK_ALERT_PROMPT
        assert templates["outlook-sharepoint-linked"]["executor_prompt"] == c9_mod.OUTLOOK_SHAREPOINT_LINKED_PROMPT
        assert templates["sandbox-python-validate"]["trigger_mode"] == c9_mod.SANDBOX_VALIDATE_TRIGGER_MODE
        assert templates["sandbox-python-validate"]["trigger_text"] == c9_mod.SANDBOX_VALIDATE_TRIGGER_TEXT


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
        assert body["badge_label"] == "C3 Pool Busy"
        assert body["components"]["c3_pool"]["state"] == "saturated"
        assert "saturated" in body["summary"].lower()

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
