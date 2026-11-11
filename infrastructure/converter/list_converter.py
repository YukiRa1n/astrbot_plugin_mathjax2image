"""
LaTeX列表转换器
将LaTeX enumerate/itemize环境转换为Markdown格式
"""

import re


class ListConverter:
    """LaTeX列表转换器"""

    def convert(self, text: str) -> str:
        """将LaTeX列表环境转换为Markdown格式"""
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
