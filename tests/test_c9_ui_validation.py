"""
Comprehensive UI/UX validation test for C9 pages.
Tests: Tasked, Pipeline, Alerts, TaskCompleted, Chat pages.
Validates: API endpoints, data flow, UI rendering, cascading task execution.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:6090")
SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"

results = {"passed": 0, "failed": 0, "warnings": 0, "details": []}


def record(status, test_name, detail=""):
    icon = {True: PASS, False: FAIL}.get(status, WARN)
    results["passed" if status is True else "failed" if status is False else "warnings"] += 1
    results["details"].append({"status": status, "test": test_name, "detail": detail})
    print(f"  {icon} {test_name}" + (f": {detail}" if detail else ""))


async def test_api_endpoint(client, path, test_name, expected_status=200):
    """Test an API endpoint returns expected status."""
    try:
        r = await client.get(f"{BASE_URL}{path}", timeout=10)
        ok = r.status_code == expected_status
        record(ok, test_name, f"HTTP {r.status_code}" if not ok else None)
        return r
    except Exception as e:
        record(False, test_name, str(e))
        return None


async def test_post_endpoint(client, path, payload, test_name, expected_status=200):
    """Test a POST API endpoint."""
    try:
        r = await client.post(f"{BASE_URL}{path}", json=payload, timeout=30)
        ok = r.status_code == expected_status
        record(ok, test_name, f"HTTP {r.status_code}" if not ok else None)
        return r
    except Exception as e:
        record(False, test_name, str(e))
        return None


async def main():
    print("\n" + "=" * 60)
    print("C9 UI/UX Validation Test Suite")
    print(f"Target: {BASE_URL}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    async with httpx.AsyncClient() as client:

        # ── 1. Core API Health ──────────────────────────────────────────
        print("\n[1] Core API Health")
        await test_api_endpoint(client, "/health", "GET /health", 200)
        await test_api_endpoint(client, "/api/status", "GET /api/status", 200)
        await test_api_endpoint(client, "/api/session-health", "GET /api/session-health", 200)

        # ── 2. Chat API ─────────────────────────────────────────────────
        print("\n[2] Chat API")
        r = await test_post_endpoint(client, "/api/chat", {
            "agent_id": "c6-kilocode",
            "prompt": "Say hello in one word.",
            "stream": False
        }, "POST /api/chat (C6 KiloCode)", 200)
        if r and r.status_code == 200:
            data = r.json()
            record(data.get("ok"), "Chat response OK", data.get("text", "")[:60])
            record(data.get("session_id") is not None, "Session ID returned", data.get("session_id"))
            record(data.get("token_estimate") is not None, "Token estimate returned", str(data.get("token_estimate")))

        # Chat sessions
        r = await test_api_endpoint(client, "/api/chat/sessions?limit=3", "GET /api/chat/sessions", 200)
        if r and r.status_code == 200:
            sessions = r.json()
            record(isinstance(sessions, list), "Sessions is array", f"{len(sessions)} sessions")

        # ── 3. Tasks API ────────────────────────────────────────────────
        print("\n[3] Tasks API")
        r = await test_api_endpoint(client, "/api/tasks", "GET /api/tasks", 200)
        if r and r.status_code == 200:
            data = r.json()
            tasks = data.get("tasks", [])
            record(isinstance(tasks, list), "Tasks is array", f"{len(tasks)} tasks")
            cascading = [t for t in tasks if "Cascading" in (t.get("name") or "")]
            record(len(cascading) > 0, "Cascading task exists", cascading[0]["id"] if cascading else "not found")

        # Task runs
        r = await test_api_endpoint(client, "/api/task-runs?limit=5", "GET /api/task-runs", 200)
        if r and r.status_code == 200:
            data = r.json()
            runs = data.get("runs", [])
            record(isinstance(runs, list), "Runs is array", f"{len(runs)} runs")

        # Task pipelines
        r = await test_api_endpoint(client, "/api/task-pipelines?limit=3", "GET /api/task-pipelines", 200)
        if r and r.status_code == 200:
            data = r.json()
            pipelines = data.get("pipelines", [])
            record(isinstance(pipelines, list), "Pipelines is array", f"{len(pipelines)} pipelines")
            if pipelines:
                p = pipelines[0]
                record("task" in p, "Pipeline has task data", None)
                record("run" in p, "Pipeline has run data", None)
                record("steps" in p, "Pipeline has steps data", f"{len(p.get('steps', []))} steps")
                record("feedback" in p, "Pipeline has feedback data", None)

        # ── 4. Alerts API ───────────────────────────────────────────────
        print("\n[4] Alerts API")
        r = await test_api_endpoint(client, "/api/alerts?limit=5", "GET /api/alerts", 200)
        if r and r.status_code == 200:
            data = r.json()
            alerts = data.get("alerts", [])
            record(isinstance(alerts, list), "Alerts is array", f"{len(alerts)} alerts")
            if alerts:
                a = alerts[0]
                record("id" in a, "Alert has ID", str(a["id"]))
                record("status" in a, "Alert has status", a.get("status"))
                record("severity" in a, "Alert has severity", a.get("severity"))
                record("task_name" in a, "Alert has task_name", a.get("task_name", "")[:40])

        # ── 5. Task Completed API ───────────────────────────────────────
        print("\n[5] Task Completed API")
        r = await test_api_endpoint(client, "/api/task-completed?limit=5", "GET /api/task-completed", 200)
        if r and r.status_code == 200:
            data = r.json()
            items = data.get("items", [])
            record(isinstance(items, list), "Completed items is array", f"{len(items)} items")
            if items:
                item = items[0]
                record("run" in item, "Item has run data", None)
                record("task_name" in item, "Item has task_name", item.get("task_name", "")[:40])

        # ── 6. Task Preview API ─────────────────────────────────────────
        print("\n[6] Task Preview API")
        r = await test_api_endpoint(client, "/api/tasks", "GET /api/tasks for preview", 200)
        if r and r.status_code == 200:
            tasks = r.json().get("tasks", [])
            if tasks:
                task_id = tasks[0]["id"]
                r2 = await test_api_endpoint(client, f"/api/task-preview?task_id={task_id}",
                                             "GET /api/task-preview", 200)
                if r2 and r2.status_code == 200:
                    data = r2.json()
                    record(data.get("ok"), "Preview OK", None)
                    record("task" in data, "Preview has task data", None)
                    record("step_results" in data, "Preview has step_results", None)
                    record("alerts" in data, "Preview has alerts", None)

        # ── 7. Sandbox API ──────────────────────────────────────────────
        print("\n[7] Sandbox API (C12b)")
        r = await test_post_endpoint(client, "/api/sandbox/exec", {
            "command": "echo 'sandbox test' && python3 --version",
            "sandbox": "c12b",
            "timeout": 10
        }, "POST /api/sandbox/exec (C12b)", 200)
        if r and r.status_code == 200:
            data = r.json()
            record(data.get("exit_code") == 0, "Sandbox exec succeeded",
                   f"exit_code={data.get('exit_code')}")

        # ── 8. Runtime Status ───────────────────────────────────────────
        print("\n[8] Runtime Status")
        r = await test_api_endpoint(client, "/api/runtime-status?force=true",
                                    "GET /api/runtime-status", 200)
        if r and r.status_code == 200:
            data = r.json()
            components = data.get("components", {})
            for key in ["c1", "c3", "c12b"]:
                comp = components.get(key, {})
                record(comp.get("ok") or comp.get("state") in ("ok", "active"),
                       f"Component {key} healthy", comp.get("state") or comp.get("message", ""))

        # ── 9. Cascading Task Validation ────────────────────────────────
        print("\n[9] Cascading Task Validation")
        r = await test_api_endpoint(client, "/api/tasks", "GET tasks for cascading check", 200)
        if r and r.status_code == 200:
            tasks = r.json().get("tasks", [])
            cascading = [t for t in tasks if "Cascading" in (t.get("name") or "")]
            if cascading:
                task = cascading[0]
                record(task.get("tasked_type") == "output", "Task type is output", task.get("tasked_type"))
                record(task.get("mode") == "chat", "Task mode is chat", task.get("mode"))
                record(len(task.get("steps", [])) == 4, "Task has 4 steps",
                       f"{len(task.get('steps', []))} steps")
                steps = task.get("steps", [])
                step_kinds = [s.get("kind") for s in steps]
                record("trigger" in step_kinds, "Has trigger step", None)
                record("sandbox" in step_kinds, "Has sandbox step", None)
                record("chat" in step_kinds, "Has chat step", None)
                record("alert" in step_kinds, "Has alert step", None)

        # ── 10. Page Rendering (HTML) ───────────────────────────────────
        print("\n[10] Page Rendering (HTML)")
        pages = [
            ("/", "Dashboard"),
            ("/chat", "Chat"),
            ("/tasked", "Tasked"),
            ("/piplinetask", "Pipeline"),
            ("/alerts", "Alerts"),
            ("/task-completed", "TaskCompleted"),
            ("/tasked-preview", "Tasked Preview"),
            ("/logs", "Logs"),
            ("/sessions", "Sessions"),
            ("/token-counter", "Token Counter"),
        ]
        for path, name in pages:
            try:
                r = await client.get(f"{BASE_URL}{path}", timeout=10, follow_redirects=True)
                ok = r.status_code == 200 and "html" in r.headers.get("content-type", "")
                record(ok, f"GET {path} ({name})", f"HTTP {r.status_code}" if not ok else None)
            except Exception as e:
                record(False, f"GET {path} ({name})", str(e))

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = results["passed"] + results["failed"] + results["warnings"]
    print(f"Results: {PASS} {results['passed']} passed | {FAIL} {results['failed']} failed | {WARN} {results['warnings']} warnings | Total: {total}")
    print("=" * 60)

    # Write results to file
    output_file = SCREENSHOT_DIR / f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
