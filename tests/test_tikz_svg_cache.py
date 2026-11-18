"""Figure cache ownership, document isolation and SVG replay regressions."""

import asyncio
import time

import pytest

from astrbot_plugin_mathjax2image.infrastructure.browser import BrowserManager
from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
    _PLACE_FIGURES,
    _RENDER_CONTENT,
    PageRenderer,
)
from astrbot_plugin_mathjax2image.infrastructure.browser.tikz_svg_cache import (
    TikzSvgCache,
)


def document(*names, caption="", shell="theme"):
    figures = "".join(
        '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true">\n'
        + name
        + "\n</script></div>"
        for name in names
    )
    return f'<head>{shell}</head><main class="render-content">{figures}{caption}</main>'


async def prepare(cache, html):
    return await cache.prepare(html, *_RENDER_CONTENT.search(html).span(1), 2.0)


@pytest.mark.asyncio
async def test_figures_merge_across_documents_without_holding_slots():
    cache = TikzSvgCache(256)
    owner = await prepare(cache, document("A", "B", "A"))
    assert owner.hits == [None, None]
    assert owner.html.count('<script type="text/tikz"') == 2
    waiter = asyncio.create_task(prepare(cache, document("B", "A", caption="new")))
    await asyncio.sleep(0)
    assert not waiter.done()
    owner.compiled = {"0": "<svg>A</svg>", "1": "<svg>B</svg>"}
    cache.finish(owner, True)
    replay = await waiter
    assert replay.hits == ["<svg>B</svg>", "<svg>A</svg>"]
    assert '<script type="text/tikz"' not in replay.html
    cache.finish(replay, True)
    assert not cache.pending
    math_caption = await prepare(
        cache, document("A", shell="themewindow.mathJaxRequired = true;")
    )
    assert math_caption.hits == ["<svg>A</svg>"]
    cache.finish(math_caption, True)
    alternate = await prepare(cache, document("A", shell="other theme"))
    assert alternate.hits == [None]
    cache.finish(alternate, False)


@pytest.mark.asyncio
async def test_failed_owner_wakes_retries_and_cancelled_waiter_does_not_own_keys():
    cache = TikzSvgCache(256)
    owner = await prepare(cache, document("A"))
    waiter = asyncio.create_task(prepare(cache, document("A", "B")))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert set(cache.pending) == set(owner.keys)
    retry = asyncio.create_task(prepare(cache, document("A")))
    await asyncio.sleep(0)
    owner.compiled = {"0": "partial"}
    cache.finish(owner, False)
    next_owner = await retry
    assert next_owner.hits == [None]
    cache.finish(next_owner, False)
    assert not cache.pending and not cache.entries


@pytest.mark.asyncio
async def test_figure_cache_budget_expiry_and_dynamic_figures(monkeypatch):
    cache = TikzSvgCache(6)
    for name, value in [("A", "aaaa"), ("B", "bbbb"), ("C", "oversize")]:
        batch = await prepare(cache, document(name))
        batch.compiled = {"0": value}
        cache.finish(batch, True)
    assert cache.size == 4 and len(cache.entries) == 1
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 301)
    expired = await prepare(cache, document("B"))
    assert expired.hits == [None] and cache.size == 0
    cache.finish(expired, False)
    assert await prepare(cache, document(r"\pgfmathrandominteger")) is None


@pytest.mark.browser
@pytest.mark.asyncio
async def test_replayed_glyphs_clips_and_duplicate_copies_have_local_ids():
    manager = BrowserManager(max_pages=1)
    svg = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
    <defs><path id="glyph" d="M0 0h5v5z"/><clipPath id="clip"><circle r="2"/></clipPath></defs>
    <use xlink:href="#glyph" clip-path="url(#clip)"/></svg>"""
    try:
        page, _ = await manager.acquire_page(100, 100)
        await page.set_content(
            '<main class="render-content"><div class="tikz-diagram" data-tikz-source="0">'
            + svg
            + '</div><div data-tikz-copy="0"></div><div data-tikz-copy="1"></div></main>'
        )
        fresh = await page.evaluate(
            _PLACE_FIGURES, {"hits": [None, svg], "native": False, "budget": 5000}
        )
        assert list(fresh) == ["0"] and 'id="glyph"' in fresh["0"]
        assert await page.evaluate("""() => {
            const figs = [...document.querySelectorAll('.tikz-diagram svg')];
            const ids = [...document.querySelectorAll('[id]')].map(n => n.id);
            return figs.length === 3 && new Set(ids).size === 6 && figs.every(s => {
                const use = s.querySelector('use');
                return use.getAttribute('xlink:href') === '#' + s.querySelector('path').id
                    && use.getAttribute('clip-path') === 'url(#' + s.querySelector('clipPath').id + ')';
            });
        }""")
    finally:
        await manager.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_renderer_skips_compilation_when_only_caption_changes(tmp_path):
    manager = BrowserManager(max_pages=1)
    renderer = PageRenderer(manager, tmp_path, image_cache_max_mb=0)
    # Substitute a deterministic compiler on the page. The normal render entry
    # point, waits, SVG capture, replay, screenshot and page disposal all run.
    compiler = """<script>
        window.compilations = 0;
        for (const script of document.querySelectorAll('script[type="text/tikz"]')) {
            script.outerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"><path d="M0 0h10v10z"/></svg>';
            window.compilations++;
        }
    </script>"""
    counts = []
    take_screenshot = renderer._take_screenshot

    async def verify(page, output):
        assert await page.locator(".tikz-diagram svg path").count() == 2
        counts.append(await page.evaluate("window.compilations"))
        await take_screenshot(page, output)

    renderer._take_screenshot = verify
    try:
        for index in range(2):
            html = document("A", "A", caption=f"Caption {index}") + compiler
            await renderer.render_to_image(html, tmp_path / f"figure-{index}.png")
        assert counts == [1, 0]
        assert not renderer._figure_cache.pending
    finally:
        await manager.close()
