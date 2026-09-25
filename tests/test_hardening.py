"""Regression tests for the quadratic-scan, attribute, and native-TeX fixes."""

import time
from pathlib import Path

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
    iter_trusted_html_spans,
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
    "unclosed tabular": "\\begin{tabular}{c}" * (BOMB_LENGTH // 18),
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


# ---- 2024 review fixes: linear scans, fence awareness, placeholder collisions ----


def test_paired_math_delimiters_stay_linear():
    """成对的 ``\\(x\\)`` 正是旧实现退化的形态。

    每个配对都通过 ``list.pop(index)`` 消耗一个 closer，N 对要搬动 O(N^2) 个
    列表元素；旧实现下 100 000 对需要数十秒。这里用远超单条消息上限的规模
    确认扫描器仍然线性。
    """
    payload = "\\(x\\)" * 100_000
    started = time.perf_counter()
    spans = scan_math_blocks(payload)
    assert len(spans) == 100_000
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_many_closers_before_one_opener_stay_linear():
    """大量 closer 堆在单个 opener 之前时不得退化。

    旧实现先 ``closes.pop(0)`` 排掉 opener 之前的 closer，每次 pop 都要整体
    左移：16000 个 closer 花 293 ms，10 万个接近 12 秒。
    """
    payload = "\\end{tikzpicture}" * 100_000 + "\\begin{tikzpicture}"
    started = time.perf_counter()
    spans = find_pairs(payload, "\\begin{tikzpicture}", "\\end{tikzpicture}")
    assert spans == []
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_unclosed_trusted_block_prefix_is_linear():
    """受控 HTML 块只有前缀、没有结束标记时不得退化。

    旧的惰性交替正则在每个候选前缀处都会重扫文档剩余部分，约 200 KB 的
    重复前缀要 5.5 秒。这里只测提取阶段：整条 ``convert_to_html`` 在 200 KB
    上主要由 Python-Markdown 自身的行内扫描占据，会掩盖这一段的目标。
    """
    payload = '<div class="tikz-diagram"><script type="text/tikz">\n' * 3_773
    started = time.perf_counter()
    text, blocks = _converter()._extract_trusted_html_blocks(payload, "token")
    assert blocks == []
    assert text == payload
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_tabular_separator_follows_first_emitted_row():
    """内容以 ``\\\\`` 开头时表头仍必须拿到分隔行。

    旧实现用原始切分的下标判断表头，被跳过的空行让 ``i == 0`` 落到第二行，
    整个 Markdown 表格因此失效。
    """
    out = TableConverter().convert(
        "\\begin{tabular}{cc}\\\\ a & b \\\\ c & d \\end{tabular}"
    )
    assert out.splitlines() == ["| a | b |", "|---|---|", "| c | d |"]


def test_tabular_column_spec_with_nested_braces():
    """列格式声明内可以嵌套花括号（``>{\\bfseries}l``）。"""
    out = TableConverter().convert(
        "\\begin{tabular}{>{\\bfseries}lc} a & b \\\\ c & d \\end{tabular}"
    )
    assert out.splitlines()[0] == "| a | b |"
    assert "\\bfseries" not in out
    assert "}" not in out


def test_trusted_html_span_scanner_matches_all_three_forms():
    """线性扫描器必须覆盖三种受控块形式，且区间与旧正则一致。"""
    tikz = (
        '<div class="tikz-diagram"><script type="text/tikz" data-disable-cache="true">\n'
        "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}\n</script></div>"
    )
    mermaid = '<pre class="mermaid">\ngraph TD; A-->B;\n</pre>'
    error = '<div class="error">boom</div>'
    text = "a" + tikz + "b" + mermaid + "c" + error + "d"
    spans = list(iter_trusted_html_spans(text))
    assert [text[start:end] for start, end in spans] == [tikz, mermaid, error]


def test_trusted_html_span_scanner_rejects_markup_inside_error_block():
    """``<div class="error">`` 的块体不允许尖括号，语义与旧正则相同。"""
    text = '<div class="error">x<div class="error">boom</div>'
    spans = list(iter_trusted_html_spans(text))
    assert [text[start:end] for start, end in spans] == [
        '<div class="error">boom</div>'
    ]


def test_tikz_inside_a_code_fence_is_left_as_source():
    """代码围栏里的 TikZ 是示例源码，不能被转换成图。

    旧行为会把围栏内容换成插件生成的 ``<div class="tikz-diagram">`` HTML，
    读者看到的就不再是用户写的代码。
    """
    payload = "```\n\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}\n```"
    out = _preprocessor().preprocess(payload)
    assert out == payload
    assert "tikz-diagram" not in out


def test_real_tikz_outside_a_fence_is_still_converted():
    """跳过围栏不能顺手把围栏外的真图也跳过。"""
    payload = (
        "```\nnot tikz\n```\n\n"
        "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}"
    )
    assert "tikz-diagram" in _preprocessor().preprocess(payload)


def test_set_notation_inside_a_code_fence_is_left_as_source():
    """集合表示法改写同样不得进入代码围栏。"""
    payload = "```\n{a \\mid b}\n```"
    assert _preprocessor().preprocess(payload) == payload


def test_set_notation_inside_math_is_still_rewritten():
    """数学区间受文本命令改写保护，集合表示法在这里仍要生效。"""
    out = _preprocessor().preprocess("$${x \\mid x > 0}$$")
    assert "\\lbrace" in out and "\\rbrace" in out


def test_forged_block_placeholder_cannot_displace_a_real_block():
    """用户正文里的 ``MATHBLOCK0MATHBLOCK`` 不能顶替插件的占位符。

    占位符现在带每次转换随机的 token，用户无法预测，因此真块一定留在自己的
    位置，也不会留下未还原的字面占位符。
    """
    import re

    html = _converter().convert_to_html("forged MATHBLOCK0MATHBLOCK\n\n$$x^2$$")
    body = re.search(r'<main class="render-content">([\s\S]*?)</main>', html).group(1)
    assert "MATHBLOCK0MATHBLOCK" in body
    assert body.count("$$x^2$$") == 1
    assert body.index("MATHBLOCK0MATHBLOCK") < body.index("$$x^2$$")


def test_many_inline_math_blocks_restore_in_one_pass():
    """上万个小公式的还原必须是单次遍历。

    旧实现每个块一次 ``str.replace``，整体是 O(块数 × 文档长度)：40 000 个
    ``$x$`` 光还原阶段就要十秒。
    """
    payload = "$x$ " * 40_000
    started = time.perf_counter()
    html = _converter().convert_to_html(payload)
    assert html.count("$x$") == 40_000
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_placeholder_pattern_is_not_confused_by_a_digit_leading_token():
    """相邻占位符 token 以数字开头时，下标也不得被贪婪吞并。

    这是修复过程中真实踩到的坑：占位符以数字结尾、下一个 token 又以数字开头时，
    还原正则的 ``\\d+`` 会读出一个越界下标并抛 IndexError。token 随机时表现为
    间歇性崩溃，因此这里用固定 token 复现。
    """
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        _block_placeholder,
        _placeholder_pattern,
    )

    token = "0123abcd"  # 以数字开头：正是会触发贪婪吞并的形态
    text = "".join(_block_placeholder(token, "MATH", i) for i in range(12)) + " tail"
    assert [
        int(value) for value in _placeholder_pattern(token, "MATH").findall(text)
    ] == list(range(12))
    restored = _placeholder_pattern(token, "MATH").sub(
        lambda match: f"<{match.group('index')}>", text
    )
    assert restored == "".join(f"<{i}>" for i in range(12)) + " tail"


