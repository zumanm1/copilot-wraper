-- C9_JOKES local SQLite (validation + audit only)

CREATE TABLE IF NOT EXISTS validation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT,
    passed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    raw_summary TEXT
);

CREATE TABLE IF NOT EXISTS health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    target TEXT NOT NULL,
    http_status INTEGER,
    body_json TEXT
);

CREATE TABLE IF NOT EXISTS pair_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    pair_name TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT,
    duration_ms INTEGER,
    FOREIGN KEY (run_id) REFERENCES validation_runs(id)
);

CREATE TABLE IF NOT EXISTS chat_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    prompt_excerpt TEXT,
    response_excerpt TEXT,
    http_status INTEGER,
    elapsed_ms INTEGER,
    source TEXT DEFAULT 'chat'  -- 'chat' | 'validate'
);

-- Agent workspace: one row per user task run (supports follow-up sessions)
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,              -- "sess_" + 8-char hex
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    task TEXT NOT NULL,               -- initial task prompt
    agent_id TEXT NOT NULL,
    chat_mode TEXT DEFAULT 'auto',
    work_mode TEXT DEFAULT 'work',
    status TEXT DEFAULT 'running',    -- running | completed | failed
    steps_taken INTEGER DEFAULT 0,
    files_created TEXT DEFAULT '[]',  -- JSON array of filenames
    summary TEXT                      -- from DONE: final answer
);

-- Per-turn conversation messages for an agent session (enables follow-up)
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn INTEGER NOT NULL,            -- 1-based
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
);

-- Workspace projects: named subdirectories created by the user
CREATE TABLE IF NOT EXISTS workspace_projects (
    id TEXT PRIMARY KEY,              -- "proj_" + 6-char hex
    created_at TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE,        -- folder slug (lowercase, hyphens only)
    display_name TEXT,                -- human-readable label
    description TEXT,
    status TEXT DEFAULT 'active'
);

-- Multi-agent sessions: one row per smux-style parallel workspace run
CREATE TABLE IF NOT EXISTS multi_agent_sessions (
    id TEXT PRIMARY KEY,              -- "mas_" + 8-char hex
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    task TEXT NOT NULL,               -- supervisor's original task
    status TEXT DEFAULT 'running',    -- running | completed | failed
    roles TEXT DEFAULT '[]',          -- JSON array of active role names
    summary TEXT                      -- supervisor final summary
);

-- Per-pane messages for each role-agent in a multi-agent session
CREATE TABLE IF NOT EXISTS multi_agent_pane_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    pane_id TEXT NOT NULL,            -- "ma-builder", "ma-executor", etc.
    role TEXT NOT NULL,               -- builder | executor | tester | debugger | ui | supervisor
    turn INTEGER NOT NULL,            -- 1-based within this pane
    role_type TEXT NOT NULL,          -- 'user' | 'assistant'
    content TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES multi_agent_sessions(id)
);

