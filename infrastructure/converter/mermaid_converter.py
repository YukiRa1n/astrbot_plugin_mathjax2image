"""
Mermaid图表转换器
将Markdown中的mermaid代码块转换为Mermaid.js可渲染的格式
"""

import html
import re

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")


MAX_MERMAID_LENGTH = 50000  # 单个 mermaid 代码块最大长度（字符）


class MermaidConverter:
    """Mermaid图表转换器

    将 ```mermaid ... ``` 代码块转换为 <pre class="mermaid">...</pre>
    Mermaid.js 会自动渲染这些元素
    """

    # Mermaid 支持的图表类型
    DIAGRAM_TYPES = [
        "graph",
        "flowchart",
        "sequenceDiagram",
        "classDiagram",
        "stateDiagram",
        "erDiagram",
        "journey",
        "gantt",
        "pie",
        "quadrantChart",
        "requirementDiagram",
        "gitGraph",
        "mindmap",
        "timeline",
        "zenuml",
        "sankey",
        "xychart",
    ]

    def convert(self, text: str) -> str:
        """转换所有Mermaid代码块

        Args:
            text: 包含Mermaid代码块的文本

        Returns:
            转换后的文本，mermaid代码块被替换为HTML格式
        """
        # 匹配 ```mermaid ... ``` 代码块
        pattern = r"```mermaid\s*\n([\s\S]*?)```"

        converted = re.sub(pattern, self._convert_mermaid_block, text)

        return converted

    def _convert_mermaid_block(self, match: re.Match) -> str:
        """转换单个Mermaid代码块"""
        raw_code = match.group(1)
        if len(raw_code) > MAX_MERMAID_LENGTH:
            logger.warning(
                f"[MathJax2Image] Mermaid代码过长: {len(raw_code)} > {MAX_MERMAID_LENGTH}"
            )
            return '<div class="error">Mermaid 代码过长，请简化后重试</div>'

        mermaid_code = raw_code.strip()

        if not mermaid_code:
            logger.warning("[MathJax2Image] 空的Mermaid代码块")
            return ""

        # 检测图表类型
        diagram_type = self._detect_diagram_type(mermaid_code)
        logger.info(f"[MathJax2Image] 检测到Mermaid图表类型: {diagram_type}")
        if diagram_type in {"graph", "flowchart"}:
            mermaid_code = self._normalize_math_labels(mermaid_code)

        # 转换为Mermaid.js可识别的HTML格式
        # 使用 <pre class="mermaid"> 标签
        escaped_code = html.escape(mermaid_code, quote=False)
        html_block = f'<pre class="mermaid">\n{escaped_code}\n</pre>'

        return html_block

    @staticmethod
    def _normalize_math_labels(code: str) -> str:
        """Normalize quoted flowchart labels to one native KaTeX expression.

        Args:
            code: Flowchart source with quoted node or pipe-delimited edge labels.

        Returns:
            Source with paired inline math and plain text combined inside ``$$``.
            Existing native math, HTML labels and ambiguous dollar signs are kept.
        """
        label_pattern = re.compile(
            r'(?P<prefix>[\[(|]\s*)"(?P<label>(?:\\.|[^"\\\n])*)"(?=\s*[\])|])'
        )
        text_escapes = {
            "\\": r"\textbackslash{}",
            "^": r"\textasciicircum{}",
            "~": r"\textasciitilde{}",
            **{char: "\\" + char for char in "%#&_{}$"},
        }

        def normalize(match: re.Match) -> str:
            label = match.group("label")
            if (
                "$$" in label
                or r"\$" in label
                or r"\"" in label
                or re.search(r"</?[a-zA-Z][^>]*>", label)
            ):
                return match.group(0)
            dollars = []
            index = 0
            while index < len(label):
                if label[index] == "\\":
                    index += 2
                    continue
                if label[index] == "$":
                    dollars.append(index)
                index += 1
            if not dollars or len(dollars) % 2:
                return match.group(0)
            parts = []
            start = 0
            for opening, closing in zip(dollars[::2], dollars[1::2]):
                formula = label[opening + 1 : closing]
                # A closing dollar before a digit usually starts another price.
                if (
                    not formula.strip()
                    or formula != formula.strip()
                    or (closing + 1 < len(label) and label[closing + 1].isdigit())
                ):
                    return match.group(0)
                text = label[start:opening]
                if text:
                    parts.append(
                        r"\text{"
                        + "".join(text_escapes.get(char, char) for char in text)
                        + "}"
                    )
                parts.append(formula)
                start = closing + 1
            if start < len(label):
                parts.append(
                    r"\text{"
                    + "".join(text_escapes.get(char, char) for char in label[start:])
                    + "}"
                )
            return match.group("prefix") + '"$$' + "".join(parts) + '$$"'

        lines = []
        frontmatter = False
        for index, line in enumerate(code.splitlines(keepends=True)):
            stripped = line.strip()
            if stripped == "---" and (index == 0 or frontmatter):
                frontmatter = not frontmatter
                lines.append(line)
            elif (
                frontmatter
                or stripped.startswith("%%")
                or re.match(r"(?:style|classDef|class|linkStyle|click)\b", stripped)
            ):
                lines.append(line)
            else:
                # Inline comments are outside the diagram's label grammar.
                diagram, comment, rest = line.partition("%%")
                lines.append(label_pattern.sub(normalize, diagram) + comment + rest)
        return "".join(lines)

    def _detect_diagram_type(self, code: str) -> str:
        """Find a diagram declaration after optional frontmatter and comments.

        Args:
            code: Mermaid diagram source.

        Returns:
            The declared diagram type, or ``unknown``.
        """
        frontmatter = False
        for index, line in enumerate(code.splitlines()):
            stripped = line.strip().lower()
            if stripped == "---" and (index == 0 or frontmatter):
                frontmatter = not frontmatter
                continue
            if frontmatter or not stripped or stripped.startswith("%%"):
                continue
            for dtype in self.DIAGRAM_TYPES:
                if re.match(re.escape(dtype.lower()) + r"\b", stripped):
                    return dtype
            break

        # 默认为flowchart
        return "unknown"

    def has_mermaid(self, text: str) -> bool:
        """检查文本是否包含Mermaid代码块"""
        return bool(re.search(r"```mermaid\s*\n", text))
