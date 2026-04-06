"""
Phase 1 + 2: Comprehensive audit of all C9 app layers.
Checks: all page routes, all API endpoints, DB integrity, UI elements,
        nav consistency, token-counter integration, SSE endpoints,
        multi-agent routes, session endpoints, CORS headers.
"""
import json, urllib.request, urllib.error, time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:6090"
issues = []   # (severity, category, title, detail)
ok_list = []

def issue(sev, cat, title, detail=""):
    issues.append((sev, cat, title, detail))

def ok(label):
    ok_list.append(label)

def get(path, expect=200):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=8)
        code = r.getcode()
        body = r.read().decode(errors="replace")
        if code != expect:
            issue("HIGH", "API", f"GET {path} returned {code} not {expect}", "")
            return None, code
        return body, code
    except urllib.error.HTTPError as e:
        issue("HIGH", "API", f"GET {path} HTTPError {e.code}", str(e))
        return None, e.code
    except Exception as e:
        issue("HIGH", "API", f"GET {path} EXCEPTION", str(e)[:120])
        return None, 0

def post(path, body, expect=200):
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(BASE + path, data=data,
              headers={"Content-Type":"application/json"}, method="POST")
        r = urllib.request.urlopen(req, timeout=8)
        code = r.getcode()
        resp = json.loads(r.read())
        if code != expect:
            issue("HIGH", "API", f"POST {path} returned {code}", "")
        return resp, code
    except urllib.error.HTTPError as e:
        issue("HIGH", "API", f"POST {path} HTTPError {e.code}", e.read().decode()[:200])
        return None, e.code
    except Exception as e:
        issue("HIGH", "API", f"POST {path} EXCEPTION", str(e)[:120])
        return None, 0

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 1 — Page routes (all HTML pages load without 500)
# ══════════════════════════════════════════════════════════════════════════════
pages = ["/", "/health", "/pairs", "/chat", "/logs", "/sessions",
         "/api", "/agent", "/multi-agent", "/multi-Agento", "/token-counter"]
for p in pages:
    body, code = get(p)
    if body and code == 200:
        ok(f"PAGE {p}")
    # Check brand on every page
    if body and "APP C9" not in body:
        issue("MED", "UI", f"Page {p} still shows old brand (not 'APP C9')", "")
    # Check nav has Token Counter link
    if body and "token-counter" not in body.lower() and "Token Counter" not in body:
        issue("MED", "UI", f"Page {p} nav missing Token Counter link", "")

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 2 — Core API endpoints
# ══════════════════════════════════════════════════════════════════════════════
body, _ = get("/api/status")
if body:
    d = json.loads(body)
    for k, v in d.items():
        if isinstance(v, dict) and not v.get("ok"):
            issue("HIGH", "BACKEND", f"Container {k} health FAIL", str(v))
        elif isinstance(v, dict):
            ok(f"Container {k} healthy")

body, _ = get("/api/session-health")
if body:
    d = json.loads(body)
    if d.get("session") not in ("active","expired","unknown"):
        issue("MED", "API", "/api/session-health missing session field", str(d))
    else:
        ok("/api/session-health returns session field")

body, _ = get("/api/chat/sessions")
if body:
    d = json.loads(body)
    if isinstance(d, list):
        ok(f"/api/chat/sessions returns list ({len(d)} sessions)")
    elif isinstance(d, dict) and "detail" in d:
        issue("HIGH", "API", "/api/chat/sessions returns 404/detail", str(d))

body, _ = get("/api/token-usage/summary")
if body:
    d = json.loads(body)
    if d.get("ok"):
        ok("/api/token-usage/summary works")
    else:
        issue("HIGH", "API", "/api/token-usage/summary returned ok=false", str(d))

body, _ = get("/api/token-usage/agents?days=7")
if body:
    d = json.loads(body)
    if not d.get("ok"):
        issue("HIGH", "API", "/api/token-usage/agents failed", str(d))
    else:
        ok("/api/token-usage/agents works")

body, _ = get("/api/token-usage/history?days=7")
if body:
    d = json.loads(body)
    if not d.get("ok"):
        issue("HIGH", "API", "/api/token-usage/history failed", str(d))
    else:
        ok("/api/token-usage/history works")

body, _ = get("/api/logs")
if body:
    d = json.loads(body)
    if "logs" not in d and "rows" not in d and not isinstance(d, list):
        issue("MED", "API", "/api/logs unexpected shape", str(d)[:120])
    else:
        ok("/api/logs works")

