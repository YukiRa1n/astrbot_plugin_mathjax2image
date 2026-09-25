"""
Markdown转换器
将Markdown转换为完整HTML
"""

import html as html_lib
import json
import math
import re
import uuid
from pathlib import Path

import markdown

from ...utils.linear_scan import scan_fenced_code, scan_math_blocks, substitute_spans

# 受控 HTML 块的起始字面量。转换器生成的 TikZ 块可能带 data-* 属性
# （<script type="text/tikz" data-tex-packages='...'>），因此起始字面量只覆盖到
# `type="text/tikz"`，属性部分由 _is_trusted_html_block 单独校验。
_TIKZ_HTML_START = '<div class="tikz-diagram"><script type="text/tikz"'
_TIKZ_HTML_END = "\n</script></div>"
_MERMAID_HTML_START = '<pre class="mermaid">\n'
_MERMAID_HTML_END = "\n</pre>"
_ERROR_HTML_START = '<div class="error">'
_ERROR_HTML_END = "</div>"
_TRUSTED_HTML_STARTS = (
    _TIKZ_HTML_START,
    _MERMAID_HTML_START,
    _ERROR_HTML_START,
)
_TRUSTED_HTML_ENDS = (_TIKZ_HTML_END, _MERMAID_HTML_END, _ERROR_HTML_END)


def _block_placeholder(token: str, kind: str, index: int) -> str:
    """每次转换独立的块占位符。

    token 让占位符不可预测：用户正文里恰好含有 `MATHBLOCK0MATHBLOCK` 之类的
    旧式定长标记时，不会被误认成插件自己的占位符而在还原阶段顶替/复制内容。

    下标后面必须再跟一次 ``kind`` 作为终止符。否则占位符会以数字结尾，而
    相邻占位符的 token 以十六进制字符开头（可能是数字），还原正则的 ``\d+``
    会贪婪地吞掉下一个 token 的前导数字，把下标读成一个越界的大数。
    """
    return f"{token}{kind}{index}{kind}"


def _placeholder_pattern(
    token: str, kind: str, *, paragraph_wrapped: bool = False
) -> re.Pattern[str]:
    """匹配 ``token + kind + index + kind``。

    收尾的 ``kind`` 让下标不可能跨到下一个占位符：相邻 token 是十六进制字符，
    可能以数字开头，没有终止符时 ``\d+`` 会把它们吞进下标。
    ``paragraph_wrapped`` 用于受控 HTML 块，Markdown 会给它们套上 ``<p>``。
    """
    body = re.escape(token) + kind + r"(?P<index>\d+)" + kind
    if paragraph_wrapped:
        body = r"(?:<p>)?" + body + r"(?:</p>)?"
    return re.compile(body)


def iter_trusted_html_spans(text: str):
    """按从左到右的顺序产出受控 HTML 块的 (start, end) 区间。

    旧的三种形式惰性正则交替在每一个候选前缀处都要重扫文档剩余部分，
    100 KB 重复的 `<div class="tikz-diagram"><script type="text/tikz">`
    要耗约 2 秒（平方级）。这里用 str.find 定位候选，总工作量线性；
    预先用 rfind 算出每种结束标记的最后位置，令缺少结束标记的候选零成本跳过。
    """
    last_end = tuple(text.rfind(end) for end in _TRUSTED_HTML_ENDS)
    # Start tokens that never occur again must not be searched for: a failed
    # ``str.find`` scans the whole remainder, so a missing form would cost O(n)
    # per candidate and reintroduce the quadratic behaviour.
    last_start = tuple(text.rfind(start) for start in _TRUSTED_HTML_STARTS)
    cursor = 0
    length = len(text)
    while cursor < length:
        start = -1
        form = -1
        for index, token in enumerate(_TRUSTED_HTML_STARTS):
            if last_start[index] < cursor:
                continue
            found = text.find(token, cursor)
            if found != -1 and (start == -1 or found < start):
                start, form = found, index
        if start == -1:
            return
        if form == 0:
            # 需要 `<script type="text/tikz"[^>]*>\n` 之后才进入块体。
            tag_end = text.find(">", start + len(_TIKZ_HTML_START))
            body_from = tag_end + 1
            if (
                tag_end == -1
                or body_from >= length
                or text[body_from] != "\n"
                or last_end[0] < body_from
            ):
                cursor = start + 1
                continue
        elif form == 1:
            body_from = start + len(_MERMAID_HTML_START)
            if last_end[1] < body_from:
                cursor = start + 1
                continue
        else:
            # `<div class="error">[^<>]*</div>`：结束标记前不得出现尖括号。
            body_from = start + len(_ERROR_HTML_START)
            if last_end[2] < body_from:
                cursor = start + 1
                continue
            body_end = text.find(_ERROR_HTML_END, body_from)
            inner = text[body_from:body_end]
            if "<" in inner or ">" in inner:
                cursor = start + 1
                continue
            yield start, body_end + len(_ERROR_HTML_END)
            cursor = body_end + len(_ERROR_HTML_END)
            continue
        body_end = text.find(_TRUSTED_HTML_ENDS[form], body_from)
        yield start, body_end + len(_TRUSTED_HTML_ENDS[form])
        cursor = body_end + len(_TRUSTED_HTML_ENDS[form])
