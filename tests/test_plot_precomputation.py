"""Accuracy, bounded work, and thread-safety checks for plot preprocessing."""

import asyncio
import math
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_mathjax2image.domain.errors import PreprocessError
from astrbot_plugin_mathjax2image.infrastructure.converter.pgfplots_preprocessor import (
    PgfplotsPreprocessor,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
    TikzPlotConverter,
)
from astrbot_plugin_mathjax2image.utils.safe_eval import compile_math_expression


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "x.__class__",
        "x[0]",
        "(lambda:1)()",
        "[x for x in (1,2)]",
        "True",
        "sin(x=1)",
        "a",
    ],
)
def test_compiled_math_rejects_non_numeric_syntax(expression):
    with pytest.raises(ValueError):
        compile_math_expression(expression, ("x",))


def test_compiled_math_preserves_degrees_precedence_and_domain_gaps():
    converter = TikzPlotConverter()
    assert converter._eval_tikz_expr(r"\x^2", -2) == 4
    assert converter._eval_tikz_expr("sin(90)", 0) == pytest.approx(1)
    assert converter._eval_tikz_expr("sin(deg(pi/2))", 0) == pytest.approx(1)
    assert converter._eval_tikz_expr("log(100)+ln(e)", 0) == pytest.approx(3)
    assert math.isnan(converter._eval_tikz_expr(r"\xi+\x", 1))
    assert math.isnan(compile_math_expression("2**10000")())
    result = converter.convert(r"\draw[domain=-1:1,samples=3] plot (\x,{1/\x});")
    assert result.count(r"\draw") == 2
    assert " -- " not in result


def test_curve_expressions_parse_once_per_curve(monkeypatch):
    import astrbot_plugin_mathjax2image.utils.safe_eval as module

    compile_math_expression.cache_clear()
    original = module.ast.parse
    calls = []

    def parse(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(module.ast, "parse", parse)
    points = TikzPlotConverter()._generate_points(-2, 2, 2000, r"\x", r"\x^2")
    assert len(calls) == 2
    assert points[0] == "(-2,4)"
    assert points[-1] == "(2,4)"


def test_curve_budget_is_local_to_each_thread():
    converter = TikzPlotConverter()
    source = r"\draw[domain=0:1,samples=1000] plot (\x,{\x^2});"
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(converter.convert, [source] * 16))
    assert len(set(results)) == 1
    assert results[0].count(" -- ") == 999
    with pytest.raises(PreprocessError):
        converter.convert(source * 3)


def test_surface_preserves_grid_and_endpoints():
    code = r"\begin{tikzpicture}\begin{axis}[domain=-1:1,samples=3]\addplot3[surf,samples y=2,y domain=2:4] {x^2+y};\end{axis}\end{tikzpicture}"
    result = PgfplotsPreprocessor().convert(code)
    assert "mesh/rows=2,mesh/cols=3,mesh/ordering=x varies" in result
    assert "(1,2,3)" in result
    assert "(-1,4,5)" in result
    assert result.count("\n(") == 6
    with pytest.raises(PreprocessError):
        PgfplotsPreprocessor(max_points=5).convert(code)


def test_parametric_and_nonstandard_semantics_fallback():
    code = r"\begin{axis}\addplot3[surf,samples=3,samples y=2,domain=0:90,y domain=0:1] ({cos(x)},{sin(x)},{y});\end{axis}"
    result = PgfplotsPreprocessor().convert(code)
    assert "(1,0,0)" in result
    assert result.count("\n(") == 6
    assert (
        PgfplotsPreprocessor().convert(r"\pgfplotsset{samples=50}" + code)
        == r"\pgfplotsset{samples=50}" + code
    )
    unknown = code.replace("cos(x)", "unknown(x)")
    assert PgfplotsPreprocessor().convert(unknown) == unknown
    assert PgfplotsPreprocessor().convert("% " + code) == "% " + code


@pytest.mark.asyncio
async def test_tikz_compile_slots_release_after_cancellation(tmp_path):
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )

    renderer = PageRenderer(MagicMock(), tmp_path, max_concurrent_tikz=1)
    entered = asyncio.Event()
    finish = asyncio.Event()
    active = 0
    peak = 0

    async def render(path, output):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await finish.wait()
            return True
        finally:
            active -= 1

    renderer._do_render = AsyncMock(side_effect=render)
    html = '<script type="text/tikz">code</script>'
    first = asyncio.create_task(renderer._render_uncached(html, tmp_path / "first.png"))
    await entered.wait()
    second = asyncio.create_task(
        renderer._render_uncached(html, tmp_path / "second.png")
    )
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    finish.set()
    await asyncio.wait_for(second, timeout=1)
    assert peak == 1


