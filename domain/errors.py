"""
领域层 - 错误类型定义
"""


class RenderError(Exception):
    """渲染错误基类"""
    pass


class BrowserError(RenderError):
    """浏览器相关错误"""
    pass


class DependencyError(RenderError):
    """依赖安装错误"""

    def __init__(self, message: str, install_command: str = ""):
        super().__init__(message)
        self.install_command = install_command


class ValidationError(RenderError):
    """验证错误"""

    def __init__(self, message: str, errors: list[str] = None):
        super().__init__(message)
        self.errors = errors or []
