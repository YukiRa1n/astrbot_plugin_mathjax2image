"""
正则表达式模式集中管理模块

本模块集中定义和预编译所有正则表达式，提高性能和可维护性。
所有正则表达式按功能分组，使用清晰的命名规范（全大写+下划线）。

设计原则:
- 单一职责: 仅负责正则表达式的定义
- 预编译优化: 使用 re.compile() 提前编译
- 清晰命名: 模式名称直接表达用途
- 功能分组: 按模块/功能组织
"""

import re
from typing import Pattern

# ============================================================================
# LaTeX 预处理器相关正则 (latex_preprocessor.py)
# ============================================================================

# 匹配 \textbf{...} 粗体命令
LATEX_TEXTBF: Pattern[str] = re.compile(r"\\textbf\{([\s\S]*?)\}")

# 匹配 \textit{...} 斜体命令
LATEX_TEXTIT: Pattern[str] = re.compile(r"\\textit\{([\s\S]*?)\}")

# 匹配 \emph{...} 强调命令
LATEX_EMPH: Pattern[str] = re.compile(r"\\emph\{([\s\S]*?)\}")

# 匹配集合表示法 {x \mid condition}
LATEX_SET_NOTATION: Pattern[str] = re.compile(r"(?<!\\)\{([^{}]*\\mid[^{}]*)\}")


# ============================================================================
# TikZ Plot 转换器相关正则 (tikz_plot_converter.py)
# ============================================================================

# 匹配 \draw[...] plot(..., {...}) ; 命令
TIKZ_PLOT_COMMAND: Pattern[str] = re.compile(
    r"\\draw\s*\[([^\]]*)\]\s*plot\s*\(\s*([^,]+)\s*,\s*\{([^}]+)\}\s*\)\s*;"
)

# 匹配 domain=min:max 参数
TIKZ_DOMAIN_PARAM: Pattern[str] = re.compile(r"domain\s*=\s*([-\d.]+)\s*:\s*([-\d.]+)")

# 匹配 samples=N 参数
TIKZ_SAMPLES_PARAM: Pattern[str] = re.compile(r"samples\s*=\s*(\d+)")

# 用于移除 domain 参数
TIKZ_DOMAIN_REMOVE: Pattern[str] = re.compile(
    r",?\s*domain\s*=\s*[-\d.]+\s*:\s*[-\d.]+"
)

# 用于移除 samples 参数
TIKZ_SAMPLES_REMOVE: Pattern[str] = re.compile(r",?\s*samples\s*=\s*\d+")


# ============================================================================
# TikZ 转换器相关正则 (tikz_converter.py)
# ============================================================================

# 匹配 \begin{tikzpicture}...\end{tikzpicture} 环境
TIKZ_PICTURE_ENV: Pattern[str] = re.compile(
    r"\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}"
)

# 匹配 \begin{tikzcd}...\end{tikzcd} 环境（交换图）
TIKZ_CD_ENV: Pattern[str] = re.compile(r"\\begin\{tikzcd\}[\s\S]*?\\end\{tikzcd\}")

# 匹配 \begin{circuitikz}...\end{circuitikz} 环境（电路图）
TIKZ_CIRCUIT_ENV: Pattern[str] = re.compile(
    r"\\begin\{circuitikz\}[\s\S]*?\\end\{circuitikz\}"
)

# 匹配 \chemfig{...} 化学结构命令（支持嵌套花括号）
TIKZ_CHEMFIG_CMD: Pattern[str] = re.compile(
    r"\\chemfig\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}"
)


# ============================================================================
# Markdown 转换器相关正则 (markdown_converter.py)
# ============================================================================

# 修复 tikzpicture 环境中的注释
MD_TIKZ_COMMENT_FIX: Pattern[str] = re.compile(r"(%[^\n]*?)\\end\{tikzpicture\}")

# 修复 tikzcd 环境中的注释
MD_TIKZCD_COMMENT_FIX: Pattern[str] = re.compile(r"(%[^\n]*?)\\end\{tikzcd\}")

# 匹配转义的换行符 \n（非字母后）
MD_ESCAPED_NEWLINE: Pattern[str] = re.compile(r"\\n(?![a-zA-Z])")

