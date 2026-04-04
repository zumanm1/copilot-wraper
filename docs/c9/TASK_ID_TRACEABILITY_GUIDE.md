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

### Original Set (Session 1 — 2026-04-03/04)

| # | Task Name | `task_id` | `tasked_type` | `mode` | `run_id` | `alert_id` | Final Status |
|---|-----------|-----------|--------------|--------|----------|-----------|-------------|
| T1 | Daily LLM Output Summary | `task_3f950f6e` | `output` | chat | `trun_32293732` | 364 | ✅ completed |
| T1-redo | Same task, redo | `task_3f950f6e` | `output` | chat | `trun_1a6a8905` | 424 | ✅ completed |
| T2 | Alert: Threshold Check | `task_21dffb26` | `alert` | chat | `trun_bcdaa631` | 420 | ✅ completed |
| T3 | Action: Sandbox Shell | `task_00843dbb` | `action` | sandbox | `trun_5468fcc6` | 421 | ✅ completed |
| T3-restart | Same task, restart | `task_00843dbb` | `action` | sandbox | `trun_e37fb4d1` | 425 | ✅ completed |
| T4 | Hook: External Trigger | `task_814e3f3b` | `hook` | chat | `trun_96a073f5` | 422 | ✅ completed |
| T5 | Combined: Full Pipeline | `task_80986663` | `combined` | chat | `trun_6b571b78` | 423 | ✅ completed |
| Clone | T1 Clone (lifecycle demo) | `task_5bc7b30d` | `output` | chat | — | — | archived → deleted |

---

### New Unique Set (Session 2 — 2026-04-04)

All 5 brand-new tasks with unique `task_id`s — no clones, no recreations.

| # | Task Name | `task_id` | `tasked_type` | `mode` | `run_id` | `alert_id` | Final Status |
|---|-----------|-----------|--------------|--------|----------|-----------|-------------|
| NEW-T1 | Python Tips Output | `task_97baeced` | `output` | chat | `trun_83871f0d` | 430 | ✅ completed |
| NEW-T2 | CPU Alert Check | `task_adc06130` | `alert` | chat | `trun_eae69a71` | 432 | ✅ completed |
| NEW-T3 | Disk Check Action | `task_f0642138` | `action` | sandbox | `trun_6167744c` | 433 | ✅ completed |
| NEW-T4 | Slack Webhook Trigger | `task_f7632723` | `hook` | chat | `trun_f0c82c94` | 435 | ✅ completed |
| NEW-T5 | Security Audit Combined | `task_0f1c9b39` | `combined` | chat | `trun_84413c8c` | 438 | ✅ completed |

**Traceability URLs for New Set:**

```
NEW-T1 output:
  Tasked:    http://localhost:6090/tasked?task_id=task_97baeced
  Pipeline:  http://localhost:6090/piplinetask?task_id=task_97baeced
  Preview:   http://localhost:6090/tasked-preview?task_id=task_97baeced&run_id=trun_83871f0d
  Completed: http://localhost:6090/task-completed?task_id=task_97baeced

NEW-T2 alert:
  Tasked:    http://localhost:6090/tasked?task_id=task_adc06130
  Pipeline:  http://localhost:6090/piplinetask?task_id=task_adc06130
  Preview:   http://localhost:6090/tasked-preview?task_id=task_adc06130&run_id=trun_eae69a71
  Completed: http://localhost:6090/task-completed?task_id=task_adc06130

NEW-T3 action (sandbox):
  Tasked:    http://localhost:6090/tasked?task_id=task_f0642138
  Pipeline:  http://localhost:6090/piplinetask?task_id=task_f0642138
  Preview:   http://localhost:6090/tasked-preview?task_id=task_f0642138&run_id=trun_6167744c
  Completed: http://localhost:6090/task-completed?task_id=task_f0642138

NEW-T4 hook:
  Tasked:    http://localhost:6090/tasked?task_id=task_f7632723
  Pipeline:  http://localhost:6090/piplinetask?task_id=task_f7632723
  Preview:   http://localhost:6090/tasked-preview?task_id=task_f7632723&run_id=trun_f0c82c94
  Completed: http://localhost:6090/task-completed?task_id=task_f7632723

NEW-T5 combined:
  Tasked:    http://localhost:6090/tasked?task_id=task_0f1c9b39
  Pipeline:  http://localhost:6090/piplinetask?task_id=task_0f1c9b39
  Preview:   http://localhost:6090/tasked-preview?task_id=task_0f1c9b39&run_id=trun_84413c8c
  Completed: http://localhost:6090/task-completed?task_id=task_0f1c9b39
```

---

### Lifecycle Operations Demonstrated (Session 1)

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

---

## 13. NEW-T1 to NEW-T5 — Full Per-Task Monitoring Guide

> Each task has a **Trace Number** (TRACE-001 to TRACE-005) as a stable reference ID
> across all 5 monitoring domains. Use it when cross-referencing pages.

---

### TRACE-001 — NEW-T1 | `output` | chat

| Field | Value |
|-------|-------|
| **Trace Number** | `TRACE-001` |
| `task_id` | `task_97baeced` |
| `run_id` | `trun_83871f0d` |
| `alert_id` | `430` |
| `tasked_type` | `output` |
| `mode` | `chat` (via C1b:8000) |
| `status` | `completed` / `workflow-complete` |
| `duration` | 1m 40s |

