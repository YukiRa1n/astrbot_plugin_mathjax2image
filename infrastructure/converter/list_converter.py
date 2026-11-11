"""
LaTeX列表转换器
将LaTeX enumerate/itemize环境转换为Markdown格式
"""

import re

from ...utils.linear_scan import find_pairs, scan_fenced_code


class ListConverter:
    """LaTeX列表转换器"""

    _PROTECTED_ENVS = (
        ("\\begin{tikzpicture}", "\\end{tikzpicture}"),
        ("\\begin{tikzcd}", "\\end{tikzcd}"),
        ('<div class="tikz-diagram">', "</div>"),
    )

    def convert(self, text: str) -> str:
        """将LaTeX列表环境转换为Markdown格式（跳过代码围栏与TikZ环境）"""
        if (
            "\\item" not in text
            and "\\begin{enumerate}" not in text
            and "\\begin{itemize}" not in text
        ):
            return text
        spans = self._protected_spans(text)
        if not spans:
            return self._convert_segment(text)
        pieces: list[str] = []
        position = 0
        for start, end in spans:
            pieces.append(self._convert_segment(text[position:start]))
            pieces.append(text[start:end])
            position = end
        pieces.append(self._convert_segment(text[position:]))
        return "".join(pieces)

    @classmethod
    def _protected_spans(cls, text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = list(scan_fenced_code(text))
        for open_token, close_token in cls._PROTECTED_ENVS:
            if open_token in text:
                spans.extend(find_pairs(text, open_token, close_token))
        if not spans:
            return spans
        spans.sort()
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _convert_segment(self, text: str) -> str:
        """转换非代码、非TikZ区间内的LaTeX列表"""
        lines = text.split("\n")
        result = []
        # 栈元素：int 表示 enumerate 当前计数，None 表示 itemize
        stack: list[int | None] = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith(r"\begin{enumerate}"):
                stack.append(0)
                continue

            if stripped.startswith(r"\end{enumerate}"):
                if stack:
                    stack.pop()
                continue

            if stripped.startswith(r"\begin{itemize}"):
                stack.append(None)
                continue

            if stripped.startswith(r"\end{itemize}"):
                if stack:
                    stack.pop()
                continue

            if stripped.startswith(r"\item"):
                content = re.sub(r"^\\item\s*", "", stripped)
                indent = "    " * max(0, len(stack) - 1)
                if stack and stack[-1] is not None:
                    stack[-1] += 1
                    result.append(f"{indent}{stack[-1]}. {content}")
                else:
                    result.append(f"{indent}- {content}")
            else:
                result.append(line)

        return "\n".join(result)
