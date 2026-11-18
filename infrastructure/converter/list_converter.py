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
        lines = text.split("\n")
        result = []
        # 栈元素：int 表示 enumerate 当前计数，None 表示 itemize
        stack: list[int | None] = []

        offset = 0
        span_index = 0
        for line in lines:
            stripped = line.lstrip()
            first = offset + len(line) - len(stripped)
            offset += len(line) + 1
            while span_index < len(spans) and spans[span_index][1] <= first:
                span_index += 1
            if span_index < len(spans) and spans[span_index][0] <= first:
                result.append(line)
                continue

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

            item_match = re.match(r"^\\item(?![A-Za-z])\s*(.*)$", stripped)
            if item_match:
                content = item_match.group(1)
                indent = "    " * max(0, len(stack) - 1)
                if stack and stack[-1] is not None:
                    stack[-1] += 1
                    result.append(f"{indent}{stack[-1]}. {content}")
                else:
                    result.append(f"{indent}- {content}")
            else:
                result.append(line)

        return "\n".join(result)

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
