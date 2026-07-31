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
        item_counter = 0
        in_enumerate = False

        for line in lines:
            stripped = line.strip()

            if stripped.startswith(r"\begin{enumerate}"):
                in_enumerate = True
                item_counter = 0
                continue

            if stripped.startswith(r"\end{enumerate}"):
                in_enumerate = False
                continue

            if stripped.startswith(r"\begin{itemize}"):
                continue

            if stripped.startswith(r"\end{itemize}"):
                continue

            if stripped.startswith(r"\item"):
                if in_enumerate:
                    item_counter += 1
                    content = re.sub(r"^\\item\s*", "", stripped)
                    result.append(f"{item_counter}. {content}")
                else:
                    content = re.sub(r"^\\item\s*", "", stripped)
                    result.append(f"- {content}")
            else:
                result.append(line)

        return "\n".join(result)