# 匹配标题格式问题 (# 后缺少空格)
MD_HEADING_FORMAT: Pattern[str] = re.compile(r"^(#{1,6})([^#\s])", re.MULTILINE)

# 检测标题行
MD_HEADING_DETECT: Pattern[str] = re.compile(r"^#{1,6}\s+", re.MULTILINE)

# 检测无序列表
MD_UNORDERED_LIST_DETECT: Pattern[str] = re.compile(r"^[-*]\s+", re.MULTILINE)

# 检测有序列表
MD_ORDERED_LIST_DETECT: Pattern[str] = re.compile(r"^\d+\.\s+", re.MULTILINE)

# 匹配 display math \[...\]
MD_DISPLAY_MATH_BRACKET: Pattern[str] = re.compile(r"\\\[[\s\S]*?\\\]")

# 匹配 inline math \(...\)
MD_INLINE_MATH_PAREN: Pattern[str] = re.compile(r"\\\([\s\S]*?\\\)")

# 匹配 display math $$...$$
MD_DISPLAY_MATH_DOLLAR: Pattern[str] = re.compile(r"\$\$.*?\$\$", re.DOTALL)

# 匹配 inline math $...$
MD_INLINE_MATH_DOLLAR: Pattern[str] = re.compile(r"\$.*?\$")

# 匹配代码块 ```...```
MD_CODE_BLOCK: Pattern[str] = re.compile(r"```[\s\S]*?```")


# ============================================================================
# 列表转换器相关正则 (list_converter.py)
# ============================================================================

# 匹配 \begin{enumerate}[...] 开始标签（可选参数）
LIST_ENUMERATE_BEGIN: Pattern[str] = re.compile(r"\\begin\{enumerate\}(\[.*?\])?")

# 匹配 \end{enumerate} 结束标签
LIST_ENUMERATE_END: Pattern[str] = re.compile(r"\\end\{enumerate\}")

# 匹配 \begin{itemize} 开始标签
LIST_ITEMIZE_BEGIN: Pattern[str] = re.compile(r"\\begin\{itemize\}")

# 匹配 \end{itemize} 结束标签
LIST_ITEMIZE_END: Pattern[str] = re.compile(r"\\end\{itemize\}")

# 匹配行首的 \item 标记
LIST_ITEM_MARKER: Pattern[str] = re.compile(r"^\\item\s*", re.MULTILINE)


# ============================================================================
# 表格转换器相关正则 (table_converter.py)
# ============================================================================

# 匹配 \begin{table}[...] 开始标签（可选参数）
TABLE_BEGIN: Pattern[str] = re.compile(r"\\begin\{table\}(\[.*?\])?")

# 匹配 \end{table} 结束标签
TABLE_END: Pattern[str] = re.compile(r"\\end\{table\}")

# 匹配 \centering 居中命令
TABLE_CENTERING: Pattern[str] = re.compile(r"\\centering")

# 匹配 \caption{...} 标题命令
TABLE_CAPTION: Pattern[str] = re.compile(r"\\caption\{.*?\}")

# 匹配 \begin{tabular}{...}...\end{tabular} 环境
TABLE_TABULAR_ENV: Pattern[str] = re.compile(
    r"\\begin\{tabular\}\{[^}]*\}([\s\S]*?)\\end\{tabular\}"
)

# 匹配 \hline 水平线
TABLE_HLINE: Pattern[str] = re.compile(r"\\hline\s*")

# 匹配 \\ 行分隔符
TABLE_ROW_SEPARATOR: Pattern[str] = re.compile(r"\\\\\s*")


# ============================================================================
# Mermaid 转换器相关正则 (mermaid_converter.py)
# ============================================================================

# 匹配 ```mermaid...``` 代码块
MERMAID_CODE_BLOCK: Pattern[str] = re.compile(r"```mermaid\s*\n([\s\S]*?)```")

# 检测 mermaid 代码块开始
MERMAID_DETECT: Pattern[str] = re.compile(r"```mermaid\s*\n")


# ============================================================================
# LaTeX 验证器相关正则 (latex_validator.py)
# ============================================================================

# 匹配 \frac{numerator}{denominator} 命令（检测参数完整性）
VALIDATOR_FRAC: Pattern[str] = re.compile(r"\\frac\{([^}]*)\}(?:\{([^}]*)\})?")

