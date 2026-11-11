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


def test_last_line_is_lexed_like_the_others():
    body = _body("```diff\n- old\n+ new\n```")
    assert '<span class="gi">+ new</span></code>' in body
    assert 'class="err"' not in body


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


TEMPLATES = {
    "default": TEMPLATE,
    "book": TEMPLATE.with_name("book.html"),
}


def _source(template):
    return template.read_text(encoding="utf-8")


def _css_declarations(source, wanted_selector):
    """Collect declarations from rules containing an exact selector."""
    declarations = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
        selector_list = {item.strip() for item in selectors.split(",")}
        if wanted_selector in selector_list:
            for name, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body):
                declarations[name] = value.strip()
    return declarations


def _function_body(source, name):
    marker = f"window.{name} = function"
    start = source.index(marker)
    start = source.index("{", start)
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise AssertionError(f"Unclosed function: {name}")


def _async_tasks(lifecycle_body):
    tasks = []
    marker = "(async function()"
    offset = 0
    while (start := lifecycle_body.find(marker, offset)) >= 0:
        start = lifecycle_body.index("{", start)
        depth = 0
        for index in range(start, len(lifecycle_body)):
            if lifecycle_body[index] == "{":
                depth += 1
            elif lifecycle_body[index] == "}":
                depth -= 1
                if depth == 0:
                    tasks.append(lifecycle_body[start + 1 : index])
                    offset = index + 1
                    break
        else:
            raise AssertionError("Unclosed async lifecycle task")
    return tasks


def _mermaid_initialization(source):
    start = source.index("s.onload = function()")
    end = source.index("flowchart:", start)
    return source[start:end]


@pytest.mark.parametrize("template", TEMPLATES.values(), ids=TEMPLATES)
def test_text_and_code_containers_can_shrink_and_wrap(template):
    source = _source(template)
    line_content = _css_declarations(source, ".line-content")
    assert line_content.get("min-width") == "0"
    assert line_content.get("overflow-wrap") == "anywhere"
    for selector in ("p", "li", "th", "td"):
        assert _css_declarations(source, selector).get("overflow-wrap") == "anywhere"


@pytest.mark.parametrize("template", TEMPLATES.values(), ids=TEMPLATES)
def test_rendered_images_stay_within_content_width(template):
    declarations = _css_declarations(_source(template), ".render-content img")
    assert declarations.get("max-width") == "100%"
    assert declarations.get("height") == "auto"


@pytest.mark.parametrize("template", TEMPLATES.values(), ids=TEMPLATES)
def test_code_chrome_uses_readable_theme_colors(template):
    source = _source(template)
    if template.name == "template.html":
        title_selector = ".code-header"
        expected_title = "var(--code-muted)"
        expected_lines = "var(--code-line-num)"
    else:
        title_selector = "pre[data-lang]::before"
        expected_title = expected_lines = "var(--muted-color)"
    assert _css_declarations(source, title_selector).get("color") == expected_title
    assert _css_declarations(source, ".line-num").get("color") == expected_lines
    assert "lineNum.setAttribute('aria-hidden', 'true')" in _function_body(
        source, "__formatRenderCode"
    )


def test_default_code_block_matches_document_palette():
    source = _source(TEMPLATES["default"])
    assert "--code-bg: #f2f4f8;" in source
    assert "--code-sheet-bg: #ffffff;" in source
    assert "--code-accent: var(--accent-color);" in source
    assert "background: var(--code-sheet-bg);" in source
    assert "pre .nf, pre .fm, pre .nd { color: #7a492e; font-weight: 500; }" in source


@pytest.mark.parametrize("template", TEMPLATES.values(), ids=TEMPLATES)
def test_mermaid_font_size_tracks_body_with_bounds(template):
    initialization = _mermaid_initialization(_source(template))
    assert "fontSize: '18px'" not in initialization
    assert "getComputedStyle(document.body)" in initialization
    assert re.search(r"fontSize:\s*[^,]+['\"]px['\"]", initialization)
    assert "Math.min" in initialization and "Math.max" in initialization


def test_book_mermaid_uses_book_palette_and_font():
    initialization = _mermaid_initialization(_source(TEMPLATES["book"]))
    for default_template_value in ('"EB Garamond"', "#3451b2", "#FCFCFD", "#F1F4FB"):
        assert default_template_value not in initialization
    assert "getComputedStyle(document.body).fontFamily" in initialization
    for book_value in ("#faf9f5", "#141413", "#1B365D"):
        assert book_value in initialization


@pytest.mark.parametrize("template", TEMPLATES.values(), ids=TEMPLATES)
def test_mermaid_blocks_are_not_formatted_as_code(template):
    body = _function_body(_source(template), "__formatRenderCode")
    assert "pre:not(.mermaid) code" in body


@pytest.mark.parametrize("template", TEMPLATES.values(), ids=TEMPLATES)
def test_mathjax_and_mermaid_start_in_independent_tasks(template):
    body = _function_body(_source(template), "__replaceRenderContent")
    tasks = _async_tasks(body)
    mathjax_tasks = [task for task in tasks if "MathJax.typesetPromise" in task]
    mermaid_tasks = [task for task in tasks if "mermaid.run" in task]
    assert len(mathjax_tasks) == len(mermaid_tasks) == 1
    assert "__ensureEngines" in mathjax_tasks[0]
    assert "mermaid.run" not in mathjax_tasks[0]
    assert "MathJax.typesetPromise" not in mermaid_tasks[0]
    assert "__ensureEngines" not in mermaid_tasks[0]