def test_paragraph_wrapped_html_placeholder_is_unwrapped():
    """受控 HTML 块还原时要连 Markdown 加的 ``<p>`` 一起去掉。"""
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        _block_placeholder,
        _placeholder_pattern,
    )

    token = "ff00ff00"
    placeholder = _block_placeholder(token, "HTML", 0)
    pattern = _placeholder_pattern(token, "HTML", paragraph_wrapped=True)
    assert pattern.sub("BLOCK", f"<p>{placeholder}</p>") == "BLOCK"
    assert pattern.sub("BLOCK", placeholder) == "BLOCK"


# ---- second review round: remaining super-linear scans ----


def test_usepackage_declarations_stay_linear():
    """大量未闭合的 ``\\usepackage[`` 不得回扫。

    旧的 ``re.sub(r"\\usepackage(?:\\[([^\\]]*)\\])?\\{([^{}]+)\\}")`` 在每个
    ``\\usepackage[`` 处都要找 ``]``，找不到就扫到文末：实测 25/50/100 KB 为
    215 ms / 846 ms / 3.75 s（每次都约 4 倍）。
    """
    from astrbot_plugin_mathjax2image.utils.linear_scan import (
        strip_usepackage_declarations,
    )

    payload = "\\usepackage[" * 9_000
    started = time.perf_counter()
    out = strip_usepackage_declarations(payload, lambda names: None)
    assert out == payload  # 未闭合 => 不是声明，原文保留
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_usepackage_declaration_semantics_are_preserved():
    """线性扫描必须与旧正则同语义：收集、拒绝选项、跳过非声明。"""
    from astrbot_plugin_mathjax2image.utils.linear_scan import (
        strip_usepackage_declarations,
    )

    seen: list[str] = []
    assert (
        strip_usepackage_declarations("a\\usepackage{physics,mhchem}b", seen.append)
        == "ab"
    )
    assert seen == ["physics,mhchem"]

    with pytest.raises(ValueError, match="package options"):
        strip_usepackage_declarations(
            "\\usepackage[italicdiff]{physics}", lambda names: None
        )

    # 未闭合的 `[`：不是声明
    unclosed = "x\\usepackage[ y"
    assert strip_usepackage_declarations(unclosed, lambda names: None) == unclosed

    # `[^{}]+` 不能跨越 `{`，但后面的合法声明仍要被收集
    mixed = "\\usepackage{a{b}\\usepackage{ok}"
    collected: list[str] = []
    out = strip_usepackage_declarations(mixed, collected.append)
    assert collected == ["ok"]
    assert out == "\\usepackage{a{b}"


