"""
TikZ环境转换器
将tikzpicture环境转换为tikzjax格式
"""

import re
from typing import TYPE_CHECKING

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")

if TYPE_CHECKING:
    from .tikz_plot_converter import TikzPlotConverter


# TikZ 代码复杂度限制（防止资源耗尽攻击）
MAX_TIKZ_LENGTH = 50000  # 最大代码长度（字符）
MAX_TIKZ_NODES = 500  # 最大节点数量
MAX_TIKZ_COMMANDS = 1000  # 最大命令数量
MAX_TIKZ_FOREACH = 20  # 最大 \foreach 数量
MAX_TIKZ_FOREACH_DEPTH = 2  # 最大 \foreach 嵌套深度


class TikzConverter:
    """TikZ环境转换器"""

    # 简单宏替换映射
    SIMPLE_MACROS = {
        "\\Z": "\\mathbb{Z}",
        "\\N": "\\mathbb{N}",
        "\\Q": "\\mathbb{Q}",
        "\\R": "\\mathbb{R}",
        "\\C": "\\mathbb{C}",
        "\\F": "\\mathbb{F}",
        "\\P": "\\mathbb{P}",
        "\\A": "\\mathbb{A}",
        "\\eps": "\\varepsilon",
        "\\vphi": "\\varphi",
    }

    def __init__(self, plot_converter: "TikzPlotConverter"):
        self._plot_converter = plot_converter

    def convert(self, text: str) -> str:
        """转换所有TikZ环境"""
        # 匹配各种TikZ环境
        text = re.sub(
            r"\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}",
            self._convert_tikz_block,
            text,
        )
        text = re.sub(
            r"\\begin\{tikzcd\}[\s\S]*?\\end\{tikzcd\}", self._convert_tikz_block, text
        )
        text = re.sub(
            r"\\begin\{circuitikz\}[\s\S]*?\\end\{circuitikz\}",
            self._convert_tikz_block,
            text,
        )

        # 匹配独立的chemfig命令
        if r"\chemfig{" in text and '<script type="text/tikz">' not in text:
            text = re.sub(
                r"\\chemfig\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}",
                self._convert_chemfig_block,
                text,
            )

        return text

    def _convert_tikz_block(self, match: re.Match) -> str:
        """转换TikZ代码块"""
        tikz_code = match.group(0)

        # 复杂度检查
        if not self._validate_tikz_complexity(tikz_code):
            logger.error("[MathJax2Image] TikZ代码过于复杂，已拒绝渲染")
            return '<div class="error">TikZ 代码过于复杂，请简化后重试</div>'

        # 应用简单宏替换（使用词边界避免误替换，如 \Z 不应影响 \Zeta）
        for macro, replacement in self.SIMPLE_MACROS.items():
            tikz_code = re.sub(
                re.escape(macro) + r'(?![a-zA-Z])',
                lambda m, r=replacement: r,
                tikz_code,
            )

        # 预处理plot命令
        tikz_code = self._plot_converter.convert(tikz_code)

        # 检测需要的包和库
        packages = self._detect_packages(tikz_code)
        tikzlibraries = self._detect_libraries(tikz_code)

        logger.info(f"[MathJax2Image] TikZ包: {packages}, 库: {tikzlibraries}")

        # 构建完整文档
        full_tikz = self._build_tikz_document(tikz_code, packages, tikzlibraries)

        # 包装为HTML
        return self._wrap_tikz_html(full_tikz)

    def _convert_chemfig_block(self, match: re.Match) -> str:
        """转换chemfig命令"""
        chemfig_cmd = match.group(0)

        if not self._validate_tikz_complexity(chemfig_cmd):
            logger.error("[MathJax2Image] chemfig代码过于复杂，已拒绝渲染")
            return '<div class="error">chemfig 代码过于复杂，请简化后重试</div>'

        full_tikz = f"""\\usepackage{{amsmath}}
\\usepackage{{amsfonts}}
\\usepackage{{amssymb}}
\\usepackage{{chemfig}}
\\begin{{document}}
        {chemfig_cmd}
\\end{{document}}"""
        logger.info(f"[MathJax2Image] chemfig独立命令: {chemfig_cmd[:100]}...")
        return self._wrap_tikz_html(full_tikz)

    def _wrap_tikz_html(self, tikz_document: str) -> str:
        """将TikZ文档包装为HTML，并阻止script标签被提前闭合"""
        safe_document = re.sub(
            r"</script", r"<\/script", tikz_document, flags=re.IGNORECASE
        )
        return (
            '<div class="tikz-diagram"><script type="text/tikz">\n'
            f"{safe_document}\n"
            "</script></div>"
        )

    def _has_chinese(self, text: str) -> bool:
        """检测文本是否包含中文字符"""
        for char in text:
            if "\u4e00" <= char <= "\u9fff":
                return True
        return False

    def _detect_packages(self, tikz_code: str) -> list[str]:
        """检测需要的宏包"""
        packages = ["amsmath", "amsfonts", "amssymb"]

        if "chemfig" in tikz_code or "chemname" in tikz_code:
            packages.append("chemfig")
        if "tikzcd" in tikz_code or "\\arrow" in tikz_code:
            packages.append("tikz-cd")
        if "circuitikz" in tikz_code or "to[" in tikz_code:
            packages.append("circuitikz")
        if "axis" in tikz_code or "addplot" in tikz_code:
            packages.append("pgfplots")
        if "tdplot" in tikz_code or "3d" in tikz_code.lower():
            packages.append("tikz-3dplot")
        if "array" in tikz_code or "tabular" in tikz_code:
            packages.append("array")

        return packages

    def _detect_libraries(self, tikz_code: str) -> list[str]:
        """检测需要的TikZ库"""
        libs = []

        if "Stealth" in tikz_code or "Latex" in tikz_code:
            libs.append("arrows.meta")
        if "calc" in tikz_code or "($" in tikz_code:
            libs.append("calc")
        if "positioning" in tikz_code or " of=" in tikz_code or " of " in tikz_code:
            libs.append("positioning")
        if "ellipse" in tikz_code or "rectangle" in tikz_code or "diamond" in tikz_code:
            libs.append("shapes.geometric")
        if "shapes" in tikz_code:
            libs.append("shapes")
        if "background" in tikz_code:
            libs.append("backgrounds")
        if "fit=" in tikz_code:
            libs.append("fit")
        if "pgfplots" in tikz_code:
            if "calc" not in libs:
                libs.append("calc")

        return libs

    def _build_tikz_document(
        self, tikz_code: str, packages: list[str], tikzlibraries: list[str]
    ) -> str:
        """构建完整的TikZ文档"""
        usepackages = "\n".join([f"\\usepackage{{{pkg}}}" for pkg in packages])
        usetikzlibs = ""
        if tikzlibraries:
            usetikzlibs = f"\\usetikzlibrary{{{','.join(tikzlibraries)}}}"

        pgfplots_config = ""
        if "pgfplots" in packages:
            pgfplots_config = "\\pgfplotsset{compat=1.16}"

        # 检测中文并添加警告注释
        has_chinese = self._has_chinese(tikz_code)
        chinese_warning = ""
        if has_chinese:
            logger.warning(
                "[MathJax2Image] TikZ代码包含中文，TikZJax不支持CJK字体，中文可能无法正确显示"
            )
            chinese_warning = "% WARNING: TikZJax does not support CJK fonts, Chinese text may not render correctly\n"

        return f"""{chinese_warning}{usepackages}
{pgfplots_config}
{usetikzlibs}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""

    def _validate_tikz_complexity(self, tikz_code: str) -> bool:
        """验证 TikZ 代码复杂度，防止资源耗尽攻击

        Returns:
            True 如果代码复杂度在安全范围内
        """
        # 检查代码长度
        if len(tikz_code) > MAX_TIKZ_LENGTH:
            logger.warning(
                f"[MathJax2Image] TikZ代码过长: {len(tikz_code)} > {MAX_TIKZ_LENGTH}"
            )
            return False

        # 统计节点数量（\\node 命令）
        node_count = len(re.findall(r"\\node", tikz_code))
        if node_count > MAX_TIKZ_NODES:
            logger.warning(
                f"[MathJax2Image] TikZ节点过多: {node_count} > {MAX_TIKZ_NODES}"
            )
            return False

        # 统计绘图命令数量（\\draw, \\path, \\fill 等）
        command_patterns = [r"\\draw", r"\\path", r"\\fill", r"\\filldraw", r"\\shade"]
        total_commands = sum(
            len(re.findall(pattern, tikz_code)) for pattern in command_patterns
        )
        if total_commands > MAX_TIKZ_COMMANDS:
            logger.warning(
                f"[MathJax2Image] TikZ命令过多: {total_commands} > {MAX_TIKZ_COMMANDS}"
            )
            return False

        # 限制 \\foreach 数量，防止宏展开指数级放大（DoS）
        foreach_count = len(re.findall(r"\\foreach", tikz_code))
        if foreach_count > MAX_TIKZ_FOREACH:
            logger.warning(
                f"[MathJax2Image] TikZ \\foreach 过多: {foreach_count} > {MAX_TIKZ_FOREACH}"
            )
            return False

        # 限制 \\foreach 嵌套深度：单遍线性扫描，用栈跟踪嵌套层级。
        # 旧实现每个 \\foreach 都重新扫描整段代码找 body，攻击者可构造
        # 约 20 层 foreach（在长度/数量限制内）使检测耗时数秒，阻塞事件
        # 循环（DoS）。此实现一次遍历即得最大嵌套深度。
        def _foreach_depth(code: str) -> int:
            """统计 \\foreach 块之间的最大嵌套层数（单遍线性）。

            \\foreach 结构是 `\\foreach \\var in {list} {body}`。
            近似处理：把 ``\\foreach`` 视为进入一个层级，body 结束后退出。
            用花括号配对近似 body 边界，只在 foreach 出现处计数。
            """
            max_d = 0
            stack: list[int] = []  # 每层 foreach 的 body 花括号深度
            brace_depth = 0
            i = 0
            n = len(code)
            while i < n:
                ch = code[i]
                if ch == "{":
                    brace_depth += 1
                    i += 1
                    continue
                if ch == "}":
                    brace_depth -= 1
                    # 栈顶 body 闭合时弹出
                    if stack and brace_depth == stack[-1]:
                        stack.pop()
                    i += 1
                    continue
                if ch == "\\" and code.startswith("\\foreach", i):
                    after = i + len("\\foreach")
                    if after >= n or not code[after].isalpha():
                        # 合法 foreach 形如 `\foreach \var in {list} {body}`：
                        # 必须含 `in`，否则不计深度（避免把普通命令误判）。
                        in_pos = code.find("in", after)
                        if in_pos == -1 or in_pos > i + 200:
                            i = after
                            continue
                        j = code.find("{", after)
                        if j != -1:
                            # 配对变量列表 {list}
                            d = 0
                            k = j
                            while k < n:
                                if code[k] == "{":
                                    d += 1
                                elif code[k] == "}":
                                    d -= 1
                                    if d == 0:
                                        break
                                k += 1
                            # body 是紧随其后的 {，进入一层 foreach
                            body_start = code.find("{", k)
                            if body_start != -1:
                                stack.append(brace_depth)  # 记录 body 起点深度
                                max_d = max(max_d, len(stack))
                                i = body_start
                                continue
                    i = after
                    continue
                i += 1
            return max_d

        if foreach_count > 0:
            nested = _foreach_depth(tikz_code)
            if nested > MAX_TIKZ_FOREACH_DEPTH:
                logger.warning(
                    f"[MathJax2Image] TikZ \\foreach 嵌套过深: {nested} > {MAX_TIKZ_FOREACH_DEPTH}"
                )
                return False

        # 检测 \loop / 递归 \def（无限循环风险）
        if re.search(r"\\loop(?![a-zA-Z])", tikz_code):
            logger.warning("[MathJax2Image] TikZ 含 \\loop，已拒绝渲染")
            return False

        return True
