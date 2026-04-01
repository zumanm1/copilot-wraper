from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def _import_cookie_extractor():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    ba_path = str(ROOT / "browser_auth")
    if ba_path not in sys.path:
        sys.path.insert(1, ba_path)
    import cookie_extractor as ce
    ce = importlib.reload(ce)
    ce._page_pool = None
    ce._chat_semaphore = None
    ce._tab1_session_ready = False
    ce._tab1_session_meta = {"ready": False, "reason": "test"}
    ce._tab1_auth_progress = ce._new_auth_progress_state()
    ce._tab1_pool_monitor = ce._new_pool_monitor_state()
    ce._tab1_auth_step_stats = {
        step_id: {"runs": 0, "total_ms": 0.0, "min_ms": None, "max_ms": None}
        for step_id, _ in ce._AUTH_STEP_DEFS
    }
    return ce


def _import_c3_server():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    ba_path = str(ROOT / "browser_auth")
    added = ba_path not in sys.path
    if added:
        sys.path.insert(0, ba_path)
    prev_server = sys.modules.pop("server", None)
    try:
        import server as c3_srv
        return importlib.reload(c3_srv)
    finally:
        sys.modules.pop("server", None)
        if prev_server is not None:
            sys.modules["server"] = prev_server
        if added and ba_path in sys.path:
            sys.path.remove(ba_path)


class _DummyPage:
    def __init__(self, url: str = "http://127.0.0.1:8001/setup") -> None:
        self.url = url
        self.closed = False
        self.front_calls = 0

    async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 0) -> None:
        self.url = url

    async def wait_for_load_state(self, state: str = "domcontentloaded", timeout: int = 0) -> None:
        return None

    async def wait_for_selector(self, selector: str, state: str = "visible", timeout: int = 0) -> None:
        return None

    async def bring_to_front(self) -> None:
        self.front_calls += 1

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class _DummyMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float, int | None]] = []
        self.down_calls = 0
        self.up_calls = 0

    async def move(self, x: float, y: float, steps: int | None = None) -> None:
        self.moves.append((x, y, steps))

    async def down(self) -> None:
        self.down_calls += 1

    async def up(self) -> None:
        self.up_calls += 1


class _DummyButton:
    def __init__(self, label: str = "Continue", box: dict | None = None) -> None:
        self.label = label
        self.box = box or {"x": 820.0, "y": 520.0, "width": 72.0, "height": 24.0}
        self.scroll_calls = 0
        self.click_calls = 0

    async def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        self.scroll_calls += 1

    async def bounding_box(self) -> dict:
        return self.box

    async def click(self, timeout: int = 0, force: bool = False) -> None:
        self.click_calls += 1


class _DummyLocator:
    def __init__(self, button: _DummyButton | None = None) -> None:
        self._button = button

    async def count(self) -> int:
        return 1 if self._button is not None else 0

    @property
    def first(self) -> _DummyButton:
        assert self._button is not None
        return self._button


class _DummyAuthPage(_DummyPage):
    def __init__(self, button: _DummyButton | None = None) -> None:
        super().__init__(url="https://m365.cloud.microsoft/chat?auth=1")
        self.button = button or _DummyButton()
        self.mouse = _DummyMouse()
        self.locator_calls: list[tuple[str, str | None]] = []

    def locator(self, selector: str, has_text: str | None = None):
        self.locator_calls.append((selector, has_text))
        if has_text in {"Continue", "Sign in", "Refresh", "OK"}:
            return _DummyLocator(self.button)
        return _DummyLocator(None)


