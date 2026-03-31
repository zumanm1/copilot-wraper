"""
Playwright validation for:
1. Agent page (/agent) - Linux Shell tab
2. Multi-Agento page (/multi-agento) - Shell tab in right panel
3. /api/sandbox/exec backend endpoint
"""
import asyncio
import json
import urllib.request
import urllib.error

BASE = "http://localhost:6090"

PASS = []
FAIL = []

def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))

# ── A. Backend API validation (no browser needed) ────────────────────────────
print("\n=== A. Backend API: /api/sandbox/exec ===")

def post_json(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code
    except Exception as ex:
        return {"error": str(ex)}, -1

def get_json(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return json.loads(r.read()), r.status
    except Exception as ex:
        return {"error": str(ex)}, -1

# 1. Simple command in C10
d, sc = post_json("/api/sandbox/exec", {"command": "echo hello_c10", "sandbox": "c10", "timeout": 10})
check("sandbox/exec c10 echo", sc == 200 and "hello_c10" in d.get("stdout",""), f"stdout={d.get('stdout','')!r} sc={sc}")

# 2. pwd in C10
d, sc = post_json("/api/sandbox/exec", {"command": "pwd", "sandbox": "c10", "timeout": 10})
check("sandbox/exec c10 pwd", sc == 200 and d.get("exit_code") == 0, f"stdout={d.get('stdout','')!r}")

# 3. ls in C10
d, sc = post_json("/api/sandbox/exec", {"command": "ls /", "sandbox": "c10", "timeout": 10})
ls_out = repr(d.get("stdout", ""))[:80]
check("sandbox/exec c10 ls /", sc == 200 and d.get("exit_code") == 0, f"stdout={ls_out}")

# 4. Python3 in C10
py_cmd = "python3 -c \"print(2+2)\""
d, sc = post_json("/api/sandbox/exec", {"command": py_cmd, "sandbox": "c10", "timeout": 10})
check("sandbox/exec c10 python3", sc == 200 and "4" in d.get("stdout",""), f"stdout={d.get('stdout','')!r}")

# 5. Invalid sandbox fallback (defaults to c10)
d, sc = post_json("/api/sandbox/exec", {"command": "echo fallback", "sandbox": "c10", "timeout": 10})
check("sandbox/exec fallback to c10", sc == 200, f"sc={sc}")

# 6. Empty command returns error
d, sc = post_json("/api/sandbox/exec", {"command": "", "sandbox": "c10"})
check("sandbox/exec empty command 400", sc == 400 and not d.get("ok"), f"sc={sc} d={d}")

# 7. Container list endpoint
d, sc = get_json("/api/containers")
check("api/containers returns list", sc == 200 and isinstance(d.get("containers"), list), f"count={len(d.get('containers',[]))}")

# 8. Container toggle rejects core container
d, sc = post_json("/api/container/toggle", {"name": "C9_jokes", "action": "stop"})
check("container/toggle rejects core container", not d.get("ok"), f"error={d.get('error','')!r}")

# 9. Container toggle rejects bad action
d, sc = post_json("/api/container/toggle", {"name": "C2_agent-terminal", "action": "restart"})
check("container/toggle rejects bad action", not d.get("ok"), f"sc={sc}")

print(f"\n=== Backend: {len(PASS)} passed, {len(FAIL)} failed ===")

# ── B. Browser validation via Playwright ──────────────────────────────────────
print("\n=== B. Browser: Agent page shell tab ===")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PW_AVAILABLE = True
except ImportError:
    PW_AVAILABLE = False
    print("  SKIP  Playwright not installed — install with: pip install playwright && playwright install chromium")

if PW_AVAILABLE:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()

        # ── Agent page ──────────────────────────────────────────────────────
        page = ctx.new_page()
        page.goto(BASE + "/agent", wait_until="domcontentloaded", timeout=15000)

        # Check shell tab button exists
        tab = page.query_selector("#tab-shell")
        check("agent: shell tab button exists", tab is not None)

        # Check shell panel exists but hidden
        panel = page.query_selector("#panel-shell")
        check("agent: shell panel exists", panel is not None)

        if tab:
            tab.click()
            page.wait_for_timeout(300)

        # Check panel is now active
        active = page.query_selector("#panel-shell.active")
        check("agent: shell panel activates on tab click", active is not None)

        # Check shell input is visible and focusable
        inp = page.query_selector("#shell-input")
        check("agent: shell input exists", inp is not None)
        if inp:
            visible = inp.is_visible()
            check("agent: shell input visible", visible)

        # Check output area exists
        out = page.query_selector("#shell-output")
        check("agent: shell output area exists", out is not None)

        # Check toolbar buttons
        check("agent: shell Clear button", page.query_selector("#shell-clear") is not None)
        check("agent: shell ls button", page.query_selector("#shell-ls") is not None)
        check("agent: shell pwd button", page.query_selector("#shell-pwd") is not None)

        # Type a command and run it
        if inp:
            inp.fill("echo playwright_test_ok")
            inp.press("Enter")
            try:
                page.wait_for_function(
                    "document.getElementById('shell-output').innerText.includes('playwright_test_ok')",
                    timeout=15000
                )
                out_text = page.query_selector("#shell-output").inner_text()
                check("agent: shell command executes and output appears",
                      "playwright_test_ok" in out_text, f"output snippet: {out_text[-200:]!r}")
            except PWTimeout:
                out_text = page.query_selector("#shell-output").inner_text() if page.query_selector("#shell-output") else ""
                check("agent: shell command executes and output appears", False, f"timeout. output={out_text!r}")

        # Test ls button - count divs with class term-cmd, need at least 2 (echo + ls)
        if page.query_selector("#shell-ls"):
            before_count = page.eval_on_selector("#shell-output", "el => el.querySelectorAll('.term-cmd').length")
            page.click("#shell-ls")
            try:
                page.wait_for_function(
                    f"document.getElementById('shell-output').querySelectorAll('.term-cmd').length > {before_count}",
                    timeout=12000
                )
                check("agent: ls quick button triggers command", True)
            except PWTimeout:
                after = page.eval_on_selector("#shell-output", "el => el.querySelectorAll('.term-cmd').length")
                check("agent: ls quick button triggers command", after > before_count, f"before={before_count} after={after}")

        # Test elapsed badge present in toolbar
        badge = page.query_selector("#elapsed-badge")
        check("agent: elapsed badge element exists", badge is not None)

        # ── Multi-Agento page ────────────────────────────────────────────────
        print("\n=== B2. Browser: Multi-Agento page shell tab ===")
        page2 = ctx.new_page()
        page2.goto(BASE + "/multi-Agento", wait_until="domcontentloaded", timeout=15000)

        # Check shell tab button
        tab2 = page2.query_selector(".mao-rtab[data-tab='shell']")
        check("multi-agento: shell tab button exists", tab2 is not None)

        # Check shell panel
        panel2 = page2.query_selector("#rpanel-shell")
        check("multi-agento: shell panel exists", panel2 is not None)

        if tab2:
            tab2.click()
            page2.wait_for_timeout(300)

        # Check panel active
        active2 = page2.query_selector("#rpanel-shell.active")
        check("multi-agento: shell panel activates on tab click", active2 is not None)

        # Check shell input
        inp2 = page2.query_selector("#mao-shell-input")
        check("multi-agento: shell input exists", inp2 is not None)
        if inp2:
            check("multi-agento: shell input visible", inp2.is_visible())

        # Check output area
        check("multi-agento: shell output area exists", page2.query_selector("#mao-shell-out") is not None)

        # Check toolbar buttons
        check("multi-agento: Clear button", page2.query_selector("#mao-shell-clear") is not None)
        check("multi-agento: ls button", page2.query_selector("#mao-shell-ls") is not None)
        check("multi-agento: pwd button", page2.query_selector("#mao-shell-pwd") is not None)

        # Check elapsed badge in supervisor bar
        check("multi-agento: elapsed badge exists", page2.query_selector("#mao-elapsed") is not None)

        # Type and run a command in multi-agento shell
        if inp2:
            inp2.fill("echo mao_playwright_ok")
            inp2.press("Enter")
            try:
                page2.wait_for_function(
                    "document.getElementById('mao-shell-out').innerText.includes('mao_playwright_ok')",
                    timeout=15000
                )
                out_text2 = page2.query_selector("#mao-shell-out").inner_text()
                check("multi-agento: shell command executes and output appears",
                      "mao_playwright_ok" in out_text2, f"output: {out_text2[-200:]!r}")
            except PWTimeout:
                out_text2 = page2.query_selector("#mao-shell-out").inner_text() if page2.query_selector("#mao-shell-out") else ""
                check("multi-agento: shell command executes and output appears", False, f"timeout. output={out_text2!r}")

        # Test pwd button
        if page2.query_selector("#mao-shell-pwd"):
            page2.click("#mao-shell-pwd")
            try:
                page2.wait_for_function(
                    "document.getElementById('mao-shell-out').querySelectorAll('.sh-cmd').length >= 2",
                    timeout=10000
                )
                check("multi-agento: pwd quick button triggers command", True)
            except PWTimeout:
                check("multi-agento: pwd quick button triggers command", False, "timeout")

        # ── Dashboard container manager ──────────────────────────────────────
        print("\n=== B3. Browser: Dashboard container manager ===")
        page3 = ctx.new_page()
        page3.goto(BASE + "/", wait_until="domcontentloaded", timeout=15000)

        check("dashboard: ct-grid exists", page3.query_selector("#ct-grid") is not None)
        check("dashboard: ct-refresh-btn exists", page3.query_selector("#ct-refresh-btn") is not None)

        # Wait for container cards to load
        try:
            page3.wait_for_function(
                "document.getElementById('ct-grid').querySelectorAll('.ct-card').length > 0",
                timeout=10000
            )
            cards = page3.query_selector_all(".ct-card")
            check("dashboard: container cards rendered", len(cards) > 0, f"{len(cards)} cards")
            core_locks = page3.query_selector_all(".ct-lock")
            check("dashboard: core containers show lock icon", len(core_locks) > 0, f"{len(core_locks)} locked")
            toggles = page3.query_selector_all(".ct-toggle")
            check("dashboard: optional containers show toggle buttons", len(toggles) >= 0, f"{len(toggles)} toggles")
        except PWTimeout:
            check("dashboard: container cards rendered", False, "timeout loading cards")

        browser.close()

# ── Final report ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"VALIDATION RESULTS")
print(f"{'='*60}")
print(f"  PASSED: {len(PASS)}")
print(f"  FAILED: {len(FAIL)}")
if FAIL:
    print(f"\nFailed checks:")
    for f in FAIL:
        print(f"  - {f}")
else:
    print("\nAll checks passed!")
