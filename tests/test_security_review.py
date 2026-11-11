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
        "\\addplot table from {secret.dat};",
        "\\def\\src{secret.dat}\\draw plot file \\src;",
        "\\def\\src{secret.dat}\\addplot table \\src;",
        "\\def\\src{secret.png}\\addplot3 graphics \\src;",
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


def test_tikz_converter_rejects_special_and_escapes_closing_main():
    converter = TikzConverter(TikzPlotConverter())
    blocked = converter.convert(
        "\\begin{tikzpicture}\\special{dvisvgm:raw <script>alert(1)</script>}\\end{tikzpicture}"
    )
    assert '<div class="error">' in blocked
    assert "text/tikz" not in blocked

    wrapped = converter.convert(
        "\\begin{tikzpicture}\\node at (0,0) {</main></script>};\\end{tikzpicture}"
    )
    assert "</main>" not in wrapped
    assert "<\\/main>" in wrapped
    assert "<\\/script>" in wrapped

    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        _RENDER_CONTENT,
    )

    html = MarkdownConverter(template_path=_TEMPLATE).convert_to_html(
        wrapped + "\n\nafter diagram"
    )
    extracted = _RENDER_CONTENT.search(html)
    assert extracted is not None
    assert "after diagram" in extracted.group(1)


def test_svg_sanitization_in_js_and_inject_script():
    import json
    import shutil
    import subprocess

    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )

    plugin_dir = Path(__file__).resolve().parents[1]
    renderer = PageRenderer(BrowserManager(max_pages=1), plugin_dir)
    inject = renderer._get_inject_script()
    assert "source.includes('<g')" not in inject
    assert "blockedSvgTags" in inject
    assert "Blocked external SVG href" in inject

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    svg_js = (plugin_dir / "static" / "svg_output.js").read_text(encoding="utf-8")
    probe = (
        "globalThis.window = globalThis;\n"
        + svg_js
        + """
        const unsafe = [
            '<svg><g fill="red"><script>alert(1)</script></g></svg>',
            '<svg><g fill="red"><foreignObject><div>x</div></foreignObject></g></svg>',
            '<svg><g fill="red"><image href="https://evil.example/a.png"/></g></svg>',
            '<svg><g fill="red" onload="alert(1)"><path d="M0 0"/></g></svg>',
            '<svg><g fill="red"><use xlink:href="https://evil.example/#x"/></g></svg>',
            '<svg><g fill="red"><set attributeName="href" to="https://evil.example/"/></g></svg>',
        ];
        const unsafeResults = unsafe.map(s => window.__compactSvgOutput(s));
        const safe = window.__compactSvgOutput(
            '<svg><defs><path id="p" d="M0 0"/></defs><g fill="red"><g fill="red"><use href="#p"/></g></g></svg>'
        );
        console.log(JSON.stringify({unsafeResults, safeOk: typeof safe === 'string' && safe.includes('#p')}));
        """
    )
    proc = subprocess.run(
        [node, "-e", probe], capture_output=True, text=True, check=True, timeout=10
    )
    data = json.loads(proc.stdout.strip())
    assert data["unsafeResults"] == [None] * 6
    assert data["safeOk"] is True


def test_spoofed_trusted_html_blocks_are_escaped_outside_code_fences():
    from astrbot_plugin_mathjax2image.infrastructure.converter import (
        LatexPreprocessor,
        ListConverter,
        MermaidConverter,
        TableConverter,
    )

    pre = LatexPreprocessor(
        TikzConverter(TikzPlotConverter()),
        ListConverter(),
        TableConverter(),
        MermaidConverter(),
    )
    md = MarkdownConverter(template_path=_TEMPLATE)
    spoofed = (
        '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true">\n'
        "\\special{dvisvgm:raw <svg onload=alert(1)>}\n</script></div>\n\n"
        '<pre class="mermaid">\ngraph TD; A-->B;\n</pre>\n\n'
        '<div class="error">fake error</div>\n\n'
        '```html\n<div class="error">code sample</div>\n```'
    )
    html = md.convert_to_html(pre.preprocess(spoofed))
    content = html.split('<main class="render-content">', 1)[1].split("</main>", 1)[0]
    assert '<script type="text/tikz"' not in content
    assert '<pre class="mermaid">' not in content
    assert '<div class="error">fake error</div>' not in content
    assert "code sample" in content