def test_validate_tab1_requires_followup_before_ready(monkeypatch):
    ce = _import_cookie_extractor()
    page = _DummyPage()
    calls: list[tuple[str, str, bool]] = []
    ready_calls: list[tuple[float, float]] = []

    async def fake_get_context():
        return SimpleNamespace()

    async def fake_get_page(context):
        return page

    async def fake_clear_auth_dialog(tab, settle_seconds=8.0):
        return True, None

    async def fake_has_auth_dialog(tab):
        return False

    async def fake_wait_ready(tab, timeout_s=30.0, stable_s=5.0):
        ready_calls.append((timeout_s, stable_s))
        return True, None

    async def fake_browser_chat_on_page(tab, context, prompt, mode="chat", timeout_ms=60000, fresh_chat=True, progress_steps=None):
        calls.append((prompt, mode, fresh_chat))
        tab.url = "https://m365.cloud.microsoft/chat?auth=1"
        return {"success": True, "text": f"ok:{prompt}"}, tab

    monkeypatch.setenv("M365_CHAT_MODE", "work")
    monkeypatch.setattr(ce, "_get_context", fake_get_context)
    monkeypatch.setattr(ce, "_get_or_create_page", fake_get_page)
    monkeypatch.setattr(ce, "_clear_auth_dialog_if_present", fake_clear_auth_dialog)
    monkeypatch.setattr(ce, "_page_has_auth_dialog", fake_has_auth_dialog)
    monkeypatch.setattr(ce, "_wait_for_m365_chat_ready", fake_wait_ready)
    monkeypatch.setattr(ce, "_browser_chat_on_page", fake_browser_chat_on_page)

    result = asyncio.run(ce.validate_tab1_with_hello(timeout_ms=1000))

    assert result["validated"] is True
    assert result["follow_up_validated"] is True
    assert ready_calls == [(30.0, 5.0), (20.0, 3.0), (12.0, 2.0)]
    assert calls == [
        ("hello", "work", True),
        ("follow up 2", "work", False),
    ]


def test_validate_tab1_tolerates_err_aborted_navigation(monkeypatch):
    ce = _import_cookie_extractor()
    page = _DummyPage()
    calls: list[tuple[str, str, bool]] = []

    async def fake_get_context():
        return SimpleNamespace()

    async def fake_get_page(context):
        return page

    async def fake_clear_auth_dialog(tab, settle_seconds=8.0):
        return True, None

    async def fake_has_auth_dialog(tab):
        return False

    async def fake_browser_chat_on_page(tab, context, prompt, mode="chat", timeout_ms=60000, fresh_chat=True, progress_steps=None):
        calls.append((prompt, mode, fresh_chat))
        tab.url = "https://m365.cloud.microsoft/chat?auth=1"
        return {"success": True, "text": f"ok:{prompt}"}, tab

    goto_calls = {"count": 0}

    async def flaky_goto(url: str, wait_until: str = "domcontentloaded", timeout: int = 0):
        goto_calls["count"] += 1
        page.url = "https://m365.cloud.microsoft/chat?auth=1"
        raise Exception("Page.goto: net::ERR_ABORTED at https://m365.cloud.microsoft/chat")

    monkeypatch.setenv("M365_CHAT_MODE", "work")
    monkeypatch.setattr(ce, "_get_context", fake_get_context)
    monkeypatch.setattr(ce, "_get_or_create_page", fake_get_page)
    monkeypatch.setattr(ce, "_clear_auth_dialog_if_present", fake_clear_auth_dialog)
    monkeypatch.setattr(ce, "_page_has_auth_dialog", fake_has_auth_dialog)
    monkeypatch.setattr(ce, "_browser_chat_on_page", fake_browser_chat_on_page)
    monkeypatch.setattr(page, "goto", flaky_goto)

    result = asyncio.run(ce.validate_tab1_with_hello(timeout_ms=1000))

    assert goto_calls["count"] == 1
    assert result["validated"] is True
    assert result["follow_up_validated"] is True
    assert calls == [
        ("hello", "work", True),
        ("follow up 2", "work", False),
    ]


