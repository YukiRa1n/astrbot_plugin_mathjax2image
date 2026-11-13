"""Regressions for the 2026-11 security review findings."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


def test_empty_fences_and_trailing_space_and_multiline_math_preprocess():
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        MarkdownConverter,
    )

    mc = MarkdownConverter(_TEMPLATE)

    empty_py = mc.convert_to_html("```python\n```")
    assert '<pre><code class="language-python"></code></pre>' in empty_py
    assert "<p><pre>" not in empty_py

    empty_tilde = mc.convert_to_html("~~~\n~~~")
    assert "<pre><code></code></pre>" in empty_tilde
    assert "<p><pre>" not in empty_tilde

    trailing_ws = mc.convert_to_html("```python\nx = 1\n```   ")
    assert "<p><pre>" not in trailing_ws
    assert "</pre></p>" not in trailing_ws
    assert "<pre><code" in trailing_ws

    math_src = "$$\n\\begin{aligned}\n#1\n- item\n\\end{aligned}\n$$"
    math_html = mc.convert_to_html(math_src)
    assert "# 1" not in math_html
    assert "#1\n- item" in math_html


@pytest.mark.asyncio
async def test_auto_send_direct_does_not_clear_other_session_last_rendered_image(
    tmp_path, monkeypatch
):
    from astrbot_plugin_mathjax2image.handlers import llm_tool_handler

    monkeypatch.setattr(llm_tool_handler, "Comp", MagicMock())
    monkeypatch.setattr(llm_tool_handler, "MessageChain", MagicMock())

    pending_img = tmp_path / "pending.png"
    direct_img = tmp_path / "direct.png"
    pending_img.write_bytes(b"p")
    direct_img.write_bytes(b"d")

    orchestrator = MagicMock()
    orchestrator.render = AsyncMock(return_value=direct_img)
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    handler = llm_tool_handler.LLMToolHandler(orchestrator, context)
    handler._last_rendered_image = pending_img

    event = MagicMock()
    event.unified_msg_origin = "session-2"
    event.get_sender_id.return_value = "user-2"
    result = await handler.handle_render_math(event, "$x$", auto_send=True)

    assert result == llm_tool_handler.IMAGE_SENT_RESULT
    assert handler._last_rendered_image == pending_img


@pytest.mark.asyncio
async def test_browser_manager_close_continues_after_idle_cleanup_error():
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )

    manager = BrowserManager(max_pages=1)
    browser = MagicMock()
    browser.close = AsyncMock()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    manager._browser = browser
    manager._owns_browser = True
    manager._playwright = playwright

    async def failing_cleanup():
        raise RuntimeError("boom")

    manager._idle_cleanup_task = asyncio.create_task(failing_cleanup())
    await asyncio.sleep(0)

    await manager.close()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_tikz_allows_bracket_backtick_and_pgfplots_axis_variants(tmp_path):
    from astrbot_plugin_mathjax2image.infrastructure.browser.native_tikz import (
        NativeTikzRenderer,
    )

    renderer = NativeTikzRenderer(str(tmp_path))
    for code in (
        r"\begin{loglogaxis}\addplot {x};\end{loglogaxis}",
        r"\begin{tikzpicture}\node {$\left[a\right]$ \`a};\end{tikzpicture}",
    ):
        renderer._reject_unsafe_tex(code)


def test_asset_disk_cache_concurrent_put_get_and_evict_are_thread_safe(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from astrbot_plugin_mathjax2image.infrastructure.browser.asset_disk_cache import (
        AssetDiskCache,
    )

    cache = AssetDiskCache(tmp_path, max_bytes=200)
    payload = b"x" * 60

    def worker(idx: int) -> None:
        for round_idx in range(12):
            url = f"https://cdn.jsdelivr.net/npm/pkg@1.0.0/f{(idx + round_idx) % 6}.js"
            assert cache.put(url, payload, "application/javascript") is True
            got = cache.get(url)
            if got is not None:
                assert got[0] == payload

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, range(6)))


@pytest.mark.asyncio
async def test_resident_plain_to_math_uses_cold_timeout_and_disk_cache_works_with_zero_mem(
    tmp_path,
):
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )
    from astrbot_plugin_mathjax2image.utils.config import safe_bool

    assert safe_bool(None, default=True) is True
    assert safe_bool(None) is False
    assert safe_bool(1) is False

    renderer = PageRenderer(
        BrowserManager(max_pages=1),
        tmp_path,
        mathjax_timeout=0,
        tikz_timeout=0,
        mermaid_timeout=0,
        screenshot_timeout=0,
        resource_cache_max_mb=0,
        asset_cache_dir=tmp_path / "disk_cache",
        asset_disk_cache_max_mb=1,
    )
    assert renderer._mathjax_timeout == 1
    assert renderer._tikz_timeout == 1
    assert renderer._mermaid_timeout == 1
    assert renderer._screenshot_timeout == 1

    # resource_cache_max_mb=0 still hits asset_disk_cache without retaining in RAM
    url = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js"
    assert renderer._disk_cache.put(url, b"window.MathJax={};", "text/javascript")
    route = MagicMock()
    route.request.url = url
    route.fulfill = AsyncMock()
    assert await renderer._serve_cached_resource(route) is True
    route.fulfill.assert_awaited_once()
    assert not renderer._cdn_cache

    # Reused plain-text page (MathJax never loaded) must get >= 60s wait timeout
    html_file = tmp_path / "doc.html"
    html_file.write_text(
        '<html><script>window.mathJaxRequired = true; window.__replaceRenderContent = () => {};</script><body><main class="render-content">$x$</main></body></html>',
        encoding="utf-8",
    )
    waited_timeouts = []
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=[True, False, None, True])

    async def fake_wait_for_function(_expr, timeout=None):
        waited_timeouts.append(timeout)

    page.wait_for_function = fake_wait_for_function
    renderer._wait_for_mermaid = AsyncMock()
    renderer._wait_for_tikz = AsyncMock()
    await renderer._load_and_wait(page, html_file)
    assert waited_timeouts[-1] >= 60_000

    # Warm page (MathJax already loaded) uses configured mathjax_timeout
    renderer._mathjax_timeout = 1234
    page2 = MagicMock()
    page2.evaluate = AsyncMock(side_effect=[True, True, None, True])
    page2.wait_for_function = fake_wait_for_function
    await renderer._load_and_wait(page2, html_file)
    assert waited_timeouts[-1] == 1234


@pytest.mark.asyncio
async def test_browser_manager_close_cancels_hung_idle_cleanup_task():
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )

    manager = BrowserManager(max_pages=1)
    browser = MagicMock()
    browser.close = AsyncMock()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    manager._browser = browser
    manager._owns_browser = True
    manager._playwright = playwright

    async def hung_cleanup():
        await asyncio.sleep(60)

    manager._idle_cleanup_task = asyncio.create_task(hung_cleanup())
    await asyncio.sleep(0)

    await asyncio.wait_for(manager.close(), timeout=1.5)
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


def test_converter_edge_cases_from_re_review():
    from astrbot_plugin_mathjax2image.infrastructure.converter import (
        LatexPreprocessor,
        ListConverter,
        MarkdownConverter,
        MermaidConverter,
        TableConverter,
        TikzConverter,
        TikzPlotConverter,
    )
    from astrbot_plugin_mathjax2image.utils.linear_scan import scan_math_blocks

    # 1. Escaped dollar \$ does not break subsequent $...$ matching
    escaped_line = r"Price is \$5 and $a*b + c*d$."
    spans = scan_math_blocks(escaped_line)
    assert [escaped_line[s:e] for s, e in spans] == ["$a*b + c*d$"]

    pre = LatexPreprocessor(
        TikzConverter(TikzPlotConverter()),
        ListConverter(),
        TableConverter(),
        MermaidConverter(),
    )
    md = MarkdownConverter(template_path=_TEMPLATE)
    html_dollar = md.convert_to_html(pre.preprocess(escaped_line))
    assert "<em>" not in html_dollar
    assert "$a*b + c*d$" in html_dollar

    # 2. Set notation skips macro/script brace arguments (\frac, \text, subscript)
    pre_expr = pre.preprocess(r"$\frac{1}{a \mid b} + \text{u \mid v} + {x \mid x > 0}$")
    assert r"\frac{1}{a \mid b}" in pre_expr
    assert r"\text{u \mid v}" in pre_expr
    assert r"\lbrace x \mid x > 0\rbrace " in pre_expr

    # 3. TableConverter handles escaped \& and matrix internal & / \\ and \\[4pt]
    tab = (
        "\\begin{tabular}{ll}\n"
        "\\hline\n"
        "Name & Formula \\\\\n"
        "\\hline\n"
        "R \\& D & $\\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}$ \\\\[4pt]\n"
        "\\hline\n"
        "\\end{tabular}"
    )
    md_tab = TableConverter().convert(tab)
    assert "| Name | Formula |" in md_tab
    assert r"| R \& D | $\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}$ |" in md_tab
    assert "[4pt]" not in md_tab

    # 4. ListConverter ignores \itemsep
    lst = "\\begin{itemize}\n\\itemsep 0pt\n\\item First\n\\end{itemize}"
    converted_lst = ListConverter().convert(lst)
    assert "- First" in converted_lst
    assert r"\itemsep 0pt" in converted_lst
    assert "- sep" not in converted_lst

    # 5. Ignored package with options succeeds; MathJax package with options raises
    html_ctex = md.convert_to_html("\\usepackage[UTF8]{ctex}\nHello $x$")
    assert "ctex" not in html_ctex
    with pytest.raises(ValueError, match="package options"):
        md.convert_to_html("\\usepackage[italicdiff]{physics}\nHello $x$")

    # 6. Matrix \\n preserved while outside \\n expands; adjacent code fence unwraps <p>
    matrix_doc = "$$\\begin{pmatrix} a \\\\ n \\end{pmatrix}$$\nLine1\\n# Heading"
    html_matrix = md.convert_to_html(pre.preprocess(matrix_doc))
    assert r"\begin{pmatrix} a \\ n \end{pmatrix}" in html_matrix
    assert "<h1>Heading</h1>" in html_matrix

    adj_fence = "Intro:\n```python\nx = 1\n```\nOutro"
    html_fence = md.convert_to_html(adj_fence)
    assert "<p><pre>" not in html_fence
    assert "</pre></p>" not in html_fence
    assert "<p>Intro:</p>" in html_fence
    assert "<p>Outro</p>" in html_fence

    # 7. 4-space indented fence & 4-space #define are treated cleanly without double <pre>
    indented_fence = "    ```python\n    #define MAX 1\n    ```"
    html_indented = md.convert_to_html(indented_fence)
    assert html_indented.count("<pre>") == 1
    assert "<h1>" not in html_indented

    # 8. Standalone pgfplots groupplot / loglogaxis converted cleanly with groupplots lib
    standalone_gp = (
        "\\usepgfplotslibrary{groupplots}\n"
        "\\begin{groupplot}[group style={group size=2 by 1}]\n"
        "\\nextgroupplot \\addplot {x};\n"
        "\\nextgroupplot \\addplot {x^2};\n"
        "\\end{groupplot}"
    )
    html_gp = md.convert_to_html(pre.preprocess(standalone_gp))
    assert '<div class="tikz-diagram"><script type="text/tikz"' in html_gp
    assert r"\usepgfplotslibrary{groupplots}" in html_gp
    assert r"\begin{tikzpicture}" in html_gp
    assert html_gp.count('<script type="text/tikz"') == 1


def test_svg_output_js_blocks_animate_variants_and_entity_encoded_href():
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    plugin_dir = Path(__file__).resolve().parents[1]
    svg_js = (plugin_dir / "static" / "svg_output.js").read_text(encoding="utf-8")
    probe = (
        "globalThis.window = globalThis;\n"
        + svg_js
        + """
        const blocked = [
            '<svg><g fill="red"><animateTransform attributeName="href" to="https://evil.example/"/></g></svg>',
            '<svg><g fill="red"><set attributeName="&#104;ref" to="https://evil.example/"/></g></svg>',
            '<svg><g fill="red"><use xlink:href="&#104;ttps://evil.example/#x"/></g></svg>',
        ];
        console.log(JSON.stringify(blocked.map(s => window.__compactSvgOutput(s))));
        """
    )
    proc = subprocess.run(
        [node, "-e", probe], capture_output=True, text=True, check=True, timeout=10
    )
    assert json.loads(proc.stdout.strip()) == [None, None, None]
