"""Regression tests for the quadratic-scan, attribute, and native-TeX fixes."""

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrbot_plugin_mathjax2image.domain.errors import PreprocessError, RenderError
from astrbot_plugin_mathjax2image.infrastructure.browser.native_tikz import (
    NativeTikzRenderer,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.latex_preprocessor import (
    LatexPreprocessor,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.list_converter import (
    ListConverter,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
    MarkdownConverter,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.mermaid_converter import (
    MermaidConverter,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.table_converter import (
    TableConverter,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
    TikzConverter,
)
from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
    TikzPlotConverter,
)
from astrbot_plugin_mathjax2image.utils.linear_scan import (
    find_pairs,
    scan_fenced_code,
    scan_math_blocks,
)

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "template.html"

# A single message is capped at 100 000 characters by MAX_RENDER_LENGTH, so that
# is the size every bomb below uses. Before the fix these were quadratic and
# pinned the event loop for tens of seconds; the bound is generous enough not to
# flake on a slow machine while still failing loudly if the pattern regresses.
BOMB_BUDGET_SECONDS = 2.0
BOMB_LENGTH = 100_000


def _preprocessor() -> LatexPreprocessor:
    return LatexPreprocessor(
        TikzConverter(TikzPlotConverter()), ListConverter(), TableConverter(),
        MermaidConverter(),
    )


def _converter() -> MarkdownConverter:
    return MarkdownConverter(template_path=TEMPLATE_PATH)


BOMBS = {
    "unclosed backticks": "`" * BOMB_LENGTH,
    "odd backticks": "`" * (BOMB_LENGTH - 1) + "a",
    "unclosed tildes": "~" * BOMB_LENGTH,
    "unclosed math parens": "\\(" * (BOMB_LENGTH // 2),
    "unclosed math brackets": "\\[" * (BOMB_LENGTH // 2),
    "unclosed tikzpicture": "\\begin{tikzpicture}" * (BOMB_LENGTH // 19),
    "unclosed tikzcd": "\\begin{tikzcd}" * (BOMB_LENGTH // 14),
    "unclosed align": "\\begin{align}" * (BOMB_LENGTH // 13),
    "unclosed chemfig": "\\chemfig{" * (BOMB_LENGTH // 9),
    "run of percent": "%" * BOMB_LENGTH,
    "run of dollars": "$" * BOMB_LENGTH,
    "run of brackets": "[" * BOMB_LENGTH,
    "run of stars": "*" * BOMB_LENGTH,
}


@pytest.mark.parametrize("payload", BOMBS.values(), ids=BOMBS.keys())
def test_degenerate_markup_does_not_hang(payload):
    """Unbalanced delimiters must not cost super-linear time."""
    pre, conv = _preprocessor(), _converter()
    started = time.perf_counter()
    try:
        conv.convert_to_html(pre.preprocess(payload), "#FDFBF0")
    except ValueError:
        pass  # rejected as degenerate markup, which is the intended fast path
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


@pytest.mark.parametrize(
    "payload",
    ["`" * 100_000, "\\(" * 50_000, "\\[" * 50_000, "%" * 100_000, "[" * 100_000,
     "~" * 100_000, "*" * 100_000],
    ids=["backticks", "math-parens", "math-brackets", "percent", "brackets",
         "tildes", "stars"],
)
def test_degenerate_guard_is_not_inverted(payload):
    """The ratio test must actually reject all-markup input.

    An inverted count made the guard a no-op: the payloads above still reached
    Python-Markdown and cost seconds each. Direct assertions on the predicate
    keep that class of bug from passing again.
    """
    assert _converter()._is_degenerate_markup(payload) is True


def test_repeated_environment_names_are_not_degenerate():
    """`\\begin{tikzpicture}` repeats are mostly letters, so the guard lets them
    through by design; the linear scanner is what keeps them fast."""
    payload = "\\begin{tikzpicture}" * 5000
    assert _converter()._is_degenerate_markup(payload) is False
    started = time.perf_counter()
    _preprocessor().preprocess(payload)
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_degenerate_guard_allows_real_documents():
    """Ordinary prose must never trip the guard."""
    converter = _converter()
    assert converter._is_degenerate_markup("这是正文段落。" * 5000) is False
    assert converter._is_degenerate_markup("plain ascii prose. " * 3000) is False
    assert converter._is_degenerate_markup("short `code`") is False  # below min length


def test_balanced_code_block_still_extracted():
    """The linear scanner keeps normal fenced blocks and inline spans intact."""
    text = "before `inline` after\n\n```python\nx = 1\n```\n"
    spans = scan_fenced_code(text)
    assert len(spans) == 2
    assert text[spans[0][0] : spans[0][1]] == "`inline`"
    assert text[spans[1][0] : spans[1][1]].startswith("```python")


def test_display_math_pairs_are_not_merged():
    """Two adjacent $$ blocks stay two spans, matching the previous regexes."""
    text = "$$a$$ and $$b$$"
    spans = find_pairs(text, "$$", "$$", allow_newline=False)
    assert [text[a:b] for a, b in spans] == ["$$a$$", "$$b$$"]


def test_math_scan_matches_individual_delimiters():
    text = "value $x^2$ and \\[y\\] end"
    spans = scan_math_blocks(text)
    assert [text[a:b] for a, b in spans] == ["$x^2$", "\\[y\\]"]


def test_math_scan_pairs_each_dollar_greedily_left_to_right():
    """A lone `$` consumes the next `$`, so two real pairs collapse into one.

    This is the pre-existing behaviour of the sequential ``\\$.*?\\$`` pass and
    the scanner is required to preserve it, not "improve" it.
    """
    text = "cost is $5 and then $x^2$ end"
    spans = scan_math_blocks(text)
    assert [text[a:b] for a, b in spans] == ["$5 and then $"]


def test_trusted_block_rejects_slash_separated_event_handler():
    """`/` is a valid HTML attribute separator; the whitelist must not miss it."""
    block = (
        '<div class="tikz-diagram"><script type="text/tikz" '
        'data-disable-cache="true" /onerror=alert(1)>\n'
        "\\draw (0,0)--(1,1);\n</script></div>"
    )
    assert _converter()._is_trusted_html_block(block) is False


def test_trusted_block_rejects_tex_injection_in_library_attribute():
    block = (
        '<div class="tikz-diagram"><script type="text/tikz" '
        'data-tikz-libraries=\'x} \\file_input:n{D:/secret}\'>\n'
        "\\draw (0,0)--(1,1);\n</script></div>"
    )
    assert _converter()._is_trusted_html_block(block) is False


def test_trusted_block_accepts_converter_output():
    attrs = (
        ' type="text/tikz" data-disable-cache="true"'
        ' data-tikz-libraries=\'calc,arrows.meta\''
    )
    block = (
        '<div class="tikz-diagram"><script' + attrs + ">\n"
        "\\draw (0,0)--(1,1);\n</script></div>"
    )
    assert _converter()._is_trusted_html_block(block) is True


@pytest.mark.parametrize(
    "code",
    [
        r"\file_get:nnN{a}{b}{c}",
        r"\ExplSyntaxOn\file_input:n{D:/secret}\ExplSyntaxOff",
        r"\input{/etc/passwd}",
        r"\@@input{secret.tex}",
        r"\immediate\write18{id}",
        r"\directlua{os.execute('id')}",
        r"\catcode`\%=14",
        r"\openout1=x",
        r"\read1 to \x",
        r"\begin{filecontents}{x}\end{filecontents}",
        r"\draw (0,0)--^^41(1,1);",
    ],
)
def test_native_tex_rejects_file_and_process_primitives(code):
    with pytest.raises(RenderError):
        NativeTikzRenderer._reject_unsafe_tex(code)


@pytest.mark.parametrize(
    "code",
    [
        r"\draw (0,0)--(1,1);",
        r"\node at (0,0) {$x^2$}; \draw[->,thick] (0,0) to[bend left] (1,1);",
        r"\foreach \x in {1,2,3} \draw (\x,0)--(\x,1);",
        r"\begin{axis}\addplot3[surf]{x*y};\end{axis}",
    ],
)
def test_native_tex_allows_ordinary_drawing(code):
    NativeTikzRenderer._reject_unsafe_tex(code)  # must not raise


@pytest.mark.parametrize(
    "attrs,expected",
    [
        (' type="text/tikz" data-disable-cache="true"', True),
        (' type="text/tikz" data-tex-packages=\'{"pgfplots": ""}\'', True),
        (' type="text/tikz" data-tikz-libraries=\'x} \\file_input:n{y}\'', False),
        (' type="text/tikz" onerror="alert(1)"', False),
        (' type="text/tikz" /onerror=alert(1)', False),
        (' type="text/javascript"', False),
        (' type="text/tikz" junk', False),
    ],
)
def test_native_block_attribute_screening(attrs, expected):
    assert NativeTikzRenderer._screen_block_attributes(attrs) is expected


def test_native_failure_message_omits_log_tail():
    """A TeX log tail can carry file contents; only a summary may travel back."""
    import inspect

    source = inspect.getsource(NativeTikzRenderer._run)
    assert "first_error" in source
    assert "detail = log.read()" not in source


def test_plot_budget_is_document_wide():
    """A second picture in the same document cannot reclaim a full allowance."""
    curve = (
        "\\begin{tikzpicture}\n"
        r"\draw[domain=0:360,samples=2000] plot(\x,{sin(\x)});"
        "\n\\end{tikzpicture}\n"
    )
    converter = _preprocessor()
    preprocess = converter.preprocess
    assert "tikz-diagram" in preprocess(curve)  # one picture fits
    with pytest.raises(PreprocessError):
        preprocess(curve * 2)  # two do not each get a fresh budget


def test_single_large_surface_still_allowed():
    """The 2D and 3D allowances stay separate: one 80x80 grid is legitimate."""
    surface = (
        "\\begin{tikzpicture}\n\\begin{axis}\n"
        r"\addplot3[surf,samples=80,samples y=80]{sin(x)*cos(y)};"
        "\n\\end{axis}\n\\end{tikzpicture}\n"
    )
    assert "tikz-diagram" in _preprocessor().preprocess(surface)