body, _ = get("/api/validation-runs?limit=5")
if body:
    ok("/api/validation-runs works")

body, _ = get("/api/multi-agent/sessions?limit=5")
if body:
    ok("/api/multi-agent/sessions works")

body, _ = get("/api/ma/sessions?limit=5")
if body:
    ok("/api/ma/sessions works")

body, _ = get("/api/agent/files")
if body:
    ok("/api/agent/files works")

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 3 — POST endpoints
# ══════════════════════════════════════════════════════════════════════════════
resp, code = post("/api/token-usage/record",
    {"agent_id":"c2-aider","page":"audit","tokens":100,"status":"ok"})
if resp and resp.get("ok"):
    ok("POST /api/token-usage/record works")
else:
    issue("HIGH","API","POST /api/token-usage/record failed", str(resp))

resp, code = post("/api/chat/summarize",
    {"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"}],
     "agent_id":"c9-jokes"})
if resp and resp.get("ok"):
    ok("POST /api/chat/summarize works")
else:
    issue("MED","API","POST /api/chat/summarize failed or slow", str(resp))

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 4 — CORS headers
# ══════════════════════════════════════════════════════════════════════════════
try:
    req = urllib.request.Request(BASE + "/api/status",
          headers={"Origin": "http://localhost:3000"}, method="GET")
    r = urllib.request.urlopen(req, timeout=5)
    headers = dict(r.headers)
    cors = headers.get("access-control-allow-origin","")
    if not cors:
        issue("LOW","CORS","No CORS header on /api/status — browser cross-origin calls will fail","")
    else:
        ok(f"CORS header present: {cors}")
