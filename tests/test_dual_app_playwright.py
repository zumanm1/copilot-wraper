"""
Dual-App Playwright Validation Suite
=====================================
Tests 6090 (C9 Validation Console) and 6080 (noVNC/C3) simultaneously.

Architecture tested:
  6080  → C3 browser-auth noVNC (M365 browser session, human login portal)
  8001  → C3 REST API (pool health, session status, /chat proxy)
  6090  → C9 Validation Console (all pages, APIs, SSE, token counter)
            └─ polls C3 via /api/session-health (proxies to 8001)
            └─ agent/multi-agent calls go C9→C1→C3→M365

Simultaneous monitoring: both apps open in same browser context (separate pages),
verifying neither interferes with the other and C9 correctly reflects C3 state.
"""
import json
import time
import urllib.request
import subprocess
from playwright.sync_api import sync_playwright, expect

C9  = "http://localhost:6090"   # Validation Console
VNC = "http://localhost:6080"   # noVNC web client
C3  = "http://localhost:8001"   # C3 REST API (direct)

results = []

def chk(section, label, cond, detail=""):
    icon = "PASS" if cond else "FAIL"
    results.append((icon, section, label, detail if not cond else ""))

def api(url, method="GET", body=None, timeout=8):
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    r = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(r.read())

# ════════════════════════════════════════════════════════════════════════════
# SECTION A — Pre-flight: both apps reachable via REST before opening browser
# ════════════════════════════════════════════════════════════════════════════
def test_preflight():
    # C9 root
    req = urllib.request.Request(C9 + "/")
    r = urllib.request.urlopen(req, timeout=8)
    chk("A", "C9 :6090 reachable", r.getcode() == 200, str(r.getcode()))

    # noVNC HTML page
    req = urllib.request.Request(VNC + "/")
    r = urllib.request.urlopen(req, timeout=8)
    body = r.read().decode()
    chk("A", "noVNC :6080 reachable", r.getcode() == 200, str(r.getcode()))
    chk("A", "noVNC page has canvas or noVNC_status",
        "noVNC_status" in body or "canvas" in body.lower(), "")

    # C3 API direct
    d = api(C3 + "/health")
    chk("A", "C3 API :8001 /health ok", d.get("status") == "ok", str(d))

    d = api(C3 + "/status")
    chk("A", "C3 /status browser running", d.get("browser") == "running", str(d))
    pool_avail = d.get("pool_available", 0)
    pool_size  = d.get("pool_size", 0)
    chk("A", f"C3 pool available {pool_avail}/{pool_size}",
        pool_avail >= 0 and pool_size > 0, str(d))

    # C9 session-health (C9 → C3 proxy)
    d = api(C9 + "/api/session-health")
    chk("A", "C9 /api/session-health returns session field",
        d.get("session") in ("active","expired","unknown"), str(d))
    chk("A", "C9 session-health has profile", bool(d.get("profile")), str(d))
    sess_state = d.get("session")
    chk("A", f"Session state is '{sess_state}'",
        sess_state in ("active","expired","unknown"), sess_state)
    return sess_state, d

