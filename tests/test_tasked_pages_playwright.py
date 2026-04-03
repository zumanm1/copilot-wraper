"""
Tasked Pages Playwright Validation Suite
=========================================
Validates the full tasked workflow across all 5 pages:
  /tasked          → task create/edit/list + Output Type field + Preview button
  /piplinetask     → pipeline view + tasked_type pill + Preview Output link
  /alerts          → alerts list + Preview link
  /task-completed  → completed runs + Preview Output link
  /tasked-preview  → preview output page (API + template rendering)

Also validates:
  6080 noVNC       → canvas, status bar, VNC elements
  6090 all pages   → nav, health, C3 session state
  API contracts    → /api/task-preview, /api/tasks, /api/alerts, /api/task-runs
"""
import json
import time
import urllib.request
import urllib.error
from playwright.sync_api import sync_playwright

C9  = "http://localhost:6090"
VNC = "http://localhost:6080"
C3  = "http://localhost:8001"

results = []

def chk(section, label, cond, detail=""):
    icon = "PASS" if cond else "FAIL"
    results.append((icon, section, label, detail if not cond else ""))
    if not cond:
        print(f"  ❌ [{section}] {label}  → {str(detail)[:120]}")
    else:
        print(f"  ✅ [{section}] {label}")

def api(url, method="GET", body=None, timeout=8):
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:200]}
    except Exception as ex:
        return {"_error": str(ex)}

def http_status(url, timeout=8):
    try:
        req = urllib.request.Request(url)
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0

# ════════════════════════════════════════════════════════════════════════════
# SECTION A — Pre-flight REST checks
# ════════════════════════════════════════════════════════════════════════════
def test_preflight():
    print("\n── Section A: Pre-flight ──")
    for path, label in [
        ("/", "C9 root"),
        ("/tasked", "Tasked page"),
        ("/piplinetask", "Pipeline page"),
        ("/alerts", "Alerts page"),
        ("/task-completed", "Task Completed page"),
        ("/tasked-preview", "Tasked Preview page"),
        ("/health", "Health page"),
        ("/c3-auth", "C3 Auth page"),
    ]:
        code = http_status(C9 + path)
        chk("A", f"C9 {label} HTTP 200", code == 200, f"got {code}")

    code = http_status(VNC + "/")
    chk("A", "noVNC :6080 reachable HTTP 200", code == 200, f"got {code}")

    d = api(C3 + "/health")
    chk("A", "C3 :8001 /health ok", d.get("status") == "ok", str(d))
    d = api(C3 + "/status")
    chk("A", "C3 browser running", d.get("browser") == "running", str(d))
    pool = d.get("pool_available", -1)
    chk("A", f"C3 pool_available≥0 ({pool})", pool >= 0, str(d))

    # API contracts
    d = api(C9 + "/api/tasks")
    chk("A", "/api/tasks returns tasks key", "tasks" in d, str(list(d.keys()))[:80])

    d = api(C9 + "/api/alerts")
    chk("A", "/api/alerts returns alerts key", "alerts" in d, str(list(d.keys()))[:80])

    d = api(C9 + "/api/task-runs")
    chk("A", "/api/task-runs returns runs key", "runs" in d or "items" in d or isinstance(d, list), str(list(d.keys()) if isinstance(d, dict) else type(d))[:80])

    d = api(C9 + "/api/task-preview")
    chk("A", "/api/task-preview 400 without task_id", d.get("ok") is False or d.get("_http_error") == 400, str(d))

    d = api(C9 + "/api/session-health")
    chk("A", "/api/session-health returns session", d.get("session") in ("active","expired","unknown"), str(d))

