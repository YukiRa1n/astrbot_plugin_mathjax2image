"""
LaTeX表格转换器
将LaTeX tabular表格转换为Markdown格式
"""
import re


class TableConverter:
    """LaTeX表格转换器"""

    def convert(self, text: str) -> str:
        """将LaTeX表格转换为Markdown格式"""
        # 移除table环境包装
        text = re.sub(r'\\begin\{table\}(\[.*?\])?', '', text)
        text = re.sub(r'\\end\{table\}', '', text)
        text = re.sub(r'\\centering', '', text)
        text = re.sub(r'\\caption\{.*?\}', '', text)

        # 处理tabular环境
        text = re.sub(
            r'\\begin\{tabular\}\{[^}]*\}([\s\S]*?)\\end\{tabular\}',
            self._convert_tabular, text
        )

        return text

    def _convert_tabular(self, match: re.Match) -> str:
        """转换tabular内容"""
        content = match.group(1)

        # 移除\\hline
        content = re.sub(r'\\hline\s*', '', content)

        # 按\\\\分割行
        rows = re.split(r'\\\\\s*', content)
        md_rows = []

        for i, row in enumerate(rows):
            row = row.strip()
            if not row:
                continue

            # 按&分割列
            cells = [c.strip() for c in row.split('&')]
            md_row = '| ' + ' | '.join(cells) + ' |'
            md_rows.append(md_row)

            # 第一行后添加分隔符
            if i == 0:
                sep = '|' + '|'.join(['---'] * len(cells)) + '|'
                md_rows.append(sep)

        return '\n'.join(md_rows)
