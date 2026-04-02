import json
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:6090"


def req(method: str, path: str, data: dict | None = None) -> dict:
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with {exc.code}: {payload}") from exc


cases = [
    {
        "name": "Live C12 Sandbox Smoke",
        "mode": "sandbox",
        "schedule_kind": "manual",
        "executor_target": "c12",
        "workspace_dir": "/home/gem",
        "trigger_mode": "always",
        "trigger_text": "sandbox execution",
        "executor_prompt": "printf 'print(\"live c12 ok\")\\n' > live_c12.py && python3 live_c12.py",
        "validation_command": "python3 -m py_compile live_c12.py",
        "test_command": "python3 live_c12.py",
    },
    {
        "name": "Live C12b Sandbox Smoke",
        "mode": "sandbox",
        "schedule_kind": "manual",
        "executor_target": "c12b",
        "workspace_dir": "/workspace",
        "trigger_mode": "always",
        "trigger_text": "sandbox execution",
        "executor_prompt": "printf 'print(\"live c12b ok\")\\n' > live_c12b.py && python3 live_c12b.py",
        "validation_command": "python3 -m py_compile live_c12b.py",
        "test_command": "python3 live_c12b.py",
    },
]


def main() -> int:
    results = []
    all_alerts = req("GET", "/api/alerts")["alerts"]
    for payload in cases:
        saved = req("POST", "/api/tasks", payload)
        task = saved["task"]
        run = req("POST", f"/api/tasks/{task['id']}/run")
        runs = req("GET", f"/api/task-runs?task_id={task['id']}")["runs"]
        pipelines = req("GET", f"/api/task-pipelines?task_id={task['id']}")["pipelines"]
        alerts = [a for a in all_alerts if a.get("task_id") == task["id"]]
        if not alerts:
            alerts = [a for a in req("GET", "/api/alerts")["alerts"] if a.get("task_id") == task["id"]]
        results.append(
            {
                "task_id": task["id"],
                "name": task["name"],
                "target": task["executor_target"],
                "run_status": run.get("status"),
                "run_ok": run.get("ok"),
                "alert_id": run.get("alert_id"),
                "run_text": run.get("text"),
                "latest_run": runs[0] if runs else None,
                "pipeline_event_kinds": [e["kind"] for e in (pipelines[0]["events"] if pipelines else [])],
                "alert": alerts[0] if alerts else None,
            }
        )
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