def test_validate_tab1_updates_progress_snapshot(monkeypatch):
    ce = _import_cookie_extractor()
    page = _DummyPage()

    async def fake_get_context():
        return SimpleNamespace()

    async def fake_get_page(context):
        return page

    async def fake_clear_auth_dialog(tab, settle_seconds=8.0):
        return True, None

    async def fake_has_auth_dialog(tab):
        return False

    async def fake_wait_ready(tab, timeout_s=30.0, stable_s=5.0):
        return True, None

    async def fake_browser_chat_on_page(tab, context, prompt, mode="chat", timeout_ms=60000, fresh_chat=True, progress_steps=None):
        if progress_steps:
            ce.update_tab1_auth_progress(progress_steps["prepare"], "done", f"prepared:{prompt}")
            ce.update_tab1_auth_progress(progress_steps["type"], "done", f"typed:{prompt}")
            ce.update_tab1_auth_progress(progress_steps["submit"], "done", f"submitted:{prompt}")
            ce.update_tab1_auth_progress(progress_steps["popup_watch"], "done", f"popup-clear:{prompt}")
            ce.update_tab1_auth_progress(progress_steps["reply"], "done", f"reply:{prompt}")
        tab.url = "https://m365.cloud.microsoft/chat?auth=1"
        return {"success": True, "text": f"ok:{prompt}"}, tab

    monkeypatch.setenv("M365_CHAT_MODE", "work")
    monkeypatch.setattr(ce, "_get_context", fake_get_context)
    monkeypatch.setattr(ce, "_get_or_create_page", fake_get_page)
    monkeypatch.setattr(ce, "_clear_auth_dialog_if_present", fake_clear_auth_dialog)
    monkeypatch.setattr(ce, "_page_has_auth_dialog", fake_has_auth_dialog)
    monkeypatch.setattr(ce, "_wait_for_m365_chat_ready", fake_wait_ready)
    monkeypatch.setattr(ce, "_browser_chat_on_page", fake_browser_chat_on_page)

    result = asyncio.run(ce.validate_tab1_with_hello(timeout_ms=1000))
    snap = ce.get_tab1_auth_progress_snapshot()

    assert result["validated"] is True
    assert snap["active"] is True
    assert snap["current_step_id"] == "pool_ready"
    assert next(s for s in snap["steps"] if s["id"] == "hello_reply")["status"] == "done"
    assert next(s for s in snap["steps"] if s["id"] == "follow_reply")["status"] == "done"
    assert next(s for s in snap["steps"] if s["id"] == "pool_ready")["status"] == "running"
    assert "pool_monitor" in snap
    assert "stats" in next(s for s in snap["steps"] if s["id"] == "hello_reply")


def test_validate_tab1_fails_if_chat_never_stabilizes(monkeypatch):
    ce = _import_cookie_extractor()
    page = _DummyPage(url="https://m365.cloud.microsoft/chat?auth=1")
    browser_calls: list[str] = []

    async def fake_get_context():
        return SimpleNamespace()

    async def fake_get_page(context):
        return page

    async def fake_clear_auth_dialog(tab, settle_seconds=8.0):
        return True, None

    async def fake_has_auth_dialog(tab):
        return False

    async def fake_wait_ready(tab, timeout_s=30.0, stable_s=5.0):
        return False, "service communication is currently unavailable"

    async def fake_browser_chat_on_page(tab, context, prompt, mode="chat", timeout_ms=60000, fresh_chat=True, progress_steps=None):
        browser_calls.append(prompt)
        return {"success": True, "text": "unexpected"}, tab

    monkeypatch.setattr(ce, "_get_context", fake_get_context)
    monkeypatch.setattr(ce, "_get_or_create_page", fake_get_page)
    monkeypatch.setattr(ce, "_clear_auth_dialog_if_present", fake_clear_auth_dialog)
    monkeypatch.setattr(ce, "_page_has_auth_dialog", fake_has_auth_dialog)
    monkeypatch.setattr(ce, "_wait_for_m365_chat_ready", fake_wait_ready)
    monkeypatch.setattr(ce, "_browser_chat_on_page", fake_browser_chat_on_page)

    result = asyncio.run(ce.validate_tab1_with_hello(timeout_ms=1000))
    snap = ce.get_tab1_auth_progress_snapshot()

    assert result["validated"] is False
    assert "service communication is currently unavailable" in result["error"]
    assert browser_calls == []
    assert next(s for s in snap["steps"] if s["id"] == "stabilize_1")["status"] == "error"


