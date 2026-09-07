"""Precompute bounded TikZ curves without per-point parsing or shared counters."""

import math
import re

try:
    from astrbot.api import logger
except ModuleNotFoundError:
    import logging

    logger = logging.getLogger("astrbot")

from ...domain.errors import PreprocessError
from ...utils.safe_eval import compile_math_expression


class TikzPlotConverter:
    """Convert expression plots into explicit numeric coordinates."""

    MAX_EVAL_POINTS = 4000

    def __init__(self, precompute_pgfplots: bool = True, max_plot_points: int = 6400):
        self._precompute_pgfplots = precompute_pgfplots
        self._max_plot_points = max(4, int(max_plot_points))

    def convert(self, tikz_code: str) -> str:
        """Precompute plots with a budget local to this conversion.

        Args:
            tikz_code: A single TikZ picture.

        Returns:
            TikZ with supported expressions replaced by sampled coordinates.
        """
        tikz_code = self._clean_html_entities(tikz_code)
        budget = [self.MAX_EVAL_POINTS]
        pattern = (
            r"\\draw\s*\[([^\]]*)\]\s*plot\s*\(\s*([^,]+)\s*,\s*\{([^}]+)\}\s*\)\s*;"
        )
        tikz_code = re.sub(
            pattern, lambda match: self._convert_plot_cmd(match, budget), tikz_code
        )
        if self._precompute_pgfplots and re.search(r"\\addplot\s*3", tikz_code):
            from .pgfplots_preprocessor import PgfplotsPreprocessor

            tikz_code = PgfplotsPreprocessor(self._max_plot_points).convert(tikz_code)
        if len(tikz_code) > 512000:
            raise PreprocessError("采样后的 TikZ 内容超过 512000 字符，请拆分图形")
        return tikz_code

    def _clean_html_entities(self, text: str) -> str:
        """Decode the small set of entities accepted by the existing converter."""
        for old, new in {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">"}.items():
            text = text.replace(old, new)
        return text

    def _convert_plot_cmd(self, match: re.Match, budget: list[int]) -> str:
        """Precompute one curve while preserving gaps at undefined samples."""
        options = match.group(1) or ""
        domain = self._parse_domain(options)
        if domain is None:
            return match.group(0)
        samples = self._parse_samples(options)
        if samples * 2 > budget[0]:
            raise PreprocessError("TikZ 曲线采样总预算不足，请减少 samples 或拆分图形")
        try:
            points = self._generate_points(
                *domain, samples, match.group(2), match.group(3)
            )
        except ValueError:
            return match.group(0)
        budget[0] -= samples * 2
        style = self._extract_style_options(options)
        segments, current = [], []
        for point in [*points, None]:
            if point is None:
                if current:
                    segments.append(" -- ".join(current))
                    current = []
            else:
                current.append(point)
        if not segments:
            raise PreprocessError("TikZ 曲线没有有效采样点，请检查函数定义域")
        logger.debug("[MathJax2Image] Precomputed %s curve samples", samples)
        return "\n".join(f"\\draw[{style}] {segment};" for segment in segments)

    def _parse_domain(self, options: str) -> tuple[float, float] | None:
        """Read numeric domain endpoints without evaluating TeX commands."""
        match = re.search(r"domain\s*=\s*([-+\d.eE]+)\s*:\s*([-+\d.eE]+)", options)
        if match:
            try:
                values = tuple(float(value) for value in match.groups())
                if all(math.isfinite(value) for value in values):
                    return values
            except ValueError:
                pass
        return None

    def _parse_samples(self, options: str) -> int:
        """Read a bounded sample count for a plain TikZ curve."""
        match = re.search(r"samples\s*=\s*(-?\d+)", options)
        samples = int(match.group(1)) if match else 50
        if samples < 1:
            raise PreprocessError("samples 必须大于 0")
        return min(samples, 2000)

    def _extract_style_options(self, options: str) -> str:
        """Keep drawing styles and remove sampling instructions."""
        style = re.sub(r",?\s*domain\s*=\s*[-+\d.eE]+\s*:\s*[-+\d.eE]+", "", options)
        return re.sub(r",?\s*samples\s*=\s*\d+", "", style).strip(" ,")

    def _generate_points(
        self, x_min: float, x_max: float, samples: int, x_expr: str, y_expr: str
    ) -> list[str | None]:
        """Evaluate a curve over its entire domain with two compiled expressions."""
        expressions = [
            re.sub(r"\\(x|pi)(?![a-zA-Z])", r"\1", expr).replace("^", "**")
            for expr in (x_expr, y_expr)
        ]
        evaluate_x, evaluate_y = [
            compile_math_expression(expr, ("x",), trig_degrees=True)
            for expr in expressions
        ]
        points = []
        for index in range(samples):
            x = (
                x_min + (x_max - x_min) * index / (samples - 1)
                if samples > 1
                else x_min
            )
            x_value, y_value = evaluate_x(x), evaluate_y(x)
            points.append(
                f"({x_value:.8g},{y_value:.8g})"
                if math.isfinite(x_value) and math.isfinite(y_value)
                else None
            )
        return points

    def _eval_tikz_expr(self, expr: str, x: float) -> float:
        """Evaluate one TikZ expression using PGF's degree convention."""
        expr = re.sub(r"\\(x|pi)(?![a-zA-Z])", r"\1", expr).replace("^", "**")
        try:
            return compile_math_expression(expr, ("x",), trig_degrees=True)(x)
        except ValueError:
            return math.nan
