"""
领域层 - 核心接口和错误定义
"""

from .interfaces import (
    IContentConverter,
    ILatexPreprocessor,
    ILatexValidator,
    IBrowserManager,
    IPageRenderer,
    IDependencyInstaller,
    IRenderOrchestrator,
)
from .errors import (
    RenderError,
    BrowserError,
    DependencyError,
    ValidationError,
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
