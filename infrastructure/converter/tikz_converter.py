"""
TikZ环境转换器
将tikzpicture环境转换为tikzjax格式
"""
import re
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from .tikz_plot_converter import TikzPlotConverter


class TikzConverter:
    """TikZ环境转换器"""

    # 简单宏替换映射
    SIMPLE_MACROS = {
        '\\Z': '\\mathbb{Z}',
        '\\N': '\\mathbb{N}',
        '\\Q': '\\mathbb{Q}',
        '\\R': '\\mathbb{R}',
        '\\C': '\\mathbb{C}',
        '\\F': '\\mathbb{F}',
        '\\P': '\\mathbb{P}',
        '\\A': '\\mathbb{A}',
        '\\eps': '\\varepsilon',
        '\\vphi': '\\varphi',
    }

    def __init__(self, plot_converter: "TikzPlotConverter"):
        self._plot_converter = plot_converter

    def convert(self, text: str) -> str:
        """转换所有TikZ环境"""
        # 匹配各种TikZ环境
        text = re.sub(
            r'\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}',
            self._convert_tikz_block, text
        )
        text = re.sub(
            r'\\begin\{tikzcd\}[\s\S]*?\\end\{tikzcd\}',
            self._convert_tikz_block, text
        )
        text = re.sub(
            r'\\begin\{circuitikz\}[\s\S]*?\\end\{circuitikz\}',
            self._convert_tikz_block, text
        )

        # 匹配独立的chemfig命令
        if r'\chemfig{' in text and '<script type="text/tikz">' not in text:
            text = re.sub(
                r'\\chemfig\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}',
                self._convert_chemfig_block, text
            )

        return text

    def _convert_tikz_block(self, match: re.Match) -> str:
        """转换TikZ代码块"""
        tikz_code = match.group(0)

        # 应用简单宏替换
        for macro, replacement in self.SIMPLE_MACROS.items():
            tikz_code = tikz_code.replace(macro, replacement)

        # 预处理plot命令
        tikz_code = self._plot_converter.convert(tikz_code)

        # 检测需要的包和库
        packages = self._detect_packages(tikz_code)
        tikzlibraries = self._detect_libraries(tikz_code)

        logger.info(f"[MathJax2Image] TikZ包: {packages}, 库: {tikzlibraries}")

        # 构建完整文档
        full_tikz = self._build_tikz_document(tikz_code, packages, tikzlibraries)

        # 包装为HTML
        return f'<div class="tikz-diagram"><script type="text/tikz">\n{full_tikz}\n</script></div>'

    def _convert_chemfig_block(self, match: re.Match) -> str:
        """转换chemfig命令"""
        chemfig_cmd = match.group(0)
        full_tikz = f"""\\usepackage{{amsmath}}
\\usepackage{{amsfonts}}
\\usepackage{{amssymb}}
\\usepackage{{chemfig}}
\\begin{{document}}
{chemfig_cmd}
\\end{{document}}"""
        logger.info(f"[MathJax2Image] chemfig独立命令: {chemfig_cmd[:100]}...")
        return f'<div class="tikz-diagram"><script type="text/tikz">\n{full_tikz}\n</script></div>'

    def _has_chinese(self, text: str) -> bool:
        """检测文本是否包含中文字符"""
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False

    def _detect_packages(self, tikz_code: str) -> list[str]:
        """检测需要的宏包"""
        packages = ['amsmath', 'amsfonts', 'amssymb']

        if 'chemfig' in tikz_code or 'chemname' in tikz_code:
            packages.append('chemfig')
        if 'tikzcd' in tikz_code or '\\arrow' in tikz_code:
            packages.append('tikz-cd')
        if 'circuitikz' in tikz_code or 'to[' in tikz_code:
            packages.append('circuitikz')
        if 'axis' in tikz_code or 'addplot' in tikz_code:
            packages.append('pgfplots')
        if 'tdplot' in tikz_code or '3d' in tikz_code.lower():
            packages.append('tikz-3dplot')
        if 'array' in tikz_code or 'tabular' in tikz_code:
            packages.append('array')

        return packages

    def _detect_libraries(self, tikz_code: str) -> list[str]:
        """检测需要的TikZ库"""
        libs = []

        if 'Stealth' in tikz_code or 'Latex' in tikz_code:
            libs.append('arrows.meta')
        if 'calc' in tikz_code or '($' in tikz_code:
            libs.append('calc')
        if 'positioning' in tikz_code or ' of=' in tikz_code or ' of ' in tikz_code:
            libs.append('positioning')
        if 'ellipse' in tikz_code or 'rectangle' in tikz_code or 'diamond' in tikz_code:
            libs.append('shapes.geometric')
        if 'shapes' in tikz_code:
            libs.append('shapes')
        if 'background' in tikz_code:
            libs.append('backgrounds')
        if 'fit=' in tikz_code:
            libs.append('fit')
        if 'pgfplots' in tikz_code:
            if 'calc' not in libs:
                libs.append('calc')

        return libs

    def _build_tikz_document(
        self, tikz_code: str, packages: list[str], tikzlibraries: list[str]
    ) -> str:
        """构建完整的TikZ文档"""
        usepackages = '\n'.join([f'\\usepackage{{{pkg}}}' for pkg in packages])
        usetikzlibs = ''
        if tikzlibraries:
            usetikzlibs = f"\\usetikzlibrary{{{','.join(tikzlibraries)}}}"

        pgfplots_config = ''
        if 'pgfplots' in packages:
            pgfplots_config = '\\pgfplotsset{compat=1.16}'

        # 检测中文并添加警告注释
        has_chinese = self._has_chinese(tikz_code)
        chinese_warning = ''
        if has_chinese:
            logger.warning("[MathJax2Image] TikZ代码包含中文，TikZJax不支持CJK字体，中文可能无法正确显示")
            chinese_warning = '% WARNING: TikZJax does not support CJK fonts, Chinese text may not render correctly\n'

        return f"""{chinese_warning}{usepackages}
{pgfplots_config}
{usetikzlibs}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""