def test_bracket_rescan_guard_rejects_unpaired_openers():
    """大量未配对的 ``[`` 会让 Python-Markdown 反复回扫，必须被拒绝。

    实测（仅 markdown，无插件代码）：``\\usepackage[`` 100 KB 约 100 s，
    ``word [word `` 100 KB 约 115 s。
    """
    converter = _converter()
    with pytest.raises(ValueError, match="未配对"):
        converter.convert_to_html(("word [word " * 12_000)[:100_000])
    with pytest.raises(ValueError, match="未配对"):
        converter.convert_to_html(("\\usepackage[" * 9_000)[:100_000])
    # 配平的普通内容不受影响
    html = converter.convert_to_html("见 [文档](https://x) 与 [1] 注记 $x$")
    assert "<a href" in html or "https://x" in html


def test_bracket_guard_ignores_brackets_inside_code_fences():
    """围栏/行内代码里的 ``[`` 不会进入 markdown，不应触发任何退化判据。

    转换器先把代码换成占位符再交给 markdown，所以两个判据都必须在遮掉代码块
    之后计算，否则一段代码样例会把正文判成攻击。
    """
    converter = _converter()
    # 代码块里大量未配对的 `[`：正常内容，必须能渲染
    fenced = "正文段落。" + "\n\n```\n" + "list.append[item = 1\n" * 3_000 + "```\n"
    assert "list.append[item" in converter.convert_to_html(fenced)
    # 全是标点的代码块也不该被比例判据拒绝（markdown 看不到它）
    punctuation = "正文" + "\n\n```\n" + "[" * 49_000 + "\n```\n"
    assert "[" * 50 in converter.convert_to_html(punctuation)
    # 行内代码同理
    inline = "正文 " + "`" + "[" * 5_000 + "`" + " 结尾"
    assert "[" * 50 in converter.convert_to_html(inline)
    # 同样的括号放在正文里就必须拒绝
    with pytest.raises(ValueError):
        converter.convert_to_html("word [word " * 5_000)
    with pytest.raises(ValueError):
        converter.convert_to_html("[" * 49_000)


def test_bracket_cost_model_matches_the_rescan_work():
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        bracket_rescan_cost,
    )

    assert bracket_rescan_cost("plain prose", 10) == 0
    assert bracket_rescan_cost("a[b]c", 10) == 2
    assert bracket_rescan_cost("[ab", 10) == 3  # 到文本末尾
    assert bracket_rescan_cost("[" * 1_000, 10) == 1_000  # 超预算即提前返回
    assert bracket_rescan_cost("a\\%[b", 10) == 2  # 与 `%` 无关，只看括号


def test_bracket_guard_accepts_every_real_document_in_the_repo():
    """守卫不能把仓库里的真实文档判成攻击。"""
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        _BRACKET_RESCAN_BUDGET,
        bracket_rescan_cost,
    )

    root = Path(__file__).resolve().parents[1]
    converter = _converter()
    checked = 0
    for pattern in ("*.md", "examples/*.md", "templates/*.html"):
        for path in root.glob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert (
                bracket_rescan_cost(text, _BRACKET_RESCAN_BUDGET)
                <= _BRACKET_RESCAN_BUDGET
            ), path
            assert converter._is_degenerate_markup(text) is False, path
            checked += 1
    assert checked >= 8


