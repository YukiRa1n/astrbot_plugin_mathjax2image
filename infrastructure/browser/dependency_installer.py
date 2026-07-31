"""
Playwright依赖安装器
解决 libnspr4.so 等系统库缺失问题
"""

import ctypes
import platform
from typing import Optional

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")


class PlaywrightDependencyInstaller:
    """
    Playwright系统依赖安装器

    运行时只检测，不修改宿主机。系统依赖应在部署阶段安装。
    """

    REQUIRED_LIBS = [
        "libnspr4.so",
        "libnss3.so",
        "libatk-1.0.so.0",
        "libatk-bridge-2.0.so.0",
        "libdrm.so.2",
        "libxkbcommon.so.0",
        "libatspi.so.0",
        "libXcomposite.so.1",
        "libXdamage.so.1",
        "libXfixes.so.3",
        "libXrandr.so.2",
        "libgbm.so.1",
        "libpango-1.0.so.0",
        "libcairo.so.2",
        "libasound.so.2",
    ]

    def __init__(self):
        self._installed: Optional[bool] = None

    def is_installed(self) -> bool:
        """检查系统依赖是否已安装"""
        if self._installed is not None:
            return self._installed

        # Windows不需要检查系统依赖
        if platform.system() == "Windows":
            self._installed = True
            return True

        # macOS通常不需要额外依赖
        if platform.system() == "Darwin":
            self._installed = True
            return True

        # Linux: 检查关键库
        missing_libs = self._check_missing_libs()
        if missing_libs:
            logger.warning(f"[MathJax2Image] 检测到缺失的系统库: {missing_libs[:3]}...")
            self._installed = False
            return False

        self._installed = True
        return True

    def _check_missing_libs(self) -> list[str]:
        """检查缺失的系统库"""
        missing = []
        for lib in self.REQUIRED_LIBS:
            if not self._can_load_lib(lib):
                missing.append(lib)
        return missing

    def _can_load_lib(self, lib_name: str) -> bool:
        """尝试加载系统库"""
        try:
            ctypes.CDLL(lib_name)
            return True
        except OSError:
            return False

    async def check_and_install(self) -> bool:
        """检查运行依赖；不会执行系统包安装。"""
        if self.is_installed():
            return True
        logger.error(
            "[MathJax2Image] 缺少 Playwright 系统依赖，请在部署阶段执行: "
            "playwright install-deps chromium"
        )
        return False
