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

---

## 10. Five Demo Tasks — All 5 `tasked_type` Variants

Created and run 2026-04-04. These cover every supported `tasked_type`.

### T1 — Output (chat mode)

**What it does:** LLM produces readable text output (bullet points via C1b Copilot API).

| Field | Value |
|-------|-------|
| `task_id` | `task_3f950f6e` |
| `tasked_type` | `output` |
| `mode` | `chat` |
| `schedule_kind` | `manual` |
| `planner_prompt` | `"Produce a 3-bullet summary of the top 3 benefits of using async Python for API servers."` |
| `executor_prompt` | `"You are a concise technical writer. Produce exactly 3 numbered bullet points, max 20 words each."` |
| `trigger_mode` | `always` |

**Run record:**
| Field | Value |
|-------|-------|
| `run_id` | `trun_32293732` |
| `alert_id` | `364` |
| `status` | `completed` |
| `terminal_reason` | `workflow-complete` |

**Redo run record (lifecycle demo):**
| `run_id` | `trun_1a6a8905` | `alert_id` | `424` |

**Traceability URLs:**
```
Tasked:    http://localhost:6090/tasked?task_id=task_3f950f6e
Pipeline:  http://localhost:6090/piplinetask?task_id=task_3f950f6e
Preview:   http://localhost:6090/tasked-preview?task_id=task_3f950f6e&run_id=trun_32293732
Completed: http://localhost:6090/task-completed?task_id=task_3f950f6e
```

