"""Tests for the persistent CDN asset cache and its use by the renderer."""

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_mathjax2image.infrastructure.browser.asset_disk_cache import (
    AssetDiskCache,
)
from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
    PageRenderer,
)

PINNED = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js"
SCOPED = "https://cdn.jsdelivr.net/npm/@fontsource/noto-serif-sc@5.3.0/600.css"
UNPINNED = (
    "https://astrbot-plugins.oss-cn-hangzhou.aliyuncs.com/astrbot-plugins/fonts/A.ttf"
)


def test_round_trip_and_content_type(tmp_path):
    cache = AssetDiskCache(tmp_path, 1024)

    assert cache.get(PINNED) is None
    assert cache.put(PINNED, b"body", "text/javascript")

    assert cache.get(PINNED) == (b"body", "text/javascript")
    # A fresh instance (as after a restart) sees the same entry.
    assert AssetDiskCache(tmp_path, 1024).get(PINNED) == (b"body", "text/javascript")


@pytest.mark.parametrize(
    ("url", "immutable"),
    [
        (PINNED, True),
        (SCOPED, True),
        ("https://unpkg.com/mermaid@10.9.3/dist/mermaid.min.js", True),
        ("https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js", False),
        (UNPINNED, False),
    ],
)
def test_only_exact_npm_versions_are_immutable(url, immutable):
    assert (AssetDiskCache.max_age(url) is None) is immutable


