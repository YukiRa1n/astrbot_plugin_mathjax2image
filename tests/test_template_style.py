"""Code block rendering, syntax highlighting and background defaults."""

import re
from pathlib import Path

import pytest
from astrbot_plugin_mathjax2image.infrastructure.converter import markdown_converter
from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
    DEFAULT_BG_COLOR,
    MarkdownConverter,
    resolve_bg_color,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "templates/template.html"


def _body(source: str) -> str:
    html = MarkdownConverter(TEMPLATE).convert_to_html(source)
    return re.search(r'<main class="render-content">([\s\S]*?)</main>', html).group(1)


def _text(html: str) -> str:
    import html as html_lib

    return html_lib.unescape(re.sub(r"<[^>]+>", "", html))


pygments = pytest.importorskip("pygments")


def test_labelled_code_is_highlighted_and_keeps_its_text():
    code = 'def f(x):\n    return "<b>" + x  # note'
    body = _body(f"```python\n{code}\n```")

    assert '<code class="language-python">' in body
    assert '<span class="k">def</span>' in body
    assert "<b>" not in body.replace('<span class="', "")  # payload stays escaped
    assert _text(body).strip() == code


def test_highlighted_lines_close_their_tags():
    """The template splits code HTML by line, so no tag may span lines."""
    body = _body('```python\ns = """first\nsecond"""\n```')
    code = re.search(r"<code[^>]*>([\s\S]*?)</code>", body).group(1)
    for line in code.split("\n"):
        assert line.count("<span") == line.count("</span>")


@pytest.mark.parametrize("fence", ["```\nx = 1\n```", "```nosuchlang\nx = 1\n```"])
def test_unlabelled_or_unknown_code_stays_plain(fence):
    body = _body(fence)
    assert "<span" not in body
    assert "x = 1" in body


def test_long_code_is_not_highlighted(monkeypatch):
    monkeypatch.setattr(markdown_converter, "_HIGHLIGHT_MAX_CHARS", 10)
    body = _body("```python\nvalue = 123456789\n```")
    assert "<span" not in body


def test_highlighting_is_optional(monkeypatch):
    monkeypatch.setattr(markdown_converter, "_pygments_highlight", None)
    body = _body("```python\nprint(1)\n```")
    assert '<code class="language-python">print(1)</code>' in body


def test_fenced_block_is_not_wrapped_in_a_paragraph():
    body = _body("前文\n\n```python\nprint(1)\n```\n\n后文")
    assert "<p><pre" not in body
    assert "</pre></p>" not in body


def test_inline_code_keeps_its_paragraph():
    body = _body("`x` 是变量，`y` 也是")
    assert body.strip().startswith("<p><code>x</code>")
    assert body.strip().endswith("</p>")


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("#FDFBF0", DEFAULT_BG_COLOR),  # 旧默认值视为未设置
        ("#fdfbf0", DEFAULT_BG_COLOR),
        (" #FFFFFF ", "#FFFFFF"),
        ("#123", "#123"),
        ("red", DEFAULT_BG_COLOR),
        (None, DEFAULT_BG_COLOR),
    ],
)
def test_background_resolution(configured, expected):
    assert resolve_bg_color(configured) == expected


def test_template_default_matches_converter_default():
    html = MarkdownConverter(TEMPLATE).convert_to_html("x")
    assert f"--bg-color: {DEFAULT_BG_COLOR};" in html
