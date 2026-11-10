"""Regressions for the 2026-11 security review findings."""

import time
from pathlib import Path

import pytest
from astrbot_plugin_mathjax2image.domain.errors import RenderError
from astrbot_plugin_mathjax2image.infrastructure.browser.native_tikz import (
    NativeTikzRenderer,
)
from astrbot_plugin_mathjax2image.infrastructure.converter import (
    MarkdownConverter,
    TikzConverter,
    TikzPlotConverter,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.pgfplots_preprocessor import (
    PgfplotsPreprocessor,
    _group,
)

_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "template.html"


def _body(markdown: str) -> str:
    html = MarkdownConverter(template_path=_TEMPLATE).convert_to_html(markdown)
    start = html.index('class="render-content"')
    return html[start:]


@pytest.mark.parametrize(
    "source, injected",
    [
        ('![$" onerror="alert(1)$](x)', 'onerror="alert(1)'),
        ('[a]($" onmouseover="alert(1)$)', 'onmouseover="alert(1)'),
        ("![$' onerror='alert(1)$](x)", "onerror='alert(1)"),
    ],
)
def test_math_placeholder_cannot_break_out_of_attribute(source, injected):
    assert injected not in _body(source)


def test_math_quotes_still_reach_mathjax_as_text():
    body = _body('$\\text{"a"}$')
    assert "$\\text{&quot;a&quot;}$" in body


def _elapsed(function, *args) -> float:
    start = time.perf_counter()
    try:
        function(*args)
    except Exception:
        pass
    return time.perf_counter() - start


def test_plot_regex_is_not_cubic_on_whitespace():
    source = "\\begin{tikzpicture}\\draw[]plot(" + " " * 2500 + "\\end{tikzpicture}"
    assert _elapsed(TikzConverter(TikzPlotConverter()).convert, source) < 0.5


def test_plot_precompute_still_handles_padded_expressions():
    converted = TikzPlotConverter().convert(
        "\\draw[domain=0:1, samples=3] plot ( \\x , {\\x*\\x} );"
    )
    assert "plot" not in converted
    assert "(0.5,0.25)" in converted


def test_pgfplots_unbalanced_groups_fail_fast():
    source = "\\addplot3 " + "\\begin{axis}[" * 4000
    assert _elapsed(PgfplotsPreprocessor().convert, source) < 0.5


def test_group_matches_nested_same_type_delimiters():
    assert _group("[a[b]c]d", 0) == ("a[b]c", 7)
    with pytest.raises(ValueError):
        _group("[a[b]c", 0)


@pytest.mark.parametrize(
    "code",
    [
        "\\begin {input}{plot.tex}",
        "\\begin%\n{input}{plot.tex}",
        "\\begin{tikzpicture}\\end {input}",
        "\\UseName{input}{plot.tex}",
        "\\csuse{input}{plot.tex}",
        "\\letcs\\x{input}\\x{plot.tex}",
        "\\ExpandArgs{c}\\x{input}",
        "\\draw plot file {data.txt};",
        "\\draw plot[smooth] file {data.txt};",
        "\\addplot table {data.txt};",
        "\\addplot table [x=a] %\n{data.txt};",
        "\\addplot3 graphics {surface.png};",
        "\\pgfimage{secret.png}",
        "\\def\\a{\\typeout{x}\\a}\\a",
        "\\wlog{x}",
        "\\showtokens{x}",
        "\\tracingall",
    ],
)
def test_native_tex_filter_rejects_bypasses(code):
    with pytest.raises(RenderError):
        NativeTikzRenderer._reject_unsafe_tex(code)


@pytest.mark.parametrize(
    "code",
    [
        "\\begin{tikzpicture}\n\\draw[->] (0,0) -- (1,1) node[right] {$f(x)$};\n\\end{tikzpicture}",
        "\\begin{axis}[view={60}{30}]\n\\addplot3[surf] {x*y};\n\\end{axis}",
        "\\draw (0,0) circle (1cm); % a table of values\n\\node {file};",
        "\\node at (0,0) {$\\infty$};",
    ],
)
def test_native_tex_filter_keeps_ordinary_drawings(code):
    NativeTikzRenderer._reject_unsafe_tex(code)


def test_mermaid_fence_is_linear_on_whitespace():
    from astrbot_plugin_mathjax2image.infrastructure.converter import MermaidConverter

    source = "```mermaid\ngraph TD\n" + " " * 40000 + "A\n```"
    assert _elapsed(MermaidConverter().convert, source) < 0.5


@pytest.mark.parametrize(
    "source, converted",
    [
        ("```mermaid\ngraph TD\nA-->B\n```", True),
        ("  ~~~~ Mermaid \ngraph TD\nA-->B\n  ~~~~~~  ", True),
        ("```mermaid\n```", False),
        ("```mermaidx\ngraph TD\n```", False),
        ("```python\nmermaid\n```", False),
    ],
)
def test_mermaid_fence_recognition(source, converted):
    from astrbot_plugin_mathjax2image.infrastructure.converter import MermaidConverter

    converter = MermaidConverter()
    assert ('<pre class="mermaid">' in converter.convert(source)) is converted
    assert converter.has_mermaid(source) is (converted or source == "```mermaid\n```")
