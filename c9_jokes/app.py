"""
C9_JOKES — read-only validation console (Flask + SQLite).
Does not modify C1–C8; only HTTP GET/POST to peer URLs you configure.
"""
from __future__ import annotations

import os
import sqlite3
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

    TARGETS = {
        "c1":  {"env": "C1_URL",  "default": "http://localhost:8000",  "label": "C1 copilot-api",         "health": "/health"},
        "c2":  {"env": "C2_URL",  "default": "http://localhost:8080",  "label": "C2 agent-terminal",      "health": "/health"},
        "c3":  {"env": "C3_URL",  "default": "http://localhost:8001",  "label": "C3 browser-auth",        "health": "/health"},
        "c5":  {"env": "C5_URL",  "default": "http://localhost:8080",  "label": "C5 claude-code",         "health": "/health"},
        "c6":  {"env": "C6_URL",  "default": "http://localhost:8080",  "label": "C6 kilocode",            "health": "/health"},
        "c7a": {"env": "C7A_URL", "default": "http://localhost:18789", "label": "C7a openclaw-gateway",   "health": "/healthz"},
        "c7b": {"env": "C7B_URL", "default": "http://localhost:8080",  "label": "C7b openclaw-cli",       "health": "/health"},
        "c8":  {"env": "C8_URL",  "default": "http://localhost:8080",  "label": "C8 hermes-agent",        "health": "/health"},
    }

    def _urls() -> dict[str, str]:
        return {
            key: os.environ.get(t["env"], t["default"]).rstrip("/")
            for key, t in TARGETS.items()
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

    def _probe_all() -> list[dict]:
        urls = _urls()
        return [
            probe_health(TARGETS[key]["label"], urls[key], TARGETS[key]["health"])
            for key in TARGETS
        ]

    @app.get("/")
    def dashboard():
        probes = _probe_all()
        return render_template("dashboard.html", probes=probes, targets=TARGETS)

    @app.get("/health")
    def page_health():
        probes = _probe_all()
        urls = _urls()
        probes.append(probe_health("C3 /status", urls["c3"], "/status"))
        return render_template("health.html", probes=probes)

    AGENTS = [
        {"id": "c2-aider",       "label": "C2 Aider (OpenAI)"},
        {"id": "c5-claude-code", "label": "C5 Claude Code (Anthropic)"},
        {"id": "c6-kilocode",    "label": "C6 KiloCode (OpenAI)"},
        {"id": "c7-openclaw",    "label": "C7b OpenClaw"},
        {"id": "c8-hermes",      "label": "C8 Hermes Agent"},
        {"id": "c9-jokes",       "label": "C9 (generic session)"},
    ]

    @app.get("/pairs")
    def page_pairs():
        return render_template("pairs.html", agents=AGENTS)

    @app.get("/chat")
    def page_chat():
        urls = _urls()
        return render_template("chat.html", c1_url=urls["c1"], agents=AGENTS)

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
        urls = _urls()
        c1 = urls["c1"]
        data = None
        err = None
        try:
            r = requests.get(f"{c1}/v1/sessions", timeout=5)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        except requests.RequestException as e:
            err = str(e)
        return render_template("sessions.html", data=data, error=err, c1_url=c1)

    @app.get("/api")
    def page_api_reference():
        return render_template("api_reference.html", urls=_urls(), targets=TARGETS)

    @app.get("/api/status")
    def api_status():
        urls = _urls()
        result = {
            key: probe_health(key, urls[key], TARGETS[key]["health"])
            for key in TARGETS
        }
        result["ts"] = datetime.now(timezone.utc).isoformat()
        return jsonify(result)

    @app.post("/api/chat")
    def api_chat():
        """Proxy chat to C1 (OpenAI format). Body: {agent_id, prompt}."""
        c1 = _urls()["c1"]
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
                f"{c1}/v1/chat/completions",
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
