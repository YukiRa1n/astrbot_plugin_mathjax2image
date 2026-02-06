"""
Markdown转换器
将Markdown转换为完整HTML
"""

import re
from pathlib import Path

import markdown


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

        # 保护数学公式和代码块
        md_text, math_blocks = self._extract_math_blocks(md_text)
        md_text, code_blocks = self._extract_code_blocks(md_text)

        # Markdown转换
        html_body = markdown.markdown(
            md_text, extensions=["fenced_code", "tables", "nl2br"]
        )

        # 还原数学公式和代码块
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

    def _restore_math_blocks(self, html: str, blocks: list[str]) -> str:
        """还原数学公式块"""
        for i, block in enumerate(blocks):
            html = html.replace(f"MATHBLOCK{i}MATHBLOCK", block)
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

            lang_class = f' class="language-{language}"' if language else ""
            code_html = f"<pre><code{lang_class}>{code_content}</code></pre>"
            html = html.replace(f"CODEBLOCK{i}CODEBLOCK", code_html)
        return html

    def _apply_template(self, html_body: str, bg_color: str) -> str:
        """应用HTML模板"""
        if self._template_cache is None:
            with open(self._template_path, "r", encoding="utf-8") as f:
                self._template_cache = f.read()

        full_html = self._template_cache.replace("{{CONTENT}}", html_body)
        full_html = full_html.replace(
            "--bg-color: #FDFBF0;", f"--bg-color: {bg_color};"
        )
        return full_html
