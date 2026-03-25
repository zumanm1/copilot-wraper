"""
C9_JOKES — read-only validation console (Flask + SQLite).
Does not modify C1–C8; only HTTP GET/POST to peer URLs you configure.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("DATABASE_PATH", "/app/data/c9.db"))


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
    )

    def _urls() -> dict[str, str]:
        return {
            "c1": os.environ.get("C1_URL", "http://localhost:8000").rstrip("/"),
            "c3": os.environ.get("C3_URL", "http://localhost:8001").rstrip("/"),
            "c7a": os.environ.get("C7A_URL", "http://localhost:18789").rstrip("/"),
        }

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

    def probe_health(name: str, url: str, path: str) -> dict:
        full = f"{url}{path}"
        try:
            r = requests.get(full, timeout=5)
            body = r.text[:2000]
            try:
                parsed = r.json()
            except Exception:
                parsed = {"raw": body[:500]}
            return {
                "name": name,
                "url": full,
                "ok": r.status_code == 200,
                "http_status": r.status_code,
                "body": parsed,
            }
        except requests.RequestException as e:
            return {
                "name": name,
                "url": full,
                "ok": False,
                "http_status": None,
                "error": str(e),
            }

    @app.get("/")
    def dashboard():
        urls = _urls()
        probes = [
            probe_health("C1", urls["c1"], "/health"),
            probe_health("C3", urls["c3"], "/health"),
            probe_health("C7a", urls["c7a"], "/healthz"),
        ]
        return render_template("dashboard.html", probes=probes, urls=urls)

    @app.get("/health")
    def page_health():
        urls = _urls()
        probes = [
            probe_health("C1", urls["c1"], "/health"),
            probe_health("C3", urls["c3"], "/health"),
            probe_health("C3 status", urls["c3"], "/status"),
            probe_health("C7a", urls["c7a"], "/healthz"),
        ]
        return render_template("health.html", probes=probes)

    @app.get("/pairs")
    def page_pairs():
        return render_template(
            "pairs.html",
            hint="Run host script: python3 tests/validate_all_agents.py [--parallel]",
        )

    @app.get("/chat")
    def page_chat():
        return render_template("chat.html", c1_url=_urls()["c1"])

    @app.get("/logs")
    def page_logs():
        rows = []
        try:
            with sqlite3.connect(DEFAULT_DB) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, created_at, agent_id, prompt_excerpt, http_status "
                    "FROM chat_logs ORDER BY id DESC LIMIT 50"
                ).fetchall()
        except sqlite3.Error:
            rows = []
        return render_template("logs.html", rows=rows)

    @app.get("/sessions")
    def page_sessions():
        urls = _urls()["c1"]
        data = None
        err = None
        try:
            r = requests.get(f"{urls}/v1/sessions", timeout=5)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        except requests.RequestException as e:
            err = str(e)
        return render_template("sessions.html", data=data, error=err, c1_url=urls)

    @app.get("/api")
    def page_api_reference():
        return render_template("api_reference.html", urls=_urls())

    @app.get("/api/status")
    def api_status():
        urls = _urls()
        return jsonify(
            {
                "c1": probe_health("c1", urls["c1"], "/health"),
                "c3": probe_health("c3", urls["c3"], "/health"),
                "c7a": probe_health("c7a", urls["c7a"], "/healthz"),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.post("/api/chat")
    def api_chat():
        """Proxy chat to C1 (OpenAI format). Body: {agent_id, prompt}."""
        urls = _urls()["c1"]
        payload_in = request.get_json(silent=True) or {}
        agent_id = (payload_in.get("agent_id") or "c9-jokes").strip()
        prompt = (payload_in.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"ok": False, "error": "prompt required"}), 400
        body = {
            "model": "copilot",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        try:
            r = requests.post(
                f"{urls}/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "X-Agent-ID": agent_id,
                },
                json=body,
                timeout=120,
            )
            text = ""
            if r.ok:
                try:
                    d = r.json()
                    text = (
                        d.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                except Exception:
                    text = r.text[:2000]
            excerpt_p = prompt[:200]
            excerpt_r = (text or "")[:500]
            try:
                with sqlite3.connect(DEFAULT_DB) as conn:
                    conn.execute(
                        "INSERT INTO chat_logs (created_at, agent_id, prompt_excerpt, response_excerpt, http_status) "
                        "VALUES (?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            agent_id,
                            excerpt_p,
                            excerpt_r,
                            r.status_code,
                        ),
                    )
            except sqlite3.Error:
                pass
            return jsonify(
                {
                    "ok": r.ok,
                    "http_status": r.status_code,
                    "text": text,
                    "raw": r.text[:2000] if not r.ok else None,
                }
            )
        except requests.RequestException as e:
            return jsonify({"ok": False, "error": str(e)}), 502

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "6090"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