def test_table_wrapper_and_caption_scan_is_linear():
    """``\\begin{table}[`` 与 ``\\caption{`` 的惰性回扫已限长。

    旧模式（``(\\[.*?\\])?`` / ``\\caption\\{.*?\\}``）在缺少闭合符时每个
    出现位置都扫到行尾：50 KB 实测 946 ms / 905 ms。
    """
    converter = TableConverter()
    # `\begin{table}` 本身总是被移除（可选参数组是可选的），只是不再为了
    # 找 `]` 而回扫；`\caption{` 因为没有 `}` 而整体保留。
    for payload, expected in (
        ("\\begin{table}[" * 5_500, "[" * 5_500),
        ("\\caption{" * 6_500, "\\caption{" * 6_500),
    ):
        started = time.perf_counter()
        assert converter.convert(payload) == expected
        assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_over_long_table_arguments_are_left_alone():
    """超过限长的参数按“不是环境/命令”处理，而不是付出平方级代价。"""
    from astrbot_plugin_mathjax2image.infrastructure.converter.table_converter import (
        _MAX_ARGUMENT,
    )

    converter = TableConverter()
    assert converter.convert("\\caption{short}") == ""
    long_caption = "\\caption{" + "x" * (_MAX_ARGUMENT + 10) + "}"
    assert converter.convert(long_caption) == long_caption
    assert converter.convert("\\begin{table}[h]x") == "x"
    assert converter.convert("\\begin{table}[" + "h" * (_MAX_ARGUMENT + 10) + "]") == (
        "[" + "h" * (_MAX_ARGUMENT + 10) + "]"
    )


def test_preamble_directives_are_bounded():
    """TikZ 声明抽取/剥离的字符类已限长（块内 50 KB 全是 ``\\usepackage[`` 时旧版约 1 s）。"""
    converter = TikzConverter(TikzPlotConverter())
    payload = "\\usepackage[" * 6_000
    started = time.perf_counter()
    converter._strip_preamble_directives(payload)
    converter._extract_preamble_directives(payload)
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS

    assert converter._extract_preamble_directives(
        "\\usepackage{pgfplots}\\usetikzlibrary{calc}"
    ) == (["pgfplots"], ["calc"])
    assert converter._strip_preamble_directives("\\usepackage{pgfplots}") == ""
    assert converter._strip_preamble_directives("\\begin{document}x\\end{document}") == "x"


def test_think_tag_filter_is_linear_and_keeps_semantics():
    """``<think>.*?</think>``（DOTALL）在未闭合时每个标签都扫到文末，100 KB 要 6.6 s。"""
    from astrbot_plugin_mathjax2image.application.llm_orchestrator import (
        LLMOrchestrator,
    )

    llm = LLMOrchestrator(context=None)
    assert llm._filter_think_tags(None) is None
    assert llm._filter_think_tags("") is None
    assert llm._filter_think_tags("plain") == "plain"
    assert llm._filter_think_tags("<think>a</think>b") == "b"
    assert llm._filter_think_tags("<think>a</think>  b") == "b"
    assert llm._filter_think_tags("x<think>a</think>y<think>b</think>z") == "xyz"
    assert llm._filter_think_tags("keep <think>unclosed") == "keep <think>unclosed"

    payload = "<think>" * 16_000
    started = time.perf_counter()
    assert llm._filter_think_tags(payload) == payload
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS


def test_pgfplots_percent_probe_is_linear():
    """注释判定不得每个 token 重扫一遍整行（50 KB 单行实测 2.0 s）。"""
    from astrbot_plugin_mathjax2image.infrastructure.converter.pgfplots_preprocessor import (
        PgfplotsPreprocessor,
        _has_unescaped_percent,
        _positions,
    )

    one_line = "\\addplot3" * 5_500
    started = time.perf_counter()
    PgfplotsPreprocessor(6400).convert(one_line)
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS

    text = "a\\%b%c"
    positions = _positions(text, "%")
    assert positions == [2, 4]
    assert _has_unescaped_percent(positions, text, 0, 3) is False  # 转义的 % 不算注释
    assert _has_unescaped_percent(positions, text, 0, 5) is True
    assert _has_unescaped_percent(positions, text, 3, 5) is True
    assert _has_unescaped_percent([0], "%x", 0, 2) is True  # 行首 % 就是注释


def test_inline_code_line_limit_is_looked_up_once():
    """行内代码的行尾只求一次（旧版每个反引号 run 都重扫到行尾，400 KB 约 1.4 s）。"""
    payload = ("`a` " * 100_000)[:800_000]
    started = time.perf_counter()
    spans = scan_fenced_code(payload)
    assert len(spans) == 100_000
    assert time.perf_counter() - started < BOMB_BUDGET_SECONDS
    # 跨行的行内代码仍然不成立
    assert scan_fenced_code("`a\nb`") == []


