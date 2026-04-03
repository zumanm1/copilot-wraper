# C9 Task ID Traceability Guide
## Tasked → piplinetask → Alerts → TaskCompleted → Preview

> **Purpose:** Step-by-step guide to creating a task and tracing its unique IDs
> across all 4 monitoring pages. Every action, every ID, every API call explained.

---

## 1. The Four IDs You Need to Know

| ID | Format | Where It Lives | What It Identifies |
|----|--------|---------------|--------------------|
| `task_id` | `task_xxxxxxxx` | `task_definitions` table | The task definition (permanent) |
| `run_id` | `trun_xxxxxxxx` | `task_runs` table | One execution of a task |
| `alert_id` | Integer (e.g. `353`) | `task_alerts` table | One alert fired by a run |
| `step_id` | `<task_id>_<name>` | `task_workflow_steps` | One step in the workflow |

> **Rule:** `task_id` is the anchor. Every `run_id`, `alert_id`, and `step_id`
> links back to it via a foreign key.

---

## 2. System Architecture (Containers)

```
Browser ──► localhost:6090 (C9b_jokes)
                │
                ├── SQLite DB  /app/data/c9.db
                ├── POST /api/tasks/{id}/run
                │       │
                │       ├── mode=chat   → POST C1b:8000/v1/chat/completions
                │       ├── mode=sandbox→ POST C12b:8210/execute
                │       └── mode=agent  → POST C10/C11 agent APIs
                │
                └── C3b:6080  (noVNC browser — M365 cookies for C1b)
```

**Port Map:**
| Port | Container | Role |
|------|-----------|------|
| 6090 | C9b_jokes | Main console — all 4 pages live here |
| 6080 | C3b_browser-auth | noVNC for M365 login |
| 8000 | C1b_copilot-api | Chat LLM (OpenAI-compatible) |
| 8210 | C12b_sandbox | Python/shell execution for sandbox tasks |

---

## 3. Step-by-Step: Create a Task and Trace It

### STEP 1 — Open Tasked (`localhost:6090/tasked`)

The left panel is the **builder form**. The right panel is the **task table**.

**Fill in the builder:**

| Field | Element | Value |
|-------|---------|-------|
| Task Name | `#task-name` | e.g. `"My Trace Task"` |
| Mode | `#task-mode` | `chat` (simplest — uses C1b) |
| Output Type | `#task-tasked-type` | `output` |
| Schedule | `#task-schedule-kind` | `manual` |
| Planner Prompt | `#task-planner` | Your question/instruction |
| Executor Prompt | `#task-executor` | Role/personality for the LLM |
| Alert Trigger | `#task-trigger-mode` | `always` (guarantees an alert fires) |
| Alert Severity | `#task-alert-severity` | `info` |

**Add Workflow Steps** (click `+ Add Step`):

```
Step 1: kind=trigger   name="Trigger"       → marks schedule start
Step 2: kind=chat      name="Execute Chat"  → runs LLM via C1b
Step 3: kind=alert     name="Create Alert"  → fires alert record
Step 4: kind=complete  name="Complete"      → marks run done
```

**Click Save Tasked** → API call:
```
POST /api/tasks
Body: { name, mode, schedule_kind, planner_prompt, executor_prompt,
        trigger_mode, alert_policy_json, steps: [...] }
Response: { ok: true, task: { id: "task_xxxxxxxx", ... } }
```
**You now have your `task_id`** — copy it.

---

### STEP 2 — Run the Task

