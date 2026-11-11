"""
领域层 - 核心接口定义
遵循依赖倒置原则(DIP)，定义抽象接口
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..plugin_types import ValidationResult


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

    def validate(self, text: str) -> "ValidationResult":
        """验证LaTeX语法

        Returns:
            ValidationResult(is_valid, errors)
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


@runtime_checkable
class ILLMOrchestrator(Protocol):
    """LLM编排器接口"""

    async def process_with_llm(self, content: str, mode: str) -> str:
        """使用LLM处理内容

        Args:
            content: 输入内容
            mode: 处理模式（math/article）

        Returns:
            处理后的内容
        """
        ...


@runtime_checkable
class ICommandHandler(Protocol):
    """命令处理器接口"""

    async def handle_command(self, command: str, args: list[str]) -> Any:
        """处理命令

        Args:
            command: 命令名称
            args: 命令参数

        Returns:
            命令执行结果
        """
        ...


@runtime_checkable
class ILLMToolHandler(Protocol):
    """LLM工具处理器接口"""

    async def call_llm_tool(self, tool_name: str, **kwargs: Any) -> Any:
        """调用LLM工具

        Args:
            tool_name: 工具名称
            **kwargs: 工具参数

        Returns:
            工具执行结果
        """
        ...


@runtime_checkable
class ISafeEvaluator(Protocol):
    """安全求值器接口"""

    def safe_eval(self, expression: str, allowed_names: dict[str, Any]) -> Any:
        """安全求值表达式

        Args:
            expression: 待求值的表达式
            allowed_names: 允许的变量名和值

        Returns:
            求值结果
        """
        ...


@runtime_checkable
class IMermaidConverter(Protocol):
    """Mermaid转换器接口"""

    def convert(self, text: str) -> str:
        """将包含Mermaid代码块的文本转换为受控HTML格式

        Args:
            text: 包含Mermaid代码块的文本

        Returns:
            转换后的文本
        """
        ...
