"""
领域层 - 核心接口定义
遵循依赖倒置原则(DIP)，定义抽象接口
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IContentConverter(Protocol):
    """内容转换器接口"""

    def convert(self, content: str) -> str:
        """转换内容"""
        ...


@runtime_checkable
class ILatexPreprocessor(Protocol):
    """LaTeX预处理器接口 - 组合多个转换器"""

    def preprocess(self, text: str) -> str:
        """预处理LaTeX文本"""
        ...


@runtime_checkable
class ILatexValidator(Protocol):
    """LaTeX验证器接口"""

    def validate(self, text: str) -> tuple[bool, str]:
        """验证LaTeX语法

        Returns:
            (is_valid, error_message)
        """
        ...


@runtime_checkable
class IBrowserManager(Protocol):
    """浏览器管理器接口"""

    async def get_browser(self) -> Any:
        """获取浏览器实例"""
        ...

    async def close(self) -> None:
        """关闭浏览器"""
        ...


@runtime_checkable
class IPageRenderer(Protocol):
    """页面渲染器接口"""

    async def render_to_image(self, html: str, output: Path) -> None:
        """将HTML渲染为图片"""
        ...


@runtime_checkable
class IDependencyInstaller(Protocol):
    """依赖安装器接口"""

    def is_installed(self) -> bool:
        """检查依赖是否已安装"""
        ...

    async def check_and_install(self) -> bool:
        """检查并安装依赖

        Returns:
            是否安装成功
        """
        ...


@runtime_checkable
class IRenderOrchestrator(Protocol):
    """渲染编排器接口"""

    async def render(self, content: str) -> Path:
        """渲染内容为图片"""
        ...

    async def close(self) -> None:
        """释放资源"""
        ...


class IMarkdownConverter(Protocol):
    """Markdown转换器接口"""

    def convert_to_html(self, markdown_text: str, bg_color: str) -> str:
        """将Markdown转换为完整HTML"""
        ...