def test_validate_tab1_retries_first_hello_after_empty_timeout(monkeypatch):
    ce = _import_cookie_extractor()
    page = _DummyPage(url="https://m365.cloud.microsoft/chat?auth=1")
    calls: list[tuple[str, bool]] = []
    recovery_calls: list[str] = []

    async def fake_get_context():
        return SimpleNamespace()

    async def fake_get_page(context):
        return page

    async def fake_clear_auth_dialog(tab, settle_seconds=8.0):
        return True, None

    async def fake_has_auth_dialog(tab):
        return False

    async def fake_wait_ready(tab, timeout_s=30.0, stable_s=5.0):
        return True, None

    async def fake_recover(tab, context, reason=""):
        recovery_calls.append(reason)
        return tab, None

    async def fake_browser_chat_on_page(tab, context, prompt, mode="chat", timeout_ms=60000, fresh_chat=True, progress_steps=None):
        calls.append((prompt, fresh_chat))
        if prompt == "hello" and len([c for c in calls if c[0] == "hello"]) == 1:
            return {"success": False, "error": "No Copilot reply captured before timeout", "text": ""}, tab
        return {"success": True, "text": f"ok:{prompt}"}, tab

    monkeypatch.setenv("M365_CHAT_MODE", "work")
    monkeypatch.setattr(ce, "_get_context", fake_get_context)
    monkeypatch.setattr(ce, "_get_or_create_page", fake_get_page)
    monkeypatch.setattr(ce, "_clear_auth_dialog_if_present", fake_clear_auth_dialog)
    monkeypatch.setattr(ce, "_page_has_auth_dialog", fake_has_auth_dialog)
    monkeypatch.setattr(ce, "_wait_for_m365_chat_ready", fake_wait_ready)
    monkeypatch.setattr(ce, "_recover_tab1_after_turn_failure", fake_recover)
    monkeypatch.setattr(ce, "_browser_chat_on_page", fake_browser_chat_on_page)

    result = asyncio.run(ce.validate_tab1_with_hello(timeout_ms=1000))
    snap = ce.get_tab1_auth_progress_snapshot()

    assert result["validated"] is True
    assert calls == [
        ("hello", True),
        ("hello", True),
        ("follow up 2", False),
    ]
    assert recovery_calls == ["No Copilot reply captured before timeout"]
    assert next(s for s in snap["steps"] if s["id"] == "hello_reply")["status"] == "done"
    assert next(s for s in snap["steps"] if s["id"] == "follow_reply")["status"] == "done"


def test_browser_chat_blocks_pool_creation_until_tab1_ready(monkeypatch):
    ce = _import_cookie_extractor()

    async def fake_get_context():
        return SimpleNamespace()

    async def fake_ensure_ready(timeout_ms=60000, force_revalidate=False):
        return {
            "validated": False,
            "error": "Tab 1 not authenticated",
            "tab1_url": "http://127.0.0.1:8001/setup",
        }

    monkeypatch.setattr(ce, "_get_context", fake_get_context)
    monkeypatch.setattr(ce, "ensure_tab1_ready_for_pool", fake_ensure_ready)

    result = asyncio.run(ce.browser_chat("hello", mode="work", timeout_ms=1000, agent_id="c2-aider"))

    assert result["success"] is False
    assert "Tab 1 not authenticated" in result["error"]
    assert ce._page_pool is None