def test_compatibility_only_setting_preserves_closed_torus_grid():
    source = r"\pgfplotsset{compat=1.16}\begin{axis}\addplot3[surf,samples=48,samples y=24,domain=0:360,y domain=0:360] ({(2+0.6*cos(y))*cos(x)},{(2+0.6*cos(y))*sin(x)},{0.6*sin(y)});\end{axis}"
    result = PgfplotsPreprocessor().convert(source)
    assert r"\pgfplotsset{compat=1.16}" in result
    coordinates = [
        tuple(map(float, point.split(",")))
        for point in re.findall(r"\n\(([^)]+)\)", result)
    ]
    assert len(coordinates) == 48 * 24
    for x, y, z in coordinates:
        assert (math.hypot(x, y) - 2) ** 2 + z**2 == pytest.approx(0.36, abs=1e-8)
    for row in range(24):
        assert coordinates[row * 48] == pytest.approx(
            coordinates[row * 48 + 47], abs=1e-8
        )
    for first, last in zip(coordinates[:48], coordinates[-48:]):
        assert first == pytest.approx(last, abs=1e-8)
    custom = source.replace("compat=1.16", "compat=1.16,samples=99")
    assert PgfplotsPreprocessor().convert(custom) == custom


def test_text_commands_leave_math_and_code_unchanged():
    from astrbot_plugin_mathjax2image.infrastructure.converter.latex_preprocessor import (
        LatexPreprocessor,
    )

    processor = LatexPreprocessor(MagicMock(), MagicMock(), MagicMock())
    source = r"\textbf{bold} $\textbf{math}$ `\textit{code}` \begin{tikzpicture}\node{\textbf{label}};\end{tikzpicture}"
    assert processor._convert_text_commands(source) == source.replace(
        r"\textbf{bold}", "**bold**"
    )


def test_typography_clamps_invalid_values_and_preserves_content():
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        MarkdownConverter,
    )

    converter = MarkdownConverter(
        Path(__file__).resolve().parents[1] / "templates/template.html",
        {
            "body_font_size": 32,
            "h1_scale": 1.5,
            "h2_scale": float("nan"),
            "h3_scale": 99,
            "line_height": "bad; color:red",
        },
    )
    html = converter.convert_to_html("# Heading\n\nBody")
    assert "--body-font-size: 32px;" in html
    assert "--h1-scale: 1.5;" in html
    assert "--h2-scale: 1.25;" in html
    assert "--h3-scale: 2;" in html
    assert "--line-height: 1.7;" in html
    assert "bad; color:red" not in html


@pytest.mark.asyncio
async def test_dense_svg_keeps_nested_paint_scopes_and_html_parser(tmp_path):
    from astrbot_plugin_mathjax2image.infrastructure.browser import (
        BrowserManager,
        PageRenderer,
    )

    manager = BrowserManager(max_pages=1)
    try:
        page, _ = await manager.acquire_page(300, 200)
        renderer = PageRenderer(manager, tmp_path)
        await page.evaluate(renderer._get_inject_script())
        result = await page.evaluate("""() => {
            const source = '<svg xmlns="http://www.w3.org/2000/svg">' +
                '<g transform="translate(3 4)">' + '<g fill="red">'.repeat(2300) +
                '<g fill="blue" transform="scale(2)"><rect id="inner" width="10" height="10"/></g>' +
                '<rect id="outer" width="10" height="10"/>' + '</g>'.repeat(2300) + '</g></svg>';
            document.body.append(document.createRange().createContextualFragment(source));
            const inner = document.querySelector('#inner');
            let depth = 0;
            for (let node = inner; node.localName !== 'svg'; node = node.parentElement) depth++;
            const clipped = '<svg xmlns="http://www.w3.org/2000/svg"><g transform="translate(20 0)">' +
                '<clipPath id="cut"><rect width="10" height="10"/></clipPath>' +
                '<g clip-path="url(#cut)" opacity=".5"><rect id="clipped" width="20" height="10"/></g></g></svg>';
            document.body.append(document.createRange().createContextualFragment(clipped));
            const matrix = inner.getCTM();
            const html = document.createRange().createContextualFragment('<strong>HTML</strong>');
            return {depth, inner: getComputedStyle(inner).fill,
                matrix: [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f],
                clipTransform: document.querySelector('#cut').getAttribute('transform'),
                opacity: document.querySelector('#clipped').parentElement.getAttribute('opacity'),
                outer: getComputedStyle(document.querySelector('#outer')).fill,
                html: html.firstChild.outerHTML};
        }""")
        assert result == {
            "depth": 1,
            "matrix": [2, 0, 0, 2, 3, 4],
            "clipTransform": None,
            "opacity": ".5",
            "inner": "rgb(0, 0, 255)",
            "outer": "rgb(255, 0, 0)",
            "html": "<strong>HTML</strong>",
        }
    finally:
        await manager.close()
