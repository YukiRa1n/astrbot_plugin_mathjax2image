"""Regression tests for rendering readiness, package loading, and asset reuse."""

import re
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
    BrowserManager,
)
from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
    PageRenderer,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
    MarkdownConverter,
)


@pytest.mark.parametrize(
    "content",
    ["Plain text", "```tex\n$\\ce{H2O}$\n```", "~~~tex\n$\\ce{H2O}$\n~~~", "`$x$`"],
)
def test_plain_content_loads_engines_on_demand(content):
    """The shell holds an engine loader instead of static engine tags.

    New pages load only the engines their content needs, while the shell stays
    identical across content types so resident pages can be reused.
    """
    converter = MarkdownConverter(Path(__file__).parents[1] / "templates/template.html")
    html = converter.convert_to_html(content)
    assert '<script src="https://cdn.jsdelivr.net/npm/mathjax@' not in html
    assert '<script src="https://cdn.jsdelivr.net/npm/@drgrice1/tikzjax@' not in html
    assert "window.__ensureEngines(document)" in html
    assert "window.mathJaxRequired = false" in html
    assert "typeset: window.mathJaxRequired" in html


def test_shell_is_identical_across_content_types():
    """Only the content and the math flag differ between document types."""
    import re

    converter = MarkdownConverter(Path(__file__).parents[1] / "templates/template.html")
    tikz = "\\begin{tikzpicture}\n\\draw (0,0) -- (1,0);\n\\end{tikzpicture}"

    def shell(source):
        html = converter.convert_to_html(source)
        html = re.sub(
            r'(<main class="render-content">)[\s\S]*?(</main>)', r"\1\2", html
        )
        return re.sub(r"window\.mathJaxRequired = (true|false);", "", html)

    assert shell("纯文本") == shell("$x^2$") == shell(tikz)


def test_explicit_packages_and_code_isolation():
    converter = MarkdownConverter(Path(__file__).parents[1] / "templates/template.html")
    html = converter.convert_to_html(
        r"\usepackage{physics,mhchem}" + "\n" + r"$$\dv[2]{x}{t} + \ce{H2O}$$"
    )
    assert 'const requestedPackages = ["mhchem", "physics"]' in html
    assert "window.mathJaxRequired = true" in html
    assert r"\usepackage" not in html
    with pytest.raises(ValueError, match="Unsupported MathJax packages"):
        converter.convert_to_html(r"\usepackage{nonexistent} $x$")
    html = converter.convert_to_html("```tex\n\\usepackage{nonexistent}\n```")
    # 代码块可能被语法高亮拆成多个 <span>，比较去掉标签后的文本
    assert r"\usepackage{nonexistent}" in re.sub(r"<[^>]+>", "", html)


@pytest.mark.asyncio
async def test_destroyed_busy_page_wakes_waiter():
    manager = BrowserManager(max_pages=1)
    manager.get_browser = AsyncMock(return_value=MagicMock())
    manager._create_page = AsyncMock(side_effect=[MagicMock(), MagicMock()])
    first, _ = await manager.acquire_page(100, 100)
    manager._dispose_page = AsyncMock()
    waiting = asyncio.create_task(manager.acquire_page(100, 100))
    await asyncio.sleep(0)
    await manager.release_page(first, exception_occurred=True)
    second, _ = await asyncio.wait_for(waiting, timeout=1)
    assert second is not first
    assert manager._active_pages_count == 1
    await manager.release_page(second, exception_occurred=True)


@pytest.mark.asyncio
async def test_cancelled_render_discards_page(tmp_path):
    manager = MagicMock()
    page = MagicMock()
    manager.acquire_page = AsyncMock(return_value=(page, True))
    manager.release_page = AsyncMock()
    renderer = PageRenderer(manager, tmp_path)
    renderer._load_and_wait = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await renderer.render_to_image("<html></html>", tmp_path / "out.png")
    manager.release_page.assert_awaited_once_with(page, True)


@pytest.mark.asyncio
@pytest.mark.browser
async def test_short_document_has_no_viewport_padding(tmp_path):
    manager = BrowserManager(max_pages=1)
    renderer = PageRenderer(manager, tmp_path)
    try:
        output = tmp_path / "short.png"
        await renderer.render_to_image(
            '<html><body style="margin:0;height:100px">Short</body></html>', output
        )
        from PIL import Image

        with Image.open(output) as image:
            assert image.height == 100
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_image_cache_keeps_independent_outputs_and_coalesces(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()

    async def render(html, output):
        started.set()
        await finish.wait()
        output.write_bytes(b"png-content")
        return True

    renderer._render_uncached = AsyncMock(side_effect=render)
    first = asyncio.create_task(renderer.render_to_image("same", tmp_path / "a.png"))
    await started.wait()
    second = asyncio.create_task(renderer.render_to_image("same", tmp_path / "b.png"))
    await asyncio.sleep(0)
    finish.set()
    await asyncio.gather(first, second)
    (tmp_path / "a.png").unlink()
    await renderer.render_to_image("same", tmp_path / "c.png")
    renderer._render_uncached.assert_awaited_once()
    assert (
        (tmp_path / "b.png").read_bytes()
        == (tmp_path / "c.png").read_bytes()
        == b"png-content"
    )
    assert not renderer._image_pending


@pytest.mark.asyncio
async def test_incomplete_images_are_not_cached(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)

    async def render(html, output):
        output.write_bytes(b"fallback")
        return False

    renderer._render_uncached = AsyncMock(side_effect=render)
    for index in range(2):
        await renderer.render_to_image("partial", tmp_path / f"{index}.png")
    assert renderer._render_uncached.await_count == 2
    assert not renderer._image_cache


def test_bare_equations_load_mathjax_and_packages_conflict_explicitly():
    converter = MarkdownConverter(Path(__file__).parents[1] / "templates/template.html")
    html = converter.convert_to_html(r"\begin{align}x&=y\\z&=1\end{align}")
    assert "window.mathJaxRequired = true" in html
    assert r"\begin{align}x&amp;=y" in html
    with pytest.raises(ValueError, match="incompatible syntax"):
        converter.convert_to_html(r"\usepackage{physics,braket} $x$")
    assert "header-line" not in html
    assert "footer-line" not in html


@pytest.mark.asyncio
async def test_image_cache_budget_and_cancelled_owner(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path)
    renderer._image_cache_limit = 8
    started = asyncio.Event()
    calls = 0

    async def render(html, output):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Future()
        output.write_bytes(b"12345")
        return True

    renderer._render_uncached = AsyncMock(side_effect=render)
    owner = asyncio.create_task(
        renderer.render_to_image("same", tmp_path / "owner.png")
    )
    await started.wait()
    waiter = asyncio.create_task(
        renderer.render_to_image("same", tmp_path / "waiter.png")
    )
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    await asyncio.wait_for(waiter, timeout=1)
    assert (tmp_path / "waiter.png").read_bytes() == b"12345"
    assert not renderer._image_pending
    await renderer.render_to_image("other", tmp_path / "other.png")
    assert renderer._image_cache_bytes == 5
    assert len(renderer._image_cache) == 1


def test_latex_package_aliases_and_unsupported_options():
    converter = MarkdownConverter(Path(__file__).parents[1] / "templates/template.html")
    html = converter.convert_to_html(r"\usepackage{amsmath,amssymb,bm,xcolor} $x$")
    assert 'const requestedPackages = ["ams", "boldsymbol", "color"]' in html
    with pytest.raises(ValueError, match="options are not supported"):
        converter.convert_to_html(r"\usepackage[italicdiff]{physics} $x$")
