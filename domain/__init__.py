"""
领域层 - 核心接口和错误定义
"""

from .errors import (
    BrowserError,
    DependencyError,
    RenderError,
    ValidationError,
)
from .interfaces import (
    IBrowserManager,
    IContentConverter,
    IDependencyInstaller,
    ILatexPreprocessor,
    ILatexValidator,
    IPageRenderer,
    IRenderOrchestrator,
)

__all__ = [
    "IContentConverter",
    "ILatexPreprocessor",
    "ILatexValidator",
    "IBrowserManager",
    "IPageRenderer",
    "IDependencyInstaller",
    "IRenderOrchestrator",
    "RenderError",
    "BrowserError",
    "DependencyError",
    "ValidationError",
]
