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
    return route


def _finished(url, body=b"body", status=200, content_type="text/javascript"):
    """A browser request that completed with ``body``."""
    response = MagicMock(status=status, headers={"content-type": content_type})
    response.body = AsyncMock(return_value=body)
    request = MagicMock(url=url)
    request.response = AsyncMock(return_value=response)
    return request


async def _handle(renderer, route):
    """Run the page's network policy handler for one request."""
    page = MagicMock(route=AsyncMock())
    await renderer._setup_network_policy(page)
    await page.route.call_args.args[1](route)
    return page


@pytest.mark.asyncio
async def test_miss_is_downloaded_by_browser_and_captured(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "c")
    first = _route(PINNED)

    page = await _handle(renderer, first)

    first.continue_.assert_awaited_once()
    events = {call.args[0] for call in page.context.on.call_args_list}
    assert events == {"requestfinished", "requestfailed"}
    await renderer._capture_response(_finished(PINNED, b"mathjax"))
    second = _route(PINNED)
    await _handle(renderer, second)
    second.continue_.assert_not_awaited()
    assert second.fulfill.call_args.kwargs["body"] == b"mathjax"
    assert renderer._disk_cache.get(PINNED) == (b"mathjax", "text/javascript")
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_context_is_watched_once(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    context = MagicMock(spec=["on"])
    renderer._watch_asset_downloads(context)
    renderer._watch_asset_downloads(context)
    assert context.on.call_count == 2


@pytest.mark.asyncio
async def test_concurrent_requests_wait_for_one_download(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    assert not await renderer._serve_cached_resource(_route(PINNED))
    waiter_route = _route(PINNED)
    waiter = asyncio.create_task(renderer._serve_cached_resource(waiter_route))
    await asyncio.sleep(0)
    assert not waiter.done()

    await renderer._capture_response(_finished(PINNED, b"shared"))

    assert await asyncio.wait_for(waiter, 1)
    assert waiter_route.fulfill.call_args.kwargs["body"] == b"shared"


@pytest.mark.asyncio
async def test_failed_download_releases_waiters(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    assert not await renderer._serve_cached_resource(_route(PINNED))
    waiter = asyncio.create_task(renderer._serve_cached_resource(_route(PINNED)))
    await asyncio.sleep(0)

    renderer._release_pending(PINNED)  # requestfailed

    assert await asyncio.wait_for(waiter, 1) is False
    assert not renderer._cdn_cache


@pytest.mark.asyncio
async def test_non_200_responses_are_not_cached(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "c")
    assert not await renderer._serve_cached_resource(_route(PINNED))
    await renderer._capture_response(_finished(PINNED, b"missing", status=404))
    assert not renderer._cdn_cache
    assert renderer._disk_cache.get(PINNED) is None
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_stale_pending_download_is_taken_over(tmp_path, monkeypatch):
    from astrbot_plugin_mathjax2image.infrastructure.browser import page_renderer

    renderer = PageRenderer(MagicMock(), tmp_path)
    assert not await renderer._serve_cached_resource(_route(PINNED))
    future, _ = renderer._cdn_pending[PINNED]
    renderer._cdn_pending[PINNED] = (future, 0.0)
    monkeypatch.setattr(page_renderer, "_PENDING_TTL_S", 1.0)

    # A page closed mid-download never reports back; do not wait for it.
    assert (
        await asyncio.wait_for(renderer._serve_cached_resource(_route(PINNED)), 1)
        is False
    )
    assert renderer._cdn_pending[PINNED][0] is not future


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
    delivery = asyncio.create_task(renderer._serve_cached_resource(route))
    await entered.wait()
    renderer._store_resource(SCOPED, b"abcde", "text/css")  # evicts PINNED
    finish.set()

    assert await delivery
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
    assert not await renderer._serve_cached_resource(_route(worker))

    await renderer._capture_response(_finished(worker, b"raw"))

    assert renderer._cdn_cache[worker].body == b"patched"
    assert renderer._disk_cache.get(worker)[0] == b"raw"


@pytest.mark.asyncio
async def test_renderer_serves_assets_from_disk_after_restart(tmp_path):
    cache_dir = tmp_path / "asset_cache"
    first = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=cache_dir)
    assert not await first._serve_cached_resource(_route(PINNED))
    await first._capture_response(_finished(PINNED, b"mathjax"))

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
    owner = asyncio.create_task(renderer._serve_cached_resource(first))
    await entered.wait()
    waiter = asyncio.create_task(renderer._serve_cached_resource(second))
    await asyncio.sleep(0)
    assert not waiter.done()
    finish.set()
    assert await owner
    assert await waiter
    first.fetch.assert_awaited_once()
    second.fetch.assert_not_awaited()
    assert second.fulfill.call_args.kwargs["body"] == b"shared font"
    assert not renderer._cdn_pending


@pytest.mark.asyncio
async def test_full_font_fetch_failure_allows_browser_fallback(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    route = _route(FULL_FONT)
    route.fetch = AsyncMock(side_effect=RuntimeError("network unavailable"))
    await _handle(renderer, route)
    route.continue_.assert_awaited_once()
    route.fulfill.assert_not_awaited()
    assert not renderer._cdn_pending
    assert not renderer._cdn_cache


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 404])
async def test_full_font_error_or_redirect_is_delivered_without_caching(
    tmp_path, status
):
    renderer = PageRenderer(MagicMock(), tmp_path, asset_cache_dir=tmp_path / "c")
    response = MagicMock(status=status)
    response.dispose = AsyncMock()
    response.body = AsyncMock(side_effect=AssertionError("Do not cache error bodies"))
    route = _route(FULL_FONT)
    route.fetch = AsyncMock(return_value=response)
    await _handle(renderer, route)
    route.fulfill.assert_awaited_once_with(response=response)
    response.dispose.assert_awaited_once()
    assert not renderer._cdn_pending
    assert renderer._disk_cache.get(FULL_FONT) is None


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
    task = asyncio.create_task(renderer._serve_cached_resource(route))
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
    route.fetch = AsyncMock(side_effect=AssertionError("Cache is disabled"))
    await _handle(renderer, route)
    route.fetch.assert_not_awaited()
    route.continue_.assert_awaited_once()
    assert not renderer._cdn_pending