except Exception as e:
    issue("LOW","CORS","CORS check failed",str(e)[:80])

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 5 — Playwright: UI/UX deep checks across all pages
# ══════════════════════════════════════════════════════════════════════════════
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page()
    errors_on_page = []
    pg.on("pageerror", lambda e: errors_on_page.append(str(e)))

    # --- Dashboard ---
    errors_on_page.clear()
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(1500)
    h1 = pg.locator("h1").first.inner_text().strip()
    if h1 != "APP C9":
        issue("HIGH","UI","Dashboard h1 not 'APP C9'", h1)
    else:
        ok("Dashboard brand APP C9")
    if pg.locator("#global-token-badge").count() == 0:
        issue("HIGH","UI","Global token badge missing from dashboard","")
    else:
        ok("Global token badge on dashboard")
    nav_count = pg.locator("nav a").count()
    if nav_count < 11:
        issue("MED","UI",f"Nav has only {nav_count} links (expected 11+)","")
    else:
        ok(f"Nav has {nav_count} links")
    if errors_on_page:
        issue("MED","JS",f"JS errors on dashboard", "; ".join(errors_on_page[:3]))

    # --- Chat page ---
    errors_on_page.clear()
    pg.goto(BASE + "/chat", wait_until="domcontentloaded")
    pg.wait_for_timeout(1000)
    for el_id in ["token-badge","new-chat-btn","sessions-btn","clear-chat",
                  "chat-window","chat-form","agent_id","ctx-banner","global-token-badge"]:
        if pg.locator(f"#{el_id}").count() == 0:
            issue("HIGH","UI",f"Chat page missing #{el_id}","")
        else:
            ok(f"Chat #{el_id} present")
    # sessions overlay hidden at start
    cls = pg.locator("#sess-overlay").get_attribute("class") or ""
    if "open" in cls:
        issue("MED","UI","Chat sessions overlay open at page load (should be hidden)","")
    else:
        ok("Chat sessions overlay hidden at start")
    # ctx-banner hidden at start
    style = pg.locator("#ctx-banner").get_attribute("style") or ""
    if "display: none" not in style and "display:none" not in style:
        # check visibility properly
        visible = pg.locator("#ctx-banner").is_visible()
        if visible:
            issue("MED","UI","Chat ctx-banner visible at page load (should be hidden)","")
        else:
            ok("Chat ctx-banner hidden at start")
    else:
        ok("Chat ctx-banner hidden (inline style)")
    if errors_on_page:
        issue("MED","JS",f"JS errors on /chat", "; ".join(errors_on_page[:3]))

    # --- Agent page ---
    errors_on_page.clear()
    pg.goto(BASE + "/agent", wait_until="domcontentloaded")
    pg.wait_for_timeout(1000)
    for el_id in ["agent-token-badge","btn-new-task","btn-run","agent-select",
                  "pool-indicator","notes-panel","global-token-badge"]:
        if pg.locator(f"#{el_id}").count() == 0:
            issue("HIGH","UI",f"Agent page missing #{el_id}","")
        else:
            ok(f"Agent #{el_id} present")
    if errors_on_page:
        issue("MED","JS","JS errors on /agent", "; ".join(errors_on_page[:3]))

    # --- Token Counter page ---
    errors_on_page.clear()
    pg.goto(BASE + "/token-counter", wait_until="domcontentloaded")
    pg.wait_for_timeout(2000)
    for el_id in ["tc-hero","stat-today","stat-period","stat-calls",
                  "stat-agents","stat-top-agent","agent-table","agent-tbody",
                  "history-tbody","page-tbody","days-select"]:
        if pg.locator(f"#{el_id}").count() == 0:
            issue("MED","UI",f"Token Counter missing #{el_id}","")
        else:
            ok(f"TC #{el_id} present")
    # Tabs functional
    tabs = pg.locator(".tc-tab").all()
    if len(tabs) < 3:
        issue("HIGH","UI",f"Token Counter only {len(tabs)} tabs (need 3)","")
    else:
        tabs[1].click(); pg.wait_for_timeout(300)
        if "active" not in (pg.locator("#panel-pages").get_attribute("class") or ""):
            issue("MED","UI","Token Counter Pages tab click does not activate panel","")
        else:
            ok("TC Pages tab click works")
        tabs[2].click(); pg.wait_for_timeout(300)
        if "active" not in (pg.locator("#panel-history").get_attribute("class") or ""):
            issue("MED","UI","Token Counter History tab click does not activate panel","")
        else:
            ok("TC History tab click works")
    # global badge on TC page
    val = pg.locator("#global-token-val").inner_text()
    if val in ["—",""]:
        issue("MED","UI","Global token badge shows '—' on Token Counter page (not fetching summary)","")
    else:
        ok(f"Global token badge value on TC page: {val}")
    if errors_on_page:
        issue("MED","JS","JS errors on /token-counter", "; ".join(errors_on_page[:3]))

    # --- Multi-Agent page ---
    errors_on_page.clear()
    pg.goto(BASE + "/multi-agent", wait_until="domcontentloaded")
    pg.wait_for_timeout(800)
    if pg.locator("h1").count() == 0:
        issue("HIGH","UI","Multi-agent page has no h1","")
    else:
        ok("Multi-agent page loads")
    if errors_on_page:
        issue("MED","JS","JS errors on /multi-agent", "; ".join(errors_on_page[:3]))

    # --- multi-Agento page ---
    errors_on_page.clear()
    pg.goto(BASE + "/multi-Agento", wait_until="domcontentloaded")
    pg.wait_for_timeout(800)
    if errors_on_page:
        issue("MED","JS","JS errors on /multi-Agento", "; ".join(errors_on_page[:3]))
    else:
        ok("multi-Agento no JS errors")

    # --- Pairs page ---
    errors_on_page.clear()
    pg.goto(BASE + "/pairs", wait_until="domcontentloaded")
    pg.wait_for_timeout(800)
    if errors_on_page:
        issue("MED","JS","JS errors on /pairs", "; ".join(errors_on_page[:3]))
    else:
        ok("Pairs no JS errors")

    # --- Sessions page ---
    errors_on_page.clear()
    pg.goto(BASE + "/sessions", wait_until="domcontentloaded")
    pg.wait_for_timeout(800)
    if errors_on_page:
        issue("MED","JS","JS errors on /sessions", "; ".join(errors_on_page[:3]))
    else:
        ok("Sessions page no JS errors")

    # ── Bounty Hunter 6: Network requests returning 4xx/5xx from pages ─────
    network_errors = []
    def on_response(resp):
        if resp.status >= 400 and "favicon" not in resp.url:
            network_errors.append(f"{resp.status} {resp.url}")
    pg.on("response", on_response)

    for path in ["/", "/chat", "/agent", "/token-counter", "/multi-agent"]:
        network_errors.clear()
        pg.goto(BASE + path, wait_until="networkidle", timeout=15000)
        pg.wait_for_timeout(1500)
        if network_errors:
            issue("HIGH","NETWORK",f"Page {path} triggers {len(network_errors)} failed requests",
                  "; ".join(network_errors[:4]))
        else:
            ok(f"Page {path} no network errors")

    b.close()

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 7 — Docker container health
# ══════════════════════════════════════════════════════════════════════════════
import subprocess
result = subprocess.run(
    ["docker","ps","--format","{{.Names}}\\t{{.Status}}"],
    capture_output=True, text=True)
