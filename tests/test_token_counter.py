"""
Token Counter — Playwright validation
Covers: brand rename, global badge, nav link, token-counter page, API endpoints,
        existing pages unbroken, tab switching, DB persistence.
"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:6090"

results = []

def chk(label, cond, detail=""):
    icon = "PASS" if cond else "FAIL"
    results.append((icon, label, detail))

def api(path, method="GET", body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

# ── Seed a token record so tables are non-empty ──────────────────────────────
r = api("/api/token-usage/record", "POST",
        {"agent_id": "c2-aider", "page": "chat", "tokens": 2500, "status": "ok"})
chk("POST /api/token-usage/record ok", r.get("ok") is True, str(r))

r = api("/api/token-usage/record", "POST",
        {"agent_id": "c5-claude-code", "page": "agent", "tokens": 8000, "status": "ok"})
chk("POST second record ok", r.get("ok") is True, str(r))

r = api("/api/token-usage/summary")
chk("/api/token-usage/summary ok",    r.get("ok") is True,   str(r))
chk("today_total > 0",                r.get("today_total", 0) > 0, str(r.get("today_total")))
chk("by_agent has c2-aider",          "c2-aider" in r.get("by_agent", {}), str(r))

r = api("/api/token-usage/agents?days=1")
chk("/api/token-usage/agents ok",     r.get("ok") is True,   str(r))
chk("agents list non-empty",          len(r.get("agents", [])) > 0, str(len(r.get("agents", []))))
if r.get("agents"):
    ag = r["agents"][0]
    chk("agent row has pct field",    "pct" in ag, str(ag.keys()))
    chk("agent row has calls field",  "calls" in ag, str(ag.keys()))

r = api("/api/token-usage/history?days=1&limit=10")
chk("/api/token-usage/history ok",   r.get("ok") is True, str(r))
chk("history rows non-empty",        len(r.get("rows", [])) > 0, str(len(r.get("rows", []))))

# ── Playwright UI checks ──────────────────────────────────────────────────────
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page()

    # 1. Brand renamed on dashboard
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    h1 = pg.locator("h1").first.inner_text().strip()
    chk("Brand renamed to APP C9",       h1 == "APP C9",  h1)

    # 2. Token Counter in nav
    nav = [a.inner_text() for a in pg.locator("nav a").all()]
    chk("Token Counter nav link exists", any("Token Counter" in t for t in nav), str(nav))

    # 3. Global token badge in header
    chk("Global token badge element",   pg.locator("#global-token-badge").count() > 0, "")
    chk("Global token val element",     pg.locator("#global-token-val").count() > 0, "")

    # 4. Token Counter page loads
    pg.goto(BASE + "/token-counter", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    title = pg.locator("h2").first.inner_text()
    chk("Token Counter page h2",        "Token Counter" in title, title)
    chk("Hero section present",         pg.locator("#tc-hero").count() > 0, "")
    chk("Agent table present",          pg.locator("#agent-table").count() > 0, "")
    chk("3 tabs present",               pg.locator(".tc-tab").count() >= 3,
        str(pg.locator(".tc-tab").count()))

    # 5. Hero today stat populated after API seeding
    today_val = pg.locator("#stat-today").inner_text()
    chk("Hero today stat not dash",     today_val not in ["—", ""], today_val)

    # 6. Agent table body has rows (after loadAll)
    tbody = pg.locator("#agent-tbody tr").count()
    chk("Agent table has data rows",    tbody > 0, str(tbody))

    # 7. Tab: Pages
    pg.locator(".tc-tab").nth(1).click()
    pg.wait_for_timeout(400)
    cls = pg.locator("#panel-pages").get_attribute("class") or ""
    chk("Pages tab panel active",       "active" in cls, cls)

    # 8. Tab: History
    pg.locator(".tc-tab").nth(2).click()
    pg.wait_for_timeout(400)
    cls = pg.locator("#panel-history").get_attribute("class") or ""
    chk("History tab panel active",     "active" in cls, cls)
    hrows = pg.locator("#history-tbody tr").count()
    chk("History table has rows",       hrows > 0, str(hrows))

    # 9. Chat page still works — brand + token-badge + global-badge intact
    pg.goto(BASE + "/chat", wait_until="domcontentloaded")
    chk("Chat page brand APP C9",       pg.locator("h1").first.inner_text().strip() == "APP C9", "")
    chk("Chat token-badge intact",      pg.locator("#token-badge").count() > 0, "")
    chk("Chat global-token-badge",      pg.locator("#global-token-badge").count() > 0, "")
    chk("Chat sessions-btn intact",     pg.locator("#sessions-btn").count() > 0, "")
    chk("Chat new-chat-btn intact",     pg.locator("#new-chat-btn").count() > 0, "")

    # 10. Agent page still works
    pg.goto(BASE + "/agent", wait_until="domcontentloaded")
    chk("Agent page brand APP C9",      pg.locator("h1").first.inner_text().strip() == "APP C9", "")
    chk("Agent token badge intact",     pg.locator("#agent-token-badge").count() > 0, "")
    chk("Agent new-task-btn intact",    pg.locator("#btn-new-task").count() > 0, "")
    chk("Agent run-btn intact",         pg.locator("#btn-run").count() > 0, "")

    # 11. Multi-agent page loads
    pg.goto(BASE + "/multi-agent", wait_until="domcontentloaded")
    chk("Multi-agent page loads",       pg.locator("h1").count() > 0, "")

    # 12. multi-Agento page loads
    pg.goto(BASE + "/multi-Agento", wait_until="domcontentloaded")
    chk("multi-Agento page loads",      pg.locator("h1").count() > 0, "")

    # 13. Global badge updates after token-counter page fetch
    pg.goto(BASE + "/token-counter", wait_until="domcontentloaded")
    pg.wait_for_timeout(2000)
    val = pg.locator("#global-token-val").inner_text()
    chk("Global badge shows token count", val not in ["—", ""], val)

    b.close()

# ── Print results ─────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("  TOKEN COUNTER — VALIDATION RESULTS")
print("=" * 60)
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
for icon, label, detail in results:
    suffix = f" — {detail}" if (icon == "FAIL" or detail) else ""
    print(f"  {'✅' if icon=='PASS' else '❌'} {icon}  {label}{suffix}")
print()
print(f"  RESULTS: {passed}/{len(results)} passed   {failed} failed")
print("=" * 60)
print()
if failed > 0:
    raise SystemExit(1)
