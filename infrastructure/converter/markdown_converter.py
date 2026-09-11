"""
Markdown转换器
将Markdown转换为完整HTML
"""

import html as html_lib
import json
import math
import re
from pathlib import Path

import markdown

from ...utils.linear_scan import scan_fenced_code, scan_math_blocks, substitute_spans

TRUSTED_HTML_PLACEHOLDER = "TRUSTEDHTML{}TRUSTEDHTML"
# 注意：转换器生成的 TikZ 块可能带 data-tex-packages 属性
# （<script type="text/tikz" data-tex-packages='...'>），正则必须允许
# 任意属性，否则该块不被识别为受信 HTML 而被整体转义成文本。
TRUSTED_HTML_PATTERN = re.compile(
    r'<div class="tikz-diagram"><script type="text/tikz"[^>]*>\n[\s\S]*?\n</script></div>'
    r'|<pre class="mermaid">\n[\s\S]*?\n</pre>'
    r'|<div class="error">[^<>]*</div>'
)
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

        # 保护插件内部生成的HTML、数学公式和代码块
        md_text, code_blocks = self._extract_code_blocks(md_text)
        md_text, trusted_html_blocks = self._extract_trusted_html_blocks(md_text)
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
        md_text, math_blocks = self._extract_math_blocks(md_text)

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
        html_body = self._restore_trusted_html_blocks(html_body, trusted_html_blocks)
        html_body = self._restore_math_blocks(html_body, math_blocks)
        html_body = self._restore_code_blocks(html_body, code_blocks)

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
        alphanumeric = sum(1 for char in text if char.isalnum())
        return (len(text) - alphanumeric) / len(text) > _MARKUP_RATIO_LIMIT

    def _fix_tikz_comments(self, text: str) -> str:
        """修复TikZ代码中注释与\\end{tikzpicture}同行的问题

        原实现用 ``(%[^\\n]*?)\\end{...}``：当文本是一整行 ``%``（每个 ``%``
        都要扫到行尾才失败）时是 O(n^2)，100 KB 的一行注释即可冻结事件循环。
        这里按行单遍扫描，只在一行确实含注释时才回溯该行。
        """
        if "%" not in text or "\\end{tikz" not in text:
            return text
        out: list[str] = []
        line_has_comment = False
        index, length = 0, len(text)
        while index < length:
            char = text[index]
            if char == "\n":
                line_has_comment = False
                out.append(char)
                index += 1
                continue
            if char == "%":
                if index == 0 or text[index - 1] != "\\":
                    line_has_comment = True
                out.append(char)
                index += 1
                continue
            if char == "\\" and line_has_comment:
                for token in self._END_TOKENS:
                    if text.startswith(token, index):
                        out.append("\n")
                        out.append(token)
                        index += len(token)
                        break
                else:
                    out.append(char)
                    index += 1
                continue
            out.append(char)
            index += 1
        return "".join(out)

    def _preprocess_markdown(self, text: str) -> str:
        """预处理Markdown，自动修复常见格式问题"""
        # 转义字符处理：\\n -> 真实换行（保护LaTeX命令）
        text = re.sub(r"\\n(?![a-zA-Z])", "\n", text)

        lines = text.split("\n")
        result = []
        in_code_block = False

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

            if (is_heading or is_list_item) and result:
                prev_line = result[-1].strip()
                prev_is_list = bool(re.match(r"^[-*]\s+", prev_line)) or bool(
                    re.match(r"^\d+\.\s+", prev_line)
                )
                if prev_line and (is_heading or not prev_is_list):
                    result.append("")

            result.append(line)

        return "\n".join(result)

    def _extract_math_blocks(self, text: str) -> tuple[str, list[str]]:
        """提取数学公式块（线性扫描，避免未闭合定界符触发二次复杂度）"""
        blocks = []

        def substitute(index, block):
            blocks.append(block)
            return f"MATHBLOCK{index}MATHBLOCK"

        return substitute_spans(text, scan_math_blocks(text), substitute), blocks

    def _extract_code_blocks(self, text: str) -> tuple[str, list[str]]:
        """提取代码块（线性扫描，避免未闭合定界符触发二次复杂度）"""
        blocks = []

        def substitute(index, block):
            blocks.append(block)
            return f"CODEBLOCK{index}CODEBLOCK"

        return substitute_spans(text, scan_fenced_code(text), substitute), blocks

    def _extract_trusted_html_blocks(self, text: str) -> tuple[str, list[str]]:
        """提取插件转换器生成的受控HTML块"""
        blocks = []

        def substitute(match):
            block = match.group(0)
            if not self._is_trusted_html_block(block):
                return self._escape_raw_html(block)

            placeholder = TRUSTED_HTML_PLACEHOLDER.format(len(blocks))
            blocks.append(block)
            return placeholder

        return TRUSTED_HTML_PATTERN.sub(substitute, text), blocks

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
        """转义用户输入中的HTML标签字符"""
        return text.replace("<", "&lt;").replace(">", "&gt;")

    def _restore_trusted_html_blocks(self, html: str, blocks: list[str]) -> str:
        """还原插件内部HTML块，并移除Markdown自动生成的段落包裹"""
        for i, block in enumerate(blocks):
            placeholder = TRUSTED_HTML_PLACEHOLDER.format(i)
            html = html.replace(f"<p>{placeholder}</p>", block, 1)
            html = html.replace(placeholder, block, 1)
        return html

    def _restore_math_blocks(self, html: str, blocks: list[str]) -> str:
        """还原数学公式块"""
        for i, block in enumerate(blocks):
            escaped_block = html_lib.escape(block, quote=False)
            html = html.replace(f"MATHBLOCK{i}MATHBLOCK", escaped_block, 1)
        return html

    def _restore_code_blocks(self, html: str, blocks: list[str]) -> str:
        """还原代码块"""
        for i, block in enumerate(blocks):
            fence = re.fullmatch(r"(`{3,}|~{3,})([^\n]*)\n([\s\S]*?)\1", block)
            if fence is None:
                code_html = "<code>" + html_lib.escape(block.strip("`")) + "</code>"
                html = html.replace(f"CODEBLOCK{i}CODEBLOCK", code_html, 1)
                continue
            language = fence.group(2).strip()
            code_content = fence.group(3)

            language = self._sanitize_language(language)
            lang_class = f' class="language-{language}"' if language else ""
            escaped_code = html_lib.escape(code_content)
            code_html = f"<pre><code{lang_class}>{escaped_code}</code></pre>"
            html = html.replace(f"CODEBLOCK{i}CODEBLOCK", code_html, 1)
        return html

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
