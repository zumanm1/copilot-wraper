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
