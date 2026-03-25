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
    http_status INTEGER
);