CREATE TABLE IF NOT EXISTS task_definitions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'chat',
    schedule_kind TEXT NOT NULL DEFAULT 'manual',
    interval_minutes INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    tabs_required INTEGER DEFAULT 1,
    template_key TEXT DEFAULT '',
    executor_target TEXT DEFAULT '',
    workspace_dir TEXT DEFAULT '',
    planner_prompt TEXT DEFAULT '',
    executor_prompt TEXT DEFAULT '',
    validation_command TEXT DEFAULT '',
    test_command TEXT DEFAULT '',
    sandbox_assist INTEGER DEFAULT 0,
    sandbox_assist_target TEXT DEFAULT '',
    sandbox_assist_workspace_dir TEXT DEFAULT '',
    sandbox_assist_command TEXT DEFAULT '',
    sandbox_assist_validation_command TEXT DEFAULT '',
    sandbox_assist_test_command TEXT DEFAULT '',
    context_handoff TEXT DEFAULT '',
    trigger_mode TEXT DEFAULT 'json',
    trigger_text TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    last_run_at TEXT,
    next_run_at TEXT,
    last_status TEXT DEFAULT 'idle',
    last_result_excerpt TEXT DEFAULT '',
    archived_at TEXT,
    completion_policy_json TEXT DEFAULT '{}',
    alert_policy_json TEXT DEFAULT '{}',
    workflow_version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    source TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'queued',
    mode TEXT DEFAULT 'chat',
    executor_target TEXT DEFAULT '',
    sandbox_session_id TEXT DEFAULT '',
    output_excerpt TEXT DEFAULT '',
    validation_status TEXT DEFAULT '',
    validation_excerpt TEXT DEFAULT '',
    test_status TEXT DEFAULT '',
    test_excerpt TEXT DEFAULT '',
    error_text TEXT DEFAULT '',
    alert_id INTEGER,
    launch_url TEXT DEFAULT '',
    current_step_id TEXT DEFAULT '',
    terminal_reason TEXT DEFAULT '',
    trigger_snapshot_json TEXT DEFAULT '{}',
    completed_at TEXT,
    parent_run_id TEXT DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_task_runs_task_created ON task_runs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_runs_status_created ON task_runs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    run_id TEXT DEFAULT '',
    alert_id INTEGER,
    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    status TEXT DEFAULT 'open',
    title TEXT NOT NULL,
    trigger_text TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    payload_json TEXT DEFAULT '',
    acknowledged_at TEXT,
    resolved_at TEXT,
    snoozed_until TEXT,
    severity TEXT DEFAULT 'info',
    repeat_key TEXT DEFAULT '',
    closed_by_run_id TEXT DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_task_alerts_task_created ON task_alerts(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_alerts_status ON task_alerts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_alerts_repeat_key ON task_alerts(repeat_key, created_at DESC);

CREATE TABLE IF NOT EXISTS task_workflow_steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    config_json TEXT DEFAULT '{}',
    on_success_step_id TEXT DEFAULT '',
    on_failure_step_id TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_task_steps_task_position ON task_workflow_steps(task_id, position, active);

CREATE TABLE IF NOT EXISTS task_step_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_name TEXT DEFAULT '',
    step_kind TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'queued',
    output_json TEXT DEFAULT '{}',
    duration_ms INTEGER DEFAULT 0,
    error_text TEXT DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_task_step_results_run_started ON task_step_results(run_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_step_results_task_step ON task_step_results(task_id, step_id, started_at DESC);

CREATE TABLE IF NOT EXISTS task_feedback_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT DEFAULT '',
    agent_id TEXT NOT NULL,
    feedback_type TEXT DEFAULT 'result',
    status TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}',
    summary TEXT DEFAULT '',
    raw_excerpt TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_task_feedback_run_created ON task_feedback_events(run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_templates (
    key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'chat',
    schedule_kind TEXT NOT NULL DEFAULT 'manual',
    interval_minutes INTEGER DEFAULT 0,
    tabs_required INTEGER DEFAULT 1,
    executor_target TEXT DEFAULT '',
    workspace_dir TEXT DEFAULT '',
    planner_prompt TEXT DEFAULT '',
    executor_prompt TEXT DEFAULT '',
    validation_command TEXT DEFAULT '',
    test_command TEXT DEFAULT '',
    sandbox_assist INTEGER DEFAULT 0,
    sandbox_assist_target TEXT DEFAULT '',
    sandbox_assist_workspace_dir TEXT DEFAULT '',
    sandbox_assist_command TEXT DEFAULT '',
    sandbox_assist_validation_command TEXT DEFAULT '',
    sandbox_assist_test_command TEXT DEFAULT '',
    context_handoff TEXT DEFAULT '',
    trigger_mode TEXT DEFAULT 'json',
    trigger_text TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    source TEXT DEFAULT 'user'
);
CREATE INDEX IF NOT EXISTS idx_task_templates_active ON task_templates(active, updated_at DESC);

CREATE TABLE IF NOT EXISTS task_run_claims (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_task_claims_exp ON task_run_claims(expires_at);

CREATE TABLE IF NOT EXISTS session_manager_sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'task',
    page TEXT DEFAULT '',
    owner_id TEXT DEFAULT '',
    task_id TEXT DEFAULT '',
    run_id TEXT DEFAULT '',
    upstream TEXT DEFAULT '',
    operation TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    timeout_ms INTEGER DEFAULT 0,
    adaptive_timeout_ms INTEGER DEFAULT 0,
    last_elapsed_ms INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 2,
    next_retry_at TEXT,
    recovered_at TEXT,
    external_session_id TEXT DEFAULT '',
    resume_payload_json TEXT DEFAULT '{}',
    state_json TEXT DEFAULT '{}',
    last_error TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sm_status_retry ON session_manager_sessions(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_sm_task_run ON session_manager_sessions(task_id, run_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sm_page_owner ON session_manager_sessions(page, owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS session_manager_metrics (
    scope TEXT NOT NULL,
    upstream TEXT NOT NULL,
    operation TEXT NOT NULL,
    sample_count INTEGER DEFAULT 0,
    avg_elapsed_ms REAL DEFAULT 0,
    max_elapsed_ms INTEGER DEFAULT 0,
    last_elapsed_ms INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, upstream, operation)
);

CREATE INDEX IF NOT EXISTS idx_task_def_due ON task_definitions(active, schedule_kind, next_run_at);
