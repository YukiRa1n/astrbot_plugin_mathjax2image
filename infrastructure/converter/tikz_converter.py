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
        # circuitikz 不在 TikZJax(beta24) 的包清单中,明确拒绝
        text = re.sub(
            r"\\begin\{circuitikz\}[\s\S]*?\\end\{circuitikz\}",
            lambda m: '<div class="error">circuitikz 不支持（TikZJax 无此包），请用 TikZ 原生命令</div>',
            text,
        )

        # 匹配独立的chemfig命令。
        # 注意: 转换后的 HTML 块含 <script type="text/tikz" data-...>,
        # 不能用精确字符串 '<script type="text/tikz">' 判断(带属性时不匹配),
        # 否则会误在已生成的 script 内容上替换。用宽松正则判断。
        if r"\chemfig{" in text and not re.search(
            r'<script\s+type=["\']text/tikz["\']', text
        ):
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

        # 提取用户显式 preamble 指令后再剥离。TikZJax 的 worker 会将
        # data-* 属性放到 preamble；若仅剥离而不提取，用户声明的库会丢失。
        explicit_packages, explicit_libraries = self._extract_preamble_directives(
            tikz_code
        )
        tikz_code = self._strip_preamble_directives(tikz_code)

        # 预处理plot命令
        tikz_code = self._plot_converter.convert(tikz_code)

        # 检测需要的包和库，并合并用户显式声明（白名单过滤）。
        packages = list(dict.fromkeys(explicit_packages + self._detect_packages(tikz_code)))
        tikzlibraries = list(
            dict.fromkeys(explicit_libraries + self._detect_libraries(tikz_code))
        )

        logger.info(f"[MathJax2Image] TikZ包: {packages}, 库: {tikzlibraries}")

        # TikZJax(drgrice1 fork)的 worker 会自动插入 \begin{document}，
        # 并读 data-tex-packages/data-tikz-libraries 预加载包/库。
        # 因此脚本内容必须只含 tikzpicture 本体（不能有 usepackage/
        # usetikzlibrary/document 包装，否则嵌套 document 报错）。
        tex_packages = {
            pkg: ""
            for pkg in packages
            if pkg not in {"amsmath", "amsfonts", "amssymb"}
        }
        return self._wrap_tikz_html(
            tikz_code,
            tex_packages=tex_packages,
            tikz_libraries=tikzlibraries,
        )

    @classmethod
    def _extract_preamble_directives(cls, tikz_code: str) -> tuple[list[str], list[str]]:
        """提取并白名单过滤用户的宏包和 TikZ 库声明。"""
        packages: list[str] = []
        for match in re.finditer(
            r"\\usepackage(?:\[([^\]]*)\])?\{([^{}]*)\}", tikz_code
        ):
            for package in match.group(2).split(","):
                package = package.strip()
                if package in cls.SUPPORTED_PACKAGES and package not in packages:
                    packages.append(package)

        libraries: list[str] = []
        for match in re.finditer(r"\\usetikzlibrary\{([^{}]*)\}", tikz_code):
            for library in match.group(1).split(","):
                library = library.strip()
                if library in cls.SUPPORTED_LIBRARIES and library not in libraries:
                    libraries.append(library)
        return packages, libraries

    @staticmethod
    def _strip_preamble_directives(tikz_code: str) -> str:
        """剥离用户输入中的 preamble 指令。"""
        # 移除 \documentclass{...} 整行
        code = re.sub(
            r"\\documentclass(\[[^\]]*\])?\{[^}]*\}", "", tikz_code
        )
        # 移除 \usepackage[...]{...} 整行(含可选参数)
        code = re.sub(
            r"\\usepackage(\[[^\]]*\])?\{[^}]*\}", "", code
        )
        # 移除 \usetikzlibrary{...} 整行
        code = re.sub(r"\\usetikzlibrary\{[^}]*\}", "", code)
        # 移除 \begin{document}/\end{document} 行
        code = re.sub(r"\\begin\{document\}", "", code)
        code = re.sub(r"\\end\{document\}", "", code)
        # 移除 \pagestyle/\thispagestyle 行
        code = re.sub(r"\\(this)?pagestyle\{[^}]*\}", "", code)
        return code

    def _convert_chemfig_block(self, match: re.Match) -> str:
        """转换chemfig命令。

        chemfig 不在 TikZJax(drgrice1 fork)的 tex_files manifest 中，
        即使检测到也无法加载，必然渲染失败。因此明确拒绝并给出提示，
        而不是假装支持。
        """
        chemfig_cmd = match.group(0)

        if not self._validate_tikz_complexity(chemfig_cmd):
            logger.error("[MathJax2Image] chemfig代码过于复杂，已拒绝渲染")
            return '<div class="error">chemfig 代码过于复杂，请简化后重试</div>'

        logger.warning(
            "[MathJax2Image] chemfig 不在 TikZJax 支持的包中，已拒绝渲染"
        )
        return '<div class="error">chemfig 不支持（TikZJax 无此包），请用 TikZ 原生命令</div>'

    def _wrap_tikz_html(
        self,
        tikz_document: str,
        *,
        tex_packages: dict[str, str] | None = None,
        tikz_libraries: list[str] | None = None,
    ) -> str:
        """将TikZ文档包装为HTML，并阻止script标签被提前闭合。

        使用 ``data-tex-packages`` 和 ``data-tikz-libraries`` 属性让
        TikZJax(drgrice1 fork)预加载宏包/库。TikZJax 的 worker 读取
        ``dataset.tikzLibraries`` 加载库文件(tex_files/*.code.tex.gz)，
        缺少该属性时即使 tex_files 已部署也不会加载库。
        """
        safe_document = re.sub(
            r"</script", r"<\/script", tikz_document, flags=re.IGNORECASE
        )
        attrs = ""
        if tex_packages:
            import json as _json

            attrs += f" data-tex-packages='{_json.dumps(tex_packages)}'"
        if tikz_libraries:
            # 注意：TikZJax worker 对 data-tikz-libraries 直接模板拼接
            # `\usetikzlibrary{${dataset.tikzLibraries}}`，不做 JSON.parse。
            # 因此必须用逗号连接字符串（如 "arrows,calc"），
            # 传 JSON 数组会变成 \usetikzlibrary{["arrows"]} 语法错误。
            attrs += f" data-tikz-libraries='{','.join(tikz_libraries)}'"
        return (
            '<div class="tikz-diagram"><script type="text/tikz"'
            f"{attrs}>\n"
            f"{safe_document}\n"
            "</script></div>"
        )

    def _has_chinese(self, text: str) -> bool:
        """检测文本是否包含中文字符"""
        for char in text:
            if "\u4e00" <= char <= "\u9fff":
                return True
        return False

    #: TikZJax(drgrice1 fork) tex_files manifest 中可用的宏包白名单。
    #: 不在其中的包(chemfig/circuitikz/graphicx/tikzmark 等)即使检测到
    #: 也无法加载，渲染必失败，因此不检测。
    SUPPORTED_PACKAGES = frozenset(
        {
            "amsbsy",
            "amsfonts",
            "amsgen",
            "amsmath",
            "amsopn",
            "amssymb",
            "amstext",
            "array",
            "etoolbox",
            "expl3",
            "hf-tikz",
            "ifthen",
            "pgfcalendar",
            "pgfplots",
            "tikz-3dplot",
            "tikz-cd",
            "xparse",
        }
    )

    def _detect_packages(self, tikz_code: str) -> list[str]:
        """检测需要的宏包(仅返回 TikZJax 实际支持的)"""
        packages = ["amsmath", "amsfonts", "amssymb"]

        if "tikzcd" in tikz_code or "\\arrow" in tikz_code:
            packages.append("tikz-cd")
        if "axis" in tikz_code or "addplot" in tikz_code:
            packages.append("pgfplots")
        if "tdplot" in tikz_code or "3d" in tikz_code.lower():
            packages.append("tikz-3dplot")
        if "array" in tikz_code or "tabular" in tikz_code:
            packages.append("array")
        if "\\patchcmd" in tikz_code or "\\AtBeginEnvironment" in tikz_code:
            packages.append("etoolbox")
        if "\\NewDocumentCommand" in tikz_code:
            packages.append("xparse")
        if "\\tikzmarkin" in tikz_code:
            packages.append("hf-tikz")

        # 只保留 TikZJax 支持的包
        return [p for p in packages if p in self.SUPPORTED_PACKAGES]

    #: TikZJax(drgrice1 fork) tex_files manifest 中实际可用的 TikZ 库。
    #: 不在其中的库即使检测到也无法加载，渲染必失败。
    SUPPORTED_LIBRARIES = frozenset(
        {
            # 基础常用
            "arrows", "arrows.meta", "calc", "positioning", "shapes",
            "shapes.geometric", "shapes.arrows", "shapes.callouts",
            "shapes.misc", "shapes.multipart", "shapes.symbols",
            "shapes.gates.logic.IEC", "shapes.gates.logic.US",
            "intersections", "decorations", "decorations.pathreplacing",
            "decorations.pathmorphing", "decorations.markings",
            "decorations.footprints", "decorations.fractals",
            "decorations.shapes", "decorations.text",
            "backgrounds", "fit", "patterns", "patterns.meta",
            "angles", "quotes", "matrix", "3d", "trees", "graphs",
            "graphs.standard", "chains", "scopes", "through",
            # P1 扩展
            "automata", "calendar", "mindmap", "bending", "er",
            "fadings", "shadings", "shadows", "spy", "plotmarks",
            "math", "fpu", "fixedpointarithmetic", "perspective",
            "petri", "lindenmayersystems", "snakes",
            # beta24 补充
            "animations", "babel", "cd", "circuits", "circuits.ee",
            "circuits.ee.IEC", "circuits.logic", "circuits.logic.CDH",
            "circuits.logic.IEC", "circuits.logic.US",
            "datavisualization", "datavisualization.3d",
            "datavisualization.barcharts", "datavisualization.formats.functions",
            "datavisualization.polar", "datavisualization.sparklines",
            "folding", "plothandlers", "rdf", "svg.path", "turtle", "views",
        }
    )

    def _detect_libraries(self, tikz_code: str) -> list[str]:
        """检测需要的TikZ库(仅返回 TikZJax 实际支持的,按命令特征匹配)"""
        libs: list[str] = []

        # 箭头库(arrows.meta tip 名: Stealth/Latex/Triangle/Circle/Bracket 等)
        if (
            "Stealth" in tikz_code
            or "Latex" in tikz_code
            or "Triangle" in tikz_code
            or "Circle" in tikz_code
            or "Bracket" in tikz_code
            or "Implies" in tikz_code
            or "Rightarrow" in tikz_code
        ):
            libs.append("arrows.meta")
        if "\\arrow" in tikz_code:
            libs.append("arrows")
        if "flex" in tikz_code or "bend angle" in tikz_code:
            libs.append("bending")
        # 坐标计算
        if "calc" in tikz_code or "($" in tikz_code or "$(" in tikz_code:
            libs.append("calc")
        # 节点定位
        if "positioning" in tikz_code or " of=" in tikz_code or " of " in tikz_code:
            libs.append("positioning")
        if "below of=" in tikz_code or "right of=" in tikz_code:
            libs.append("positioning")
        # 形状
        if (
            "ellipse" in tikz_code
            or "rectangle" in tikz_code
            or "diamond" in tikz_code
            or "regular polygon" in tikz_code
            or "star" in tikz_code
        ):
            libs.append("shapes.geometric")
        if "shapes" in tikz_code:
            libs.append("shapes")
        if "arrowhead" in tikz_code:
            libs.append("shapes.arrows")
        if "callout" in tikz_code:
            libs.append("shapes.callouts")
        if "multipart" in tikz_code:
            libs.append("shapes.multipart")
        if "signal" in tikz_code:
            libs.append("shapes.symbols")
        # 路径交点
        if "name path" in tikz_code or "intersection of" in tikz_code:
            libs.append("intersections")
        # 装饰
        if "decorate" in tikz_code or "decoration=" in tikz_code:
            libs.append("decorations.pathreplacing")
            libs.append("decorations.pathmorphing")
        if "snake" in tikz_code or "zigzag" in tikz_code:
            libs.append("decorations.pathmorphing")
        if "mark=at position" in tikz_code or "postaction={decorate" in tikz_code:
            libs.append("decorations.markings")
        if "text along path" in tikz_code or "text effects" in tikz_code:
            libs.append("decorations.text")
        if "footprint" in tikz_code:
            libs.append("decorations.footprints")
        if "Koch" in tikz_code:
            libs.append("decorations.fractals")
        # 背景
        if "background" in tikz_code or "on background layer" in tikz_code:
            libs.append("backgrounds")
        # 节点拟合
        if "fit=" in tikz_code:
            libs.append("fit")
        # 填充图案
        if "pattern=" in tikz_code:
            libs.append("patterns")
        if "pattern meta" in tikz_code or "pattern color" in tikz_code:
            libs.append("patterns.meta")
        # 角度
        if "pic[draw" in tikz_code or "angle =" in tikz_code or "angle=" in tikz_code:
            libs.append("angles")
            libs.append("quotes")
        # 矩阵
        if "matrix of nodes" in tikz_code or "matrix of math nodes" in tikz_code:
            libs.append("matrix")
        # 3D
        if "canvas is" in tikz_code or "x={(1,0,0)}" in tikz_code:
            libs.append("3d")
        if "view={" in tikz_code or "perspective" in tikz_code:
            libs.append("perspective")
        # 树
        if "child {" in tikz_code or "child[" in tikz_code:
            libs.append("trees")
        # 图(graph)
        if "graph [" in tikz_code or "\\graph" in tikz_code:
            libs.append("graphs")
        # 链
        if "on chain" in tikz_code or "chain=" in tikz_code:
            libs.append("chains")
        # 自动机: [state]、node[state]、[state,...] 等选项(边界匹配避免误报)
        if (
            re.search(r"(?:^|[\[,])\s*state\s*(?=,|\])", tikz_code)
            or "state/.style" in tikz_code
            or "initial" in tikz_code
            or "accepting" in tikz_code
        ):
            libs.append("automata")
        # 日历
        if "\\calendar" in tikz_code or "dates=" in tikz_code:
            libs.append("calendar")
        # 思维导图
        if "mindmap" in tikz_code or "concept" in tikz_code:
            libs.append("mindmap")
        # ER 图
        if "entity" in tikz_code or "relationship" in tikz_code:
            libs.append("er")
        # 阴影/渐变
        if "drop shadow" in tikz_code or "shadow" in tikz_code:
            libs.append("shadows")
        if "shade" in tikz_code or "shading=" in tikz_code:
            libs.append("shadings")
        if "fading" in tikz_code:
            libs.append("fadings")
        # 放大镜
        if "spy using outlines" in tikz_code or "spy in node" in tikz_code:
            libs.append("spy")
        # 绘图标记
        if "mark=*" in tikz_code or "mark options" in tikz_code:
            libs.append("plotmarks")
        # 数学
        if "\\pgfmathparse" in tikz_code or "let " in tikz_code:
            libs.append("math")
        if "fpu" in tikz_code or "fixed point arithmetic" in tikz_code:
            libs.append("fpu")
        # Petri 网
        if "place" in tikz_code or "transition" in tikz_code:
            libs.append("petri")
        # L-system
        if "l-system" in tikz_code or "\\pgfdeclarelindenmayersystem" in tikz_code:
            libs.append("lindenmayersystems")
        # 数据可视化
        if "\\datavisualization" in tikz_code:
            libs.append("datavisualization")
        # SVG 路径
        if "svg[" in tikz_code or "\\pgfpathsvg" in tikz_code:
            libs.append("svg.path")
        # 海龟
        if "turtle" in tikz_code:
            libs.append("turtle")

        # tikz-cd 包内部会加载 cd -> matrix,quotes,arrows.meta；显式的
        # rrow 命令本身不代表通用 arrows 库，避免错误地加载 arrows。
        if "tikzcd" in tikz_code and r"rrow" in tikz_code:
            libs = [lib for lib in libs if lib != "arrows"]

        # 只保留 TikZJax 支持的库,去重
        return list(dict.fromkeys(l for l in libs if l in self.SUPPORTED_LIBRARIES))

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

        # 注意：TikZJax 会自动套用 standalone 文档类(\documentclass[margin=0pt]
        # {standalone} 预定义在 preamble)。这里不能再包 \begin{document}，
        # 否则报 "Unknown environment 'document'"。只输出 usepackage/
        # usetikzlibrary + tikzpicture 本体。
        return f"""{chinese_warning}{usepackages}
{pgfplots_config}
{usetikzlibs}
{tikz_code}"""

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
