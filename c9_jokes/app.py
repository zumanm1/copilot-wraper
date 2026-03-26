"""
C9_JOKES — read-only validation console (Flask + SQLite).
Does not modify C1–C8; only HTTP GET/POST to peer URLs you configure.
"""
from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, render_template, request

# Reusable HTTP session with connection pooling for C1 calls
_http = requests.Session()
_adapter = HTTPAdapter(pool_connections=6, pool_maxsize=6)
_http.mount("http://", _adapter)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("DATABASE_PATH", "/app/data/c9.db"))

# ── Container targets ─────────────────────────────────────────────────────────
TARGETS = {
    "c1":  {"env": "C1_URL",  "default": "http://localhost:8000",  "label": "C1 copilot-api",       "health": "/health"},
    "c2":  {"env": "C2_URL",  "default": "http://localhost:8080",  "label": "C2 agent-terminal",    "health": "/health"},
    "c3":  {"env": "C3_URL",  "default": "http://localhost:8001",  "label": "C3 browser-auth",      "health": "/health"},
    "c5":  {"env": "C5_URL",  "default": "http://localhost:8080",  "label": "C5 claude-code",       "health": "/health"},
    "c6":  {"env": "C6_URL",  "default": "http://localhost:8080",  "label": "C6 kilocode",          "health": "/health"},
    "c7a": {"env": "C7A_URL", "default": "http://localhost:18789", "label": "C7a openclaw-gateway", "health": "/healthz"},
    "c7b": {"env": "C7B_URL", "default": "http://localhost:8080",  "label": "C7b openclaw-cli",     "health": "/health"},
    "c8":  {"env": "C8_URL",  "default": "http://localhost:8080",  "label": "C8 hermes-agent",      "health": "/health"},
}