@pytest.mark.parametrize(
    "block, trusted",
    [
        (
            '<div class="tikz-diagram"><script type="text/tikz">\n\\draw (0,0)--(1,1);\n</script></div>',
            False,
        ),
        (
            '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="false">\n\\draw (0,0)--(1,1);\n</script></div>',
            False,
        ),
        (
            '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true" data-tikz-libraries="calc,{bad}">\n\\draw (0,0)--(1,1);\n</script></div>',
            False,
        ),
        (
            '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true" data-tikz-libraries="not_a_real_lib">\n\\draw (0,0)--(1,1);\n</script></div>',
            False,
        ),
        (
            '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true" data-tex-packages=\'{"evil": ""}\'>\n\\draw (0,0)--(1,1);\n</script></div>',
            False,
        ),
        (
            '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true" data-tex-packages=\'{"amsmath": "opt"}\'>\n\\draw (0,0)--(1,1);\n</script></div>',
            False,
        ),
        (
            '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true">\n\\special{x}\n</script></div>',
            False,
        ),
        (
            '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true" data-tex-packages=\'{"amsmath": ""}\' data-tikz-libraries="calc,arrows.meta">\n\\draw (0,0)--(1,1);\n</script></div>',
            True,
        ),
    ],
)
def test_markdown_trusted_block_strict_attribute_and_body_validation(block, trusted):
    assert (
        MarkdownConverter(template_path=_TEMPLATE)._is_trusted_html_block(block)
        is trusted
    )
    assert (
        NativeTikzRenderer._screen_block_attributes(
            ' type="text/tikz" data-disable-cache="false"'
        )
        is False
    )
    assert (
        NativeTikzRenderer._screen_block_attributes(
            ' type="text/tikz" data-tikz-libraries="calc,{bad}"'
        )
        is False
    )


@pytest.mark.asyncio
async def test_resident_key_ignores_mermaid_prose_and_skips_stateful_macros(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )

    plugin_dir = Path(__file__).resolve().parents[1]
    renderer = PageRenderer(BrowserManager(max_pages=1), plugin_dir)
    md = MarkdownConverter(template_path=_TEMPLATE)

    async def get_resident_key(markdown_source: str) -> str:
        html = md.convert_to_html(markdown_source)
        html_file = tmp_path / "doc.html"
        html_file.write_text(html, encoding="utf-8")
        recorded_keys: list[str] = []

        async def fake_eval(script, arg=None):
            if "window.__residentKey = key" in script:
                recorded_keys.append(arg)
                return None
            if "window.__residentKey === key" in script:
                return False
            if "mathJaxRequired" in script:
                return False
            return 0

        page = MagicMock()
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(side_effect=fake_eval)
        await renderer._load_and_wait(page, html_file)
        return recorded_keys[-1]

    plain_key = await get_resident_key("Hello world")
    prose_mermaid_key = await get_resident_key('Prose mentioning `class="mermaid"`')
    assert plain_key != ""
    assert plain_key == prose_mermaid_key

    for stateful in (
        r"$\Newextarrow{\myarrow}{5,5}{0x2192}$",
        r"$\mathtoolsset{showonlyrefs}$",
        r"$\DeclarePairedDelimiter{\myabs}{\lvert}{\rvert}$",
        r"$\mhchemoptions{arrows=pgf}$",
        r"$\require{physics}$",
        r"$\style{color:red}{x}$",
        r"$\cssId{ leaked }{x}$",
    ):
        assert await get_resident_key(stateful) == ""