def test_native_svg_insertion_is_single_pass():
    """SVG 回填必须一次拼接，不能逐个重建整个文档（O(k x n)）。"""
    import re

    from astrbot_plugin_mathjax2image.infrastructure.browser.native_tikz import (
        _insert_replacements,
    )

    doc = "".join(f"<script>{index}</script>" for index in range(1_000))
    matches = list(re.finditer(r"<script>.*?</script>", doc))
    assert len(matches) == 1_000
    assert _insert_replacements(doc, matches, ["<svg/>"] * 1_000) == "<svg/>" * 1_000

    # 未匹配的原文必须原样保留
    doc2 = "a<script>1</script>b"
    matches2 = list(re.finditer(r"<script>.*?</script>", doc2))
    assert _insert_replacements(doc2, matches2, ["[svg]"]) == "a[svg]b"


# ---- third review round: CommonMark fence pairing ----


def test_inline_fence_mention_is_not_a_fence():
    """正文里的 ``` 不是围栏开启符（围栏必须在行首）。

    旧实现只要求“run 后面有换行”，于是「见 ```mermaid 写法」会和后面真正的
    围栏错配：正文里的行内 run 被当成开启符，真正的代码块丢失。
    """
    text = "见 ```mermaid 的写法\n\n```python\nx = 1\n```\n"
    spans = scan_fenced_code(text)
    assert [text[a:b] for a, b in spans] == ["```python\nx = 1\n```"]


def test_closing_fence_must_be_at_least_as_long_as_the_opener():
    """4 反引号围栏里的 3 反引号行不是闭合符（CommonMark）。"""
    outer = "````\n```\ncode\n```\n````\n"
    spans = scan_fenced_code(outer)
    assert len(spans) == 1
    # 区间止于闭合符末尾（不含其后的换行），与既有语义一致
    assert spans[0] == (0, len(outer) - 1)


def test_closing_fence_longer_than_the_opener_is_accepted():
    """闭合符比开启符长是合法的，不能因此丢掉整个代码块。"""
    text = "```\ncode\n````\n"
    spans = scan_fenced_code(text)
    assert [text[a:b] for a, b in spans] == ["```\ncode\n````"]
    html = _converter().convert_to_html(text)
    assert "<pre><code>code</code></pre>" in html


def test_indented_and_unclosed_fences():
    """缩进围栏照旧识别；未闭合围栏照旧不产出区间（保持既有语义）。"""
    indented = "  ```python\n  x = 1\n  ```\n"
    spans = scan_fenced_code(indented)
    assert len(spans) == 1
    assert indented[spans[0][0] : spans[0][1]].endswith("  ```")
    html = _converter().convert_to_html(indented)
    assert "language-python" in html

    assert scan_fenced_code("```\ncode\n") == []
    # 行内 `~~~` 不是代码（CommonMark 只允许反引号做行内代码）
    assert scan_fenced_code("prose ~~~ more\n") == []


def test_nested_longer_fence_renders_as_one_block():
    """4 反引号外层 + 3 反引号内层：整块作为一个代码块，内层原样保留。"""
    html = _converter().convert_to_html("````\n```\ncode\n````\n")
    assert html.count("<pre><code") == 1
    assert "```\ncode" in html


def test_code_sample_survives_an_inline_fence_mention():
    """回归：正文提到 ``` 之后，后面的代码块不能再被拆成正文。

    修复前渲染出来是（已人工看图核对）：``\\textbf{bold text}`` 被改写成真正的
    粗体、集合被改成裸文本 ``\\lbrace a \\mid b\\rbrace``，还多出一个空代码块
    和孤零零的 ```。
    """
    sample = "\\textbf{bold text} 和集合 {a \\mid b}"
    payload = "正文里提到 ```tex 代码块：\n\n```\n" + sample + "\n```\n"
    html = _converter().convert_to_html(_preprocessor().preprocess(payload))
    assert "**bold text**" not in html
    assert "\\lbrace" not in html
    assert sample in html
    assert html.count("<pre><code") == 1
    assert "正文里提到 ```tex 代码块：" in html


def test_usepackage_inside_a_fence_is_protected_after_an_inline_mention():
    """回归：代码块里的 ``\\usepackage`` 不得泄漏（泄漏会让整条渲染失败）。

    README 就是这样挂的：正文里出现过行内反引号围栏，导致后面的 ```latex 块
    没有被识别，``\\usepackage{pgfplots}`` 落到 MathJax 白名单上抛 ValueError。
    """
    payload = "正文提到 ```tex 代码块：\n\n```latex\n\\usepackage{pgfplots}\n```\n"
    html = _converter().convert_to_html(_preprocessor().preprocess(payload))
    assert "\\usepackage{pgfplots}" in html
