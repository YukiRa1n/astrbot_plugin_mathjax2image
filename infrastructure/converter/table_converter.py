"""
LaTeX表格转换器
将LaTeX tabular表格转换为Markdown格式
"""

import re

from ...utils.linear_scan import find_pairs, substitute_spans

#: 参数（`\begin{table}[...]` 选项、`\caption{...}`）允许的最大长度。
#: 限长后字符类不会在缺少闭合符时回扫到行尾：100 KB 全是
#: `\begin{table}[` 或 `\caption{` 时，惰性 `.*?` 版本要数秒。真实参数远短于此，
#: 超过上限的按“不是环境/命令”处理（原文保留）。
_MAX_ARGUMENT = 200

_TABLE_BEGIN = re.compile(
    r"\\begin\{table\}(?:\[[^\n]{0," + str(_MAX_ARGUMENT) + r"}?\])?"
)
_TABLE_END = re.compile(r"\\end\{table\}")
_TABLE_CENTERING = re.compile(r"\\centering")
_TABLE_CAPTION = re.compile(r"\\caption\{[^\n]{0," + str(_MAX_ARGUMENT) + r"}?\}")


class TableConverter:
    """LaTeX表格转换器"""

    _TABULAR_OPEN = "\\begin{tabular}"
    _TABULAR_CLOSE = "\\end{tabular}"

    def convert(self, text: str) -> str:
        """将LaTeX表格转换为Markdown格式"""
        # 移除table环境包装（模式已限长，见 _MAX_ARGUMENT）
        text = _TABLE_BEGIN.sub("", text)
        text = _TABLE_END.sub("", text)
        text = _TABLE_CENTERING.sub("", text)
        text = _TABLE_CAPTION.sub("", text)

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