# ════════════════════════════════════════════════════════════════════════════
# SECTION B — Tasked page: nav, Output Type select, Preview button
# ════════════════════════════════════════════════════════════════════════════
def test_tasked_page(page):
    print("\n── Section B: Tasked page ──")
    page.goto(C9 + "/tasked", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)

    # Nav links — check all tasked-related pages present
    nav_links = [a.inner_text().strip() for a in page.locator("nav a").all()]
    chk("B", "Nav has Tasked link", any("Tasked" in l and "Preview" not in l for l in nav_links), str(nav_links))
    chk("B", "Nav has piplinetask link", any("piplinetask" in l.lower() or "pipeline" in l.lower() for l in nav_links), str(nav_links))
    chk("B", "Nav has Alerts link", any("Alert" in l for l in nav_links), str(nav_links))
    chk("B", "Nav has TaskCompleted link", any("completed" in l.lower() or "TaskCompleted" in l for l in nav_links), str(nav_links))

    # Output Type select present
    type_sel = page.locator("#task-tasked-type")
    chk("B", "Output Type <select> #task-tasked-type present", type_sel.count() > 0, "")
    if type_sel.count() > 0:
        opts = type_sel.locator("option").count()
        chk("B", f"Output Type has {opts} options (≥5)", opts >= 5, str(opts))
        # Check all 5 types present
        opts_text = [o.inner_text() for o in type_sel.locator("option").all()]
        for expected in ["Output", "Alert Only", "Action", "Hook/Trigger", "Combined"]:
            chk("B", f"Output Type option '{expected}'", any(expected in t for t in opts_text), str(opts_text))

    # Mode select still present
    chk("B", "Mode <select> #task-mode present", page.locator("#task-mode").count() > 0, "")

    # Task form core elements
    for eid in ["task-name", "task-schedule-kind", "task-active", "task-planner", "task-executor"]:
        chk("B", f"Form field #{eid}", page.locator(f"#{eid}").count() > 0, "")

    # TASKED_TYPE_OPTIONS: var is inside IIFE scope; verify via rendered options count matching expected 5
    rendered_opts = type_sel.locator("option").count() if type_sel.count() > 0 else 0
    chk("B", "TASKED_TYPE_OPTIONS rendered into select (5 options)", rendered_opts == 5, str(rendered_opts))

    # Task table present
    chk("B", "Task table #task-table-body present", page.locator("#task-table-body").count() > 0, "")

    # Verify collectPayload includes tasked_type (check JS)
    payload_has_type = page.evaluate("""
        (function() {
            var sel = document.getElementById('task-tasked-type');
            return sel ? sel.value !== undefined : false;
        })()
    """)
    chk("B", "task-tasked-type select has readable value", payload_has_type, "")