MATHJAX_PACKAGES = {
    "ams",
    "amscd",
    "bbox",
    "boldsymbol",
    "braket",
    "cancel",
    "cases",
    "centernot",
    "color",
    "empheq",
    "enclose",
    "extpfeil",
    "gensymb",
    "mathtools",
    "mhchem",
    "newcommand",
    "physics",
    "textcomp",
    "textmacros",
    "unicode",
    "upgreek",
    "verb",
}

MATHJAX_PACKAGE_ALIASES = {
    "amsmath": "ams",
    "amsfonts": "ams",
    "amssymb": "ams",
    "bm": "boldsymbol",
    "xcolor": "color",
}

# 行首的（可嵌套）引用标记，形如 "> "、">> "、"> > "，已被转义为 &gt;
_BLOCKQUOTE_MARKERS = re.compile(r"^(?: {0,3}&gt;[ \t]?)+", re.MULTILINE)
LANGUAGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
BG_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
# Permitted values for the data-* attributes on a trusted TikZ block: a JSON
# package map or a comma-separated library list. No braces, quotes, backslashes
# or backticks, so nothing can escape into TeX or the worker template.
ATTRIBUTE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_,.\-\[\]:{}\" ]*$")

# Python-Markdown's own block and inline scanners are super-linear in runs of
# bare markup characters (a single 100 KB line of `%` or `` ` `` costs seconds
# even with no plugin code involved). Real documents sit far below this bound:
# the plugin's own README is 0.17 and its densest example is 0.41. Reject only
# text that is essentially nothing but markup, before it reaches either stage.
_MARKUP_RATIO_LIMIT = 0.90
_MARKUP_RATIO_MIN_LENGTH = 20000
# C-level complement of `\w`, used to count alphanumeric characters without a
# per-character Python loop.
_NON_WORD = re.compile(r"\W")


