"""The Playwright renderer against a fake browser (the real run needs Chromium: tabfree-api[render])."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import SiteRole
from tabfree_api.exports import PlaywrightRenderer


class FakePage:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    def set_default_timeout(self, ms: float) -> None: ...

    def goto(self, url: str, wait_until: str) -> None:
        self.log.append(("goto", url))

    def wait_for_timeout(self, ms: int) -> None: ...

    def emulate_media(self, media: str) -> None:
        self.log.append(("media", media))

    def evaluate(self, script: str) -> list[int]:
        return [1280, 3010]

    def pdf(self, **kwargs: Any) -> bytes:
        self.log.append(("pdf", kwargs))
        return b"%PDF"

    def screenshot(self, **kwargs: Any) -> bytes:
        self.log.append(("png", kwargs))
        return b"\x89PNG"


class FakeSessions:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def create(self, *args: Any, **kwargs: Any) -> str:
        return "token"

    def delete(self, token: str) -> None:
        self.deleted.append(token)


@pytest.fixture
def browser_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    log: list[tuple[str, Any]] = []

    class Context:
        def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
            log.append(("cookies", cookies))

        def new_page(self) -> FakePage:
            return FakePage(log)

    class Browser:
        def new_context(self, **kwargs: Any) -> Context:
            return Context()

        def close(self) -> None:
            log.append(("close", None))

    class Manager:
        chromium = types.SimpleNamespace(launch=lambda: Browser())

        def __enter__(self) -> Manager:
            return self

        def __exit__(self, *exc: object) -> None: ...

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: Manager()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return log


INFO = SessionInfo("erin", "Erin", frozenset(), SiteRole.VIEWER, 0.0)


def test_pdf_is_one_page_the_size_of_the_dashboard(browser_log: list[tuple[str, Any]]) -> None:
    sessions = FakeSessions()
    renderer = PlaywrightRenderer("http://portal:8000/", sessions, settle_ms=0)
    assert renderer.render(INFO, "sales analysis", {}, "pdf") == b"%PDF"
    calls = dict(browser_log)
    assert calls["goto"] == "http://portal:8000/dashboards/sales%20analysis?print=1&state=e30"
    assert calls["media"] == "screen"
    # Fixed paper cut the dashboard's frame across pages and left blank ones.
    assert calls["pdf"] == {
        "print_background": True,
        "width": "1280px",
        "height": "3010px",
        "page_ranges": "1",
    }
    assert calls["cookies"][0]["url"] == "http://portal:8000"
    assert sessions.deleted == ["token"]  # the short render session never outlives the render


def test_png_is_a_full_page_screenshot(browser_log: list[tuple[str, Any]]) -> None:
    renderer = PlaywrightRenderer("http://portal:8000", FakeSessions(), settle_ms=0)
    assert renderer.render_path(INFO, "/drafts/7", {}, "png") == b"\x89PNG"
    calls = dict(browser_log)
    assert calls["goto"] == "http://portal:8000/drafts/7?print=1"
    assert calls["png"] == {"full_page": True, "type": "png"}
    with pytest.raises(ValueError, match="path on this server"):
        renderer.render_path(INFO, "//evil.example/x", {}, "png")