**To recreate:**
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "T1 — Daily LLM Output Summary",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "output",
    "planner_prompt": "Produce a 3-bullet summary of the top 3 benefits of using async Python for API servers.",
    "executor_prompt": "You are a concise technical writer. Produce exactly 3 numbered bullet points, max 20 words each.",
    "context_handoff": "Output-type task: generates readable LLM text output",
    "trigger_mode": "always",
    "steps": [
      {"id":"t1_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"t1_chat","name":"Generate Summary","kind":"chat","position":2,"config":{"prompt":"List 3 benefits of async Python for APIs"}},
      {"id":"t1_alert","name":"Output Alert","kind":"alert","position":3,"config":{"title":"T1 Output Ready","severity":"info","summary":"LLM summary generated successfully"}},
      {"id":"t1_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
# Then run it:
curl -s -X POST "http://localhost:6090/api/tasks/{NEW_TASK_ID}/run"
```

---

### T2 — Alert (chat mode)

**What it does:** Evaluates a condition via LLM and fires an alert based on the JSON result.

| Field | Value |
|-------|-------|
| `task_id` | `task_21dffb26` |
| `tasked_type` | `alert` |
| `mode` | `chat` |
| `planner_prompt` | `"Check: is the value 42 greater than the limit of 10? Answer only JSON: {\"triggered\": true/false, \"reason\": \"...\"}"` |
| `executor_prompt` | `"You are a condition evaluator. Respond with a brief explanation confirming or denying the condition."` |
| `trigger_mode` | `json` |

**Run record:**
| Field | Value |
|-------|-------|
| `run_id` | `trun_bcdaa631` |
| `alert_id` | `420` |
| `status` | `completed` |
| `terminal_reason` | `workflow-complete` |

**Traceability URLs:**
```
Tasked:    http://localhost:6090/tasked?task_id=task_21dffb26
Pipeline:  http://localhost:6090/piplinetask?task_id=task_21dffb26
Preview:   http://localhost:6090/tasked-preview?task_id=task_21dffb26&run_id=trun_bcdaa631
Completed: http://localhost:6090/task-completed?task_id=task_21dffb26
```

**To recreate:**
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "T2 — Alert: Threshold Condition Check",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "alert",
    "planner_prompt": "Check: is the value 42 greater than the limit of 10? Answer only JSON: {\"triggered\": true, \"reason\": \"42 exceeds limit of 10\"}",
    "executor_prompt": "You are a condition evaluator. Respond with a brief explanation confirming or denying the condition.",
    "context_handoff": "Alert-type task: evaluates conditions and fires alerts via JSON trigger",
    "trigger_mode": "json",
    "steps": [
      {"id":"t2b_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"t2b_condition","name":"Condition Check","kind":"chat","position":2,"config":{"prompt":"Is value 42 > limit 10?"}},
      {"id":"t2b_alert","name":"Threshold Alert","kind":"alert","position":3,"config":{"title":"T2 Threshold Exceeded","severity":"warning","summary":"Value 42 exceeds limit of 10"}},
      {"id":"t2b_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
```

---

### T3 — Action (sandbox mode via C12b)

**What it does:** Executes shell commands in the C12b sandbox, validates output, fires alert on completion.

| Field | Value |
|-------|-------|
| `task_id` | `task_00843dbb` |
| `tasked_type` | `action` |
| `mode` | `sandbox` |
| `executor_target` | `C12b` |
| `planner_prompt` | `"Run: date && ls /workspace | wc -l"` |
| `trigger_mode` | `always` |

**Run record:**
| Field | Value |
|-------|-------|
| `run_id` | `trun_5468fcc6` |
| `alert_id` | `421` |
| `status` | `completed` |
| `terminal_reason` | `workflow-complete` |
| `output` | `ACTION_DONE\nSat Apr  4 08:35:00 UTC 2026\nfiles=13\nvalidation_ok` |

**Restart run record:**
| `run_id` | `trun_e37fb4d1` | `alert_id` | `425` |

**Traceability URLs:**
```
Tasked:    http://localhost:6090/tasked?task_id=task_00843dbb
Pipeline:  http://localhost:6090/piplinetask?task_id=task_00843dbb
Preview:   http://localhost:6090/tasked-preview?task_id=task_00843dbb&run_id=trun_5468fcc6
Completed: http://localhost:6090/task-completed?task_id=task_00843dbb
```

**To recreate:**
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "T3 — Action: Sandbox Shell Execution",
    "mode": "sandbox",
    "schedule_kind": "manual",
    "tasked_type": "action",
    "planner_prompt": "Run a shell action: echo ACTION_DONE && date && echo files=$(ls /workspace | wc -l)",
    "executor_prompt": "Execute in sandbox and report: date, file count, validation status.",
    "context_handoff": "Action-type task: executes shell commands in C12b sandbox",
    "trigger_mode": "always",
    "executor_target": "C12b",
    "workspace_dir": "/workspace",
    "validation_command": "echo validation_ok",
    "steps": [
      {"id":"t3_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"t3_sandbox","name":"Shell Action","kind":"sandbox","position":2,"config":{"command":"echo ACTION_DONE && date && echo files=$(ls /workspace | wc -l)"}},
      {"id":"t3_alert","name":"Action Alert","kind":"alert","position":3,"config":{"title":"T3 Action Completed","severity":"info","summary":"Shell action executed in C12b sandbox"}},
      {"id":"t3_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
```

---

### T4 — Hook (chat mode, simulates external webhook trigger)

**What it does:** Simulates an external system webhook by generating a JSON deployment payload via LLM.

| Field | Value |
|-------|-------|
| `task_id` | `task_814e3f3b` |
| `tasked_type` | `hook` |
| `mode` | `chat` |
| `planner_prompt` | `"Simulate sending a webhook payload. Generate a JSON webhook body for a deployment event."` |
| `trigger_mode` | `always` |

**Run record:**
| Field | Value |
|-------|-------|
| `run_id` | `trun_96a073f5` |
| `alert_id` | `422` |
| `status` | `completed` |
| `terminal_reason` | `workflow-complete` |

**Traceability URLs:**
```
Tasked:    http://localhost:6090/tasked?task_id=task_814e3f3b
Pipeline:  http://localhost:6090/piplinetask?task_id=task_814e3f3b
Preview:   http://localhost:6090/tasked-preview?task_id=task_814e3f3b&run_id=trun_96a073f5
Completed: http://localhost:6090/task-completed?task_id=task_814e3f3b
```

**To recreate:**
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "T4 — Hook: External System Trigger",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "hook",
    "planner_prompt": "Simulate sending a webhook payload. Generate a JSON webhook body for a deployment event.",
    "executor_prompt": "You are a webhook dispatcher. Respond with a JSON webhook payload for a deployment event.",
    "context_handoff": "Hook-type task: simulates triggering external systems via webhook",
    "trigger_mode": "always",
    "steps": [
      {"id":"t4_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"t4_hook","name":"Webhook Dispatch","kind":"chat","position":2,"config":{"prompt":"Generate deployment webhook JSON payload"}},
      {"id":"t4_alert","name":"Hook Alert","kind":"alert","position":3,"config":{"title":"T4 Hook Dispatched","severity":"info","summary":"External webhook payload generated and dispatched"}},
      {"id":"t4_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
```

---

### T5 — Combined (all trigger types in one task)

**What it does:** Combines output generation, alert firing, and structured recommendations — exercises all trigger mechanisms in a single run.

| Field | Value |
|-------|-------|
| `task_id` | `task_80986663` |
| `tasked_type` | `combined` |
| `mode` | `chat` |
| `planner_prompt` | `"Generate a combined report with SUMMARY, RECOMMENDED_ACTION, and SEVERITY sections."` |
| `trigger_mode` | `always` |

**Run record:**
| Field | Value |
|-------|-------|
| `run_id` | `trun_6b571b78` |
| `alert_id` | `423` |
| `status` | `completed` |
| `terminal_reason` | `workflow-complete` |

**Traceability URLs:**
```
Tasked:    http://localhost:6090/tasked?task_id=task_80986663
Pipeline:  http://localhost:6090/piplinetask?task_id=task_80986663
Preview:   http://localhost:6090/tasked-preview?task_id=task_80986663&run_id=trun_6b571b78
Completed: http://localhost:6090/task-completed?task_id=task_80986663
```

**To recreate:**
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "T5 — Combined: Full Pipeline (Output + Alert + Action)",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "combined",
    "planner_prompt": "Generate a combined report with SUMMARY, RECOMMENDED_ACTION, and SEVERITY sections for a system incident.",
    "executor_prompt": "You are an incident analyst. Structure your response with clearly labeled SUMMARY, RECOMMENDED_ACTION, and SEVERITY sections.",
    "context_handoff": "Combined-type task: exercises output, alert, and action paths in one run",
    "trigger_mode": "always",
    "steps": [
      {"id":"t5_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"t5_chat","name":"Generate Report","kind":"chat","position":2,"config":{"prompt":"Generate incident report with SUMMARY, RECOMMENDED_ACTION, SEVERITY"}},
      {"id":"t5_alert","name":"Combined Alert","kind":"alert","position":3,"config":{"title":"T5 Combined Report Ready","severity":"high","summary":"Full pipeline report generated with output, alert, and action steps"}},
      {"id":"t5_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
```

---

## 11. Full Task Lifecycle Operations

All operations are on C9b at `http://localhost:6090`. These can be called from the UI buttons or directly via API.

### Create
```bash
curl -X POST http://localhost:6090/api/tasks \
  -H "Content-Type: application/json" \
  -d '{...task definition...}'
# Returns: { ok: true, task: { id: "task_xxxxxxxx", ... } }
```

### Edit (update fields)
```bash
curl -X POST http://localhost:6090/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"id":"task_xxxxxxxx","name":"Updated Name","notes":"Updated notes"}'
# Returns: { ok: true, task: { id, name, ... } }
```

### Run (trigger execution)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/run
# Returns: { ok: true, run_id: "trun_xxxxxxxx", status: "completed"|"running", alert_id: N }
```

### Redo (re-run — creates new run record)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/redo
# Returns: same as /run — new run_id, new alert_id
# Demo: T1 redo → run_id=trun_1a6a8905, alert_id=424
```

### Stop (cancel a running task)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/stop
# Returns: { ok: true, task_id: "task_xxxxxxxx", status: "cancelled" }
# Note: if already completed, sets status=cancelled on the run
```

### Pause (freeze task scheduling)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/pause
# Returns: { ok: true, task: { last_status: "paused", active: false, ... } }
# Demo: T4 paused successfully → last_status=paused
```

### Resume (re-enable task after pause)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/resume
# Returns: { ok: true, task: { last_status: "idle", active: true, ... } }
# Demo: T4 resumed → last_status=idle
```

### Restart (stop + immediate re-run)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/restart
# Returns: { ok: true, run_id: "trun_xxxxxxxx", status: "completed", alert_id: N }
# Demo: T3 restart → run_id=trun_e37fb4d1, alert_id=425
```

### Clone (duplicate task definition)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/clone
# Returns: { ok: true, task: { id: "task_NEW", name: "...Original Name (Clone)", ... }, source_task_id: "task_xxxxxxxx" }
# Demo: T1 cloned → task_5bc7b30d ("T1 — Daily LLM Output Summary (Clone)")
```

### Archive (soft-disable, hide from active list)
```bash
curl -X POST http://localhost:6090/api/tasks/task_xxxxxxxx/archive
# Returns: { ok: true, task: { lifecycle_state: "archived", active: false, archived_at: "...", ... } }
# Demo: Clone task_5bc7b30d archived → lifecycle_state=archived
```

### Delete (permanent, removes all related data)
```bash
curl -X DELETE http://localhost:6090/api/tasks/task_xxxxxxxx
# Returns: { ok: true, deleted: "task_xxxxxxxx", name: "Task Name" }
# Demo: Clone task_5bc7b30d deleted → confirmed ok:true
# WARNING: This also deletes task_runs, task_alerts, step_results for that task
```

### Alert Lifecycle
```bash
# Acknowledge
curl -X POST http://localhost:6090/api/alerts/{alert_id}/status \
  -H "Content-Type: application/json" -d '{"status":"acknowledged"}'

# Resolve
curl -X POST http://localhost:6090/api/alerts/{alert_id}/status \
  -H "Content-Type: application/json" -d '{"status":"resolved"}'

# Snooze 30 minutes
curl -X POST http://localhost:6090/api/alerts/{alert_id}/status \
  -H "Content-Type: application/json" -d '{"status":"snoozed","snooze_minutes":30}'

# Reopen
curl -X POST http://localhost:6090/api/alerts/{alert_id}/status \
  -H "Content-Type: application/json" -d '{"status":"open"}'
```

---

## 12. Master Summary Table — All Tasks, Creation to Finish

Recorded: 2026-04-04

| # | Task Name | `task_id` | `tasked_type` | `mode` | `run_id` | `alert_id` | Final Status | Pages Traced |
|---|-----------|-----------|--------------|--------|----------|-----------|-------------|-------------|
| T1 | Daily LLM Output Summary | `task_3f950f6e` | `output` | chat | `trun_32293732` | 364 | ✅ completed | Tasked, Pipeline, Alerts, Completed, Preview |
| T1-redo | Same task, redo run | `task_3f950f6e` | `output` | chat | `trun_1a6a8905` | 424 | ✅ completed | Pipeline, Alerts, Preview |
| T2 | Alert: Threshold Check | `task_21dffb26` | `alert` | chat | `trun_bcdaa631` | 420 | ✅ completed | Tasked, Pipeline, Alerts, Completed, Preview |
| T3 | Action: Sandbox Shell | `task_00843dbb` | `action` | sandbox | `trun_5468fcc6` | 421 | ✅ completed | Tasked, Pipeline, Alerts, Completed, Preview |
| T3-restart | Same task, restart | `task_00843dbb` | `action` | sandbox | `trun_e37fb4d1` | 425 | ✅ completed | Pipeline, Preview |
| T4 | Hook: External Trigger | `task_814e3f3b` | `hook` | chat | `trun_96a073f5` | 422 | ✅ completed | Tasked, Pipeline, Alerts, Completed, Preview |
| T5 | Combined: Full Pipeline | `task_80986663` | `combined` | chat | `trun_6b571b78` | 423 | ✅ completed | Tasked, Pipeline, Alerts, Completed, Preview |
| Clone | T1 Clone (lifecycle demo) | `task_5bc7b30d` | `output` | chat | — | — | archived → deleted | — |

### Lifecycle Operations Demonstrated

| Operation | Task | Result |
|-----------|------|--------|
| **create** | All T1–T5 | 5 tasks created with all `tasked_type` variants |
| **run** | All T1–T5 | All 5 reached `status=completed` |
| **redo** | T1 | New `run_id=trun_1a6a8905`, `alert_id=424` |
| **restart** | T3 | New `run_id=trun_e37fb4d1`, `alert_id=425` |
| **stop** | T2 | `status=cancelled` |
| **pause** | T4 | `last_status=paused`, `active=false` |
| **resume** | T4 | `last_status=idle`, `active=true` |
| **clone** | T1 | New `task_id=task_5bc7b30d` ("T1 Clone") |
| **archive** | T1 Clone | `lifecycle_state=archived`, `archived_at` set |
| **delete** | T1 Clone | `{ ok: true, deleted: "task_5bc7b30d" }` |
| **edit** | T1 | Notes updated via `POST /api/tasks` with `id` |