class MarkdownConverter:
    """Markdown转换器"""

    def __init__(self, template_path: Path, typography: dict | None = None):
        self._template_path = template_path
        self._template_cache: str | None = None
        typography = typography if isinstance(typography, dict) else {}
        self._typography = {}
        for name, default, low, high in (
            ("body_font_size", 36, 16, 64),
            ("h1_scale", 1.55, 1, 3),
            ("h2_scale", 1.25, 1, 2.5),
            ("h3_scale", 1.10, 1, 2),
            ("line_height", 1.7, 1.2, 2.2),
        ):
            try:
                value = float(typography.get(name, default))
                if not math.isfinite(value):
                    value = default
            except (TypeError, ValueError):
                value = default
            self._typography[name] = max(low, min(high, value))

    def convert_to_html(self, md_text: str, bg_color: str = "#FDFBF0") -> str:
        """将Markdown转换为完整HTML"""
        # 预处理前先做退化输入检查：纯标记字符的长文本会让 Python-Markdown
        # 自身的扫描器退化成超线性，且没有任何渲染价值。
        if self._is_degenerate_markup(md_text):
            raise ValueError(
                "内容几乎全为 Markdown/LaTeX 标记字符，无法渲染；请提供正文文本"
            )
        # 预处理
        md_text = self._fix_tikz_comments(md_text)
        md_text = self._preprocess_markdown(md_text)

        # 保护插件内部生成的HTML、数学公式和代码块。
        # token 让占位符在本次转换内唯一：用户正文里的同名标记无法顶替真块。
        token = uuid.uuid4().hex
        md_text, code_blocks = self._extract_code_blocks(md_text, token)
        md_text, trusted_html_blocks = self._extract_trusted_html_blocks(md_text, token)
        declared_packages = set()

        def collect_packages(match):
            if match.group(1) is not None:
                raise ValueError("MathJax package options are not supported")
            names = {
                MATHJAX_PACKAGE_ALIASES.get(name.strip(), name.strip())
                for name in match.group(2).split(",")
            }
            unsupported = names - MATHJAX_PACKAGES
            if unsupported:
                raise ValueError(
                    "Unsupported MathJax packages: " + ", ".join(sorted(unsupported))
                )
            declared_packages.update(names)
            return ""

        md_text = re.sub(
            r"\\usepackage(?:\[([^\]]*)\])?\{([^{}]+)\}", collect_packages, md_text
        )
        md_text, math_blocks = self._extract_math_blocks(md_text, token)

        # Python-Markdown默认保留原始HTML，这里显式转义用户输入中的标签
        md_text = self._escape_raw_html(md_text)

        # Markdown转换
        html_body = markdown.markdown(
            md_text,
            extensions=[
                "fenced_code",
                "tables",
                "nl2br",
                "sane_lists",  # 更严格的列表解析
            ],
            extension_configs={
                "fenced_code": {"lang_prefix": "language-"},
            },
            output_format="html",  # 使用标准 HTML5 输出
        )

        # Select optional packages from math only, never from code examples.
        math_source = "\n".join(math_blocks)
        packages = declared_packages
        for declaration in re.findall(r"\\require\{([^{}]+)\}", math_source):
            packages.update(
                name.strip()
                for name in declaration.split(",")
                if name.strip() in MATHJAX_PACKAGES
            )
        if {"physics", "braket"} <= packages:
            raise ValueError(
                "physics and braket have incompatible syntax; select one package"
            )
        # Physics changes standard commands, so activate it only when requested.
        # Other common extensions keep MathJax's existing autoload behavior.

        # 还原插件内部HTML、数学公式和代码块
        html_body = self._restore_trusted_html_blocks(html_body, trusted_html_blocks, token)
        html_body = self._restore_math_blocks(html_body, math_blocks, token)
        html_body = self._restore_code_blocks(html_body, code_blocks, token)

        # 应用模板
        result = self._apply_template(html_body, bg_color)
        result = result.replace("{{MATH_REQUIRED}}", json.dumps(bool(math_blocks)))
        result = result.replace("{{MATH_PACKAGES}}", json.dumps(sorted(packages)))
        if not math_blocks:
            result = re.sub(
                r'<script src="[^" ]*/mathjax@[^" ]+"></script>', "", result
            )
        if not any('class="tikz-diagram"' in block for block in trusted_html_blocks):
            result = re.sub(
                r'<(?:script|link)[^>]+(?:src|href)="[^" ]*/@drgrice1/tikzjax[^" ]+"[^>]*>(?:</script>)?',
                "",
                result,
            )
        return result

    _END_TOKENS = ("\\end{tikzpicture}", "\\end{tikzcd}")

    @staticmethod
    def _is_degenerate_markup(text: str) -> bool:
        """Detect text that is almost entirely markup characters.

        Args:
            text: Preprocessed document text.

        Returns:
            True when the content is long enough to matter and almost no
            character is alphanumeric, so it carries no renderable prose.
        """
        if len(text) < _MARKUP_RATIO_MIN_LENGTH:
            return False
        # Counting with a generator costs ~1.7 ms on a 32 KB page, which is more
        # than everything else in this method combined. Removing the non-word
        # characters leaves the word characters, so its length *is* the count.
        # `\w` differs from `str.isalnum` only on the underscore, which is
        # immaterial against a 0.90 threshold.
        alphanumeric = len(_NON_WORD.sub("", text))
        return (len(text) - alphanumeric) / len(text) > _MARKUP_RATIO_LIMIT

    def _fix_tikz_comments(self, text: str) -> str:
        """修复TikZ代码中注释与\\end{tikzpicture}同行的问题

        原实现用 ``(%[^\\n]*?)\\end{...}``：当文本是一整行 ``%``（每个 ``%``
        都要扫到行尾才失败）时是 O(n^2)，100 KB 的一行注释即可冻结事件循环。
        注释行才需要修复，而注释必然止于行尾，因此按行处理即可；行内两个结束
        标记都要换行（原版两个正则分别处理，此处合并为一次遍历）。

        实现只用 ``str.split``/``partition``/``replace`` 这些 C 层操作：逐字符
        的 Python 循环同样线性，但每字符开销高数倍，在普通文档上反而比原版慢
        约 60 倍。
        """
        if "%" not in text or "\\end{tikz" not in text:
            return text
        # Splitting a large document that needs no fixing does cost more than
        # the original regex (a few tens of microseconds on a 15 KB page), and
        # is the one remaining regression here. A cheap probe was tried and
        # dropped: any pattern loose enough to catch an in-line comment
        # (`\draw ...; % note \end{tikzpicture}`) is loose enough to backtrack
        # over a whole-line run of `%`, reintroducing the quadratic behaviour
        # this function exists to remove. Trading a DoS for 0.06 ms is a bad
        # trade, so the cost is accepted and recorded here.
        lines = text.split("\n")
        for index, line in enumerate(lines):
            # 没有注释、或没有同行的结束标记时，整行无需改动
            if "%" not in line or "\\end{tikz" not in line:
                continue
            head, percent, tail = line.partition("%")
            # 原正则 `[^\n]*?` 是惰性的，每行只有第一个结束标记被换行；
            # 这里保持同一语义，避免安全修复顺带改变渲染结果。
            for token in self._END_TOKENS:
                position = tail.find(token)
                if position != -1:
                    tail = tail[:position] + "\n" + tail[position:]
                    break
            lines[index] = head + percent + tail
        return "\n".join(lines)

    def _preprocess_markdown(self, text: str) -> str:
        """预处理Markdown，自动修复常见格式问题"""
        # 转义字符处理：\\n -> 真实换行（保护LaTeX命令）
        text = re.sub(r"\\n(?![a-zA-Z])", "\n", text)

        lines = text.split("\n")
        result = []
        in_code_block = False
        # 当前列表中各层级的原始缩进（空格数），用于把 2/3 空格缩进归一为 4 空格
        list_indents: list[int] = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_code_block = not in_code_block
                result.append(line)
                continue

            if in_code_block:
                result.append(line)
                continue

            # 修复标题格式
            heading_match = re.match(r"^(#{1,6})([^#\s])", stripped)
            if heading_match:
                stripped = (
                    heading_match.group(1)
                    + " "
                    + stripped[len(heading_match.group(1)) :]
                )
                line = stripped

            # 在标题或列表项前添加空行
            is_heading = bool(re.match(r"^#{1,6}\s+", stripped))
            is_list_item = bool(re.match(r"^[-*]\s+", stripped)) or bool(
                re.match(r"^\d+\.\s+", stripped)
            )

            # Python-Markdown 只认 4 空格缩进的子列表，LLM 常输出 2 空格缩进，
            # 这里按缩进层级重排为 4 的倍数；非缩进的正文行结束当前列表。
            if is_list_item and not line.startswith("\t"):
                indent = len(line) - len(line.lstrip(" "))
                while list_indents and list_indents[-1] > indent:
                    list_indents.pop()
                if not list_indents or list_indents[-1] < indent:
                    list_indents.append(indent)
                line = " " * (4 * (len(list_indents) - 1)) + stripped
            elif stripped and not line.startswith((" ", "\t")):
                list_indents.clear()

            if (is_heading or is_list_item) and result:
                prev_line = result[-1].strip()
                prev_is_list = bool(re.match(r"^[-*]\s+", prev_line)) or bool(
                    re.match(r"^\d+\.\s+", prev_line)
                )
                if prev_line and (is_heading or not prev_is_list):
                    result.append("")

            result.append(line)

        return "\n".join(result)

    def _extract_math_blocks(self, text: str, token: str) -> tuple[str, list[str]]:
        """提取数学公式块（线性扫描，避免未闭合定界符触发二次复杂度）"""
        blocks = []

        def substitute(index, block):
            blocks.append(block)
            return _block_placeholder(token, "MATH", index)

        return substitute_spans(text, scan_math_blocks(text), substitute), blocks

    def _extract_code_blocks(self, text: str, token: str) -> tuple[str, list[str]]:
        """提取代码块（线性扫描，避免未闭合定界符触发二次复杂度）"""
        blocks = []

        def substitute(index, block):
            blocks.append(block)
            return _block_placeholder(token, "CODE", index)

        return substitute_spans(text, scan_fenced_code(text), substitute), blocks

    def _extract_trusted_html_blocks(
        self, text: str, token: str
    ) -> tuple[str, list[str]]:
        """提取插件转换器生成的受控HTML块（线性扫描，见 iter_trusted_html_spans）"""
        blocks: list[str] = []
        parts: list[str] = []
        cursor = 0
        for start, end in iter_trusted_html_spans(text):
            parts.append(text[cursor:start])
            block = text[start:end]
            if self._is_trusted_html_block(block):
                parts.append(_block_placeholder(token, "HTML", len(blocks)))
                blocks.append(block)
            else:
                parts.append(self._escape_raw_html(block))
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts), blocks

    def _tokenize_attributes(self, source: str) -> dict[str, str] | None:
        """严格解析标签属性串；出现任何未白名单化的内容即返回 None。

        HTML 允许 ``/`` 作为属性分隔符（``data-x='1' /onerror=alert(1)``），
        因此不能只按空白切分再回头找残留。这里逐字符消费，要求每个属性都
        形如 ``name="value"`` / ``name='value'`` 且名字在白名单内，任何多余
        字符（含 ``/``）都判定为不可信。
        """
        allowed = {
            "data-tex-packages",
            "data-tikz-libraries",
            "data-disable-cache",
        }
        attributes: dict[str, str] = {}
        cursor, length = 0, len(source)
        while True:
            while cursor < length and source[cursor].isspace():
                cursor += 1
            if cursor >= length:
                return attributes
            start = cursor
            while cursor < length and (
                source[cursor].isalnum() or source[cursor] in "-_:."
            ):
                cursor += 1
            if cursor == start:
                return None  # 非属性字符（含 '/'、'<' 等）
            name = source[start:cursor].lower()
            if name not in allowed:
                return None
            while cursor < length and source[cursor].isspace():
                cursor += 1
            if cursor >= length or source[cursor] != "=":
                return None
            cursor += 1
            while cursor < length and source[cursor].isspace():
                cursor += 1
            if cursor >= length or source[cursor] not in "\"'":
                return None
            quote = source[cursor]
            cursor += 1
            value_start = cursor
            while cursor < length and source[cursor] != quote:
                cursor += 1
            if cursor >= length:
                return None
            value = source[value_start:cursor]
            # A permitted name does not make an arbitrary value safe. These
            # values are interpolated into TeX preamble (and, for the WASM
            # path, into the worker's \usetikzlibrary{} template), so keep them
            # to what a package or library list can contain.
            if not ATTRIBUTE_VALUE_PATTERN.fullmatch(value):
                return None
            attributes[name] = value
            cursor += 1

    def _is_trusted_html_block(self, block: str) -> bool:
        """校验受控HTML块，避免用户闭合标签后注入脚本"""
        tikz_match = re.fullmatch(
            r'<div class="tikz-diagram"><script type="text/tikz"([^>]*)>\n([\s\S]*?)\n</script></div>',
            block,
        )
        if tikz_match:
            # 内容中不允许出现 script 标签(防闭合注入)
            if re.search(r"</?script", tikz_match.group(2), re.IGNORECASE):
                return False
            return self._tokenize_attributes(tikz_match.group(1)) is not None

        mermaid_match = re.fullmatch(
            r'<pre class="mermaid">\n([\s\S]*?)\n</pre>', block
        )
        if mermaid_match:
            return "<" not in mermaid_match.group(1) and ">" not in mermaid_match.group(
                1
            )

        return bool(re.fullmatch(r'<div class="error">[^<>]*</div>', block))

    def _escape_raw_html(self, text: str) -> str:
        """转义用户输入中的HTML标签字符

        行首的 ``>`` 是 Markdown 引用标记，转义后引用块会变成字面量 ``&gt;``，
        因此转义后再把行首标记还原；单独的 ``>`` 无法构成标签，还原是安全的。
        """
        escaped = text.replace("<", "&lt;").replace(">", "&gt;")
        return _BLOCKQUOTE_MARKERS.sub(
            lambda m: m.group(0).replace("&gt;", ">"), escaped
        )

    def _restore_trusted_html_blocks(
        self, html: str, blocks: list[str], token: str
    ) -> str:
        """还原插件内部HTML块，并移除Markdown自动生成的段落包裹。

        单次正则遍历，而非逐块 ``str.replace``：后者是 O(块数 × 文档长度)，
        上万块时还原阶段自己就能跑成秒级。
        """
        if not blocks:
            return html
        pattern = _placeholder_pattern(token, "HTML", paragraph_wrapped=True)
        return pattern.sub(lambda m: blocks[int(m.group("index"))], html)

    def _restore_math_blocks(self, html: str, blocks: list[str], token: str) -> str:
        """还原数学公式块"""
        if not blocks:
            return html
        return _placeholder_pattern(token, "MATH").sub(
            lambda m: html_lib.escape(blocks[int(m.group("index"))], quote=False),
            html,
        )

    def _restore_code_blocks(self, html: str, blocks: list[str], token: str) -> str:
        """还原代码块"""
        if not blocks:
            return html
        rendered = [self._render_code_block(block) for block in blocks]
        return _placeholder_pattern(token, "CODE").sub(
            lambda m: rendered[int(m.group("index"))], html
        )

    def _render_code_block(self, block: str) -> str:
        """把一个代码块源码渲染为 ``<pre><code>`` HTML。"""
        fence = re.fullmatch(r"(`{3,}|~{3,})([^\n]*)\n([\s\S]*?)\1", block)
        if fence is None:
            return "<code>" + html_lib.escape(block.strip("`")) + "</code>"
        language = self._sanitize_language(fence.group(2).strip())
        lang_class = f' class="language-{language}"' if language else ""
        escaped_code = html_lib.escape(fence.group(3))
        return f"<pre><code{lang_class}>{escaped_code}</code></pre>"

    def _sanitize_language(self, language: str) -> str:
        """仅保留安全的代码语言标识"""
        return language if LANGUAGE_PATTERN.fullmatch(language) else ""

    def _apply_template(self, html_body: str, bg_color: str) -> str:
        """应用HTML模板"""
        if self._template_cache is None:
            with open(self._template_path, encoding="utf-8") as f:
                self._template_cache = f.read()

        safe_bg_color = bg_color if BG_COLOR_PATTERN.fullmatch(bg_color) else "#FDFBF0"
        template = self._template_cache
        for name, value in self._typography.items():
            variable = "--" + name.replace("_", "-")
            unit = "px" if name == "body_font_size" else ""
            template = re.sub(
                re.escape(variable) + r":\s*[^;]+;",
                f"{variable}: {value:g}{unit};",
                template,
                count=1,
            )
        full_html = template.replace("{{CONTENT}}", html_body)
        # Match any existing --bg-color value (template default may differ in case)
        full_html = re.sub(
            r"--bg-color:\s*#[0-9a-fA-F]{3,8}\s*;",
            f"--bg-color: {safe_bg_color};",
            full_html,
            count=1,
        )
        return full_html
