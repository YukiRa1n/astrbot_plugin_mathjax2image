"""
Mermaid图表转换器
将Markdown中的mermaid代码块转换为Mermaid.js可渲染的格式
"""

import re

from astrbot.api import logger


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
        mermaid_code = match.group(1).strip()

        if not mermaid_code:
            logger.warning("[MathJax2Image] 空的Mermaid代码块")
            return ""

        # 检测图表类型
        diagram_type = self._detect_diagram_type(mermaid_code)
        logger.info(f"[MathJax2Image] 检测到Mermaid图表类型: {diagram_type}")

        # 转换为Mermaid.js可识别的HTML格式
        # 使用 <pre class="mermaid"> 标签
        html = f'<pre class="mermaid">\n{mermaid_code}\n</pre>'

        return html

    def _detect_diagram_type(self, code: str) -> str:
        """检测Mermaid图表类型"""
        first_line = code.split("\n")[0].strip().lower()

        for dtype in self.DIAGRAM_TYPES:
            if first_line.startswith(dtype.lower()):
                return dtype

        # 默认为flowchart
        return "unknown"

    def has_mermaid(self, text: str) -> bool:
        """检查文本是否包含Mermaid代码块"""
        return bool(re.search(r"```mermaid\s*\n", text))