def test_browser_chat_initializes_pool_after_tab1_ready(monkeypatch):
    ce = _import_cookie_extractor()
    context = SimpleNamespace()
    calls: list[tuple[str, str]] = []

    class FakePool:
        def __init__(self, size: int) -> None:
            self._initialized = False
            self._size = size
            self._free_tabs = SimpleNamespace(qsize=lambda: 0)
            self._agent_tabs = {}
            self._base_size = size

        @property
        def agents(self):
            return list(self._agent_tabs.keys())

        async def initialize(self, ctx, progress_step_id=None):
            calls.append(("initialize", "pool"))
            self._initialized = True

        async def acquire(self, agent_id="", timeout=120.0):
            calls.append(("acquire", agent_id))
            return "page-1"

        def release(self, agent_id=""):
            calls.append(("release", agent_id))

        def update_tab(self, agent_id, page):
            calls.append(("update", agent_id))

    async def fake_get_context():
        return context

    async def fake_ensure_ready(timeout_ms=60000, force_revalidate=False):
        return {"validated": True}

    async def fake_browser_chat_on_page(page, ctx, prompt, mode="chat", timeout_ms=60000, fresh_chat=True):
        calls.append(("chat", mode))
        return {"success": True, "text": "READY"}, page

    monkeypatch.setattr(ce, "_get_context", fake_get_context)
    monkeypatch.setattr(ce, "ensure_tab1_ready_for_pool", fake_ensure_ready)
    monkeypatch.setattr(ce, "PagePool", FakePool)
    monkeypatch.setattr(ce, "_browser_chat_on_page", fake_browser_chat_on_page)

    result = asyncio.run(ce.browser_chat("hello", mode="work", timeout_ms=1000, agent_id="c2-aider"))

    assert result["success"] is True
    assert ("initialize", "pool") in calls
    assert ("acquire", "c2-aider") in calls


