"""
LaTeX预处理器
组合多个转换器，预处理LaTeX文本
"""
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tikz_converter import TikzConverter
    from .list_converter import ListConverter
    from .table_converter import TableConverter
    from .mermaid_converter import MermaidConverter


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
        # \\textbf{...} -> **...**
        text = re.sub(r'\\textbf\{([\s\S]*?)\}', lambda m: f"**{m.group(1)}**", text)
        # \\textit{...} -> *...*
        text = re.sub(r'\\textit\{([\s\S]*?)\}', lambda m: f"*{m.group(1)}*", text)
        # \\emph{...} -> *...*
        text = re.sub(r'\\emph\{([\s\S]*?)\}', lambda m: f"*{m.group(1)}*", text)
        return text

    def _fix_set_notation(self, text: str) -> str:
        """修复集合表示法 {... \\mid ...}"""
        return re.sub(r'(?<!\\)\{([^{}]*\\mid[^{}]*)\}', r'\\lbrace \1\\rbrace ', text)
