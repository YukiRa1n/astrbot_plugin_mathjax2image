"""Regression tests for bounded concurrent work and independent cache delivery."""

import asyncio
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_mathjax2image.domain.errors import PreprocessError, RenderError
from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
    PageRenderer,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
    TikzConverter,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
    TikzPlotConverter,
)

_CURVE = r"\begin{tikzpicture}\draw[domain=0:1,samples=800] plot (\x,{\x^2});\end{tikzpicture}"
_SURFACE = r"\begin{tikzpicture}\begin{axis}\addplot3[surf,samples=2,samples y=2] {x+y};\end{axis}\end{tikzpicture}"
_PLAIN_PICTURE = r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"


@pytest.mark.parametrize(
    ("source", "other", "over_budget"),
    [
        (_CURVE * 3, _PLAIN_PICTURE, True),
        (_CURVE * 2, _CURVE.replace("800", "1000") * 2, False),
        (_SURFACE * 2, _PLAIN_PICTURE, True),
    ],
)
def test_document_budgets_survive_interleaved_conversion(source, other, over_budget):
    plot = TikzPlotConverter(max_plot_points=6)
    converter = TikzConverter(plot)
    original = plot.convert
    started, resume = threading.Event(), threading.Event()
    first = True

    def interleaved(code, budget=None, surface_budget=None):
        nonlocal first
        if first:
            first = False
            started.set()
            assert resume.wait(3)
        return original(code, budget, surface_budget)

    plot.convert = interleaved
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(converter.convert, source)
        assert started.wait(3)
        try:
            assert converter.convert(other).count('class="tikz-diagram"') > 0
        finally:
            resume.set()
        if over_budget:
            with pytest.raises(PreprocessError):
                owner.result(timeout=3)
        else:
            assert owner.result(timeout=3).count(" -- ") == 2 * 799


@pytest.mark.asyncio
async def test_failed_concurrent_images_share_one_attempt_and_later_retry(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def fail(_html, _output):
        entered.set()
        await finish.wait()
        raise RenderError("TikZ timed out")

    renderer._render_uncached = AsyncMock(side_effect=fail)
    owner = asyncio.create_task(renderer.render_to_image("same", tmp_path / "a.png"))
    await entered.wait()
    waiters = [
        asyncio.create_task(renderer.render_to_image("same", tmp_path / f"{i}.png"))
        for i in range(2)
    ]
    await asyncio.sleep(0)
    finish.set()
    results = await asyncio.gather(owner, *waiters, return_exceptions=True)
    assert all(isinstance(result, RenderError) for result in results)
    assert renderer._render_uncached.await_count == 1
    assert not renderer._image_pending
    with pytest.raises(RenderError):
        await renderer.render_to_image("same", tmp_path / "retry.png")
    assert renderer._render_uncached.await_count == 2


@pytest.mark.asyncio
async def test_tikz_queue_leaves_capacity_for_plain_content(render_engine_factory):
    engine = render_engine_factory()
    renderer = engine._page_renderer
    entered, finish = asyncio.Event(), asyncio.Event()
    queued = asyncio.Event()
    acquire = renderer._tikz_slots.acquire

    async def observed_acquire():
        if renderer._tikz_slots.locked():
            queued.set()
        return await acquire()

    renderer._tikz_slots.acquire = observed_acquire

    async def render(html_path, output):
        if 'type="text/tikz"' in html_path.read_text():
            entered.set()
            await finish.wait()
        output.write_bytes(html_path.read_bytes())
        return True

    renderer._do_render = AsyncMock(side_effect=render)
    first = asyncio.create_task(engine.render('<script type="text/tikz">one</script>'))
    await entered.wait()
    second = asyncio.create_task(engine.render('<script type="text/tikz">two</script>'))
    try:
        await asyncio.wait_for(queued.wait(), 2)
        output = await asyncio.wait_for(engine.render("plain content"), timeout=1)
        assert output.read_text() == "plain content"
        assert not first.done() and not second.done()
    finally:
        finish.set()
        await asyncio.gather(first, second)
    assert engine._pending_renders == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["preprocess", "markdown"])