# 匹配积分语法 \int_{lower}^{upper}
VALIDATOR_INTEGRAL: Pattern[str] = re.compile(r"\\int_\{([^}]*)\}\^\{([^}]*)\}")

# 匹配 \begin{tikzpicture} 开始标签
VALIDATOR_TIKZ_BEGIN: Pattern[str] = re.compile(r"\\begin\{tikzpicture\}")

# 匹配 \end{tikzpicture} 结束标签
VALIDATOR_TIKZ_END: Pattern[str] = re.compile(r"\\end\{tikzpicture\}")

# 匹配 \begin{tikzcd} 开始标签
VALIDATOR_TIKZCD_BEGIN: Pattern[str] = re.compile(r"\\begin\{tikzcd\}")

# 匹配 \end{tikzcd} 结束标签
VALIDATOR_TIKZCD_END: Pattern[str] = re.compile(r"\\end\{tikzcd\}")

# 匹配 \begin{equation} 开始标签
VALIDATOR_EQUATION_BEGIN: Pattern[str] = re.compile(r"\\begin\{equation\}")

# 匹配 \end{equation} 结束标签
VALIDATOR_EQUATION_END: Pattern[str] = re.compile(r"\\end\{equation\}")

# 匹配 \begin{align} 开始标签
VALIDATOR_ALIGN_BEGIN: Pattern[str] = re.compile(r"\\begin\{align\}")

# 匹配 \end{align} 结束标签
VALIDATOR_ALIGN_END: Pattern[str] = re.compile(r"\\end\{align\}")


# ============================================================================
# 导出所有正则表达式模式
# ============================================================================

__all__ = [
    # LaTeX 预处理器
    "LATEX_TEXTBF",
    "LATEX_TEXTIT",
    "LATEX_EMPH",
    "LATEX_SET_NOTATION",
    # TikZ Plot 转换器
    "TIKZ_PLOT_COMMAND",
    "TIKZ_DOMAIN_PARAM",
    "TIKZ_SAMPLES_PARAM",
    "TIKZ_DOMAIN_REMOVE",
    "TIKZ_SAMPLES_REMOVE",
    # TikZ 转换器
    "TIKZ_PICTURE_ENV",
    "TIKZ_CD_ENV",
    "TIKZ_CIRCUIT_ENV",
    "TIKZ_CHEMFIG_CMD",
    # Markdown 转换器
    "MD_TIKZ_COMMENT_FIX",
    "MD_TIKZCD_COMMENT_FIX",
    "MD_ESCAPED_NEWLINE",
    "MD_HEADING_FORMAT",
    "MD_HEADING_DETECT",
    "MD_UNORDERED_LIST_DETECT",
    "MD_ORDERED_LIST_DETECT",
    "MD_DISPLAY_MATH_BRACKET",
    "MD_INLINE_MATH_PAREN",
    "MD_DISPLAY_MATH_DOLLAR",
    "MD_INLINE_MATH_DOLLAR",
    "MD_CODE_BLOCK",
    # 列表转换器
    "LIST_ENUMERATE_BEGIN",
    "LIST_ENUMERATE_END",
    "LIST_ITEMIZE_BEGIN",
    "LIST_ITEMIZE_END",
    "LIST_ITEM_MARKER",
    # 表格转换器
    "TABLE_BEGIN",
    "TABLE_END",
    "TABLE_CENTERING",
    "TABLE_CAPTION",
    "TABLE_TABULAR_ENV",
    "TABLE_HLINE",
    "TABLE_ROW_SEPARATOR",
    # Mermaid 转换器
    "MERMAID_CODE_BLOCK",
    "MERMAID_DETECT",
    # LaTeX 验证器
    "VALIDATOR_FRAC",
    "VALIDATOR_INTEGRAL",
    "VALIDATOR_TIKZ_BEGIN",
    "VALIDATOR_TIKZ_END",
    "VALIDATOR_TIKZCD_BEGIN",
    "VALIDATOR_TIKZCD_END",
    "VALIDATOR_EQUATION_BEGIN",
    "VALIDATOR_EQUATION_END",
    "VALIDATOR_ALIGN_BEGIN",
    "VALIDATOR_ALIGN_END",
]
