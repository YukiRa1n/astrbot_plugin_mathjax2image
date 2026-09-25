"""
LaTeX表格转换器
将LaTeX tabular表格转换为Markdown格式
"""

import re

from ...utils.linear_scan import find_pairs, substitute_spans


class TableConverter:
    """LaTeX表格转换器"""

    _TABULAR_OPEN = "\\begin{tabular}"
    _TABULAR_CLOSE = "\\end{tabular}"

    def convert(self, text: str) -> str:
        """将LaTeX表格转换为Markdown格式"""
        # 移除table环境包装
        text = re.sub(r"\\begin\{table\}(\[.*?\])?", "", text)
        text = re.sub(r"\\end\{table\}", "", text)
        text = re.sub(r"\\centering", "", text)
        text = re.sub(r"\\caption\{.*?\}", "", text)

        # 处理tabular环境。配对交给线性扫描器：惰性正则
        # `\\begin\{tabular\}([\s\S]*?)\\end\{tabular\}` 在 \end{tabular} 缺失时
        # 每个起点都要扫到文末，O(n^2) 会让 100 KB 的输入耗时约 4.7 秒。
        spans = find_pairs(text, self._TABULAR_OPEN, self._TABULAR_CLOSE)
        return substitute_spans(text, spans, self._convert_tabular_span)

    def _convert_tabular_span(self, _index: int, block: str) -> str:
        """转换一个已配对好的 tabular 环境（含两端标记）。"""
        body = block[len(self._TABULAR_OPEN) : -len(self._TABULAR_CLOSE)]
        return self._convert_tabular(self._strip_column_spec(body))

    @staticmethod
    def _strip_column_spec(body: str) -> str:
        """移除起始的列格式声明，保留 `>{\\bfseries}l` 这类嵌套声明。

        旧的 `\\{[^}]*\\}` 只吃到一个 `}`，`>{\\bfseries}l` 会被截断，
        残留的 `}l` 落进表格内容里。
        """
        if not body.startswith("{"):
            return body
        depth = 0
        for index, char in enumerate(body):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return body[index + 1 :]
        return body  # 未闭合：按普通内容处理，不猜

    def _convert_tabular(self, content: str) -> str:
        """转换tabular内容"""
        # 移除\\hline
        content = re.sub(r"\\hline\s*", "", content)

        # 按\\\\分割行
        rows = re.split(r"\\\\\s*", content)
        md_rows = []

        for row in rows:
            row = row.strip()
            if not row:
                continue

            # 按&分割列
            cells = [c.strip() for c in row.split("&")]
            md_rows.append("| " + " | ".join(cells) + " |")

            # 首个真正输出的行才是表头。旧实现用原始切分的下标 i == 0 判断，
            # 内容以 `\\\\` 开头时首行为空会被跳过，表头就拿不到分隔行，
            # 整个 Markdown 表格失效。
            if len(md_rows) == 1:
                md_rows.append("|" + "|".join(["---"] * len(cells)) + "|")

        return "\n".join(md_rows)
