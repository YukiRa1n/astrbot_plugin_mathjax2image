"""
LaTeX预处理器
组合多个转换器，预处理LaTeX文本
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .list_converter import ListConverter
    from .mermaid_converter import MermaidConverter
    from .table_converter import TableConverter
    from .tikz_converter import TikzConverter


class LatexPreprocessor:
    """LaTeX预处理器 - 组合多个转换器"""

    def __init__(
        self,
        tikz_converter: "TikzConverter",
        list_converter: "ListConverter",
        table_converter: "TableConverter",
        mermaid_converter: "MermaidConverter" = None,
    ):
        self._tikz_converter = tikz_converter
        self._list_converter = list_converter
        self._table_converter = table_converter
        self._mermaid_converter = mermaid_converter

    def preprocess(self, text: str) -> str:
        """预处理LaTeX文本"""
        # 1. 转换LaTeX文本命令为Markdown
        text = self._convert_text_commands(text)

        # 2. 处理集合表示法
        text = self._fix_set_notation(text)

        # 3. 处理LaTeX列表
        text = self._list_converter.convert(text)

        # 4. 处理LaTeX表格
        text = self._table_converter.convert(text)

        # 5. 处理TikZ绘图环境
        text = self._tikz_converter.convert(text)

        # 6. 处理Mermaid图表
        if self._mermaid_converter:
            text = self._mermaid_converter.convert(text)

        return text

    def _convert_text_commands(self, text: str) -> str:
        """将LaTeX文本命令转换为Markdown格式"""
        pattern = (
            r"(?P<protected>```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`"
            r"|\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}"
            r"|\\begin\{tikzcd\}[\s\S]*?\\end\{tikzcd\}"
            r"|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)"
            r"|\$\$[\s\S]*?\$\$|\$[^$\n]*\$"
            r"|\\begin\{(?P<env>align\*?|equation\*?|gather\*?)\}[\s\S]*?\\end\{(?P=env)\})"
            r"|\\(?P<command>textbf|textit|emph)\{(?P<body>[^{}]*)\}"
        )

        def replace(match):
            if match.group("protected") is not None:
                return match.group(0)
            marker = "**" if match.group("command") == "textbf" else "*"
            return marker + match.group("body") + marker

        return re.sub(pattern, replace, text)

    def _fix_set_notation(self, text: str) -> str:
        """修复集合表示法 {... \\mid ...}

        使用限制长度的非贪婪匹配，防止灾难性回溯（ReDoS）
        """
        # 限制每个部分最多 200 字符，防止正则表达式引擎指数级回溯
        return re.sub(
            r"(?<!\\)\{([^{}]{0,200}?\\mid[^{}]{0,200}?)\}",
            r"\\lbrace \1\\rbrace ",
            text,
        )
