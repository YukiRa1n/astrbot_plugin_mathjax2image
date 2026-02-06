"""
LaTeX列表转换器
将LaTeX enumerate/itemize环境转换为Markdown格式
"""

import re


class ListConverter:
    """LaTeX列表转换器"""

    def convert(self, text: str) -> str:
        """将LaTeX列表环境转换为Markdown格式"""
        # 移除enumerate环境标记
        text = re.sub(r"\\begin\{enumerate\}(\[.*?\])?", "", text)
        text = re.sub(r"\\end\{enumerate\}", "", text)

        # 移除itemize环境标记
        text = re.sub(r"\\begin\{itemize\}", "", text)
        text = re.sub(r"\\end\{itemize\}", "", text)

        # 转换\\item为Markdown列表项
        return self._convert_items(text)

    def _convert_items(self, text: str) -> str:
        """转换\\item为Markdown列表项"""
        lines = text.split("\n")
        result = []
        item_counter = 0

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(r"\item"):
                item_counter += 1
                # 移除\\item并添加编号
                content = re.sub(r"^\\item\s*", "", stripped)
                result.append(f"{item_counter}. {content}")
            else:
                result.append(line)

        return "\n".join(result)
