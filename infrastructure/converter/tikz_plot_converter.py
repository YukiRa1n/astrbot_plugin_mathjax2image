"""
TikZ plot命令转换器
将TikZ plot命令转换为坐标点序列（TikZJax不支持plot函数）
"""

import math
import re

from astrbot.api import logger
from ...utils import safe_eval_math


class TikzPlotConverter:
    """TikZ plot命令转换器"""

    def convert(self, tikz_code: str) -> str:
        """将TikZ plot命令转换为坐标点序列"""
        # 预处理：清理HTML实体
        tikz_code = self._clean_html_entities(tikz_code)

        # 匹配并转换 \\draw[options] plot (\\x, {expr});
        pattern = (
            r"\\draw\s*\[([^\]]*)\]\s*plot\s*\(\s*([^,]+)\s*,\s*\{([^}]+)\}\s*\)\s*;"
        )
        return re.sub(pattern, self._convert_plot_cmd, tikz_code)

    def _clean_html_entities(self, text: str) -> str:
        """清理HTML实体"""
        replacements = {
            "&nbsp;": " ",
            "&amp;": "&",
            "&lt;": "<",
            "&gt;": ">",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _convert_plot_cmd(self, match: re.Match) -> str:
        """转换单个plot命令"""
        full_match = match.group(0)
        options = match.group(1) or ""
        x_expr = match.group(2)
        y_expr = match.group(3)

        # 解析domain和samples
        domain = self._parse_domain(options)
        samples = self._parse_samples(options)

        if domain is None:
            logger.warning(f"[MathJax2Image] plot命令缺少domain: {full_match[:50]}")
            return full_match

        x_min, x_max = domain

        # 移除domain和samples选项，保留样式选项
        style_options = self._extract_style_options(options)

        # 生成坐标点
        points = self._generate_points(x_min, x_max, samples, x_expr, y_expr)

        if not points:
            logger.warning(f"[MathJax2Image] plot生成0个有效点: {full_match[:50]}")
            return f"% plot转换失败: {full_match[:30]}..."

        # 生成\\draw命令
        coords = " -- ".join(points)
        result = f"\\draw[{style_options}] {coords};"
        logger.info(f"[MathJax2Image] plot转换: {len(points)}个点")
        return result

    def _parse_domain(self, options: str) -> tuple[float, float] | None:
        """解析domain参数"""
        match = re.search(r"domain\s*=\s*([-\d.]+)\s*:\s*([-\d.]+)", options)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None

    def _parse_samples(self, options: str) -> int:
        """解析samples参数"""
        match = re.search(r"samples\s*=\s*(\d+)", options)
        return int(match.group(1)) if match else 50

    def _extract_style_options(self, options: str) -> str:
        """提取样式选项（移除domain和samples）"""
        style = re.sub(r",?\s*domain\s*=\s*[-\d.]+\s*:\s*[-\d.]+", "", options)
        style = re.sub(r",?\s*samples\s*=\s*\d+", "", style)
        return style.strip(" ,")

    def _generate_points(
        self, x_min: float, x_max: float, samples: int, x_expr: str, y_expr: str
    ) -> list[str]:
        """生成坐标点"""
        points = []
        step = (x_max - x_min) / (samples - 1) if samples > 1 else 0

        for i in range(samples):
            x = x_min + i * step
            x_val = self._eval_tikz_expr(x_expr, x)
            y_val = self._eval_tikz_expr(y_expr, x)

            if not (
                math.isnan(x_val)
                or math.isnan(y_val)
                or math.isinf(x_val)
                or math.isinf(y_val)
            ):
                points.append(f"({x_val:.4f},{y_val:.4f})")

        return points

    def _eval_tikz_expr(self, expr: str, x: float) -> float:
        """计算TikZ数学表达式"""
        # 替换\\x为实际值
        expr = expr.replace("\\x", str(x))

        # 替换TikZ/LaTeX数学函数
        # 注意：必须先替换 \\pi，再替换其他内容，避免反斜杠问题
        # 注意：必须先替换 log (避免被 ln 规则影响)，再替换 ln
        replacements = [
            (r"\\pi", str(math.pi)),  # 先替换 \\pi
            (r"\bpi\b", str(math.pi)),  # 再替换独立的 pi
            (r"sqrt\s*\(", "sqrt("),
            (r"sin\s*\(", "sin("),
            (r"cos\s*\(", "cos("),
            (r"tan\s*\(", "tan("),
            (r"exp\s*\(", "exp("),
            (
                r"\blog\s*\(",
                "log10(",
            ),  # log -> log10 (常用对数，使用 \b 避免匹配 ln 中的 log)
            (r"\bln\s*\(", "log("),  # ln -> log (自然对数)
            (r"abs\s*\(", "abs("),
            (r"\^", "**"),
        ]

        for pattern, repl in replacements:
            expr = re.sub(pattern, repl, expr)

        # 使用安全求值器替代 eval()
        return safe_eval_math(expr)
