"""
TikZ plot命令转换器
将TikZ plot命令转换为坐标点序列（TikZJax不支持plot函数）
"""

import math
import re

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")
from ...utils import safe_eval_math


class TikzPlotConverter:
    """TikZ plot命令转换器"""

    # 每次 convert() 总评估预算：samples×plots 可能被攻击者放大
    # （50 个 plot × 2000 点 = 10 万次 AST 解析，实测阻塞事件循环约 460 秒）。
    # 预算按"点"计（每点 x/y 各评估 1 次），单 plot 正常 2000 点够用。
    MAX_EVAL_POINTS = 4000

    def __init__(self) -> None:
        self._eval_points_used = 0
        # 表达式 → 可复用求值器缓存（同一 plot 的 x/y 表达式通常重复，
        # 避免每个点都重新 ast.parse，极大降低 CPU 开销）
        self._expr_cache: dict[str, object] = {}

    def convert(self, tikz_code: str) -> str:
        """将TikZ plot命令转换为坐标点序列"""
        # 每次转换重置预算和缓存
        self._eval_points_used = 0
        self._expr_cache.clear()
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
        samples = int(match.group(1)) if match else 50
        # 上限 2000 点：每点约 15 字符，2000 点约 3 万字符，
        # 避免生成的坐标串绕过 MAX_TIKZ_LENGTH 造成渲染放大
        return min(samples, 2000)

    def _extract_style_options(self, options: str) -> str:
        """提取样式选项（移除domain和samples）"""
        style = re.sub(r",?\s*domain\s*=\s*[-\d.]+\s*:\s*[-\d.]+", "", options)
        style = re.sub(r",?\s*samples\s*=\s*\d+", "", style)
        return style.strip(" ,")

    def _generate_points(
        self, x_min: float, x_max: float, samples: int, x_expr: str, y_expr: str
    ) -> list[str]:
        """生成坐标点（受总评估预算约束，防止 samples×plots 放大 DoS）"""
        points = []
        step = (x_max - x_min) / (samples - 1) if samples > 1 else 0

        # 本 plot 最多还能评估的点数（x/y 各算 1 次）
        remaining = max(0, self.MAX_EVAL_POINTS - self._eval_points_used)
        if remaining < 2:
            logger.warning(
                "[MathJax2Image] plot 总评估点数超过预算，已跳过"
            )
            return points
        effective_samples = min(samples, remaining // 2)

        for i in range(effective_samples):
            x = x_min + i * step
            x_val = self._eval_tikz_expr(x_expr, x)
            y_val = self._eval_tikz_expr(y_expr, x)
            self._eval_points_used += 2

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
        # 替换\x为实际值（使用词边界避免误替换如\xi）
        replaced = re.sub(r'\\x(?![a-zA-Z])', str(x), expr)

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
            replaced = re.sub(pattern, repl, replaced)

        # 使用安全求值器替代 eval()
        return safe_eval_math(replaced)