def test_pool_expand_blocks_until_tab1_ready(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    monkeypatch.setenv("API1_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BROWSER_AUTH_SKIP_WARM_NOVNC", "1")
    c3_server = _import_c3_server()

    async def fake_get_context():
        return SimpleNamespace()

    async def fake_ensure_ready(timeout_ms=60000, force_revalidate=False):
        return {
            "validated": False,
            "error": "Tab 1 still on setup",
            "tab1_url": "http://127.0.0.1:8001/setup",
        }

    monkeypatch.setattr(c3_server, "get_context", fake_get_context)
    monkeypatch.setattr(c3_server, "ensure_tab1_ready_for_pool", fake_ensure_ready)

    with TestClient(c3_server.app) as client:
        response = client.post("/pool-expand?target_size=6")

    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "blocked"
    assert "Tab 1 still on setup" in body["message"]


def test_pool_auth_failure_does_not_invalidate_tab1(monkeypatch):
    ce = _import_cookie_extractor()
    page = _DummyPage(url="https://m365.cloud.microsoft/chat?auth=1")
    ce._pool_pages.clear()
    ce._pool_pages.add(page)
    ce._tab1_session_ready = True
    ce._tab1_session_meta = {"ready": True}

    invalidated: list[str] = []

    def fake_invalidate(reason: str = ""):
        invalidated.append(reason)

    monkeypatch.setattr(ce, "invalidate_tab1_ready_state", fake_invalidate)

    changed = ce._maybe_invalidate_tab1_for_auth(page, "auth_dialog_present")

    assert changed is False
    assert invalidated == []


def test_prepare_pool_from_tab1_restores_tab1_focus(monkeypatch):
    ce = _import_cookie_extractor()
    ce._pool_pages.clear()
    tab1 = _DummyPage()
    context = SimpleNamespace(pages=[tab1])

    class FakePool:
        def __init__(self, size: int) -> None:
            self._initialized = False
            self.size = size
            self.available = 0
            self._base_size = size

        @property
        def agents(self):
            return []

        async def initialize(self, ctx, progress_step_id=None):
            self._initialized = True
            self.available = 2

        async def reload_all_tabs(self):
            self.available = 2
            return 2

        async def expand_to(self, ctx, target_size, progress_step_id=None):
            self.size = target_size
            self.available = target_size
            return target_size - 2

    monkeypatch.setattr(ce, "_get_context", lambda: context)
    monkeypatch.setattr(ce, "PagePool", FakePool)
    ce._page_pool = None

    result = asyncio.run(ce.prepare_pool_from_tab1(context=context, reload_existing=False))

    assert result["pool_initialized"] is True
    assert result["tab1_front"] is True
    assert tab1.front_calls == 1


def test_prepare_pool_from_tab1_tracks_expansion_steps(monkeypatch):
    ce = _import_cookie_extractor()
    tab1 = _DummyPage()
    context = SimpleNamespace(pages=[tab1])

    class FakePool:
        def __init__(self, size: int) -> None:
            self._initialized = False
            self.size = size
            self.available = 0
            self._base_size = size
            self._agents = ["c2-aider"]

        @property
        def agents(self):
            return list(self._agents)

        async def initialize(self, ctx, progress_step_id=None):
            self._initialized = True
            self.available = 4

        async def reload_all_tabs(self):
            return 0

        async def expand_to(self, ctx, target_size, progress_step_id=None):
            self.size = target_size
            self.available = 5
            return target_size - 5

    monkeypatch.setattr(ce, "_get_context", lambda: context)
    monkeypatch.setattr(ce, "PagePool", FakePool)
    ce._page_pool = None
    ce.reset_tab1_auth_progress("validate-auth")
    ce.mark_tab1_auth_progress_done("pool_ready", "Tab 1 ready for pool")

    result = asyncio.run(
        ce.prepare_pool_from_tab1(
            context=context,
            reload_existing=False,
            target_size=12,
            source="pool-expand",
        )
    )
    snap = ce.get_tab1_auth_progress_snapshot()

    assert result["target_size"] == 12
    assert result["pool_tabs_added"] == 7
    assert next(s for s in snap["steps"] if s["id"] == "pool_target")["status"] == "done"
    assert next(s for s in snap["steps"] if s["id"] == "pool_expand_done")["status"] == "done"
    assert snap["pool_monitor"]["target_size"] == 12
    assert snap["pool_monitor"]["phase"] == "ready"


def test_click_auth_dialog_button_uses_mouse_center():
    ce = _import_cookie_extractor()
    page = _DummyAuthPage()

    note = asyncio.run(ce._click_auth_dialog_button(page))

    assert note == "clicked:continue(mouse)"
    assert page.front_calls == 1
    assert page.mouse.down_calls == 1
    assert page.mouse.up_calls == 1
    assert len(page.mouse.moves) == 1
    x, y, steps = page.mouse.moves[0]
    assert x == 856.0
    assert y == 532.0
    assert steps == 8
    assert page.button.scroll_calls == 1
    assert page.button.click_calls == 0


def test_auth_progress_endpoint_returns_snapshot(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    monkeypatch.setenv("API1_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BROWSER_AUTH_SKIP_WARM_NOVNC", "1")
    c3_server = _import_c3_server()

    expected = {"run_id": "auth-123", "active": False, "steps": []}
    monkeypatch.setattr(c3_server, "get_tab1_auth_progress_snapshot", lambda: expected)

    with TestClient(c3_server.app) as client:
        response = client.get("/auth-progress")

    assert response.status_code == 200
    assert response.json() == expected


def test_page_pool_initialize_only_keeps_prepared_tabs(monkeypatch):
    ce = _import_cookie_extractor()
    ce._pool_pages.clear()

    created: list[_DummyPage] = []

    class _DummyContext:
        async def new_page(self):
            page = _DummyPage(url="about:blank")
            created.append(page)
            return page

    prep_results = iter(
        [
            (False, "Authentication required dialog persisted after page preparation"),
            (True, None),
        ]
    )

    async def fake_prepare(page, timeout_ms=30000, settle_seconds=8.0):
        page.url = "https://m365.cloud.microsoft/chat?auth=1"
        return next(prep_results)

    monkeypatch.setattr(ce, "_prepare_m365_chat_page", fake_prepare)

    pool = ce.PagePool(2)
    asyncio.run(pool.initialize(_DummyContext()))

    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is False
    assert pool.available == 1
    assert pool._initialized is True