for line in result.stdout.strip().splitlines():
    parts = line.split("\t")
    if len(parts) == 2:
        name, status = parts
        if "unhealthy" in status.lower():
            issue("HIGH","DOCKER",f"Container {name} UNHEALTHY", status)
        elif "starting" in status.lower():
            issue("MED","DOCKER",f"Container {name} still STARTING", status)
        else:
            ok(f"Container {name}: {status[:30]}")

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 8 — DB integrity check inside C9 container
# ══════════════════════════════════════════════════════════════════════════════
db_check = subprocess.run(
    ["docker","exec","C9_jokes","python3","-c",
     """
import sqlite3
from pathlib import Path
db = Path('/app/data/c9.db')
if not db.exists():
    print('DB_MISSING')
else:
    with sqlite3.connect(db) as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print('TABLES:', ','.join(sorted(tables)))
        for t in tables:
            n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n} rows")
"""],
    capture_output=True, text=True)
db_out = db_check.stdout.strip()
if "DB_MISSING" in db_out:
    issue("HIGH","DB","SQLite DB file missing at /app/data/c9.db","")
else:
    expected_tables = ["chat_logs","agent_sessions","agent_messages",
                       "multi_agent_sessions","chat_sessions","chat_messages",
                       "token_usage","ma_sessions","ma_projects","ma_pane_messages"]
    if "TABLES:" in db_out:
        present = db_out.split("TABLES:")[1].split("\n")[0].strip().split(",")
        for t in expected_tables:
            if t not in present:
                issue("HIGH","DB",f"DB table '{t}' missing", str(present))
            else:
                ok(f"DB table {t} exists")
    else:
        issue("HIGH","DB","Could not read DB tables", db_out[:200])

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 9 — Starlette version check (ensure no broken 1.0.0)
# ══════════════════════════════════════════════════════════════════════════════
ver_check = subprocess.run(
    ["docker","exec","C9_jokes","python3","-c",
     "import starlette; print(starlette.__version__)"],
    capture_output=True, text=True)
starlette_ver = ver_check.stdout.strip()
if starlette_ver == "1.0.0":
    issue("HIGH","DOCKER","Starlette 1.0.0 still installed — causes Jinja2 unhashable cache bug",
          "Run: pip install 'starlette>=0.41,<0.47'")
else:
    ok(f"Starlette version: {starlette_ver}")

# ══════════════════════════════════════════════════════════════════════════════
# BOUNTY HUNTER 10 — SSE endpoint smoke test (/api/agent/run)
# ══════════════════════════════════════════════════════════════════════════════
try:
    req = urllib.request.Request(
        BASE + "/api/agent/run?task=test&agent_id=c9-jokes&max_steps=1",
        headers={"Accept":"text/event-stream"})
    r = urllib.request.urlopen(req, timeout=5)
    first = r.read(512).decode(errors="replace")
    if "data:" in first or "event:" in first:
        ok("SSE /api/agent/run streams data")
    else:
        issue("MED","API","SSE /api/agent/run not streaming expected data", first[:120])
    r.close()
except Exception as e:
    issue("MED","API","SSE /api/agent/run exception", str(e)[:120])

# ══════════════════════════════════════════════════════════════════════════════
# PRINT REPORT
# ══════════════════════════════════════════════════════════════════════════════
SEV_ORDER = {"HIGH":0,"MED":1,"LOW":2}
issues.sort(key=lambda x: SEV_ORDER.get(x[0],3))

print()
print("=" * 70)
print("  APP C9 — PHASE 1+2 AUDIT REPORT")
print(f"  {len(ok_list)} checks passed   {len(issues)} issues found")
print("=" * 70)

if issues:
    print()
    print("  ── ISSUES (ranked by severity) ─────────────────────────────────────")
    for i,(sev,cat,title,detail) in enumerate(issues,1):
        badge = {"HIGH":"🔴","MED":"🟡","LOW":"🔵"}.get(sev,"⚪")
        print(f"  #{i:02d} {badge} [{sev}] [{cat}] {title}")
        if detail:
            print(f"       → {detail[:100]}")
else:
    print("\n  ✅ No issues found!")

print()
print("  ── PASSING CHECKS ──────────────────────────────────────────────────")
for item in ok_list:
    print(f"  ✅ {item}")
print("=" * 70)
print()