async def test_cancelled_cpu_work_keeps_its_slot_until_thread_exits(
    render_engine_factory, stage
):
    engine = render_engine_factory(max_concurrent_renders=1)
    started, finish = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    second_started = threading.Event()
    lock = threading.Lock()
    active, peak = 0, 0

    def blocked(content, *_args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if content == "first":
                loop.call_soon_threadsafe(started.set)
                assert finish.wait(3)
            else:
                second_started.set()
            return content
        finally:
            with lock:
                active -= 1

    if stage == "preprocess":
        engine._latex_preprocessor.preprocess = blocked
    else:
        engine._markdown_converter.convert_to_html = blocked

    async def render(html_path, output):
        output.write_bytes(html_path.read_bytes())
        return True

    engine._page_renderer._do_render = AsyncMock(side_effect=render)
    owner = asyncio.create_task(engine.render("first"))

    await asyncio.wait_for(started.wait(), 2)
    owner.cancel()
    second = asyncio.create_task(engine.render("second"))
    try:
        await asyncio.sleep(0.02)
        owner.cancel()
        await asyncio.sleep(0.02)
        assert not owner.done()
        assert not second_started.is_set()
    finally:
        finish.set()
        results = await asyncio.gather(owner, second, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1].read_text() == "second"
    assert peak == 1
    assert engine._pending_renders == 0


@pytest.mark.asyncio
async def test_cancelled_cached_write_finishes_before_artifact_cleanup(
    render_engine_factory, tmp_path, monkeypatch
):
    engine = render_engine_factory()
    html = "cached"
    renderer = engine._page_renderer
    renderer._image_cache[hashlib.sha256(html.encode()).digest()] = (
        b"png",
        time.monotonic(),
    )
    renderer._image_cache_bytes = 3
    original = Path.write_bytes
    started, finish = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    outputs = []

    def blocked_write(path, data):
        outputs.append(path)
        loop.call_soon_threadsafe(started.set)
        assert finish.wait(3)
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", blocked_write)
    task = asyncio.create_task(engine.render(html))

    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    try:
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert outputs and not any(path.exists() for path in outputs)
    assert not list(tmp_path.glob("render_*.png"))


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_delivery", [False, True])
async def test_cache_eviction_defers_disposal_without_blocking_other_resources(
    tmp_path, cancel_delivery
):
    manager = MagicMock()
    renderer = PageRenderer(manager, tmp_path)
    renderer._cdn_cache_limit = 2
    responses = [
        MagicMock(
            status=200,
            headers={"content-type": "text/javascript"},
            body=AsyncMock(return_value=b"ok"),
            dispose=AsyncMock(),
        )
        for _ in range(3)
    ]
    manager.request_context.get = AsyncMock(side_effect=responses)

    def route_for(name):
        route = MagicMock(fulfill=AsyncMock())
        route.request.url = f"https://cdn.jsdelivr.net/{name}.js"
        return route

    assert await renderer._serve_cached_resource(route_for("a"))
    entered, finish = asyncio.Event(), asyncio.Event()

    async def slow_fulfill(**_kwargs):
        entered.set()
        await finish.wait()

    slow = route_for("a")
    slow.fulfill = AsyncMock(side_effect=slow_fulfill)
    delivery = asyncio.create_task(renderer._serve_cached_resource(slow))
    await entered.wait()
    try:
        # Evict A during its warm delivery; B must still be delivered immediately.
        assert await asyncio.wait_for(
            renderer._serve_cached_resource(route_for("b")), 1
        )
        responses[0].dispose.assert_not_awaited()
        assert not renderer._cdn_lock.locked()
        assert await asyncio.wait_for(
            renderer._serve_cached_resource(route_for("b")), 1
        )
        assert renderer._cdn_cache_bytes == 2
        if cancel_delivery:
            delivery.cancel()
    finally:
        finish.set()
        result = (await asyncio.gather(delivery, return_exceptions=True))[0]
    if cancel_delivery:
        assert isinstance(result, asyncio.CancelledError)
    else:
        assert result is True
    responses[0].dispose.assert_awaited_once()
    responses[1].dispose.assert_not_awaited()

    # A cold owner also delivers outside the lock and remains pinned.
    cold = route_for("c")
    entered.clear()
    finish.clear()
    cold.fulfill = AsyncMock(side_effect=slow_fulfill)
    delivery = asyncio.create_task(renderer._serve_cached_resource(cold))
    await entered.wait()
    try:
        assert await asyncio.wait_for(
            renderer._serve_cached_resource(route_for("c")), 1
        )
        assert not renderer._cdn_lock.locked()
        responses[2].dispose.assert_not_awaited()
    finally:
        finish.set()
        await delivery
    assert not renderer._cdn_pending