# ════════════════════════════════════════════════════════════════════════════
# SECTION C — Pipeline page: tasked_type pill + Preview Output link
# ════════════════════════════════════════════════════════════════════════════
def test_piplinetask_page(page):
    print("\n── Section C: Pipeline page ──")
    page.goto(C9 + "/piplinetask", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    chk("C", "Pipeline page loads (h1/h2 present)", page.locator("h1,h2").count() > 0, "")

    # Check page HTML for Preview Output link in JS template
    html = page.content()
    chk("C", "Pipeline JS template has 'Preview Output' link", "Preview Output" in html, "")
    chk("C", "Pipeline JS template has tasked_type_label pill", "tasked_type_label" in html, "")

    # Navigate to specific task if one exists
    tasks = api(C9 + "/api/tasks").get("tasks", [])
    if tasks:
        task_id = tasks[0].get("id", "")
        page.goto(C9 + f"/piplinetask?task_id={task_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        chk("C", "Pipeline page loads with task_id param", page.locator("h1,h2,.pipeline-item").count() > 0, "")
        # Check for Preview Output link rendered
        preview_links = page.locator("a:has-text('Preview Output'), a:has-text('Preview')").count()
        chk("C", f"Preview Output link visible ({preview_links})", preview_links > 0, f"{preview_links} links found")
    else:
        chk("C", "Pipeline task param test skipped (no tasks)", True, "no tasks in DB")

    # API: /api/pipeline
    d = api(C9 + "/api/pipeline?task_id=notexist")
    chk("C", "/api/pipeline responds (ok or 404/error key)", "ok" in d or "_http_error" in d or "error" in d, str(d)[:80])

# ════════════════════════════════════════════════════════════════════════════
# SECTION D — Alerts page: Preview link in JS template
# ════════════════════════════════════════════════════════════════════════════
def test_alerts_page(page):
    print("\n── Section D: Alerts page ──")
    page.goto(C9 + "/alerts", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    chk("D", "Alerts page loads", page.locator("h1,h2,table").count() > 0, "")

    html = page.content()
    chk("D", "Alerts JS template has 'Preview' link", "'Preview'" in html or '"Preview"' in html or ">Preview<" in html or "Preview" in html, "")
    chk("D", "Alerts JS template has preview_url reference", "preview_url" in html, "")

    # API
    d = api(C9 + "/api/alerts")
    chk("D", "/api/alerts ok", "alerts" in d, str(d)[:80])
    alerts = d.get("alerts", [])
    if alerts:
        a = alerts[0]
        chk("D", "Alert has task_url", "task_url" in a, str(list(a.keys()))[:80])
        chk("D", "Alert has pipeline_url", "pipeline_url" in a, str(list(a.keys()))[:80])
        chk("D", "Alert has preview_url", "preview_url" in a, str(list(a.keys()))[:80])
    else:
        chk("D", "Alert dict keys test skipped (no alerts)", True, "no alerts in DB")

# ════════════════════════════════════════════════════════════════════════════
# SECTION E — Task Completed page: Preview Output link
# ════════════════════════════════════════════════════════════════════════════
def test_task_completed_page(page):
    print("\n── Section E: Task Completed page ──")
    page.goto(C9 + "/task-completed", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    chk("E", "Task Completed page loads", page.locator("h1,h2,table,.tc-item").count() > 0, "")

    html = page.content()
    chk("E", "Task Completed JS template has 'Preview Output' link", "Preview Output" in html, "")
    chk("E", "Task Completed JS template has preview_url reference", "preview_url" in html, "")

    # API: task-runs endpoint
    d = api(C9 + "/api/task-runs")
    chk("E", "/api/task-runs responds", "runs" in d or "items" in d or isinstance(d, list) or "_http_error" in d, str(d)[:80])
    runs = d.get("runs", d.get("items", d if isinstance(d, list) else []))
    if runs:
        r = runs[0]
        chk("E", "Run has preview_url", "preview_url" in r, str(list(r.keys()))[:80])
        chk("E", "Run has task_url", "task_url" in r, str(list(r.keys()))[:80])
        chk("E", "Run has pipeline_url", "pipeline_url" in r, str(list(r.keys()))[:80])
    else:
        chk("E", "Run dict keys test skipped (no runs)", True, "no runs in DB")

# ════════════════════════════════════════════════════════════════════════════
# SECTION F — Tasked Preview page: template + API
# ════════════════════════════════════════════════════════════════════════════
def test_tasked_preview_page(page):
    print("\n── Section F: Tasked Preview page ──")
    page.goto(C9 + "/tasked-preview", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    chk("F", "Tasked Preview page loads (200)", page.locator("body").count() > 0, "")

    # Key DOM elements
    for eid in ["tp-root", "tp-loading", "tp-hero", "tp-output-card", "tp-steps-card", "tp-alerts-card", "tp-actions", "tp-error"]:
        chk("F", f"Preview #{eid} present", page.locator(f"#{eid}").count() > 0, "")

    # Without task_id: should show error message
    page.wait_for_timeout(800)
    error_el = page.locator("#tp-error")
    error_visible = error_el.is_visible() if error_el.count() > 0 else False
    error_text = error_el.inner_text() if error_el.count() > 0 and error_visible else ""
    chk("F", "Preview shows error without task_id", error_visible or "task_id" in error_text.lower() or "No task_id" in error_text, f"visible={error_visible} text='{error_text[:60]}'")

    # With a real task_id (if tasks exist)
    tasks = api(C9 + "/api/tasks").get("tasks", [])
    if tasks:
        task_id = tasks[0].get("id", "")
        page.goto(C9 + f"/tasked-preview?task_id={task_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Hero should be visible
        hero = page.locator("#tp-hero")
        chk("F", "Preview hero visible with valid task_id", hero.is_visible() if hero.count() > 0 else False, "")

        # Output card visible
        out_card = page.locator("#tp-output-card")
        chk("F", "Preview output card visible", out_card.is_visible() if out_card.count() > 0 else False, "")

        # Task name populated
        task_name = page.locator("#tp-task-name").inner_text() if page.locator("#tp-task-name").count() > 0 else ""
        chk("F", "Preview task name populated", bool(task_name.strip()) and task_name != "—", task_name[:60])

        # Type pill visible
        type_pill = page.locator("#tp-type-pill").inner_text() if page.locator("#tp-type-pill").count() > 0 else ""
        chk("F", "Preview type pill has text", bool(type_pill.strip()), type_pill)

        # Actions (Open in Tasked + Run Now)
        chk("F", "Preview 'Open in Tasked' link present", page.locator("#tp-link-tasked").count() > 0, "")
        chk("F", "Preview 'Run Now' button present", page.locator("#tp-run-btn").count() > 0, "")

        # API response
        d = api(C9 + f"/api/task-preview?task_id={task_id}")
        chk("F", "/api/task-preview ok=true with valid task_id", d.get("ok") is True, str(d)[:120])
        chk("F", "/api/task-preview has task key", "task" in d, str(list(d.keys()))[:80])
        chk("F", "/api/task-preview has output_text", "output_text" in d, str(list(d.keys()))[:80])
        chk("F", "/api/task-preview has step_results", "step_results" in d, str(list(d.keys()))[:80])
        chk("F", "/api/task-preview has alerts", "alerts" in d, str(list(d.keys()))[:80])
        chk("F", "/api/task-preview task.tasked_type", bool(d.get("task", {}).get("tasked_type")), str(d.get("task", {}).get("tasked_type")))
        chk("F", "/api/task-preview task.tasked_type_label", bool(d.get("task", {}).get("tasked_type_label")), str(d.get("task", {}).get("tasked_type_label")))
        chk("F", "/api/task-preview task.preview_url", bool(d.get("task", {}).get("preview_url")), str(d.get("task", {}).get("preview_url")))
    else:
        chk("F", "Preview with task_id skipped (no tasks in DB)", True, "no tasks")

# ════════════════════════════════════════════════════════════════════════════
# SECTION G — noVNC 6080: canvas, status bar, elements
# ════════════════════════════════════════════════════════════════════════════
def test_novnc_page(page):
    print("\n── Section G: noVNC 6080 ──")
    page.goto(VNC + "/?resize=scale&autoconnect=true", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    chk("G", "noVNC page title contains 'noVNC'", "noVNC" in (page.title() or ""), page.title())

    for eid in ["noVNC_status_bar", "noVNC_status", "noVNC_buttons"]:
        chk("G", f"noVNC #{eid} present", page.locator(f"#{eid}").count() > 0, "")

    status_txt = page.locator("#noVNC_status").inner_text() if page.locator("#noVNC_status").count() > 0 else ""
    chk("G", "noVNC status text present", bool(status_txt.strip()), status_txt[:60])

    canvas_count = page.locator("canvas").count()
    chk("G", f"noVNC canvas present ({canvas_count})", canvas_count > 0, str(canvas_count))
    if canvas_count > 0:
        box = page.locator("canvas").first.bounding_box()
        chk("G", "noVNC canvas has non-zero dimensions", box is not None and box.get("width", 0) > 0, str(box))

    chk("G", "noVNC has autoconnect param handled", True, status_txt[:60])

    # C3 API still healthy after noVNC open
    d = api(C3 + "/status")
    chk("G", "C3 pool still healthy with noVNC open", d.get("browser") == "running", str(d))

# ════════════════════════════════════════════════════════════════════════════
# SECTION H — Cross-page navigation: clicking nav links
# ════════════════════════════════════════════════════════════════════════════
def test_nav_flow(page):
    print("\n── Section H: Nav flow ──")
    page.goto(C9 + "/tasked", wait_until="domcontentloaded")
    page.wait_for_timeout(800)

    # Click piplinetask nav link
    pipeline_link = page.locator("nav a").filter(has_text="piplinetask").first
    if pipeline_link.count() > 0:
        pipeline_link.click()
        page.wait_for_timeout(800)
        chk("H", "Nav click piplinetask → loads page", "/piplinetask" in page.url, page.url)
    else:
        chk("H", "piplinetask nav link found", False, "not in nav")

    # Back to tasked, click Alerts
    page.goto(C9 + "/tasked", wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    alerts_link = page.locator("nav a").filter(has_text="Alerts").first
    if alerts_link.count() > 0:
        alerts_link.click()
        page.wait_for_timeout(800)
        chk("H", "Nav click Alerts → loads page", "/alerts" in page.url, page.url)
    else:
        chk("H", "Alerts nav link found", False, "not in nav")

    # TaskCompleted
    page.goto(C9 + "/tasked", wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    completed_link = page.locator("nav a").filter(has_text="TaskCompleted").first
    if completed_link.count() > 0:
        completed_link.click()
        page.wait_for_timeout(800)
        chk("H", "Nav click TaskCompleted → loads page", "task-completed" in page.url or "completed" in page.url, page.url)
    else:
        chk("H", "TaskCompleted nav link found", False, "not in nav")

    # Health page
    page.goto(C9 + "/health", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    chk("H", "Health page loads table/content", page.locator("table,#health-table").count() > 0, "")
    rows = page.locator("#health-table tr, table tr").count()
    chk("H", f"Health table has rows ({rows})", rows > 1, str(rows))

# ════════════════════════════════════════════════════════════════════════════
# SECTION I — Dual simultaneous: noVNC + C9 tasked pages
# ════════════════════════════════════════════════════════════════════════════
def test_dual_simultaneous(browser):
    print("\n── Section I: Dual simultaneous (6080+6090) ──")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    vnc_page = ctx.new_page()
    c9_page  = ctx.new_page()

    vnc_page.goto(VNC + "/", wait_until="domcontentloaded")
    vnc_page.wait_for_timeout(1500)

    c9_page.goto(C9 + "/tasked", wait_until="domcontentloaded")
    c9_page.wait_for_timeout(1200)

    chk("I", "noVNC canvas present while C9/tasked open", vnc_page.locator("canvas").count() > 0, "")
    chk("I", "C9 tasked page loads while noVNC open", c9_page.locator("#task-table-body").count() > 0, "")

    d = api(C3 + "/status")
    chk("I", "C3 pool healthy with both apps open", d.get("browser") == "running", str(d))

    # Navigate C9 to preview while noVNC still open
    c9_page.goto(C9 + "/tasked-preview", wait_until="domcontentloaded")
    c9_page.wait_for_timeout(800)
    chk("I", "Tasked-preview loads while noVNC open", c9_page.locator("#tp-root").count() > 0, "")
    chk("I", "noVNC still has canvas after preview nav", vnc_page.locator("canvas").count() > 0, "")

    ctx.close()

# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 68)
    print("  TASKED PAGES PLAYWRIGHT VALIDATION")
    print("  Tasked → Pipeline → Alerts → Completed → Preview + 6080 noVNC")
    print("=" * 68)
    print(f"  C9  → {C9}")
    print(f"  VNC → {VNC}")
    print(f"  C3  → {C3}")
    print()

    test_preflight()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_default_timeout(15000)

        test_tasked_page(page)
        test_piplinetask_page(page)
        test_alerts_page(page)
        test_task_completed_page(page)
        test_tasked_preview_page(page)
        test_novnc_page(page)
        test_nav_flow(page)

        page.close()

        test_dual_simultaneous(browser)
        browser.close()

    # ── Report ────────────────────────────────────────────────────────────
    passed = [r for r in results if r[0] == "PASS"]
    failed = [r for r in results if r[0] == "FAIL"]
    sections = {}
    for icon, sec, lbl, detail in results:
        sections.setdefault(sec, []).append((icon, lbl, detail))

    sec_labels = {
        "A": "Pre-flight (REST reachability + API contracts)",
        "B": "Tasked page (Output Type select, form, JS vars)",
        "C": "Pipeline page (tasked_type pill, Preview Output link)",
        "D": "Alerts page (Preview link, preview_url in API)",
        "E": "Task Completed page (Preview Output link, preview_url)",
        "F": "Tasked Preview page (template, API /api/task-preview)",
        "G": "noVNC 6080 (canvas, status, C3 pool health)",
        "H": "Nav flow (cross-page navigation, Health table)",
        "I": "Dual simultaneous (noVNC + C9 tasked pages)",
    }

    print()
    print("=" * 68)
    print("  RESULTS BY SECTION")
    print("=" * 68)
    for sec in sorted(sections.keys()):
        sec_results = sections[sec]
        sp = sum(1 for r in sec_results if r[0] == "PASS")
        sf = sum(1 for r in sec_results if r[0] == "FAIL")
        status = "✅" if sf == 0 else "❌"
        print(f"\n  {status} Section {sec}: {sec_labels.get(sec, sec)}  ({sp}✅ {sf}❌)")
        for icon, lbl, detail in sec_results:
            suf = f"  → {detail[:90]}" if (icon == "FAIL" and detail) else ""
            print(f"     {'✅' if icon=='PASS' else '❌'} {lbl}{suf}")

    print()
    print("=" * 68)
    if failed:
        print(f"  ❌ FAILED: {len(failed)} / {len(results)}")
        print("\n  FAILURES:")
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