@pytest.mark.asyncio
async def test_cdn_pending_released_on_continue_error_and_aborted_render(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )

    plugin_dir = Path(__file__).resolve().parents[1]
    bm = BrowserManager(max_pages=1)
    renderer = PageRenderer(bm, plugin_dir)

    mock_page = MagicMock()
    mock_page.route = AsyncMock()
    mock_page.context = MagicMock()
    await renderer._setup_network_policy(mock_page)
    handler = mock_page.route.call_args_list[0][0][1]

    url = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js"
    mock_route = MagicMock()
    mock_route.request.url = url
    mock_route.request.method = "GET"
    mock_route.continue_ = AsyncMock(side_effect=RuntimeError("Target closed"))

    with pytest.raises(RuntimeError, match="Target closed"):
        await handler(mock_route)
    assert url not in renderer._cdn_pending

    # Also verify _do_render releases page-owned pending futures on failure.
    owner_route = MagicMock()
    owner_route.request.url = url
    owner_route.request.method = "GET"
    assert await renderer._serve_cached_resource(owner_route, page=mock_page) is False
    assert url in renderer._cdn_pending
    pending_future = renderer._cdn_pending[url][0]

    bm.acquire_page = AsyncMock(return_value=(mock_page, True))
    bm.release_page = AsyncMock()
    renderer._load_and_wait = AsyncMock(side_effect=RuntimeError("boom"))
    html_path = tmp_path / "fail.html"
    html_path.write_text("<html></html>", encoding="utf-8")

    with pytest.raises(RenderError):
        await renderer._do_render(html_path, tmp_path / "out.png")
    assert url not in renderer._cdn_pending
    assert pending_future.done() and pending_future.result() is None