# ── AI agents that can chat ───────────────────────────────────────────────────
AGENTS = [
    {"id": "c2-aider",       "label": "C2 Aider (OpenAI)"},
    {"id": "c5-claude-code", "label": "C5 Claude Code (Anthropic)"},
    {"id": "c6-kilocode",    "label": "C6 KiloCode (OpenAI)"},
    {"id": "c7-openclaw",    "label": "C7b OpenClaw"},
    {"id": "c8-hermes",      "label": "C8 Hermes Agent"},
    {"id": "c9-jokes",       "label": "C9 (generic session)"},
]


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
    )

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _db() -> sqlite3.Connection:
        conn = sqlite3.connect(DEFAULT_DB)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init_db() -> None:
        DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
        schema = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(DEFAULT_DB) as conn:
            conn.executescript(schema)

    @app.before_request
    def _ensure_db() -> None:
        if request.endpoint == "static":
            return
        if not DEFAULT_DB.exists():
            init_db()

    # ── Core probe helper ─────────────────────────────────────────────────────

    def _urls() -> dict[str, str]:
        return {
            key: os.environ.get(t["env"], t["default"]).rstrip("/")
            for key, t in TARGETS.items()
        }

    def probe_health(name: str, url: str, path: str) -> dict:
        full = f"{url}{path}"
        t0 = time.monotonic()
        try:
            r = _http.get(full, timeout=5)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            try:
                parsed = r.json()
            except Exception:
                parsed = {"raw": r.text[:500]}
            return {
                "name": name,
                "url": full,
                "ok": r.status_code == 200,
                "http_status": r.status_code,
                "body": parsed,
                "elapsed_ms": elapsed_ms,
            }
        except requests.RequestException as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return {
                "name": name,
                "url": full,
                "ok": False,
                "http_status": None,
                "error": str(e),
                "elapsed_ms": elapsed_ms,
            }

    def _probe_all() -> list[dict]:
        urls = _urls()
        return [
            probe_health(TARGETS[key]["label"], urls[key], TARGETS[key]["health"])
            for key in TARGETS
        ]

    # ── Chat proxy helper (shared by /api/chat and /api/validate) ─────────────

    def _chat_one(agent_id: str, prompt: str, c1_url: str) -> dict:
        """Call C1 for a single agent. Returns {ok, http_status, text, elapsed_ms}.

        C1 now keys its response cache on (style, agent_id, prompt), so each
        agent receives a unique response even when the prompt is identical.
        """
        body = {
            "model": "copilot",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        t0 = time.monotonic()
        try:
            r = _http.post(
                f"{c1_url}/v1/chat/completions",
                headers={"Content-Type": "application/json", "X-Agent-ID": agent_id},
                json=body,
                timeout=180,
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            text = ""
            if r.ok:
                try:
                    d = r.json()
                    text = d.get("choices", [{}])[0].get("message", {}).get("content", "")
                except Exception:
                    text = r.text[:2000]
            return {
                "ok": r.ok,
                "http_status": r.status_code,
                "text": text,
                "raw": r.text[:2000] if not r.ok else None,
                "elapsed_ms": elapsed_ms,
            }
        except requests.RequestException as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return {"ok": False, "http_status": None, "text": "", "error": str(e), "elapsed_ms": elapsed_ms}

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE ROUTES
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/")
    def dashboard():
        probes = _probe_all()
        up = sum(1 for p in probes if p["ok"])
        return render_template("dashboard.html", probes=probes, targets=TARGETS, up=up, total=len(probes))

    @app.get("/health")
    def page_health():
        probes = _probe_all()
        urls = _urls()
        probes.append(probe_health("C3 /status", urls["c3"], "/status"))
        return render_template("health.html", probes=probes)

    @app.get("/pairs")
    def page_pairs():
        return render_template("pairs.html", agents=AGENTS)

    @app.get("/chat")
    def page_chat():
        urls = _urls()
        return render_template("chat.html", c1_url=urls["c1"], agents=AGENTS)

    @app.get("/logs")
    def page_logs():
        agent_filter = request.args.get("agent", "").strip()
        try:
            offset = max(0, int(request.args.get("offset", 0)))
        except ValueError:
            offset = 0
        limit = 20
        rows = []
        total = 0
        try:
            with _db() as conn:
                if agent_filter:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM chat_logs WHERE agent_id=?", (agent_filter,)
                    ).fetchone()[0]
                    rows = conn.execute(
                        "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status "
                        "FROM chat_logs WHERE agent_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                        (agent_filter, limit, offset),
                    ).fetchall()
                else:
                    total = conn.execute("SELECT COUNT(*) FROM chat_logs").fetchone()[0]
                    rows = conn.execute(
                        "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status "
                        "FROM chat_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                        (limit, offset),
                    ).fetchall()
        except sqlite3.Error:
            rows = []
        return render_template(
            "logs.html",
            rows=rows,
            agents=AGENTS,
            agent_filter=agent_filter,
            offset=offset,
            limit=limit,
            total=total,
            prev_offset=max(0, offset - limit),
            next_offset=offset + limit,
            has_prev=offset > 0,
            has_next=(offset + limit) < total,
        )

    @app.get("/sessions")
    def page_sessions():
        urls = _urls()
        c1 = urls["c1"]
        data = None
        err = None
        try:
            r = _http.get(f"{c1}/v1/sessions", timeout=5)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        except requests.RequestException as e:
            err = str(e)
        return render_template("sessions.html", data=data, error=err, c1_url=c1)

    @app.get("/api")
    def page_api_reference():
        return render_template("api_reference.html", urls=_urls(), targets=TARGETS, agents=AGENTS)

    # ─────────────────────────────────────────────────────────────────────────
    # JSON API ROUTES
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/session-health")
    def api_session_health():
        """Proxy C3's /session-health endpoint; used by the LED indicator on all pages."""
        c3_url = _urls().get("c3", "http://browser-auth:8001")
        try:
            r = _http.get(f"{c3_url}/session-health", timeout=5)
            return jsonify(r.json()), r.status_code
        except Exception as exc:
            import datetime as _dt
            now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            return jsonify({"session": "unknown", "profile": "unknown", "reason": str(exc), "checked_at": now}), 503

    @app.get("/api/status")
    def api_status():
        """Probe all containers and persist each result to health_snapshots."""
        urls = _urls()
        result = {}
        ts = datetime.now(timezone.utc).isoformat()
        rows_to_insert = []
        for key in TARGETS:
            p = probe_health(key, urls[key], TARGETS[key]["health"])
            result[key] = p
            import json
            rows_to_insert.append((
                ts, key,
                p.get("http_status"),
                json.dumps(p.get("body") or {"error": p.get("error", "")}),
            ))
        # Persist to health_snapshots (Feature 1 — wire dead table)
        try:
            with _db() as conn:
                conn.executemany(
                    "INSERT INTO health_snapshots (captured_at, target, http_status, body_json) VALUES (?,?,?,?)",
                    rows_to_insert,
                )
        except sqlite3.Error:
            pass
        result["ts"] = ts
        return jsonify(result)

    @app.get("/api/health-history")
    def api_health_history():
        """Return last N health snapshots per target. ?target=c1&limit=10"""
        target = request.args.get("target", "").strip()
        try:
            limit = max(1, min(50, int(request.args.get("limit", 10))))
        except ValueError:
            limit = 10
        rows = []
        try:
            with _db() as conn:
                if target:
                    rows = conn.execute(
                        "SELECT captured_at, target, http_status, body_json FROM health_snapshots "
                        "WHERE target=? ORDER BY id DESC LIMIT ?",
                        (target, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT captured_at, target, http_status, body_json FROM health_snapshots "
                        "ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        except sqlite3.Error:
            pass
        return jsonify([dict(r) for r in rows])

    @app.post("/api/chat")
    def api_chat():
        """Proxy a single chat to C1. Body: {agent_id, prompt}."""
        c1 = _urls()["c1"]
        payload_in = request.get_json(silent=True) or {}
        agent_id = (payload_in.get("agent_id") or "c9-jokes").strip()
        prompt = (payload_in.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"ok": False, "error": "prompt required"}), 400
        result = _chat_one(agent_id, prompt, c1)
        # Log to DB
        try:
            with _db() as conn:
                conn.execute(
                    "INSERT INTO chat_logs (created_at, agent_id, prompt_excerpt, response_excerpt, http_status) "
                    "VALUES (?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        agent_id,
                        prompt[:200],
                        (result.get("text") or "")[:500],
                        result.get("http_status"),
                    ),
                )
        except sqlite3.Error:
            pass
        return jsonify(result)

    @app.post("/api/validate")
    def api_validate():
        """
        Run all agents with a prompt, persist to validation_runs + pair_results.

        Body:
          prompt:     str  (default "Tell me a joke")
          agent_ids:  list (optional, default all)
          parallel:   bool (default false) — fire all agents concurrently via
                      ThreadPoolExecutor; C1 keys its cache on (style, agent_id,
                      prompt) so each agent receives a distinct response.

        Returns: {run_id, mode, passed, failed, total, wall_ms, results: [...]}
        """
        c1 = _urls()["c1"]
        payload = request.get_json(silent=True) or {}
        prompt = (payload.get("prompt") or "Tell me a joke").strip()
        parallel = bool(payload.get("parallel", False))
        requested_ids = payload.get("agent_ids") or [a["id"] for a in AGENTS]
        agents_to_run = [a for a in AGENTS if a["id"] in requested_ids]
        if not agents_to_run:
            return jsonify({"ok": False, "error": "no matching agents"}), 400

        mode = "web-parallel" if parallel else "web-sequential"
        started_at = datetime.now(timezone.utc).isoformat()
        wall_t0 = time.monotonic()
        run_id = None
        try:
            with _db() as conn:
                cur = conn.execute(
                    "INSERT INTO validation_runs (started_at, mode, passed, failed) VALUES (?,?,0,0)",
                    (started_at, mode),
                )
                run_id = cur.lastrowid
        except sqlite3.Error:
            pass

        def _run_one(agent: dict) -> dict:
            r = _chat_one(agent["id"], prompt, c1)
            ok = r["ok"] and bool((r.get("text") or "").strip())
            detail = r.get("text") or r.get("error") or r.get("raw") or ""
            # Each thread writes its own pair_result immediately
            if run_id:
                try:
                    with _db() as conn:
                        conn.execute(
                            "INSERT INTO pair_results (run_id, pair_name, ok, detail, duration_ms) "
                            "VALUES (?,?,?,?,?)",
                            (run_id, agent["id"], 1 if ok else 0, detail[:500], r.get("elapsed_ms")),
                        )
                except sqlite3.Error:
                    pass
            return {
                "agent_id": agent["id"],
                "label": agent["label"],
                "ok": ok,
                "http_status": r.get("http_status"),
                "text": r.get("text", ""),
                "elapsed_ms": r.get("elapsed_ms"),
                "error": r.get("error"),
            }

        results: list[dict] = []

        if parallel:
            # Fire all agents concurrently. C3's _chat_lock will queue them
            # internally; C1 treats each as a distinct session via X-Agent-ID.
            # max_workers = number of agents so all start immediately.
            with ThreadPoolExecutor(max_workers=len(agents_to_run)) as pool:
                futures = {pool.submit(_run_one, agent): agent for agent in agents_to_run}
                # Collect in submission order for consistent table display
                ordered: dict = {}
                for fut in as_completed(futures):
                    agent = futures[fut]
                    try:
                        ordered[agent["id"]] = fut.result()
                    except Exception as exc:
                        ordered[agent["id"]] = {
                            "agent_id": agent["id"], "label": agent["label"],
                            "ok": False, "http_status": None,
                            "text": "", "elapsed_ms": None,
                            "error": str(exc),
                        }
            # Preserve original agent order
            results = [ordered[a["id"]] for a in agents_to_run if a["id"] in ordered]
        else:
            for agent in agents_to_run:
                results.append(_run_one(agent))

        wall_ms = int((time.monotonic() - wall_t0) * 1000)
        passed = sum(1 for r in results if r["ok"])
        failed = len(results) - passed
        finished_at = datetime.now(timezone.utc).isoformat()

        if run_id:
            try:
                with _db() as conn:
                    conn.execute(
                        "UPDATE validation_runs SET finished_at=?, passed=?, failed=?, raw_summary=? WHERE id=?",
                        (finished_at, passed, failed, f"{passed}/{len(results)} passed ({mode})", run_id),
                    )
            except sqlite3.Error:
                pass

        return jsonify({
            "run_id": run_id,
            "mode": mode,
            "started_at": started_at,
            "finished_at": finished_at,
            "wall_ms": wall_ms,
            "passed": passed,
            "failed": failed,
            "total": len(results),
            "prompt": prompt,
            "results": results,
        })

    @app.get("/api/validation-runs")
    def api_validation_runs():
        """Return last N validation runs with their pair results."""
        try:
            limit = max(1, min(50, int(request.args.get("limit", 10))))
        except ValueError:
            limit = 10
        runs = []
        try:
            with _db() as conn:
                run_rows = conn.execute(
                    "SELECT id, started_at, finished_at, mode, passed, failed, raw_summary "
                    "FROM validation_runs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                for run in run_rows:
                    run_dict = dict(run)
                    pairs = conn.execute(
                        "SELECT pair_name, ok, detail, duration_ms FROM pair_results WHERE run_id=? ORDER BY id",
                        (run["id"],),
                    ).fetchall()
                    run_dict["pairs"] = [dict(p) for p in pairs]
                    runs.append(run_dict)
        except sqlite3.Error:
            pass
        return jsonify(runs)

    @app.get("/api/logs")
    def api_logs():
        """JSON log rows filterable by agent_id with pagination."""
        agent_filter = request.args.get("agent", "").strip()
        try:
            limit = max(1, min(100, int(request.args.get("limit", 20))))
            offset = max(0, int(request.args.get("offset", 0)))
        except ValueError:
            limit, offset = 20, 0
        rows = []
        total = 0
        try:
            with _db() as conn:
                if agent_filter:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM chat_logs WHERE agent_id=?", (agent_filter,)
                    ).fetchone()[0]
                    rows = conn.execute(
                        "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status "
                        "FROM chat_logs WHERE agent_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                        (agent_filter, limit, offset),
                    ).fetchall()
                else:
                    total = conn.execute("SELECT COUNT(*) FROM chat_logs").fetchone()[0]
                    rows = conn.execute(
                        "SELECT id, created_at, agent_id, prompt_excerpt, response_excerpt, http_status "
                        "FROM chat_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                        (limit, offset),
                    ).fetchall()
        except sqlite3.Error:
            pass
        return jsonify({
            "total": total,
            "offset": offset,
            "limit": limit,
            "rows": [dict(r) for r in rows],
        })

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "6090"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