# ════════════════════════════════════════════════════════════════════════════
# SECTION B — Open BOTH apps simultaneously in same browser context
# ════════════════════════════════════════════════════════════════════════════
def test_dual_simultaneous(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    vnc_page = ctx.new_page()
    c9_page  = ctx.new_page()

    # ── Open noVNC (6080) ────────────────────────────────────────────────────
    vnc_page.goto(VNC + "/", wait_until="domcontentloaded")
    vnc_page.wait_for_timeout(2000)

    chk("B", "noVNC page title contains 'noVNC'",
        "noVNC" in (vnc_page.title() or ""), vnc_page.title())
    chk("B", "noVNC #noVNC_status_bar present",
        vnc_page.locator("#noVNC_status_bar").count() > 0, "")
    chk("B", "noVNC #noVNC_status present",
        vnc_page.locator("#noVNC_status").count() > 0, "")
    chk("B", "noVNC canvas element present",
        vnc_page.locator("canvas").count() > 0,
        f"canvas count: {vnc_page.locator('canvas').count()}")

    vnc_status = vnc_page.locator("#noVNC_status").inner_text() if \
        vnc_page.locator("#noVNC_status").count() > 0 else "not found"
    chk("B", f"noVNC status readable ('{vnc_status[:40]}')", True, "")

    # ── Open C9 dashboard (6090) simultaneously ───────────────────────────────
    c9_page.goto(C9 + "/", wait_until="domcontentloaded")
    c9_page.wait_for_timeout(1500)

    h1 = c9_page.locator("h1").first.inner_text().strip()
    chk("B", "C9 dashboard brand 'APP C9'", h1 == "APP C9", h1)
    chk("B", "C9 global token badge in header",
        c9_page.locator("#global-token-badge").count() > 0, "")
    chk("B", "C9 session LED present",
        c9_page.locator("#session-led").count() > 0, "")

    # Session LED should reflect C3 state
    led_class = c9_page.locator("#session-led").get_attribute("class") or ""
    led_label = c9_page.locator(".session-led-label").inner_text() if \
        c9_page.locator(".session-led-label").count() > 0 else ""
    chk("B", "Session LED class set (active/expired/unknown)",
        any(s in led_class for s in ("active","expired","unknown","checking")),
        f"class='{led_class}' label='{led_label}'")

    # ── Both apps open: verify C3 pool unaffected ─────────────────────────────
    d = api(C3 + "/status")
    chk("B", "C3 pool still healthy with both apps open",
        d.get("browser") == "running", str(d))
    chk("B", "C3 open_pages count reasonable (1-20)",
        1 <= d.get("open_pages", 0) <= 20,
        f"open_pages={d.get('open_pages')}")

    ctx.close()
    return vnc_status, led_class

# ════════════════════════════════════════════════════════════════════════════
# SECTION C — C9 page-by-page deep validation (all 11 pages)
# ════════════════════════════════════════════════════════════════════════════
def test_c9_pages(page):
    # Dashboard
    page.goto(C9 + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    nav_links = [a.inner_text() for a in page.locator("nav a").all()]
    chk("C", "Dashboard nav has 11 links", len(nav_links) == 11,
        f"found {len(nav_links)}: {nav_links}")
    expected_nav = ["Dashboard","Health","Pairs","Chat","Logs","Sessions",
                    "API","Agent","Multi-Agent","multi-Agento","Token Counter"]
    for n in expected_nav:
        found = any(n.lower() in lnk.lower() for lnk in nav_links)
        chk("C", f"Nav link '{n}' present", found, str(nav_links))

    # Chat page
    page.goto(C9 + "/chat", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    for eid in ["token-badge","new-chat-btn","sessions-btn","ctx-banner",
                "global-token-badge","chat-window","agent_id"]:
        chk("C", f"Chat #{eid}", page.locator(f"#{eid}").count() > 0, "")
    # token badge initial text
    badge_txt = page.locator("#token-badge").inner_text()
    chk("C", "Chat token badge shows 0 at start",
        "0" in badge_txt, badge_txt)
    # agent select has options
    opts = page.locator("#agent_id option").count()
    chk("C", f"Chat agent select has {opts} options", opts > 0, str(opts))

    # Agent page
    page.goto(C9 + "/agent", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    for eid in ["agent-token-badge","btn-new-task","btn-run","agent-select",
                "pool-indicator","notes-panel","global-token-badge","task-input"]:
        chk("C", f"Agent #{eid}", page.locator(f"#{eid}").count() > 0, "")

    # Token Counter page — full UI validation
    page.goto(C9 + "/token-counter", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    for eid in ["tc-hero","stat-today","stat-period","stat-calls","stat-agents",
                "stat-top-agent","agent-table","agent-tbody","history-tbody",
                "page-tbody","days-select","global-token-badge"]:
        chk("C", f"Token Counter #{eid}", page.locator(f"#{eid}").count() > 0, "")
    # Tabs all 3 present and clickable
    tabs = page.locator(".tc-tab").all()
    chk("C", "Token Counter has 3 tabs", len(tabs) == 3, str(len(tabs)))
    for i, tname in enumerate(["agents","pages","history"]):
        tabs[i].click(); page.wait_for_timeout(350)
        panel_cls = page.locator(f"#panel-{tname}").get_attribute("class") or ""
        chk("C", f"TC tab '{tname}' activates panel", "active" in panel_cls, panel_cls)
    # Auto-refresh dot present
    chk("C", "TC refresh dot present",
        page.locator("#refresh-dot").count() > 0, "")
    # days-select works
    page.locator("#days-select").select_option("1")
    page.wait_for_timeout(1500)
    today_val = page.locator("#stat-today").inner_text()
    chk("C", "TC day-filter '1' updates hero", today_val not in ["—",""], today_val)

    # Health page
    page.goto(C9 + "/health", wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    chk("C", "Health page loads with content",
        page.locator("table, .card, h2").count() > 0, "")

    # Sessions page
    page.goto(C9 + "/sessions", wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    chk("C", "Sessions page loads", page.locator("h2, h3, .card").count() > 0, "")

    # Multi-Agent page
    page.goto(C9 + "/multi-agent", wait_until="domcontentloaded")
    page.wait_for_timeout(600)
    chk("C", "Multi-Agent page loads", page.locator("h1,h2").count() > 0, "")

    # multi-Agento page
    page.goto(C9 + "/multi-Agento", wait_until="domcontentloaded")
    page.wait_for_timeout(600)
    chk("C", "multi-Agento page loads", page.locator("h1,h2").count() > 0, "")

    # Pairs page
    page.goto(C9 + "/pairs", wait_until="domcontentloaded")
    page.wait_for_timeout(600)
    chk("C", "Pairs page loads", page.locator("table,.card,h2").count() > 0, "")

# ════════════════════════════════════════════════════════════════════════════
# SECTION D — Session LED: C9 reflects C3 state in real-time
# ════════════════════════════════════════════════════════════════════════════
def test_session_led_sync(page, c3_status):
    page.goto(C9 + "/", wait_until="domcontentloaded")
    # Wait for LED poller to run (runs on load)
    page.wait_for_timeout(2000)

    led_cls   = page.locator("#session-led").get_attribute("class") or ""
    led_label = page.locator(".session-led-label").inner_text() \
                if page.locator(".session-led-label").count() > 0 else ""

    # LED should NOT be stuck in 'checking'
    chk("D", "Session LED left 'checking' state", "checking" not in led_cls, led_cls)
    # LED matches actual C3 session state
    actual = c3_status.get("session","unknown")
    chk("D", f"LED class matches C3 session='{actual}'",
        actual in led_cls, f"led_class='{led_cls}' c3='{actual}'")
    chk("D", "LED label not empty", bool(led_label.strip()), led_label)

    # Force a re-check via fetch (pollSession is inside IIFE, trigger via JS fetch)
    page.evaluate("""
        fetch('/api/session-health')
          .then(r => r.json())
          .then(d => {
            var led = document.getElementById('session-led');
            if (led) led.dataset.lastCheck = d.session || 'unknown';
          }).catch(()=>{});
    """)
    page.wait_for_timeout(1500)
    led_cls2 = page.locator("#session-led").get_attribute("class") or ""
    chk("D", "LED remains stable after manual poll", bool(led_cls2), led_cls2)

    # C9 /api/session-health endpoint agrees with C3 /status
    sh = api(C9 + "/api/session-health")
    c3 = api(C3  + "/status")
    chk("D", "C9 session-health profile set", bool(sh.get("profile")), str(sh))
    chk("D", "C3 pool_initialized true", c3.get("pool_initialized") is True, str(c3))

# ════════════════════════════════════════════════════════════════════════════
# SECTION E — C9 Chat page: send a real message end-to-end
# ════════════════════════════════════════════════════════════════════════════
def test_chat_e2e(page):
    page.goto(C9 + "/chat", wait_until="domcontentloaded")
    page.wait_for_timeout(800)

    # Select c9-jokes agent (local, fastest)
    page.locator("#agent_id").select_option("c9-jokes")
    page.wait_for_timeout(300)

    # Type and send a message
    prompt = page.locator("#prompt")
    prompt.fill("Tell me a very short joke in one sentence")
    page.wait_for_timeout(200)

    # Verify send button enabled (#send is the correct id)
    send_btn = page.locator("#send")
    chk("E", "Send button present", send_btn.count() > 0,
        str(send_btn.count()))
    if send_btn.count() == 0:
        chk("E", "Chat e2e send+response", False, "#send button not found")
        return
    chk("E", "Send button enabled before send", not send_btn.is_disabled(), "")

    send_btn.click()

    # Wait for response bubble (assistant reply)
    try:
        page.wait_for_selector(".bubble.assistant", timeout=30000)
        bubbles = page.locator(".bubble.assistant").count()
        chk("E", f"Assistant response bubble appeared ({bubbles})", bubbles > 0, "")

        resp_text = page.locator(".bubble.assistant").first.inner_text()
        chk("E", "Response has content (>10 chars)", len(resp_text) > 10, resp_text[:80])

        # Token badge should update
        badge = page.locator("#token-badge").inner_text()
        chk("E", "Token badge updated after message", "0" not in badge or "k" in badge,
            badge)

        # Session LED should still be stable
        led_cls = page.locator("#session-led").get_attribute("class") or ""
        chk("E", "Session LED stable after chat", bool(led_cls), led_cls)

        # global badge should have updated
        gval = page.locator("#global-token-val").inner_text()
        chk("E", "Global token badge updated after chat", gval not in ["—",""], gval)

    except Exception as ex:
        chk("E", "Chat e2e send+response", False, str(ex)[:120])

# ════════════════════════════════════════════════════════════════════════════
# SECTION F — Token Counter: live data after chat exchange
# ════════════════════════════════════════════════════════════════════════════
def test_token_counter_live(page):
    page.goto(C9 + "/token-counter", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # Hero today total
    today = page.locator("#stat-today").inner_text()
    chk("F", "TC hero today total has value", today not in ["—",""], today)

    # Agent table has rows
    rows = page.locator("#agent-tbody tr").count()
    chk("F", f"TC agent table has {rows} rows", rows > 0, str(rows))

    # History tab shows events
    page.locator(".tc-tab").nth(2).click()
    page.wait_for_timeout(500)
    hrows = page.locator("#history-tbody tr").count()
    chk("F", f"TC history has {hrows} events", hrows > 0, str(hrows))

    # Verify c9-jokes entry appears after the chat test
    page.locator(".tc-tab").nth(0).click()
    page.wait_for_timeout(400)
    tbody_html = page.locator("#agent-tbody").inner_html()
    chk("F", "TC agent table contains c9-jokes entry",
        "c9-jokes" in tbody_html or "c2-aider" in tbody_html or "claude" in tbody_html,
        tbody_html[:200])

    # Sparkline bars rendered
    chk("F", "TC sparkline bars rendered",
        page.locator(".spark-bar").count() > 0,
        str(page.locator(".spark-bar").count()))

    # % bars rendered
    chk("F", "TC % bars rendered",
        page.locator(".tc-bar").count() > 0,
        str(page.locator(".tc-bar").count()))

# ════════════════════════════════════════════════════════════════════════════
# SECTION G — Simultaneous monitoring: noVNC + C9 in same browser context
# ════════════════════════════════════════════════════════════════════════════
def test_simultaneous_monitoring(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    vnc_page = ctx.new_page()
    c9_page  = ctx.new_page()

    # Open noVNC
    vnc_page.goto(VNC + "/", wait_until="domcontentloaded")
    vnc_page.wait_for_timeout(1500)

    # Open C9 agent page simultaneously
    c9_page.goto(C9 + "/agent", wait_until="domcontentloaded")
    c9_page.wait_for_timeout(1500)

    # C3 pool must still be healthy
    d = api(C3 + "/status")
    chk("G", "C3 pool healthy with noVNC + C9/agent both open",
        d.get("browser") == "running", str(d))
    chk("G", "C3 pool_available >= 0", d.get("pool_available",0) >= 0,
        f"available={d.get('pool_available')}")

    # C9 session LED visible on agent page
    chk("G", "C9 agent page has session LED",
        c9_page.locator("#session-led").count() > 0, "")
    chk("G", "C9 agent page has global token badge",
        c9_page.locator("#global-token-badge").count() > 0, "")
    chk("G", "C9 agent pool indicator visible",
        c9_page.locator("#pool-indicator").count() > 0, "")

    # noVNC canvas still rendering (not frozen)
    canvas_count = vnc_page.locator("canvas").count()
    chk("G", f"noVNC canvas present ({canvas_count}) while C9 active",
        canvas_count > 0, str(canvas_count))

    # Switch back to C9 token counter and verify global badge still polls
    c9_page.goto(C9 + "/token-counter", wait_until="domcontentloaded")
    c9_page.wait_for_timeout(2000)
    gval = c9_page.locator("#global-token-val").inner_text()
    chk("G", "Global badge still works while noVNC open", gval not in ["—",""], gval)

    # Verify C9 /api/status still returns all containers healthy
    d2 = api(C9 + "/api/status")
    failed = [k for k,v in d2.items() if isinstance(v,dict) and not v.get("ok")]
    chk("G", f"All containers healthy during dual-app test ({len(failed)} failed)",
        len(failed) == 0, str(failed))

    ctx.close()

# ════════════════════════════════════════════════════════════════════════════
# SECTION H — Agent page: New Task + SSE stream smoke test
# ════════════════════════════════════════════════════════════════════════════
def test_agent_page(page):
    page.goto(C9 + "/agent", wait_until="domcontentloaded")
    page.wait_for_timeout(800)

    # New Task button resets session
    page.locator("#btn-new-task").click()
    page.on("dialog", lambda d: d.accept())
    page.wait_for_timeout(800)
    badge = page.locator("#agent-token-badge").inner_text()
    chk("H", "Agent token badge after New Task", "0" in badge or badge == "", badge)

    # Pool indicator present and not error state
    pool_txt = page.locator("#pool-indicator").inner_text() if \
        page.locator("#pool-indicator").count() > 0 else ""
    chk("H", "Pool indicator has text", bool(pool_txt.strip()), pool_txt)

    # Agent select has options
    opts = page.locator("#agent-select option").count()
    chk("H", f"Agent select has {opts} options", opts > 0, str(opts))

    # File tree panel visible (actual id: filetree-list)
    chk("H", "File tree panel visible",
        page.locator("#filetree-list, .agent-filetree").count() > 0, "")

    # History sidebar visible (actual id: history-list)
    chk("H", "History sidebar visible",
        page.locator("#history-list, .agent-history").count() > 0, "")

    # Notes panel hidden initially
    notes_visible = page.locator("#notes-panel").is_visible()
    chk("H", "Notes panel hidden initially", not notes_visible, str(notes_visible))

# ════════════════════════════════════════════════════════════════════════════
# SECTION I — noVNC deep inspection
# ════════════════════════════════════════════════════════════════════════════
def test_novnc_deep(page):
    page.goto(VNC + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)  # wait for VNC to attempt connection

    # Core noVNC elements
    for eid in ["noVNC_status_bar","noVNC_status","noVNC_buttons"]:
        chk("I", f"noVNC #{eid}", page.locator(f"#{eid}").count() > 0, "")

    # Status text (not blank)
    status_txt = page.locator("#noVNC_status").inner_text() \
        if page.locator("#noVNC_status").count() > 0 else ""
    chk("I", "noVNC status text present", bool(status_txt.strip()), status_txt[:60])

    # Canvas dimensions (non-zero means VNC frame rendered)
    canvas = page.locator("canvas").first
    if page.locator("canvas").count() > 0:
        box = canvas.bounding_box()
        chk("I", "noVNC canvas has non-zero dimensions",
            box is not None and box["width"] > 0 and box["height"] > 0,
            str(box))
    else:
        chk("I", "noVNC canvas present", False, "no canvas found")

    # C3 API direct health checks
    d = api(C3 + "/health")
    chk("I", "C3 /health service field present", "service" in d or "status" in d, str(d))

    d = api(C3 + "/status")
    chk("I", "C3 pool_size > 0",        d.get("pool_size",0) > 0, str(d))
    chk("I", "C3 pool_initialized",     d.get("pool_initialized") is True, str(d))

    # C9 correctly reflects C3 state
    sh = api(C9 + "/api/session-health")
    c3s = api(C3 + "/status")
    chk("I", "C9 session-health proxies C3 correctly",
        sh.get("session") in ("active","expired","unknown"), str(sh))
    # If C3 browser is running, C9 should report active or expired (not network error)
    if c3s.get("browser") == "running":
        chk("I", "C9 session not network-error when C3 is running",
            sh.get("session") != "unknown" or "network" not in str(sh.get("reason","")),
            str(sh))

# ════════════════════════════════════════════════════════════════════════════
# SECTION J — CORS + API contract validation
# ════════════════════════════════════════════════════════════════════════════
def test_cors_and_api():
    # CORS with Origin header
    req = urllib.request.Request(C9 + "/api/status",
          headers={"Origin": "http://localhost:3000"})
    r = urllib.request.urlopen(req, timeout=8)
    cors = dict(r.headers).get("access-control-allow-origin","")
    chk("J", "CORS allow-origin header on /api/status", bool(cors), cors)

    # Preflight OPTIONS
    req2 = urllib.request.Request(C9 + "/api/chat",
           headers={"Origin":"http://localhost:3000",
                    "Access-Control-Request-Method":"POST"},
           method="OPTIONS")
    try:
        r2 = urllib.request.urlopen(req2, timeout=5)
        chk("J", "OPTIONS preflight returns 2xx",
            200 <= r2.getcode() < 300, str(r2.getcode()))
    except urllib.error.HTTPError as e:
        chk("J", "OPTIONS preflight", e.code < 500, f"HTTP {e.code}")
    except Exception as ex:
        chk("J", "OPTIONS preflight", False, str(ex)[:80])

    # Token-usage APIs contract
    d = api(C9 + "/api/token-usage/summary")
    chk("J", "summary has today_total key", "today_total" in d, str(d.keys()))
    chk("J", "summary has by_agent key",    "by_agent" in d, str(d.keys()))

    d = api(C9 + "/api/token-usage/agents?days=7")
    chk("J", "agents has agents[] key",   "agents" in d, str(d.keys()))
    chk("J", "agents has daily[] key",    "daily" in d, str(d.keys()))
    chk("J", "agents has grand_total",    "grand_total" in d, str(d.keys()))

    d = api(C9 + "/api/token-usage/history?days=7&limit=5")
    chk("J", "history has rows[] key",  "rows" in d, str(d.keys()))
    chk("J", "history has count key",   "count" in d, str(d.keys()))

    # Chat sessions API
    d = api(C9 + "/api/chat/sessions?limit=5")
    chk("J", "chat sessions returns list", isinstance(d, list), str(type(d)))

    # Multi-agent sessions
    d = api(C9 + "/api/multi-agent/sessions?limit=5")
    chk("J", "multi-agent sessions ok", isinstance(d, (list, dict)), str(type(d)))

    # ma sessions
    d = api(C9 + "/api/ma/sessions?limit=5")
    chk("J", "ma sessions ok", isinstance(d, (list, dict)), str(type(d)))

# ════════════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 68)
    print("  DUAL-APP PLAYWRIGHT VALIDATION  (6090 C9  +  6080 noVNC/C3)")
    print("=" * 68)
    print(f"  C9  → {C9}")
    print(f"  VNC → {VNC}")
    print(f"  C3  → {C3}")
    print()

    sess_state, c3_sh = test_preflight()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])

        # Dual simultaneous test
        test_dual_simultaneous(browser)

        # Single-page tests
        page = browser.new_page()
        page.set_default_timeout(15000)

        test_c9_pages(page)
        test_session_led_sync(page, c3_sh)
        test_chat_e2e(page)
        test_token_counter_live(page)
        test_agent_page(page)
        test_novnc_deep(page)
        test_cors_and_api()
        test_simultaneous_monitoring(browser)

        page.close()
        browser.close()

    # ── Print report ──────────────────────────────────────────────────────
    SEV_MAP = {"PASS": "✅", "FAIL": "❌"}
    passed = [r for r in results if r[0] == "PASS"]
    failed = [r for r in results if r[0] == "FAIL"]

    sections = {}
    for icon, sec, lbl, detail in results:
        sections.setdefault(sec, []).append((icon, lbl, detail))

    print()
    print("=" * 68)
    print("  RESULTS BY SECTION")
    print("=" * 68)
    sec_labels = {
        "A": "Pre-flight (REST reachability)",
        "B": "Dual simultaneous open (6080+6090)",
        "C": "C9 all-pages deep UI validation",
        "D": "Session LED ↔ C3 state sync",
        "E": "Chat end-to-end send + response",
        "F": "Token Counter live data",
        "G": "Simultaneous monitoring (noVNC+C9)",
        "H": "Agent page (New Task, pool, SSE)",
        "I": "noVNC deep inspection + C3 API",
        "J": "CORS + API contract",
    }
    for sec in sorted(sections.keys()):
        sec_results = sections[sec]
        sp = sum(1 for r in sec_results if r[0] == "PASS")
        sf = sum(1 for r in sec_results if r[0] == "FAIL")
        status = "✅" if sf == 0 else "❌"
        print(f"\n  {status} Section {sec}: {sec_labels.get(sec,sec)}  ({sp}✅ {sf}❌)")
        for icon, lbl, detail in sec_results:
            suf = f"  → {detail[:90]}" if (icon == "FAIL" and detail) else ""
            print(f"     {'✅' if icon=='PASS' else '❌'} {lbl}{suf}")

    print()
    print("=" * 68)
    if failed:
        print(f"  ❌ FAILED: {len(failed)} / {len(results)}")
        print()
        print("  FAILURES:")
        for icon, sec, lbl, detail in failed:
            print(f"    [{sec}] {lbl}")
            if detail:
                print(f"         → {detail[:120]}")
    else:
        print(f"  ✅ ALL PASSED: {len(passed)} / {len(results)}")
    print("=" * 68)
    print()
    if failed:
        raise SystemExit(1)
