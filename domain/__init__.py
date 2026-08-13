"""
领域层 - 错误定义
"""

from .errors import (
    RenderError,
    BrowserError,
    DependencyError,
    ValidationError,
)

__all__ = [
    "RenderError",
    "BrowserError",
    "DependencyError",
    "ValidationError",
]
