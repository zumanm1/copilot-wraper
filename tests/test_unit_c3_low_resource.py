from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _import_cookie_extractor():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    ba_path = str(ROOT / "browser_auth")
    if ba_path not in sys.path:
        sys.path.insert(1, ba_path)
    import cookie_extractor as ce
    return importlib.reload(ce)


class _DummyPage:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


def test_trim_free_tabs_closes_surplus_free_tabs():
    ce = _import_cookie_extractor()
    pool = ce.PagePool(4)
    ce._pool_pages.clear()

    pages = [_DummyPage(f"p{i}") for i in range(4)]
    for page in pages:
        ce._pool_pages.add(page)
        pool._free_tabs.put_nowait(page)
    pool._agent_tabs = {"a": object(), "b": object()}

    closed = asyncio.run(pool.trim_free_tabs())

    assert closed == 2
    assert pool.available == 2
    assert sum(1 for p in pages if p.closed) == 2


def test_trim_free_tabs_drops_all_free_tabs_when_agent_tabs_exceed_base():
    ce = _import_cookie_extractor()
    pool = ce.PagePool(3)
    ce._pool_pages.clear()

    pages = [_DummyPage(f"p{i}") for i in range(2)]
    for page in pages:
        ce._pool_pages.add(page)
        pool._free_tabs.put_nowait(page)
    pool._agent_tabs = {f"agent-{i}": object() for i in range(5)}

    closed = asyncio.run(pool.trim_free_tabs())

    assert closed == 2
    assert pool.available == 0
    assert all(p.closed for p in pages)

