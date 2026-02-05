"""
MathJax2Image 类型定义
"""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class RenderMode(Enum):
    """渲染模式"""
    MATH = "math"      # 数学文章
    ARTICLE = "art"    # 普通文章
    DIRECT = "render"  # 直接渲染


@dataclass
class RenderConfig:
    """渲染配置"""
    bg_color: str = "#FDFBF0"
    viewport_width: int = 1150
    viewport_height: int = 2000
    mathjax_timeout: int = 10000
    tikz_timeout: int = 300000
    screenshot_timeout: int = 60000


@dataclass
class RenderResult:
    """渲染结果"""
    success: bool
    image_path: Optional[Path] = None
    error_message: Optional[str] = None


@dataclass
class LLMConfig:
    """LLM配置"""
    provider_id: str = ""
    math_prompt: str = ""
    article_prompt: str = ""


@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: list[str]

    @property
    def error_message(self) -> str:
        return "\n".join(self.errors)
