"""
LaTeX验证器
验证LaTeX语法正确性
"""
import re

from ...types import ValidationResult


class LatexValidator:
    """LaTeX验证器"""

    def validate(self, text: str) -> ValidationResult:
        """验证LaTeX语法

        Returns:
            ValidationResult(is_valid, errors)
        """
        errors = []

        # 1. 检查大括号匹配
        brace_error = self._check_braces(text)
        if brace_error:
            errors.append(brace_error)

        # 2. 检查\\frac参数
        frac_errors = self._check_frac(text)
        errors.extend(frac_errors)

        # 3. 检查积分语法
        integral_errors = self._check_integral(text)
        errors.extend(integral_errors)

        # 4. 检查$配对
        dollar_error = self._check_dollar(text)
        if dollar_error:
            errors.append(dollar_error)

        # 5. 检查环境配对
        env_errors = self._check_environments(text)
        errors.extend(env_errors)

        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def _check_braces(self, text: str) -> str | None:
        """检查大括号匹配"""
        clean_text = text.replace(r'\{', '').replace(r'\}', '')
        open_count = clean_text.count('{')
        close_count = clean_text.count('}')
        if open_count != close_count:
            return f"大括号不匹配: {{ 有 {open_count} 个，}} 有 {close_count} 个"
        return None

    def _check_frac(self, text: str) -> list[str]:
        """检查\\frac参数"""
        errors = []
        fracs = re.findall(r'\\frac\{([^}]*)\}(?:\{([^}]*)\})?', text)
        for frac in fracs:
            if not frac[1]:
                errors.append(f"\\frac 命令缺少第二个参数: \\frac{{{frac[0]}}}")
        return errors

    def _check_integral(self, text: str) -> list[str]:
        """检查积分语法"""
        errors = []
        pattern = r'\\int_\{([^}]*)\}\^\{([^}]*)\}'
        for match in re.finditer(pattern, text):
            lower, upper = match.group(1), match.group(2)
            if r'\frac' in lower and lower.count('{') > lower.count('}'):
                errors.append(f"积分下限中有未闭合的 \\frac: {lower[:30]}...")
            if r'\frac' in upper and upper.count('{') > upper.count('}'):
                errors.append(f"积分上限中有未闭合的 \\frac: {upper[:30]}...")
        return errors

    def _check_dollar(self, text: str) -> str | None:
        """检查$配对"""
        dollar_count = text.count('$') - text.count(r'\$')
        if dollar_count % 2 != 0:
            return "数学公式分隔符 $ 数量为奇数，可能未闭合"
        return None

    def _check_environments(self, text: str) -> list[str]:
        """检查环境配对"""
        errors = []
        environments = [
            ('tikzpicture', r'\\begin\{tikzpicture\}', r'\\end\{tikzpicture\}'),
            ('tikzcd', r'\\begin\{tikzcd\}', r'\\end\{tikzcd\}'),
            ('equation', r'\\begin\{equation\}', r'\\end\{equation\}'),
            ('align', r'\\begin\{align\}', r'\\end\{align\}'),
        ]
        for name, begin_pat, end_pat in environments:
            begin_count = len(re.findall(begin_pat, text))
            end_count = len(re.findall(end_pat, text))
            if begin_count != end_count:
                errors.append(
                    f"环境 {name} 不匹配: begin 有 {begin_count} 个，end 有 {end_count} 个"
                )
        return errors
