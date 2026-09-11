"""
LaTeX预处理器
组合多个转换器，预处理LaTeX文本
"""

import re
from typing import TYPE_CHECKING

from ...utils.linear_scan import find_pairs, scan_fenced_code

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

    #: Ordered ``(open, close, allow_newline)`` protected ranges. Scanned in one
    #: linear pass instead of a lazy-regex alternation: an unmatched opening
    #: delimiter used to cost O(n) at every occurrence, so a message made only
    #: of ``\begin{tikzpicture}`` (or backticks) burned O(n^2) and froze the loop.
    _PROTECTED_PAIRS = (
        ("$$", "$$", True),
        ("\\[", "\\]", True),
        ("\\(", "\\)", True),
        ("$", "$", False),
        ("\\begin{tikzpicture}", "\\end{tikzpicture}", True),
        ("\\begin{tikzcd}", "\\end{tikzcd}", True),
        ("\\begin{align*}", "\\end{align*}", True),
        ("\\begin{align}", "\\end{align}", True),
        ("\\begin{equation*}", "\\end{equation*}", True),
        ("\\begin{equation}", "\\end{equation}", True),
        ("\\begin{gather*}", "\\end{gather*}", True),
        ("\\begin{gather}", "\\end{gather}", True),
    )
    _MARKER_PAIRS = (("\\[", "\\]"), ("\\(", "\\)"))
    _TEXT_COMMAND = re.compile(r"\\(textbf|textit|emph)\{([^{}]*)\}")

    def _protected_ranges(self, text: str) -> list[tuple[int, int]]:
        """Linear scan of protected (non-transformable) ranges."""
        spans: list[tuple[int, int]] = list(scan_fenced_code(text))
        for open_token, close_token, allow_newline in self._PROTECTED_PAIRS:
            spans.extend(
                find_pairs(text, open_token, close_token, allow_newline=allow_newline)
            )
        spans.sort()
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _convert_text_commands(self, text: str) -> str:
        """将LaTeX文本命令转换为Markdown格式"""
        ranges = self._protected_ranges(text)
        pieces: list[str] = []
        position = 0
        for start, end in ranges:
            pieces.append(self._transform_segment(text[position:start]))
            pieces.append(text[start:end])
            position = end
        pieces.append(self._transform_segment(text[position:]))
        return "".join(pieces)

    def _transform_segment(self, segment: str) -> str:
        """Apply text-command rewrites and set-notation only outside math ranges."""
        if not segment:
            return segment
        if segment.startswith(("\\[", "\\(")):
            return segment
        segment = self._TEXT_COMMAND.sub(self._replace_text_command, segment)
        return self._fix_set_notation(segment)

    @staticmethod
    def _replace_text_command(match: re.Match) -> str:
        marker = "**" if match.group(1) == "textbf" else "*"
        return marker + match.group(2) + marker

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
