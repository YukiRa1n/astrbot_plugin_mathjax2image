"""Regression tests for lean startup and bounded concurrent render admission."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from astrbot_plugin_mathjax2image.domain.errors import BrowserError, RenderError
from astrbot_plugin_mathjax2image.infrastructure.browser import browser_manager
from playwright.async_api import Error as PlaywrightError

pytestmark = pytest.mark.asyncio


async def test_concurrent_start_uses_one_driver_and_shell(monkeypatch):
    browser = MagicMock(is_connected=MagicMock(return_value=True), close=AsyncMock())
    browser_type = MagicMock(launch=AsyncMock(return_value=browser))
    type(browser_type).executable_path = PropertyMock(
        side_effect=AssertionError("Full browser path must not be checked")
    )
    context = MagicMock(dispose=AsyncMock())
    playwright = MagicMock(chromium=browser_type, stop=AsyncMock())
    playwright.request.new_context = AsyncMock(return_value=context)
    driver = MagicMock(start=AsyncMock(return_value=playwright))
    monkeypatch.setattr(
        browser_manager, "async_playwright", MagicMock(return_value=driver)
    )
    manager = browser_manager.BrowserManager()
    results = await asyncio.gather(*(manager.get_browser() for _ in range(6)))
    assert all(result is browser for result in results)
    driver.start.assert_awaited_once()
    browser_type.launch.assert_awaited_once_with(headless=True)
    await manager.close()
    context.dispose.assert_awaited_once()
    playwright.stop.assert_awaited_once()


async def test_launch_failure_does_not_install_for_unrelated_errors(monkeypatch):
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(side_effect=PlaywrightError("Target closed"))
    playwright.request.new_context = AsyncMock(return_value=MagicMock())
    driver = MagicMock(start=AsyncMock(return_value=playwright))
    install = AsyncMock()
    monkeypatch.setattr(
        browser_manager, "async_playwright", MagicMock(return_value=driver)
    )
    monkeypatch.setattr(browser_manager, "_install_browser", install)
    manager = browser_manager.BrowserManager(auto_install_browser=True)
    with pytest.raises(PlaywrightError, match="Target closed"):
        await manager.get_browser()
    install.assert_not_awaited()
    playwright.chromium.launch.side_effect = PlaywrightError(
        "Executable doesn't exist at shell"
    )
    manager.auto_install_browser = False
    with pytest.raises(BrowserError, match="--only-shell"):
        await manager.get_browser()
    install.assert_not_awaited()


async def test_cancelled_installer_kills_child_and_installs_shell_only(monkeypatch):
    process = MagicMock(returncode=None)
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError)
    process.wait = AsyncMock()
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(browser_manager.asyncio, "create_subprocess_exec", create)
    with pytest.raises(asyncio.CancelledError):
        await browser_manager._install_browser("chromium")
    assert create.call_args.args[-2:] == ("chromium", "--only-shell")
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


async def test_queue_caps_active_work_and_rejects_excess(tmp_path):
    from astrbot_plugin_mathjax2image.application.render_orchestrator import (
        RenderOrchestrator,
    )

    orchestrator = RenderOrchestrator(
        tmp_path, browser_max_pages=2, max_concurrent_renders=4, max_queued_renders=1
    )
    running = 0
    peak = 0
    both_running = asyncio.Event()
    finish = asyncio.Event()

    async def render(content, skip):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        if running == 2:
            both_running.set()
        try:
            await finish.wait()
            return Path(content)
        finally:
            running -= 1

    orchestrator._render_locked = AsyncMock(side_effect=render)
    first = asyncio.create_task(orchestrator.render("one"))
    second = asyncio.create_task(orchestrator.render("two"))
    await both_running.wait()
    queued = asyncio.create_task(orchestrator.render("three"))
    await asyncio.sleep(0)
    with pytest.raises(RenderError, match="队列已满"):
        await orchestrator.render("four")
    finish.set()
    assert await asyncio.gather(first, second, queued) == [
        Path("one"),
        Path("two"),
        Path("three"),
    ]
    assert peak == 2
    assert orchestrator._pending_renders == 0
    assert await orchestrator.render("five") == Path("five")


async def test_queue_timeout_and_cancellation_release_admission(tmp_path):
    from astrbot_plugin_mathjax2image.application.render_orchestrator import (
        RenderOrchestrator,
    )

    orchestrator = RenderOrchestrator(
        tmp_path, browser_max_pages=1, max_queued_renders=1, render_queue_timeout=20
    )
    started = asyncio.Event()
    finish = asyncio.Event()

    async def render(content, skip):
        started.set()
        await finish.wait()
        return Path(content)

    orchestrator._render_locked = AsyncMock(side_effect=render)
    first = asyncio.create_task(orchestrator.render("one"))
    await started.wait()
    with pytest.raises(RenderError, match="等待渲染超时"):
        await orchestrator.render("two")
    queued = asyncio.create_task(orchestrator.render("three"))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert orchestrator._pending_renders == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert orchestrator._pending_renders == 0
    finish.set()
    assert await orchestrator.render("four") == Path("four")


async def test_response_eviction_disposes_driver_buffers(tmp_path):
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )

    manager = MagicMock()
    responses = [
        MagicMock(
            status=200,
            headers={},
            body=AsyncMock(return_value=b"12345"),
            dispose=AsyncMock(),
        )
        for _ in range(3)
    ]
    manager.request_context.get = AsyncMock(side_effect=responses)
    renderer = PageRenderer(manager, tmp_path)
    renderer._cdn_cache_limit = 10
    for name in ["a", "b", "c"]:
        route = MagicMock(fulfill=AsyncMock())
        route.request.url = f"https://cdn.jsdelivr.net/{name}.js"
        assert await renderer._serve_cached_resource(route)
    responses[0].dispose.assert_awaited_once()
    responses[1].dispose.assert_not_awaited()
    responses[2].dispose.assert_not_awaited()
    assert renderer._cdn_cache_bytes == 10


async def test_close_waits_for_inflight_browser_start(monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    browser = MagicMock(is_connected=MagicMock(return_value=True), close=AsyncMock())

    async def launch(**options):
        entered.set()
        await finish.wait()
        return browser

    context = MagicMock(dispose=AsyncMock())
    playwright = MagicMock(stop=AsyncMock())
    playwright.chromium.launch = launch
    playwright.request.new_context = AsyncMock(return_value=context)
    monkeypatch.setattr(
        browser_manager,
        "async_playwright",
        MagicMock(return_value=MagicMock(start=AsyncMock(return_value=playwright))),
    )
    manager = browser_manager.BrowserManager()
    starting = asyncio.create_task(manager.get_browser())
    await entered.wait()
    closing = asyncio.create_task(manager.close())
    await asyncio.sleep(0)
    playwright.stop.assert_not_awaited()
    finish.set()
    await asyncio.gather(starting, closing)
    browser.close.assert_awaited_once()
    context.dispose.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert manager._browser is None


async def test_idle_page_cap_closes_excess_pages_and_cancels_timer():
    manager = browser_manager.BrowserManager(
        max_pages=2, max_idle_pages=1, idle_timeout=30
    )
    manager.get_browser = AsyncMock(return_value=MagicMock())
    pages = [
        MagicMock(
            is_closed=MagicMock(return_value=False),
            goto=AsyncMock(),
            viewport_size={"width": 100, "height": 100},
        )
        for _ in range(2)
    ]
    for page in pages:
        page.context.close = AsyncMock()
    manager._create_page = AsyncMock(side_effect=pages)
    await manager.acquire_page(100, 100)
    await manager.acquire_page(100, 100)
    await asyncio.gather(*(manager.release_page(page) for page in pages))
    assert manager._pool.qsize() == manager._active_pages_count == 1
    assert sum(page.context.close.await_count for page in pages) == 1
    await manager.close()
    assert manager._idle_timer is None
    assert sum(page.context.close.await_count for page in pages) == 2


async def test_idle_expiry_preserves_leased_page_and_request_context():
    manager = browser_manager.BrowserManager(max_pages=1, idle_timeout=0.02)
    manager.get_browser = AsyncMock(return_value=MagicMock())
    page = MagicMock(
        is_closed=MagicMock(return_value=False),
        goto=AsyncMock(),
        viewport_size={"width": 100, "height": 100},
    )
    page.context.close = AsyncMock()
    manager._create_page = AsyncMock(return_value=page)
    context = MagicMock(dispose=AsyncMock())
    manager.request_context = context
    await manager.acquire_page(100, 100)
    await manager.release_page(page)
    leased, _ = await manager.acquire_page(100, 100)
    await asyncio.sleep(0.04)
    assert leased is page
    page.context.close.assert_not_awaited()
    await manager.release_page(page)
    await asyncio.sleep(0.04)
    assert manager._active_pages_count == manager._pool.qsize() == 0
    page.context.close.assert_awaited_once()
    context.dispose.assert_not_awaited()
    await manager.close()
    context.dispose.assert_awaited_once()


async def test_zero_cache_budgets_disable_retention(tmp_path):
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )

    manager = MagicMock()
    renderer = PageRenderer(
        manager, tmp_path, resource_cache_max_mb=0, image_cache_max_mb=0
    )
    route = MagicMock(fulfill=AsyncMock())
    assert not await renderer._serve_cached_resource(route)
    route.fulfill.assert_not_awaited()

    async def render(html, output):
        output.write_bytes(b"png")
        return True

    renderer._render_uncached = AsyncMock(side_effect=render)
    await renderer.render_to_image("same", tmp_path / "one.png")
    await renderer.render_to_image("same", tmp_path / "two.png")
    assert renderer._render_uncached.await_count == 2
    assert renderer._image_cache_bytes == 0