def test_windows_native_job_kill_on_close_and_access_denied(monkeypatch):
    import os

    if os.name != "nt":
        pytest.skip("Windows-only job object test")

    import ctypes
    from contextlib import ExitStack

    from astrbot_plugin_mathjax2image.infrastructure.browser import windows_native_job

    with ExitStack() as stack:
        job = windows_native_job._kernel.CreateJobObjectW(None, None)
        assert job
        stack.callback(windows_native_job._winapi.CloseHandle, job)
        limits = windows_native_job._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            windows_native_job._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        ok = windows_native_job._kernel.SetInformationJobObject(
            job,
            windows_native_job._JOB_EXTENDED_LIMIT_INFO,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        assert ok

        def fake_query(_job, _info_class, buf, _size, _ret):
            ctypes.c_ulong.from_buffer(buf, 4).value = 1
            (ctypes.c_size_t * 1).from_buffer(buf, 8)[0] = 4
            return 1

        def deny_open(*_args):
            err = PermissionError("Access is denied")
            err.winerror = 5
            raise err

        monkeypatch.setattr(
            windows_native_job._kernel, "QueryInformationJobObject", fake_query
        )
        monkeypatch.setattr(windows_native_job._winapi, "OpenProcess", deny_open)
        assert windows_native_job._member_handles(job, stack) == []


def test_template_placeholders_inside_code_blocks_are_preserved():
    source = (
        "$x^2$\n\n"
        "```js\n"
        "const req = {{MATH_REQUIRED}};\n"
        "const pkgs = {{MATH_PACKAGES}};\n"
        "const css = '--bg-color: #123456;';\n"
        "```"
    )
    html = MarkdownConverter(template_path=_TEMPLATE).convert_to_html(
        source, bg_color="#FFFFFF"
    )
    content = html.split('<main class="render-content">', 1)[1].split("</main>", 1)[0]
    plain = content
    import re

    plain = re.sub(r"<[^>]+>", "", plain)
    assert "{{MATH_REQUIRED}}" in plain
    assert "{{MATH_PACKAGES}}" in plain
    assert "#123456" in plain


def test_nested_enumerate_and_set_notation_and_wake_prefix_and_bool_eval():
    import ast
    import math
    from unittest.mock import MagicMock

    from astrbot_plugin_mathjax2image.domain.interfaces import (
        ILatexValidator,
        IMermaidConverter,
    )
    from astrbot_plugin_mathjax2image.handlers.command_handler import CommandHandler
    from astrbot_plugin_mathjax2image.infrastructure.converter import (
        LatexPreprocessor,
        ListConverter,
        MermaidConverter,
        TableConverter,
    )
    from astrbot_plugin_mathjax2image.infrastructure.validator.latex_validator import (
        LatexValidator,
    )
    from astrbot_plugin_mathjax2image.utils.safe_eval import (
        SafeMathEvaluator,
        safe_eval_math,
    )

    nested = (
        "\\begin{enumerate}\n"
        "\\item Outer one\n"
        "\\begin{enumerate}\n"
        "\\item Inner a\n"
        "\\item Inner b\n"
        "\\end{enumerate}\n"
        "\\item Outer two\n"
        "\\end{enumerate}"
    )
    converted_list = ListConverter().convert(nested)
    assert "1. Outer one" in converted_list
    assert "    1. Inner a" in converted_list
    assert "    2. Inner b" in converted_list
    assert "2. Outer two" in converted_list

    pre = LatexPreprocessor(
        TikzConverter(TikzPlotConverter()),
        ListConverter(),
        TableConverter(),
        MermaidConverter(),
    )
    tikz_with_mid = pre.preprocess(
        "\\begin{tikzpicture}\\node at (0,0) {$a \\mid b$};\\end{tikzpicture}"
    )
    assert "\\lbrace" not in tikz_with_mid
    assert "{$a \\mid b$}" in tikz_with_mid

    handler = CommandHandler.__new__(CommandHandler)
    ev = MagicMock()
    ev.get_message_str.return_value = "!render $a + b$ and $c$"
    assert handler._extract_command_content(ev, "render", "$a") == "$a + b$ and $c$"
    ev.get_message_str.return_value = "请帮我 !render $x$ 和 $y$"
    assert handler._extract_command_content(ev, "render", "") == "$x$ 和 $y$"

    assert math.isnan(safe_eval_math("True + 1"))
    with pytest.raises(ValueError, match="Unsupported constant type"):
        SafeMathEvaluator().visit(ast.parse("False", mode="eval"))

    assert isinstance(LatexValidator(), ILatexValidator)
    assert isinstance(MermaidConverter(), IMermaidConverter)

    for tpl_name in ("template.html", "book.html"):
        tpl = (Path(__file__).resolve().parents[1] / "templates" / tpl_name).read_text(
            encoding="utf-8"
        )
        assert "autoload: {html: []}" in tpl
        assert "if (initialTypeset) window.mathJaxReady = true;" in tpl
        assert "delete window.__engines[name];" in tpl
        assert "window.__ensureMermaid" in tpl


def test_non_math_to_math_resident_reuse_waits_for_typeset_promise():
    import json
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    html = MarkdownConverter(template_path=_TEMPLATE).convert_to_html("Plain text")
    scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html)
    # Execute config + engine + lifecycle scripts against a minimal DOM stub:
    # Job 1 starts with mathJaxRequired=false (typeset=false), then Job 2 sets
    # mathJaxRequired=true and calls __replaceRenderContent('$x$').
    harness = (
        """
        globalThis.window = globalThis;
        const mainNode = {
            innerHTML: '',
            replaceChildren() {},
            querySelector() { return null; },
            querySelectorAll() { return []; },
        };
        globalThis.document = {
            readyState: 'complete',
            body: {},
            head: {
                appendChild(el) {
                    if (el.src && el.src.includes('tex-chtml.js')) {
                        setTimeout(() => {
                            // Real MathJax 3.2.2 has no MathJax.startup.options object.
                            MathJax.startup.defaultReady = () => {
                                MathJax.startup.promise = Promise.resolve();
                            };
                            MathJax.texReset = () => {};
                            MathJax.typesetClear = () => {};
                            MathJax.typesetPromise = () => new Promise(r => {
                                globalThis.__finishTypeset = r;
                            });
                            el.onload();
                            MathJax.startup.ready();
                        }, 5);
                    }
                }
            },
            addEventListener() {},
            createElement(tag) { return { tag }; },
            querySelector(sel) { return sel === 'main.render-content' ? mainNode : null; },
            querySelectorAll() { return []; },
        };
        globalThis.getComputedStyle = () => ({ fontSize: '36px', fontFamily: 'serif' });
        """
        + "\n".join(scripts)
        + """
        (async () => {
            window.mathJaxRequired = true;
            window.__replaceRenderContent('<p>$x$</p>');
            await new Promise(r => setTimeout(r, 20));
            const readyBeforeTypeset = window.mathJaxReady;
            globalThis.__finishTypeset();
            await new Promise(r => setTimeout(r, 10));
            const readyAfterTypeset = window.mathJaxReady;
            console.log(JSON.stringify({ readyBeforeTypeset, readyAfterTypeset }));
        })();
        """
    )
    proc = subprocess.run(
        [node, "-e", harness], capture_output=True, text=True, check=True, timeout=10
    )
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result == {"readyBeforeTypeset": False, "readyAfterTypeset": True}
