"""Optional native backend dependency, fallback, and process lifecycle tests."""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_mathjax2image.domain.errors import RenderError
from astrbot_plugin_mathjax2image.infrastructure.browser.native_tikz import (
    NativeTikzRenderer,
)
from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
    PageRenderer,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
    TikzConverter,
)


@pytest.mark.asyncio
async def test_native_missing_dependency_and_plain_html(tmp_path):
    compiler = NativeTikzRenderer(str(tmp_path))
    assert await compiler.render_html("<p>plain</p>", 1) == "<p>plain</p>"
    with pytest.raises(RenderError, match="requires latex"):
        await compiler.render_html(
            TikzConverter(MagicMock())._wrap_tikz_html(
                r"\begin{tikzpicture}\end{tikzpicture}"
            ),
            1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RenderError("missing package"), OSError("missing binary"), ValueError("bad SVG")],
)
async def test_native_failure_falls_back_to_original_html(tmp_path, failure):
    renderer = PageRenderer(MagicMock(), tmp_path, tikz_backend="native")
    renderer._native_tikz.render_html = AsyncMock(side_effect=failure)
    html = '<div class="tikz-diagram"><script type="text/tikz">x</script></div>'
    seen = []

    async def render(path, output):
        seen.append(path.read_text(encoding="utf-8"))
        return True

    renderer._do_render = render
    assert await renderer._render_uncached(html, tmp_path / "out.png")
    assert seen == [html]
    assert renderer._tikz_slots._value == 1


@pytest.mark.asyncio
async def test_native_success_and_cancel_release_slot(tmp_path):
    renderer = PageRenderer(MagicMock(), tmp_path, tikz_backend="native")
    renderer._native_tikz.render_html = AsyncMock(return_value="<svg/>")

    async def render(path, output):
        assert path.read_text(encoding="utf-8") == "<svg/>"
        return True

    renderer._do_render = render
    html = '<script type="text/tikz">x</script>'
    assert await renderer._render_uncached(html, tmp_path / "out.png")
    renderer._native_tikz.render_html.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await renderer._render_uncached(html, tmp_path / "out.png")
    assert renderer._tikz_slots._value == 1
    assert PageRenderer(MagicMock(), tmp_path)._native_tikz is None


@pytest.mark.asyncio
async def test_cancel_kills_and_reaps_native_process(tmp_path):
    compiler = NativeTikzRenderer()
    task = asyncio.create_task(
        compiler._run(
            [
                sys.executable,
                "-c",
                "import os,time; from pathlib import Path; Path('pid').write_text(str(os.getpid())); time.sleep(30)",
            ],
            tmp_path,
            os.environ.copy(),
        )
    )
    for _ in range(100):
        if (tmp_path / "pid").exists():
            break
        await asyncio.sleep(0.02)
    assert (tmp_path / "pid").exists()
    import psutil

    pid = int((tmp_path / "pid").read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not psutil.pid_exists(pid)


@pytest.mark.asyncio
async def test_svg_ids_and_loader_removal(tmp_path):
    for name in ["latex", "dvisvgm"]:
        (tmp_path / (name + (".exe" if os.name == "nt" else ""))).touch()
    compiler = NativeTikzRenderer(str(tmp_path))

    async def run(command, work, env):
        if Path(command[0]).stem == "dvisvgm":
            (work / "plot.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><defs><path id="glyph" d="M0 0L1 1"/></defs><use href="#glyph"/></svg>'
            )

    compiler._run = run
    block = TikzConverter(MagicMock())._wrap_tikz_html(
        r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"
    )
    html = (
        block
        + block
        + '<script src="https://cdn.jsdelivr.net/npm/@drgrice1/tikzjax@1/dist/tikzjax.js"></script>'
    )
    result = await compiler.render_html(html, 1)
    assert 'id="native0-glyph"' in result and 'href="#native1-glyph"' in result
    assert "text/tikz" not in result and "@drgrice1/tikzjax" not in result
    with pytest.raises(RenderError, match="rejected"):
        await compiler.render_html(
            TikzConverter(MagicMock())._wrap_tikz_html(r"\input{secrets}"), 1
        )


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows native job ownership")
async def test_timeout_waits_for_descendants_to_release_stage_files(tmp_path):
    import psutil

    # The executable under test launches a helper, just as a converter can
    # spawn a font tool. The helper keeps a file open until termination.
    helper = tmp_path / "helper.py"
    helper.write_text(
        "import os, sys, time, subprocess\n"
        "from pathlib import Path\n"
        "if len(sys.argv) == 1:\n"
        "    subprocess.run([sys.executable, __file__, 'leaf'], check=True)\n"
        "else:\n"
        "    locked = open('output.dvi', 'wb')\n"
        "    Path('pids.tmp').write_text(f'{os.getppid()} {os.getpid()}')\n"
        "    Path('pids.tmp').replace('pids')\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )
    compiler = NativeTikzRenderer()
    task = asyncio.create_task(
        compiler._run([sys.executable, str(helper)], tmp_path, os.environ.copy())
    )
    try:
        for _ in range(200):
            if (tmp_path / "pids").exists():
                break
            if task.done():
                await task
            await asyncio.sleep(0.02)
        pids = list(map(int, (tmp_path / "pids").read_text().split()))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=0.02)
        assert all(not psutil.pid_exists(pid) for pid in pids)
        (tmp_path / "output.dvi").unlink()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
