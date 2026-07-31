"""
Markdown转换器
将Markdown转换为完整HTML
"""

import html as html_lib
import re
from pathlib import Path

import markdown

TRUSTED_HTML_PLACEHOLDER = "TRUSTEDHTML{}TRUSTEDHTML"
TRUSTED_HTML_PATTERN = re.compile(
    r'<div class="tikz-diagram"><script type="text/tikz">\n[\s\S]*?\n</script></div>'
    r'|<pre class="mermaid">\n[\s\S]*?\n</pre>'
    r'|<div class="error">[^<>]*</div>'
)
LANGUAGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
BG_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class MarkdownConverter:
    """Markdown转换器"""

    def __init__(self, template_path: Path):
        self._template_path = template_path
        self._template_cache: str | None = None

    def convert_to_html(self, md_text: str, bg_color: str = "#FDFBF0") -> str:
        """将Markdown转换为完整HTML"""
        # 预处理
        md_text = self._fix_tikz_comments(md_text)
        md_text = self._preprocess_markdown(md_text)

        # 保护插件内部生成的HTML、数学公式和代码块
        md_text, code_blocks = self._extract_code_blocks(md_text)
        md_text, trusted_html_blocks = self._extract_trusted_html_blocks(md_text)
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

        # 还原插件内部HTML、数学公式和代码块
        html_body = self._restore_trusted_html_blocks(html_body, trusted_html_blocks)
        html_body = self._restore_math_blocks(html_body, math_blocks)
        html_body = self._restore_code_blocks(html_body, code_blocks)

        # 应用模板
        return self._apply_template(html_body, bg_color)

    def _fix_tikz_comments(self, text: str) -> str:
        """修复TikZ代码中注释与\\end{tikzpicture}同行的问题"""
        text = re.sub(
            r"(%[^\n]*?)\\end\{tikzpicture\}", r"\1\n\\end{tikzpicture}", text
        )
        text = re.sub(r"(%[^\n]*?)\\end\{tikzcd\}", r"\1\n\\end{tikzcd}", text)
        return text

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
        """提取数学公式块"""
        blocks = []

        def substitute(match):
            placeholder = f"MATHBLOCK{len(blocks)}MATHBLOCK"
            blocks.append(match.group(0))
            return placeholder

        text = re.sub(r"\\\[[\s\S]*?\\\]", substitute, text)
        text = re.sub(r"\\\([\s\S]*?\\\)", substitute, text)
        text = re.sub(r"\$\$.*?\$\$", substitute, text, flags=re.DOTALL)
        text = re.sub(r"\$.*?\$", substitute, text)

        return text, blocks

    def _extract_code_blocks(self, text: str) -> tuple[str, list[str]]:
        """提取代码块"""
        blocks = []

        def substitute(match):
            placeholder = f"CODEBLOCK{len(blocks)}CODEBLOCK"
            blocks.append(match.group(0))
            return placeholder

        text = re.sub(r"```[\s\S]*?```", substitute, text)
        return text, blocks

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

    def _is_trusted_html_block(self, block: str) -> bool:
        """校验受控HTML块，避免用户闭合标签后注入脚本"""
        tikz_match = re.fullmatch(
            r'<div class="tikz-diagram"><script type="text/tikz">\n([\s\S]*?)\n</script></div>',
            block,
        )
        if tikz_match:
            return not re.search(r"</?script", tikz_match.group(1), re.IGNORECASE)

        mermaid_match = re.fullmatch(r'<pre class="mermaid">\n([\s\S]*?)\n</pre>', block)
        if mermaid_match:
            return "<" not in mermaid_match.group(1) and ">" not in mermaid_match.group(1)

        return bool(re.fullmatch(r'<div class="error">[^<>]*</div>', block))

    def _escape_raw_html(self, text: str) -> str:
        """转义用户输入中的HTML标签字符"""
        return text.replace("<", "&lt;").replace(">", "&gt;")

    def _restore_trusted_html_blocks(self, html: str, blocks: list[str]) -> str:
        """还原插件内部HTML块，并移除Markdown自动生成的段落包裹"""
        for i, block in enumerate(blocks):
            placeholder = TRUSTED_HTML_PLACEHOLDER.format(i)
            html = html.replace(f"<p>{placeholder}</p>", block)
            html = html.replace(placeholder, block)
        return html

    def _restore_math_blocks(self, html: str, blocks: list[str]) -> str:
        """还原数学公式块"""
        for i, block in enumerate(blocks):
            escaped_block = html_lib.escape(block, quote=False)
            html = html.replace(f"MATHBLOCK{i}MATHBLOCK", escaped_block)
        return html

    def _restore_code_blocks(self, html: str, blocks: list[str]) -> str:
        """还原代码块"""
        for i, block in enumerate(blocks):
            content = block.strip("`")
            if "\n" in content:
                parts = content.split("\n", 1)
                language = parts[0].strip()
                code_content = parts[1] if len(parts) > 1 else ""
            else:
                language = ""
                code_content = content

            language = self._sanitize_language(language)
            lang_class = f' class="language-{language}"' if language else ""
            escaped_code = html_lib.escape(code_content)
            code_html = f"<pre><code{lang_class}>{escaped_code}</code></pre>"
            html = html.replace(f"CODEBLOCK{i}CODEBLOCK", code_html)
        return html

    def _sanitize_language(self, language: str) -> str:
        """仅保留安全的代码语言标识"""
        return language if LANGUAGE_PATTERN.fullmatch(language) else ""

    def _apply_template(self, html_body: str, bg_color: str) -> str:
        """应用HTML模板"""
        if self._template_cache is None:
            with open(self._template_path, "r", encoding="utf-8") as f:
                self._template_cache = f.read()

        safe_bg_color = bg_color if BG_COLOR_PATTERN.fullmatch(bg_color) else "#FDFBF0"
        full_html = self._template_cache.replace("{{CONTENT}}", html_body)
        # Match any existing --bg-color value (template default may differ in case)
        full_html = re.sub(
            r"--bg-color:\s*#[0-9a-fA-F]{3,8}\s*;",
            f"--bg-color: {safe_bg_color};",
            full_html,
            count=1,
        )
        return full_html
