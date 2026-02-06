"""
领域层 - 错误类型定义
"""

from enum import Enum


class ErrorCode(Enum):
    """错误代码枚举"""

    BROWSER_LAUNCH_FAILED = "BROWSER_LAUNCH_FAILED"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    RENDER_TIMEOUT = "RENDER_TIMEOUT"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    LLM_CALL_FAILED = "LLM_CALL_FAILED"
    SAFE_EVAL_FAILED = "SAFE_EVAL_FAILED"
    PREPROCESS_FAILED = "PREPROCESS_FAILED"


class RenderError(Exception):
    """渲染错误基类"""

    def __init__(self, message: str, code: ErrorCode = None):
        super().__init__(message)
        self.code = code


class BrowserError(RenderError):
    """浏览器相关错误"""

    def __init__(self, message: str):
        super().__init__(message, code=ErrorCode.BROWSER_LAUNCH_FAILED)


class DependencyError(RenderError):
    """依赖安装错误"""

    def __init__(self, message: str, install_command: str = ""):
        super().__init__(message, code=ErrorCode.DEPENDENCY_MISSING)
        self.install_command = install_command


class ValidationError(RenderError):
    """验证错误"""

    def __init__(self, message: str, errors: list[str] = None):
        super().__init__(message, code=ErrorCode.VALIDATION_FAILED)
        self.errors = errors or []


class PreprocessError(RenderError):
    """预处理错误"""

    def __init__(self, message: str):
        super().__init__(message, code=ErrorCode.PREPROCESS_FAILED)


class LLMError(RenderError):
    """LLM调用错误"""

    def __init__(self, message: str):
        super().__init__(message, code=ErrorCode.LLM_CALL_FAILED)


class SafeEvalError(RenderError):
    """安全求值错误"""

    def __init__(self, message: str):
        super().__init__(message, code=ErrorCode.SAFE_EVAL_FAILED)