def test_unpinned_entries_expire(tmp_path):
    cache = AssetDiskCache(tmp_path, 1024)
    cache.put(UNPINNED, b"font", "font/ttf")
    meta_path = next(tmp_path.glob("*.json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["stored"] = time.time() - 8 * 24 * 3600
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert cache.get(UNPINNED) is None
    assert not list(tmp_path.iterdir())


def test_truncated_body_is_discarded(tmp_path):
    cache = AssetDiskCache(tmp_path, 1024)
    cache.put(PINNED, b"complete", "text/javascript")
    next(tmp_path.glob("*.bin")).write_bytes(b"part")

    assert cache.get(PINNED) is None
    assert not list(tmp_path.glob("*.json"))


def test_evicts_least_recently_used_beyond_budget(tmp_path):
    cache = AssetDiskCache(tmp_path, 10)
    urls = [f"https://cdn.jsdelivr.net/npm/pkg@1.0.{i}/a.js" for i in range(3)]
    for index, url in enumerate(urls[:2]):
        cache.put(url, b"12345", "text/javascript")
        # mtime resolution differs across filesystems; order entries explicitly.
        meta = cache._paths(url)[1]
        os.utime(meta, (index, index))
    cache.get(urls[0])  # refresh the older entry

    cache.put(urls[2], b"12345", "text/javascript")

    assert cache.get(urls[0]) is not None
    assert cache.get(urls[1]) is None
    assert cache.get(urls[2]) is not None


def test_oversized_and_disabled_caches_store_nothing(tmp_path):
    assert not AssetDiskCache(tmp_path, 4).put(PINNED, b"12345", "text/javascript")
    disabled = AssetDiskCache(tmp_path, 0)
    assert not disabled.enabled
    assert not disabled.put(PINNED, b"1", "text/javascript")
    assert not list(tmp_path.iterdir())


def _route(url):
    route = MagicMock()
    route.request.url = url
    route.request.method = "GET"
    route.fulfill = AsyncMock()
    route.continue_ = AsyncMock()
    route.abort = AsyncMock()
    route.fetch = AsyncMock(return_value=_response())
    return route


def _response(body=b"body", status=200, content_type="text/javascript"):
    response = MagicMock(status=status, headers={"content-type": content_type})
    response.body = AsyncMock(return_value=body)
    response.dispose = AsyncMock()
    return response


async def _handle(renderer, route):
    """Run the page's network policy handler for one request."""
    page = MagicMock()
    page.context.route = AsyncMock()
    await renderer._setup_network_policy(page)
    await page.context.route.call_args.args[1](route)


@pytest.mark.asyncio
async def test_cache_miss_downloads_and_caches_validated_response(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "c")
    first = _route(PINNED)
    first.fetch.return_value = _response(b"mathjax")

    await _handle(renderer, first)

    first.fetch.assert_awaited_once()
    first.continue_.assert_not_awaited()
    assert first.fulfill.call_args.kwargs["body"] == b"mathjax"
    second = _route(PINNED)
    await _handle(renderer, second)
    second.fetch.assert_not_awaited()
    assert second.fulfill.call_args.kwargs["body"] == b"mathjax"
    assert renderer._disk_cache.get(PINNED) == (b"mathjax", "text/javascript")
    assert not renderer._cdn_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_mb", [0, 1])
@pytest.mark.parametrize("allowed", [False, True])
async def test_cdn_redirect_target_is_checked_before_fetch(tmp_path, cache_mb, allowed):
    renderer = PageRenderer(MagicMock(), tmp_path, resource_cache_max_mb=cache_mb)
    redirect = _response(status=302)
    target = PINNED.replace("tex-chtml.js", "tex-svg.js")
    redirect.headers["location"] = (
        "tex-svg.js" if allowed else "https://example.com/asset.js"
    )
    final = _response(b"redirected")
    route = _route(PINNED)
    route.fetch.side_effect = [redirect, final]
    await _handle(renderer, route)

    redirect.dispose.assert_awaited_once()
    route.continue_.assert_not_awaited()
    assert all(
        call.kwargs["max_redirects"] == 0 for call in route.fetch.await_args_list
    )
    if allowed:
        assert [call.kwargs["url"] for call in route.fetch.await_args_list] == [
            PINNED,
            target,
        ]
        route.fulfill.assert_awaited_once()
        route.abort.assert_not_awaited()
        final.dispose.assert_awaited_once()
    else:
        route.fetch.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        route.abort.assert_awaited_once_with("blockedbyclient")
        assert not renderer._cdn_cache
    assert not renderer._cdn_pending


def test_legacy_css_cache_is_refetched(tmp_path):
    cache = AssetDiskCache(tmp_path, 1024)
    assert cache.put(SCOPED, b"a{}", "text/css")
    _, metadata = cache._paths(SCOPED)
    entry = json.loads(metadata.read_text(encoding="utf-8"))
    del entry["css_base_resolved"]
    metadata.write_text(json.dumps(entry), encoding="utf-8")
    assert cache.get(SCOPED) is None
    assert cache.put(SCOPED, b"b{}", "text/css")
    assert cache.get(SCOPED) == (b"b{}", "text/css")


@pytest.mark.browser
@pytest.mark.asyncio
async def test_redirected_stylesheet_resolves_relative_imports(tmp_path, monkeypatch):
    from playwright.async_api import Route
    from astrbot_plugin_mathjax2image.infrastructure.browser import BrowserManager

    base = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/"
    entry = base + "entry/style.css"
    final = base + "styles/theme.css"
    palette = base + "styles/palette.css"
    requests = []

    async def fetch(route, *, url, **options):
        requests.append(url)
        if url == entry:
            redirect = _response(status=302)
            redirect.headers["location"] = final
            return redirect
        bodies = {
            final: b'@import "palette.css";',
            palette: b"body { color: rgb(7,8,9) }",
        }
        return _response(bodies[url], content_type="text/css")

    monkeypatch.setattr(Route, "fetch", fetch)
    manager = BrowserManager(max_pages=1)
    renderer = PageRenderer(manager, tmp_path)
    html = f'<link rel="stylesheet" href="{entry}"><main>test</main>'
    try:
        take_screenshot = renderer._take_screenshot

        async def check_color(page, output):
            assert (
                await page.evaluate("getComputedStyle(document.body).color")
                == "rgb(7, 8, 9)"
            )
            await take_screenshot(page, output)

        renderer._take_screenshot = check_color
        await renderer.render_to_image(html, tmp_path / "redirect.png")
        assert palette in requests and base + "entry/palette.css" not in requests
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_concurrent_requests_wait_for_one_download(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()
    owner_route = _route(PINNED)

    async def fetch(**kwargs):
        started.set()
        await finish.wait()
        return _response(b"shared")

    owner_route.fetch.side_effect = fetch
    owner = asyncio.create_task(renderer._serve_remote_resource(owner_route))
    await started.wait()
    waiter_route = _route(PINNED)
    waiter = asyncio.create_task(renderer._serve_remote_resource(waiter_route))
    await asyncio.sleep(0)
    assert not waiter.done()

    finish.set()
    await asyncio.wait_for(asyncio.gather(owner, waiter), 1)
    waiter_route.fetch.assert_not_awaited()
    assert waiter_route.fulfill.call_args.kwargs["body"] == b"shared"


@pytest.mark.asyncio
async def test_failed_download_releases_waiters(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()
    owner_route = _route(PINNED)

    async def fetch(**kwargs):
        started.set()
        await finish.wait()
        raise RuntimeError("network unavailable")

    owner_route.fetch.side_effect = fetch
    owner = asyncio.create_task(_handle(renderer, owner_route))
    await started.wait()
    route = _route(PINNED)
    waiter = asyncio.create_task(_handle(renderer, route))
    await asyncio.sleep(0)

    finish.set()
    await asyncio.wait_for(asyncio.gather(owner, waiter), 1)
    owner_route.abort.assert_awaited_once()
    route.fetch.assert_awaited_once()
    route.fulfill.assert_awaited_once()
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_non_200_responses_are_not_cached(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "c")
    route = _route(PINNED)
    route.fetch.return_value = _response(b"missing", status=404)
    await _handle(renderer, route)
    route.fulfill.assert_awaited_once_with(response=route.fetch.return_value)
    assert not renderer._cdn_cache
    assert renderer._disk_cache.get(PINNED) is None
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_stale_pending_download_is_taken_over(tmp_path, monkeypatch):
    from astrbot_plugin_mathjax2image.infrastructure.browser import page_renderer

    renderer = PageRenderer(MagicMock(), tmp_path)
    future = asyncio.get_running_loop().create_future()
    renderer._cdn_pending[PINNED] = (future, 0.0)
    monkeypatch.setattr(page_renderer, "_PENDING_TTL_S", 1.0)

    route = _route(PINNED)
    await asyncio.wait_for(renderer._serve_remote_resource(route), 1)
    route.fetch.assert_awaited_once()
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_memory_budget_evicts_least_recently_used(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    renderer._cdn_cache_limit = 10
    urls = [f"https://cdn.jsdelivr.net/npm/p@1.0.{i}/a.js" for i in range(3)]
    renderer._store_resource(urls[0], b"12345", "text/javascript")
    renderer._store_resource(urls[1], b"12345", "text/javascript")
    assert renderer._lookup_resource(urls[0]) is not None

    renderer._store_resource(urls[2], b"12345", "text/javascript")

    assert set(renderer._cdn_cache) == {urls[0], urls[2]}
    assert renderer._cdn_cache_bytes == 10


@pytest.mark.asyncio
async def test_eviction_during_delivery_keeps_the_body(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    renderer._cdn_cache_limit = 5
    renderer._store_resource(PINNED, b"12345", "text/javascript")
    entered, finish = asyncio.Event(), asyncio.Event()
    route = _route(PINNED)

    async def slow_fulfill(**kwargs):
        entered.set()
        await finish.wait()

    route.fulfill = AsyncMock(side_effect=slow_fulfill)
    delivery = asyncio.create_task(renderer._serve_remote_resource(route))
    await entered.wait()
    renderer._store_resource(SCOPED, b"abcde", "text/css")  # evicts PINNED
    finish.set()

    await delivery
    assert route.fulfill.call_args.kwargs["body"] == b"12345"
    assert list(renderer._cdn_cache) == [SCOPED]


@pytest.mark.asyncio
async def test_tikz_worker_is_patched_in_memory_and_raw_on_disk(tmp_path, monkeypatch):
    from astrbot_plugin_mathjax2image.infrastructure.browser import page_renderer

    worker = (
        "https://cdn.jsdelivr.net/npm/@drgrice1/tikzjax@1.0.0-beta24/dist/run-tex.js"
    )
    monkeypatch.setattr(page_renderer, "optimize_tikz_worker", lambda body: b"patched")
    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "c")
    route = _route(worker)
    route.fetch.return_value = _response(b"raw")
    await renderer._serve_remote_resource(route)

    assert renderer._cdn_cache[worker].body == b"patched"
    assert renderer._disk_cache.get(worker)[0] == b"raw"


@pytest.mark.asyncio
async def test_renderer_serves_assets_from_disk_after_restart(tmp_path):
    cache_dir = tmp_path / "asset_cache"
    first = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=cache_dir)
    initial = _route(PINNED)
    initial.fetch.return_value = _response(b"mathjax")
    await first._serve_remote_resource(initial)

    # A new renderer, as after AstrBot restarts, must not touch the network.
    second = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=cache_dir)
    route = _route(PINNED)
    await _handle(second, route)

    route.continue_.assert_not_awaited()
    assert route.fulfill.call_args.kwargs["body"] == b"mathjax"
    assert route.fulfill.call_args.kwargs["headers"]["content-type"] == (
        "text/javascript"
    )


@pytest.mark.asyncio
async def test_renderer_without_cache_dir_keeps_memory_only_behavior(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    assert renderer._disk_cache is None
    disabled = PageRenderer(
        MagicMock(), tmp_path, asset_cache_dir=tmp_path, asset_disk_cache_max_mb=0
    )
    assert disabled._disk_cache is None


# Full fonts can exceed Chromium's response inspector buffer.
FULL_FONT = "https://cdn.jsdelivr.net/gh/tw93/Kami@6b399e93a5dfce0eaa7e3aabcc04cc260f01b8f6/assets/fonts/TsangerJinKai02-W04.ttf"


@pytest.mark.asyncio
async def test_full_font_download_is_cached_without_browser_response_body(tmp_path):
    body = b"font" * (3 * 1024 * 1024)
    response = MagicMock(status=200, headers={"content-type": "font/ttf"})
    response.body = AsyncMock(return_value=body)
    response.dispose = AsyncMock()
    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "fonts")
    route = _route(FULL_FONT)
    route.fetch = AsyncMock(return_value=response)

    await _handle(renderer, route)

    route.fetch.assert_awaited_once()
    route.continue_.assert_not_awaited()
    assert route.fetch.call_args.kwargs["max_redirects"] == 0
    assert route.fulfill.call_args.kwargs["body"] == body
    response.dispose.assert_awaited_once()
    assert not renderer._cdn_pending
    assert renderer._disk_cache.get(FULL_FONT) == (body, "font/ttf")
    restarted = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "fonts")
    warm = _route(FULL_FONT)
    warm.fetch = AsyncMock(side_effect=AssertionError("Network must not be used"))
    await _handle(restarted, warm)
    warm.fetch.assert_not_awaited()
    warm.continue_.assert_not_awaited()
    assert warm.fulfill.call_args.kwargs["body"] == body


@pytest.mark.parametrize(
    ("url", "immutable"),
    [
        (FULL_FONT, True),
        (FULL_FONT.replace("6b399e93a5dfce0eaa7e3aabcc04cc260f01b8f6", "main"), False),
        (
            FULL_FONT.replace("6b399e93a5dfce0eaa7e3aabcc04cc260f01b8f6", "6b399e9"),
            False,
        ),
        (FULL_FONT + "?version=1", False),
        (FULL_FONT.replace("cdn.jsdelivr.net", "example.com"), False),
    ],
)
def test_full_github_commits_are_immutable(url, immutable):
    assert (AssetDiskCache.max_age(url) is None) is immutable


@pytest.mark.asyncio
async def test_full_font_concurrent_requests_share_one_download(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()
    response = MagicMock(status=200, headers={"content-type": "font/ttf"})
    response.dispose = AsyncMock()

    async def body():
        entered.set()
        await finish.wait()
        return b"shared font"

    response.body = body
    first, second = _route(FULL_FONT), _route(FULL_FONT)
    first.fetch = AsyncMock(return_value=response)
    second.fetch = AsyncMock(side_effect=AssertionError("Duplicate font download"))
    owner = asyncio.create_task(renderer._serve_remote_resource(first))
    await entered.wait()
    waiter = asyncio.create_task(renderer._serve_remote_resource(second))
    await asyncio.sleep(0)
    assert not waiter.done()
    finish.set()
    await asyncio.gather(owner, waiter)
    first.fetch.assert_awaited_once()
    second.fetch.assert_not_awaited()
    assert second.fulfill.call_args.kwargs["body"] == b"shared font"
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_full_font_fetch_failure_does_not_retry_outside_network_policy(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    route = _route(FULL_FONT)
    route.fetch = AsyncMock(side_effect=RuntimeError("network unavailable"))
    await _handle(renderer, route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.continue_.assert_not_awaited()
    route.fulfill.assert_not_awaited()
    assert not renderer._cdn_pending
    assert not renderer._cdn_cache


@pytest.mark.asyncio
async def test_cancelled_font_download_disposes_response_and_releases_waiters(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    entered = asyncio.Event()
    response = MagicMock(status=200)
    response.dispose = AsyncMock()

    async def body():
        entered.set()
        await asyncio.Event().wait()

    response.body = body
    route = _route(FULL_FONT)
    route.fetch = AsyncMock(return_value=response)
    task = asyncio.create_task(renderer._serve_remote_resource(route))
    await entered.wait()
    pending = renderer._cdn_pending[FULL_FONT][0]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pending.done() and not pending.cancelled()
    assert not renderer._cdn_pending
    response.dispose.assert_awaited_once()
    assert not renderer._cdn_cache


@pytest.mark.asyncio
async def test_font_download_respects_disabled_cache(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path, resource_cache_max_mb=0)
    route = _route(FULL_FONT)
    await _handle(renderer, route)
    route.fetch.assert_awaited_once()
    route.fulfill.assert_awaited_once_with(response=route.fetch.return_value)
    assert not renderer._cdn_cache
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_same_font_requested_during_disk_lookup_downloads_once(
    tmp_path, monkeypatch
):
    from astrbot_plugin_mathjax2image.infrastructure.browser import page_renderer

    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "cache")
    checking, release = asyncio.Event(), asyncio.Event()
    run_in_thread = page_renderer.run_in_thread

    async def delayed_lookup(func, *args):
        if func == renderer._disk_cache.get:
            checking.set()
            await release.wait()
            return None
        return await run_in_thread(func, *args)

    monkeypatch.setattr(page_renderer, "run_in_thread", delayed_lookup)
    response = MagicMock(status=200, headers={"content-type": "font/ttf"})
    response.body = AsyncMock(return_value=b"font bytes")
    response.dispose = AsyncMock()
    first, second = _route(FULL_FONT), _route(FULL_FONT)
    first.fetch = second.fetch = AsyncMock(return_value=response)
    a = asyncio.create_task(renderer._serve_remote_resource(first))
    await checking.wait()
    b = asyncio.create_task(renderer._serve_remote_resource(second))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(a, b)
    first.fetch.assert_awaited_once()
    assert all(
        r.fulfill.call_args.kwargs["body"] == b"font bytes" for r in (first, second)
    )
    assert not renderer._cdn_pending