Click **Run Now** on the task row (or the Save form's Run button):
```
POST /api/tasks/{task_id}/run
Response: { ok: true, run_id: "trun_xxxxxxxx", status: "completed"|"running" }
```

**State transitions in DB:**
```
task_runs.status: queued → running → completed | failed | alert-open | cancelled
task_definitions.last_status: updated to final status
```

**You now have your `run_id`** — copy it.

---

### STEP 3 — Trace on Piplinetask (`localhost:6090/piplinetask`)

The pipeline page is **run-centric** — it shows every step and event for a run.

**Navigate:** Paste your `task_id` into the Task ID filter box → Refresh.

Or use the direct URL:
```
http://localhost:6090/piplinetask?task_id=task_xxxxxxxx
http://localhost:6090/piplinetask?run_id=trun_xxxxxxxx
```

**API call (JS makes this automatically):**
```
GET /api/task-pipelines?task_id=task_xxxxxxxx
Response: {
  ok: true,
  pipelines: [{
    task: { id, name, mode, schedule_kind, ... },
    run:  { id: "trun_...", status, terminal_reason, duration_label,
            preview_url: "/tasked-preview?task_id=X&run_id=Y" },
    step_results: [{ step_id, status, duration_label, output }, ...],
    alerts: [{ id, title, severity, status }, ...],
    feedback: [...],
    recovery_sessions: [...],
    trace: { orchestration, planner, timer, executor, alert_generator,
             completion, recovery }
  }]
}
```

**What you see on the page:**
- Status badge (running=blue pulse, completed=green, failed=red)
- Step flow diagram (nodes connected by arrows)
- Trace grid (9 panels: orchestrator, planner, timer, executor, assist, alert gen, completion, session manager)
- Step results (each step's output, timing, status)
- Agent feedback (if agent mode)
- Timeline (ordered event log)

---

### STEP 4 — Trace on Alerts (`localhost:6090/alerts`)

Alerts are created when `trigger_mode=always` or when sandbox output contains `{"triggered": true}`.

**Navigate:** Filter by Status = `open`.

Or direct URL (no built-in filter param — use the UI filter):
```
http://localhost:6090/alerts
```

**API call:**
```
GET /api/alerts?limit=250
Response: {
  ok: true,
  alerts: [{
    id: 353,
    task_id: "task_xxxxxxxx",
    run_id:  "trun_xxxxxxxx",       ← links back to your run
    title, severity, status, summary, trigger_text,
    created_at, acknowledged_at, resolved_at,
    preview_url: "/tasked-preview?task_id=X&run_id=Y",  ← NEW: includes run_id
    task_url, pipeline_url, completed_url
  }, ...]
}
```

**Alert lifecycle actions (buttons on each card):**
| Action | API Call | New Status |
|--------|----------|-----------|
| Ack | `POST /api/alerts/{id}/status` `{status:"acknowledged"}` | acknowledged |
| Resolve | `POST /api/alerts/{id}/status` `{status:"resolved"}` | resolved |
| Snooze 30m | `POST /api/alerts/{id}/status` `{status:"snoozed", snooze_minutes:30}` | snoozed |
| Reopen | `POST /api/alerts/{id}/status` `{status:"open"}` | open |

---

### STEP 5 — Trace on Task Completed (`localhost:6090/task-completed`)

This page shows only **terminal runs**: `completed`, `failed`, `cancelled`.

**Navigate:** Paste your `task_id` in the filter box, or use:
```
http://localhost:6090/task-completed?task_id=task_xxxxxxxx
```

**API call:**
```
GET /api/task-completed?task_id=task_xxxxxxxx
Response: {
  ok: true,
  items: [{
    run:  { id: "trun_...", status: "completed", terminal_reason: "workflow-complete",
            duration_label: "18s",
            preview_url: "/tasked-preview?task_id=X&run_id=Y",  ← run-specific
            task_url, pipeline_url, completed_url },
    latest_alert: { id, status, severity },
    latest_recovery_session: { status, last_error },
    feedback: [...]
  }]
}
```

**Actions available:**
| Button | API Call |
|--------|----------|
| Redo | `POST /api/tasks/{task_id}/redo` |
| Clone Task | `POST /api/tasks/{task_id}/clone` |
| Archive | `POST /api/tasks/{task_id}/archive` |
| Open Pipeline | links to `/piplinetask?task_id=X` |
| Preview Output | links to `/tasked-preview?task_id=X&run_id=Y` |

---

### STEP 6 — Preview Output (`localhost:6090/tasked-preview`)

The preview page shows the **compiled output** of a specific run.

**URL format (with specific run):**
```
http://localhost:6090/tasked-preview?task_id=task_xxxxxxxx&run_id=trun_xxxxxxxx
```

**API call:**
```
GET /api/task-preview?task_id=task_xxxxxxxx&run_id=trun_xxxxxxxx
Response: {
  ok: true,
  task:         { full task definition },
  run:          { full run record },
  step_results: [{ step_id, status, output, duration_label }, ...],
  recent_runs:  [last 20 runs for this task],
  output_text:  "compiled output from all steps"
}
```

---

## 4. Full ID Relationship Map

```
task_definitions
└── id = task_xxxxxxxx          ← YOUR ANCHOR ID
    ├── task_workflow_steps
    │   └── step_id = task_xxx_step_1 (trigger, chat, alert, complete)
    │
    └── task_runs
        └── id = trun_xxxxxxxx  ← RUN ID (one per execution)
            ├── task_step_results
            │   └── step_id → references task_workflow_steps
            │
            ├── task_alerts
            │   └── id = 353    ← ALERT ID
            │       └── run_id = trun_xxxxxxxx
            │
            ├── task_feedback_events
            └── session_manager_sessions (recovery)
```

---

## 5. Cross-Page Navigation (URL Matrix)

| From Page | To Page | URL Pattern |
|-----------|---------|-------------|
| Tasked | Piplinetask | `/piplinetask?task_id=task_xxx` |
| Tasked | Alerts | `/alerts` (then filter) |
| Tasked | Task Completed | `/task-completed?task_id=task_xxx` |
| Tasked | Preview | `/tasked-preview?task_id=task_xxx` |
| Piplinetask | Preview | `/tasked-preview?task_id=X&run_id=Y` |
| Alerts | Preview | `/tasked-preview?task_id=X&run_id=Y` ← includes run_id |
| Task Completed | Preview | `/tasked-preview?task_id=X&run_id=Y` ← includes run_id |
| Any | Tasked (edit) | `/tasked?task_id=task_xxx` |

---

## 6. API Quick Reference

```bash
# List all tasks
curl http://localhost:6090/api/tasks

# Create a task
curl -X POST http://localhost:6090/api/tasks -H "Content-Type: application/json" \
  -d '{"name":"My Task","mode":"chat","schedule_kind":"manual","planner_prompt":"...","executor_prompt":"...","trigger_mode":"always","steps":[...]}'

# Run a task
curl -X POST http://localhost:6090/api/tasks/{task_id}/run

# Check pipeline (all runs)
curl "http://localhost:6090/api/task-pipelines?task_id={task_id}"

# Check specific run pipeline
curl "http://localhost:6090/api/task-pipelines?run_id={run_id}"

# Check alerts for a task
curl "http://localhost:6090/api/alerts" | jq '.alerts[] | select(.task_id=="{task_id}")'

# Acknowledge an alert
curl -X POST http://localhost:6090/api/alerts/{alert_id}/status \
  -H "Content-Type: application/json" -d '{"status":"acknowledged"}'

# Check completed runs
curl "http://localhost:6090/api/task-completed?task_id={task_id}"

# Preview a specific run
curl "http://localhost:6090/api/task-preview?task_id={task_id}&run_id={run_id}"
```

---

## 7. Smoke Test Result (Validation Proof)

The following trace was validated live against C9b on 2026-04-03:

| Step | ID | Result |
|------|----|--------|
| Task Created | `task_7ee61f8e` | ✅ Smoke Test — C9b Trace Task |
| Run Executed | `trun_d17766f2` | ✅ status=completed, terminal=workflow-complete |
| Pipeline | 1 pipeline item | ✅ 4 step_results, duration=18s |
| Alert | `alert_id=353` | ✅ severity=info, status=open |
| Task Completed | 1 terminal run | ✅ duration=18s |
| Preview URL | `?task_id=task_7ee61f8e&run_id=trun_d17766f2` | ✅ output_text contains LLM response |
| Output | `"Why do Python programmers prefer dark mode?` | ✅ C1b responded via C3b M365 |

---

## 8. Bugs Fixed During This Session

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `piplinetask.html` | Missing `}` closing `loadPipelines()` — entire init block (event listeners, timers, self-call) was trapped inside the function. Caused: duplicate listeners on every refresh, timer leaks, recursive self-call | Added `}` after `finally { pipelineIsLoading = false; }` |
| 2 | `tasked.html` | `stepKindIcon()` defined twice (identical dead copy) | Removed duplicate |
| 3 | `tasked.html` | `renderWorkflowDiagram(steps)` first definition dead code — overridden by second definition that uses `state.steps` directly; call site passed param that was ignored | Removed dead first definition, unified all call sites to `renderWorkflowDiagram()` |
| 4 | `app.py` | `preview_url` in run records used only `task_id` — always opened latest run instead of the specific run | Added `&run_id=trun_xxx` to `_task_run_to_dict` |
| 5 | `app.py` | Same `preview_url` missing `run_id` in alert records | Added `&run_id=` from `alert.run_id` in `_task_alert_to_dict` |

---

## 9. Container Health Checklist

Before testing, verify:
```bash
docker ps --filter "name=C9b" --format "{{.Names}}: {{.Status}}"
# Expected: C9b_jokes: Up N minutes (healthy)

curl http://localhost:6090/api/runtime-status | jq '.components | to_entries[] | "\(.key): \(.value.state)"'
# Expected: c1:ok, c3:ok, c12b:ok
```

After any `app.py` change, restart C9b (templates reload automatically):
```bash
docker restart C9b_jokes
```