#### How to Create (curl)
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NEW-T1 — Python Tips Output",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "output",
    "planner_prompt": "List 3 essential Python tips for writing cleaner code.",
    "executor_prompt": "You are a senior Python developer. Give exactly 3 concise tips, one sentence each.",
    "context_handoff": "Output-type task: produces LLM text output",
    "trigger_mode": "always",
    "steps": [
      {"id":"nt1_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"nt1_chat","name":"Generate Tips","kind":"chat","position":2,"config":{"prompt":"List 3 essential Python tips"}},
      {"id":"nt1_alert","name":"Output Alert","kind":"alert","position":3,"config":{"title":"NEW-T1 Output Ready","severity":"info","summary":"Python tips generated"}},
      {"id":"nt1_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
# Copy task_id from response, then run:
curl -s -X POST "http://localhost:6090/api/tasks/{task_id}/run"
```

#### How to Monitor on Each Page

**1. Tasked** — `http://localhost:6090/tasked?task_id=task_97baeced`
- Find the row for `NEW-T1 — Python Tips Output`
- Columns to check: `Type=output`, `Mode=chat`, `Status=completed`, `Steps=4`
- Actions available: Run Now, Edit, Clone, Archive, Delete
- Live observed: `last_status=completed`, `tasked_type=output`, `steps=4`

**2. Piplinetask** — `http://localhost:6090/piplinetask?task_id=task_97baeced`
- API: `GET /api/task-pipelines?task_id=task_97baeced`
- Shows: run badge (green=completed), 4-step flow diagram, trace grid
- Live observed: `run_id=trun_83871f0d`, `status=completed`, `terminal=workflow-complete`
- Step flow: `nt1_trigger → nt1_chat → nt1_alert → nt1_complete` (all completed)

**3. Alerts** — `http://localhost:6090/alerts`
- Filter by task or severity `info`
- API: `GET /api/alerts?limit=500` → filter `task_id=task_97baeced`
- Live observed: `alert_id=430`, `title="NEW-T1 — Python Tips Output"`, `severity=info`, `status=open`
- Actions: Acknowledge → `POST /api/alerts/430/status {"status":"acknowledged"}`

**4. Task Completed** — `http://localhost:6090/task-completed?task_id=task_97baeced`
- API: `GET /api/task-completed?task_id=task_97baeced`
- Live observed: `run_id=trun_83871f0d`, `status=completed`, `duration=1m 40s`
- Actions: Redo, Clone Task, Open Pipeline, Preview Output

**5. Preview** — `http://localhost:6090/tasked-preview?task_id=task_97baeced&run_id=trun_83871f0d`
- API: `GET /api/task-preview?task_id=task_97baeced&run_id=trun_83871f0d`
- Live observed: 4 step_results, output starts: *"Here are 3 essential Python tips that make a big difference..."*

---

### TRACE-002 — NEW-T2 | `alert` | chat

| Field | Value |
|-------|-------|
| **Trace Number** | `TRACE-002` |
| `task_id` | `task_adc06130` |
| `run_id` | `trun_eae69a71` |
| `alert_id` | `432` |
| `tasked_type` | `alert` |
| `mode` | `chat` (via C1b:8000) |
| `status` | `completed` / `workflow-complete` |
| `duration` | 47s |

#### How to Create (curl)
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NEW-T2 — CPU Alert Check",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "alert",
    "planner_prompt": "Check: is CPU usage at 95% above the warning threshold of 80%? Respond only as JSON: {\"triggered\": true, \"reason\": \"CPU 95% exceeds 80% threshold\"}",
    "executor_prompt": "You are a monitoring system. Evaluate the condition and confirm the alert reason clearly.",
    "context_handoff": "Alert-type task: fires alerts based on JSON condition evaluation",
    "trigger_mode": "json",
    "steps": [
      {"id":"nt2_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"nt2_condition","name":"Condition Check","kind":"chat","position":2,"config":{"prompt":"Is CPU 95% > threshold 80%?"}},
      {"id":"nt2_alert","name":"CPU Alert","kind":"alert","position":3,"config":{"title":"NEW-T2 CPU Threshold Exceeded","severity":"warning","summary":"CPU usage 95% exceeds 80% warning threshold"}},
      {"id":"nt2_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
curl -s -X POST "http://localhost:6090/api/tasks/{task_id}/run"
```

#### How to Monitor on Each Page

**1. Tasked** — `http://localhost:6090/tasked?task_id=task_adc06130`
- Live observed: `last_status=completed`, `tasked_type=alert`, `mode=chat`, `steps=4`
- Note: `trigger_mode=json` means alert fires only when LLM returns `{"triggered": true}`

**2. Piplinetask** — `http://localhost:6090/piplinetask?task_id=task_adc06130`
- Live observed: `run_id=trun_eae69a71`, `status=completed`, `terminal=workflow-complete`
- Step flow: `nt2_trigger → nt2_condition → nt2_alert → nt2_complete`

**3. Alerts** — `http://localhost:6090/alerts`
- Live observed: `alert_id=432`, `title="NEW-T2 CPU Threshold Exceeded"`, `severity=warning`, `status=open`
- Note: severity=`warning` (amber badge) — higher urgency than info
- Acknowledge: `POST /api/alerts/432/status {"status":"acknowledged"}`

**4. Task Completed** — `http://localhost:6090/task-completed?task_id=task_adc06130`
- Live observed: `run_id=trun_eae69a71`, `status=completed`, `duration=47s`

**5. Preview** — `http://localhost:6090/tasked-preview?task_id=task_adc06130&run_id=trun_eae69a71`
- Live observed: 4 step_results, output: *"Yes. ✅ 95% is greater than the 80% threshold, so the condition is true."*

---

### TRACE-003 — NEW-T3 | `action` | sandbox (C12b)

| Field | Value |
|-------|-------|
| **Trace Number** | `TRACE-003` |
| `task_id` | `task_f0642138` |
| `run_id` | `trun_6167744c` |
| `alert_id` | `433` |
| `tasked_type` | `action` |
| `mode` | `sandbox` (via C12b:8210) |
| `status` | `completed` / `workflow-complete` |
| `duration` | 0s (fast shell exec) |

#### How to Create (curl)
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NEW-T3 — Disk Check Action",
    "mode": "sandbox",
    "schedule_kind": "manual",
    "tasked_type": "action",
    "planner_prompt": "Run disk check: df -h /workspace and count files in /workspace.",
    "executor_prompt": "Execute shell commands and report disk usage and file count.",
    "context_handoff": "Action-type task: runs shell commands in C12b sandbox",
    "trigger_mode": "always",
    "executor_target": "C12b",
    "workspace_dir": "/workspace",
    "validation_command": "echo disk_check_ok",
    "steps": [
      {"id":"nt3_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"nt3_sandbox","name":"Disk Action","kind":"sandbox","position":2,"config":{"command":"df -h /workspace && echo files=$(ls /workspace | wc -l) && echo DISK_CHECK_DONE"}},
      {"id":"nt3_alert","name":"Disk Alert","kind":"alert","position":3,"config":{"title":"NEW-T3 Disk Check Complete","severity":"info","summary":"Disk usage and file count reported from C12b"}},
      {"id":"nt3_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
curl -s -X POST "http://localhost:6090/api/tasks/{task_id}/run"
```

#### How to Monitor on Each Page

**1. Tasked** — `http://localhost:6090/tasked?task_id=task_f0642138`
- Live observed: `last_status=completed`, `tasked_type=action`, `mode=sandbox`, `steps=4`
- Note: `executor_target=C12b` — execution route goes through C12b:8210, not C1b

**2. Piplinetask** — `http://localhost:6090/piplinetask?task_id=task_f0642138`
- Live observed: `run_id=trun_6167744c`, `status=completed`, `terminal=workflow-complete`
- Step flow: `nt3_trigger → nt3_sandbox → nt3_alert → nt3_complete`
- The sandbox step shows raw shell output in the step_results panel

**3. Alerts** — `http://localhost:6090/alerts`
- Live observed: `alert_id=433`, `title="NEW-T3 Disk Check Complete"`, `severity=info`, `status=open`

**4. Task Completed** — `http://localhost:6090/task-completed?task_id=task_f0642138`
- Live observed: `run_id=trun_6167744c`, `status=completed`, `duration=0s`

**5. Preview** — `http://localhost:6090/tasked-preview?task_id=task_f0642138&run_id=trun_6167744c`
- Live observed: 4 step_results, output: *"Sandbox target: C12b Lean Sandbox / Workspace: /workspace / Execution: completed / Validation: completed / Filesystem ... DISK_CHECK_DONE"*

---

### TRACE-004 — NEW-T4 | `hook` | chat

| Field | Value |
|-------|-------|
| **Trace Number** | `TRACE-004` |
| `task_id` | `task_f7632723` |
| `run_id` | `trun_f0c82c94` |
| `alert_id` | `435` |
| `tasked_type` | `hook` |
| `mode` | `chat` (via C1b:8000) |
| `status` | `completed` / `workflow-complete` |
| `duration` | 27s |

#### How to Create (curl)
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NEW-T4 — Slack Webhook Trigger",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "hook",
    "planner_prompt": "Generate a Slack webhook payload JSON for a build-success notification.",
    "executor_prompt": "You are a CI/CD notification dispatcher. Produce a valid Slack webhook JSON payload with text, username, icon_emoji, and attachments fields.",
    "context_handoff": "Hook-type task: generates and simulates external webhook dispatch",
    "trigger_mode": "always",
    "steps": [
      {"id":"nt4_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"nt4_hook","name":"Slack Webhook","kind":"chat","position":2,"config":{"prompt":"Generate Slack build-success webhook JSON payload"}},
      {"id":"nt4_alert","name":"Hook Alert","kind":"alert","position":3,"config":{"title":"NEW-T4 Slack Hook Dispatched","severity":"info","summary":"Slack build-success webhook payload generated"}},
      {"id":"nt4_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
curl -s -X POST "http://localhost:6090/api/tasks/{task_id}/run"
```

#### How to Monitor on Each Page

**1. Tasked** — `http://localhost:6090/tasked?task_id=task_f7632723`
- Live observed: `last_status=completed`, `tasked_type=hook`, `mode=chat`, `steps=4`
- Hook tasks simulate external system triggers — the LLM generates the payload that would be dispatched

**2. Piplinetask** — `http://localhost:6090/piplinetask?task_id=task_f7632723`
- Live observed: `run_id=trun_f0c82c94`, `status=completed`, `terminal=workflow-complete`
- Step flow: `nt4_trigger → nt4_hook → nt4_alert → nt4_complete`

**3. Alerts** — `http://localhost:6090/alerts`
- Live observed: `alert_id=435`, `title="NEW-T4 — Slack Webhook Trigger"`, `severity=info`, `status=open`
- Acknowledge: `POST /api/alerts/435/status {"status":"acknowledged"}`

**4. Task Completed** — `http://localhost:6090/task-completed?task_id=task_f7632723`
- Live observed: `run_id=trun_f0c82c94`, `status=completed`, `duration=27s`

**5. Preview** — `http://localhost:6090/tasked-preview?task_id=task_f7632723&run_id=trun_f0c82c94`
- Live observed: 4 step_results, output: *"Below is a ready-to-use Slack Incoming Webhook JSON payload for a successful build notification, using Block Kit..."*

---

### TRACE-005 — NEW-T5 | `combined` | chat

| Field | Value |
|-------|-------|
| **Trace Number** | `TRACE-005` |
| `task_id` | `task_0f1c9b39` |
| `run_id` | `trun_84413c8c` |
| `alert_id` | `438` |
| `tasked_type` | `combined` |
| `mode` | `chat` (via C1b:8000) |
| `status` | `completed` / `workflow-complete` |
| `duration` | 7m 40s |

#### How to Create (curl)
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NEW-T5 — Security Audit Combined",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "combined",
    "planner_prompt": "Produce a security audit summary covering FINDINGS, RISK_LEVEL, and REMEDIATION_STEPS for a web API with no rate limiting and plain-text password storage.",
    "executor_prompt": "You are a security auditor. Structure your response with clearly labeled FINDINGS, RISK_LEVEL (Critical/High/Medium/Low), and REMEDIATION_STEPS sections.",
    "context_handoff": "Combined-type task: generates structured output, fires alert, and logs action steps",
    "trigger_mode": "always",
    "steps": [
      {"id":"nt5_trigger","name":"Trigger","kind":"trigger","position":1},
      {"id":"nt5_chat","name":"Audit Report","kind":"chat","position":2,"config":{"prompt":"Generate security audit with FINDINGS, RISK_LEVEL, REMEDIATION_STEPS"}},
      {"id":"nt5_alert","name":"Audit Alert","kind":"alert","position":3,"config":{"title":"NEW-T5 Security Audit Ready","severity":"high","summary":"Security audit report generated — review FINDINGS and REMEDIATION_STEPS"}},
      {"id":"nt5_complete","name":"Complete","kind":"complete","position":4}
    ]
  }'
curl -s -X POST "http://localhost:6090/api/tasks/{task_id}/run"
```

#### How to Monitor on Each Page

**1. Tasked** — `http://localhost:6090/tasked?task_id=task_0f1c9b39`
- Live observed: `last_status=completed`, `tasked_type=combined`, `mode=chat`, `steps=4`
- Combined tasks exercise all paths: output generation + alert firing + structured action reporting

**2. Piplinetask** — `http://localhost:6090/piplinetask?task_id=task_0f1c9b39`
- Live observed: `run_id=trun_84413c8c`, `status=completed`, `terminal=workflow-complete`
- Step flow: `nt5_trigger → nt5_chat → nt5_alert → nt5_complete`
- Note: duration 7m 40s — longest run due to large structured output from C1b

**3. Alerts** — `http://localhost:6090/alerts`
- Live observed: `alert_id=438`, `title="NEW-T5 — Security Audit Combined"`, `severity=high`, `status=open`
- Note: severity=`high` (red badge) — highest severity in the new set
- Acknowledge: `POST /api/alerts/438/status {"status":"acknowledged"}`

**4. Task Completed** — `http://localhost:6090/task-completed?task_id=task_0f1c9b39`
- Live observed: `run_id=trun_84413c8c`, `status=completed`, `duration=7m 40s`

**5. Preview** — `http://localhost:6090/tasked-preview?task_id=task_0f1c9b39&run_id=trun_84413c8c`
- Live observed: 4 step_results, output: *"Below is a generic security audit report... FINDINGS / RISK_LEVEL / REMEDIATION_STEPS..."*

---

## 14. Trace Number Quick Reference

| Trace # | Task Name | `task_id` | `tasked_type` | `run_id` | `alert_id` | Duration |
|---------|-----------|-----------|--------------|----------|-----------|---------|
| `TRACE-001` | NEW-T1 Python Tips Output | `task_97baeced` | `output` | `trun_83871f0d` | 430 | 1m 40s |
| `TRACE-002` | NEW-T2 CPU Alert Check | `task_adc06130` | `alert` | `trun_eae69a71` | 432 | 47s |
| `TRACE-003` | NEW-T3 Disk Check Action | `task_f0642138` | `action` | `trun_6167744c` | 433 | 0s |
| `TRACE-004` | NEW-T4 Slack Webhook Trigger | `task_f7632723` | `hook` | `trun_f0c82c94` | 435 | 27s |
| `TRACE-005` | NEW-T5 Security Audit Combined | `task_0f1c9b39` | `combined` | `trun_84413c8c` | 438 | 7m 40s |

### Domain Monitoring Cheat Sheet (use Trace # to look up IDs above)

| Domain | URL Pattern | API Endpoint | Key Fields to Check |
|--------|-------------|-------------|---------------------|
| **Tasked** | `/tasked?task_id={task_id}` | `GET /api/tasks` | `last_status`, `tasked_type`, `mode`, `steps` |
| **Piplinetask** | `/piplinetask?task_id={task_id}` | `GET /api/task-pipelines?task_id=` | `run_id`, `status`, `terminal_reason`, step flow |
| **Alerts** | `/alerts` (filter by task) | `GET /api/alerts?limit=500` | `alert_id`, `title`, `severity`, `status` |
| **Task Completed** | `/task-completed?task_id={task_id}` | `GET /api/task-completed?task_id=` | `run_id`, `status`, `duration_label` |
| **Preview** | `/tasked-preview?task_id={task_id}&run_id={run_id}` | `GET /api/task-preview?task_id=&run_id=` | `step_results`, `output_text` |

---

## 15. Complete Operations Guide — Create, Update, Start, Stop, Restart, Resume, and Trace

> This section is a self-contained reference for the full task lifecycle.
> Every operation is shown as both a **UI action** and a **curl API call**.
> Use the TRACE-001–005 IDs in Section 14 as concrete examples throughout.

---

### 15.1 CREATE a Task

#### Via UI
1. Open `http://localhost:6090/tasked`
2. Fill the left-side builder form:

| Field | Input | Notes |
|-------|-------|-------|
| Task Name | e.g. `"My New Task"` | Required. Shows in all pages. |
| Mode | `chat` / `sandbox` / `agent` | `chat` = C1b LLM. `sandbox` = C12b shell. |
| Output Type (`tasked_type`) | `output` / `alert` / `action` / `hook` / `combined` | Determines how output is handled and what alert is fired. |
| Schedule | `manual` | Once-off. Other options: `interval`, `cron`. |
| Planner Prompt | Your question or instruction | What the task should do. |
| Executor Prompt | Role/persona for the LLM | How the LLM should respond. |
| Alert Trigger | `always` / `json` | `always` = alert every run. `json` = alert only if output contains `{"triggered":true}`. |
| Alert Severity | `info` / `warning` / `high` / `critical` | Sets badge colour on Alerts page. |

3. Click **`+ Add Step`** to build the workflow:

```
Step 1  kind=trigger   → marks the start point
Step 2  kind=chat      → executes LLM (or kind=sandbox for shell)
Step 3  kind=alert     → fires the alert record
Step 4  kind=complete  → closes the run
```

4. Click **Save Tasked** → new `task_id` appears in the task table on the right.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My New Task",
    "mode": "chat",
    "schedule_kind": "manual",
    "tasked_type": "output",
    "planner_prompt": "List 3 key facts about Docker networking.",
    "executor_prompt": "You are a concise DevOps engineer. Answer in 3 bullet points.",
    "context_handoff": "Output task producing DevOps notes",
    "trigger_mode": "always",
    "steps": [
      {"id":"my_trigger", "name":"Trigger",  "kind":"trigger",  "position":1},
      {"id":"my_chat",    "name":"Execute",  "kind":"chat",     "position":2, "config":{"prompt":"List 3 Docker networking facts"}},
      {"id":"my_alert",   "name":"Alert",    "kind":"alert",    "position":3, "config":{"title":"Task Done","severity":"info","summary":"Output ready"}},
      {"id":"my_complete","name":"Complete", "kind":"complete", "position":4}
    ]
  }'
```
**Response:**
```json
{ "ok": true, "task": { "id": "task_xxxxxxxx", "name": "My New Task", ... } }
```
Copy the `task_id` — you will use it for every subsequent operation.

---

### 15.2 UPDATE (Edit) a Task

#### Via UI
1. Open `http://localhost:6090/tasked?task_id={task_id}`
2. The builder form pre-fills with the task's current values.
3. Edit any field (name, prompts, steps, schedule, alert settings).
4. Click **Save Tasked** — the same `task_id` is preserved; only the fields you changed are updated.

#### Via API
Pass the existing `id` in the body — the API treats it as an update:
```bash
# Update name and executor prompt
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "task_97baeced",
    "name": "NEW-T1 — Python Tips Output (v2)",
    "executor_prompt": "You are a Python expert. Give 3 tips with one code example each.",
    "notes": "Updated 2026-04-04: added code examples to executor prompt"
  }'
```
**Response:** `{ "ok": true, "task": { "id": "task_97baeced", ... updated fields ... } }`

> The `task_id` never changes. All existing `run_id` and `alert_id` records remain linked.

---

### 15.3 START (Run) a Task

#### Via UI
- On the Tasked page task row: click **Run Now** button.
- Or open the task and click the **Run** button in the detail panel.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/task_97baeced/run"
```
**Response:**
```json
{
  "ok": true,
  "task_id": "task_97baeced",
  "run_id": "trun_83871f0d",
  "status": "completed",
  "alert_id": 430,
  "terminal_reason": "workflow-complete",
  "text": "Here are 3 essential Python tips..."
}
```
- A new `run_id` is created for every execution.
- A new `alert_id` is created if the trigger condition is met.
- Status progresses: `queued → running → completed | failed | cancelled`

---

### 15.4 STOP a Task

Use when a task is **currently running** and you need to cancel it immediately.

#### Via UI
- On Tasked or Piplinetask page: click **Stop** button on the running task row.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/task_adc06130/stop"
```
**Response:**
```json
{ "ok": true, "task_id": "task_adc06130", "status": "cancelled" }
```
- The current run's status becomes `cancelled`.
- The task definition remains active — it can be re-run at any time.
- If called on an already-completed task, it sets that run's status to `cancelled`.

---

### 15.5 PAUSE a Task

Use to **freeze scheduling** — the task will not auto-run on its next interval until resumed.

#### Via UI
- On Tasked page: click the **Pause** toggle or button on the task row.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/task_f7632723/pause"
```
**Response fields to check:**
```json
{ "ok": true, "task": { "last_status": "paused", "active": false, ... } }
```
- `active=false` stops the scheduler from queuing new runs.
- Existing in-progress runs continue to completion.
- Demonstrated live: T4 (`task_f7632723`) paused → `last_status=paused`.

---

### 15.6 RESUME a Task

Reverses a pause — re-enables the task for scheduling and manual runs.

#### Via UI
- On Tasked page: click **Resume** on a paused task.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/task_f7632723/resume"
```
**Response fields to check:**
```json
{ "ok": true, "task": { "last_status": "idle", "active": true, ... } }
```
- `active=true` means the scheduler will queue runs again.
- Demonstrated live: T4 resumed → `last_status=idle`.

---

### 15.7 RESTART a Task

Combines **stop + immediate re-run** in one call. Creates a brand-new `run_id`.

#### Via UI
- On Tasked or Piplinetask page: click **Restart** button.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/task_f0642138/restart"
```
**Response:**
```json
{
  "ok": true,
  "task_id": "task_f0642138",
  "run_id": "trun_e37fb4d1",
  "status": "completed",
  "alert_id": 425,
  "terminal_reason": "workflow-complete"
}
```
- Produces a new `run_id` and new `alert_id`.
- Demonstrated live: T3 (`task_f0642138`) restarted → `run_id=trun_e37fb4d1`, `alert_id=425`.

---

### 15.8 REDO a Task

Re-runs the task using exactly the same definition. Creates a new `run_id` (does not stop anything first).

#### Via UI
- On Task Completed page: click **Redo** button on any completed run row.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/task_3f950f6e/redo"
```
**Response:** same shape as `/run` — new `run_id`, new `alert_id`.
- Demonstrated live: T1 redo → `run_id=trun_1a6a8905`, `alert_id=424`.

---

### 15.9 CLONE a Task

Creates a full copy of the task definition with a new unique `task_id`. The clone starts with `last_status=idle` — it has no run history.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/task_97baeced/clone"
```
**Response:**
```json
{
  "ok": true,
  "task": { "id": "task_NEWID", "name": "NEW-T1 — Python Tips Output (Clone)", ... },
  "source_task_id": "task_97baeced"
}
```
- New task gets all the same steps, prompts, and settings.
- The note field includes `"Cloned from task_97baeced."`.
- You can immediately edit and run the clone independently.

---

### 15.10 ARCHIVE a Task

Soft-disables a task. It disappears from the active task list but all run history is preserved.

#### Via API
```bash
curl -s -X POST "http://localhost:6090/api/tasks/{task_id}/archive"
```
**Response fields to check:**
```json
{ "ok": true, "task": { "lifecycle_state": "archived", "active": false, "archived_at": "2026-04-04T..." } }
```

---

### 15.11 DELETE a Task

Permanently removes the task and all linked run records, alerts, and step results.

#### Via API
```bash
curl -s -X DELETE "http://localhost:6090/api/tasks/{task_id}"
```
**Response:**
```json
{ "ok": true, "deleted": "task_xxxxxxxx", "name": "Task Name" }
```
> **Warning:** This is irreversible. Archive instead if you might need the history.

---

### 15.12 TRACE a Task Through All 5 Pages

Once a task has been created and run, trace it using its `task_id` and `run_id`.
Use TRACE-001 (`task_97baeced` / `trun_83871f0d`) as the worked example throughout.

---

#### PAGE 1 — Tasked (`/tasked`)

**Purpose:** View and manage the task *definition*. This is where you create, edit, clone, archive, and delete tasks.

**URL:**
```
http://localhost:6090/tasked?task_id=task_97baeced
```

**What to check on this page:**

| Column / Field | Meaning | Expected (TRACE-001) |
|----------------|---------|----------------------|
| Task Name | Identifier | `NEW-T1 — Python Tips Output` |
| Type | `tasked_type` | `output` |
| Mode | Execution route | `chat` |
| Last Status | Most recent run result | `completed` |
| Steps | Workflow step count | `4` |
| Last Run | Timestamp of last execution | shown |
| Actions | Run, Edit, Clone, Archive, Delete | all available |

**API equivalent:**
```bash
curl -s "http://localhost:6090/api/tasks" | python3 -c "
import sys, json
tasks = json.load(sys.stdin).get('tasks', [])
t = next(t for t in tasks if t['id']=='task_97baeced')
print('name:', t['name'])
print('type:', t['tasked_type'])
print('mode:', t['mode'])
print('status:', t['last_status'])
print('steps:', len(t.get('steps',[])))
"
```

---

#### PAGE 2 — Piplinetask (`/piplinetask`)

**Purpose:** Monitor the *execution pipeline* of a task. Shows every step, their status, timing, and events for each run.

**URL:**
```
http://localhost:6090/piplinetask?task_id=task_97baeced
```
Or filter by specific run:
```
http://localhost:6090/piplinetask?run_id=trun_83871f0d
```

**What to check on this page:**

| Panel | Meaning | Expected (TRACE-001) |
|-------|---------|----------------------|
| Status badge | Run status (colour-coded) | Green = `completed` |
| Run ID | Which execution | `trun_83871f0d` |
| Terminal reason | Why the run ended | `workflow-complete` |
| Step flow diagram | Visual step → step graph | 4 nodes, all green |
| Step results | Per-step output and timing | 4 steps completed |
| Trace grid | Orchestrator, planner, executor, alert, completion panels | all populated |
| Timeline | Ordered event log | trigger → chat → alert → complete |

**API equivalent:**
```bash
curl -s "http://localhost:6090/api/task-pipelines?task_id=task_97baeced" | python3 -c "
import sys, json
pipes = json.load(sys.stdin).get('pipelines', [])
p = pipes[0]
r = p.get('run') or {}
print('run_id:', r.get('id'))
print('status:', r.get('status'))
print('terminal:', r.get('terminal_reason'))
for s in p.get('step_results', []):
    print(f'  step {s.get(\"step_id\")}: {s.get(\"status\")}')
"
```

---

#### PAGE 3 — Alerts (`/alerts`)

**Purpose:** View and action alerts fired by task runs. Each run that meets the trigger condition creates one alert record.

**URL:**
```
http://localhost:6090/alerts
```
Then use the page's filter to select by status (`open`), severity, or search by task name.

**What to check on this page:**

| Field | Meaning | Expected (TRACE-001) |
|-------|---------|----------------------|
| Alert ID | Unique integer | `430` |
| Title | Alert title from step config | `NEW-T1 — Python Tips Output` |
| Severity | `info` / `warning` / `high` / `critical` | `info` (blue badge) |
| Status | `open` / `acknowledged` / `resolved` / `snoozed` | `open` |
| Task link | Opens Tasked for this task | → `task_97baeced` |
| Pipeline link | Opens Piplinetask for this run | → `trun_83871f0d` |
| Preview link | Opens Preview for this run | → `task_id + run_id` |

**Alert actions (buttons on each card):**

| Button | API Call | Result |
|--------|----------|--------|
| Acknowledge | `POST /api/alerts/430/status {"status":"acknowledged"}` | Badge changes to grey |
| Resolve | `POST /api/alerts/430/status {"status":"resolved"}` | Removed from open list |
| Snooze 30m | `POST /api/alerts/430/status {"status":"snoozed","snooze_minutes":30}` | Hidden for 30 minutes |
| Reopen | `POST /api/alerts/430/status {"status":"open"}` | Returns to open list |

**API equivalent:**
```bash
curl -s "http://localhost:6090/api/alerts?limit=500" | python3 -c "
import sys, json
alerts = json.load(sys.stdin).get('alerts', [])
for a in alerts:
    if a.get('task_id') == 'task_97baeced':
        print('alert_id:', a['id'])
        print('title:', a['title'])
        print('severity:', a['severity'])
        print('status:', a['status'])
        print('run_id:', a.get('run_id'))
"
```

---

#### PAGE 4 — Task Completed (`/task-completed`)

**Purpose:** View all **terminal** runs — `completed`, `failed`, or `cancelled`. This is the post-run history and the entry point for redo and re-run actions.

**URL:**
```
http://localhost:6090/task-completed?task_id=task_97baeced
```

**What to check on this page:**

| Field | Meaning | Expected (TRACE-001) |
|-------|---------|----------------------|
| Run ID | Which execution completed | `trun_83871f0d` |
| Status | Terminal state | `completed` (green) |
| Terminal Reason | How it ended | `workflow-complete` |
| Duration | How long the run took | `1m 40s` |
| Output Excerpt | First ~200 chars of LLM output | `"Here are 3 essential Python tips..."` |
| Alert badge | Alert that fired for this run | `alert_id=430, info` |

**Actions available:**

| Button | API Call | What it does |
|--------|----------|-------------|
| Redo | `POST /api/tasks/task_97baeced/redo` | Re-runs task, new run_id + alert_id |
| Clone Task | `POST /api/tasks/task_97baeced/clone` | Creates copy with new task_id |
| Open Pipeline | links to `/piplinetask?task_id=task_97baeced` | See step-by-step execution detail |
| Preview Output | links to `/tasked-preview?task_id=task_97baeced&run_id=trun_83871f0d` | Full output and step results |

**API equivalent:**
```bash
curl -s "http://localhost:6090/api/task-completed?task_id=task_97baeced" | python3 -c "
import sys, json
items = json.load(sys.stdin).get('items', [])
for item in items:
    r = item.get('run') or {}
    print('run_id:', r.get('id'))
    print('status:', r.get('status'))
    print('duration:', r.get('duration_label'))
    print('terminal:', r.get('terminal_reason'))
"
```

---

#### PAGE 5 — Preview (`/tasked-preview`)

**Purpose:** Inspect the *full compiled output* of a specific run — every step's input, output, timing, and the final LLM response. Always include `run_id` to pin to the exact run.

**URL:**
```
http://localhost:6090/tasked-preview?task_id=task_97baeced&run_id=trun_83871f0d
```

**What to check on this page:**

| Section | Content | Expected (TRACE-001) |
|---------|---------|----------------------|
| Task Definition panel | Name, mode, prompts, schedule | `NEW-T1`, `chat`, `output` |
| Run selector | Dropdown of all runs for this task | `trun_83871f0d` selected |
| Step Results | Per-step: status, duration, input, output | 4 steps, all completed |
| Output Text | Full compiled LLM response | Complete Python tips text |
| Recent Runs | Last 20 runs for this task | All runs listed |

**API equivalent:**
```bash
curl -s "http://localhost:6090/api/task-preview?task_id=task_97baeced&run_id=trun_83871f0d" | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('run') or {}
print('run_id:', r.get('id'))
print('steps:', len(d.get('step_results', [])))
print('output (first 200):', (d.get('output_text') or '')[:200])
for s in d.get('step_results', []):
    print(f'  step {s.get(\"step_id\")}: status={s.get(\"status\")} duration={s.get(\"duration_label\")}')
"
```

---

### 15.13 Full End-to-End Workflow Walkthrough

> Follow these steps to take any task from zero to fully traced in all 5 pages.

```
STEP 1 — CREATE
  POST /api/tasks  →  get task_id

STEP 2 — VERIFY on Tasked
  http://localhost:6090/tasked?task_id={task_id}
  Confirm: name, type, mode, steps visible

STEP 3 — RUN
  POST /api/tasks/{task_id}/run  →  get run_id + alert_id

STEP 4 — TRACE on Piplinetask
  http://localhost:6090/piplinetask?task_id={task_id}
  Confirm: status=completed, all steps green, terminal=workflow-complete

STEP 5 — TRACE on Alerts
  http://localhost:6090/alerts  (filter to open)
  Confirm: alert_id present, severity correct, status=open
  Action: Acknowledge the alert

STEP 6 — TRACE on Task Completed
  http://localhost:6090/task-completed?task_id={task_id}
  Confirm: run_id listed, status=completed, duration shown

STEP 7 — TRACE on Preview
  http://localhost:6090/tasked-preview?task_id={task_id}&run_id={run_id}
  Confirm: step results visible, output_text populated

STEP 8 — LIFECYCLE OPERATIONS (optional, to test controls)
  STOP:    POST /api/tasks/{task_id}/stop
  PAUSE:   POST /api/tasks/{task_id}/pause
  RESUME:  POST /api/tasks/{task_id}/resume
  RESTART: POST /api/tasks/{task_id}/restart  →  new run_id
  REDO:    POST /api/tasks/{task_id}/redo     →  new run_id
  CLONE:   POST /api/tasks/{task_id}/clone   →  new task_id
  ARCHIVE: POST /api/tasks/{task_id}/archive
  DELETE:  DELETE /api/tasks/{task_id}        ← permanent
```

---

### 15.14 Worked Example with TRACE-001 (NEW-T1)

```bash
# 1. Create
curl -s -X POST "http://localhost:6090/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"name":"NEW-T1 — Python Tips Output","mode":"chat","schedule_kind":"manual",
       "tasked_type":"output","planner_prompt":"List 3 essential Python tips for writing cleaner code.",
       "executor_prompt":"You are a senior Python developer. Give exactly 3 concise tips, one sentence each.",
       "trigger_mode":"always",
       "steps":[
         {"id":"nt1_trigger","name":"Trigger","kind":"trigger","position":1},
         {"id":"nt1_chat","name":"Generate Tips","kind":"chat","position":2,"config":{"prompt":"List 3 Python tips"}},
         {"id":"nt1_alert","name":"Output Alert","kind":"alert","position":3,"config":{"title":"Tips Ready","severity":"info","summary":"Python tips generated"}},
         {"id":"nt1_complete","name":"Complete","kind":"complete","position":4}
       ]}'
# → task_id: task_97baeced

# 2. Run
curl -s -X POST "http://localhost:6090/api/tasks/task_97baeced/run"
# → run_id: trun_83871f0d  alert_id: 430  status: completed

# 3. Check Piplinetask
curl -s "http://localhost:6090/api/task-pipelines?task_id=task_97baeced"
# → status: completed  terminal: workflow-complete  4 steps

# 4. Check Alert
curl -s "http://localhost:6090/api/alerts?limit=500" | python3 -c "
import sys,json; [print(a['id'],a['severity'],a['status']) for a in json.load(sys.stdin)['alerts'] if a.get('task_id')=='task_97baeced']"
# → 430  info  open

# 5. Acknowledge Alert
curl -s -X POST "http://localhost:6090/api/alerts/430/status" \
  -H "Content-Type: application/json" -d '{"status":"acknowledged"}'
# → status: acknowledged

# 6. Check Completed
curl -s "http://localhost:6090/api/task-completed?task_id=task_97baeced"
# → run_id: trun_83871f0d  status: completed  duration: 1m 40s

# 7. Preview
curl -s "http://localhost:6090/api/task-preview?task_id=task_97baeced&run_id=trun_83871f0d"
# → 4 step_results  output_text: "Here are 3 essential Python tips..."

# 8. Redo (new run)
curl -s -X POST "http://localhost:6090/api/tasks/task_97baeced/redo"
# → run_id: trun_1a6a8905  alert_id: 424  status: completed

# 9. Clone
curl -s -X POST "http://localhost:6090/api/tasks/task_97baeced/clone"
# → new task_id: task_5bc7b30d

# 10. Archive clone
curl -s -X POST "http://localhost:6090/api/tasks/task_5bc7b30d/archive"
# → lifecycle_state: archived

# 11. Delete clone
curl -s -X DELETE "http://localhost:6090/api/tasks/task_5bc7b30d"
# → deleted: task_5bc7b30d
```
