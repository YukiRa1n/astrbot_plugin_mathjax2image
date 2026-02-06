"""
Playwright依赖安装器
解决 libnspr4.so 等系统库缺失问题
"""

import asyncio
import ctypes
import platform
from typing import Optional

from astrbot.api import logger


class PlaywrightDependencyInstaller:
    """
    Playwright系统依赖安装器

    依赖安装策略:
    1. 启动时检测 (Lazy Check) - 首次调用时检测，避免插件加载时阻塞
    2. 检测方法 - 尝试加载关键 .so 文件 (ctypes.CDLL)
    3. 自动安装 - 优先使用 playwright install-deps chromium
    4. 降级处理 - 安装失败时记录详细错误，提供手动安装命令
    5. 缓存结果 - 安装成功后缓存状态，避免重复检测
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
        self._install_attempted: bool = False

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
        """检查并安装依赖

        Returns:
            是否安装成功（或已安装）
        """
        # 已确认安装成功
        if self._installed:
            return True

        # 已尝试安装但失败
        if self._install_attempted and not self._installed:
            return False

        # 检查是否需要安装
        if self.is_installed():
            return True

        # 尝试安装
        logger.info("[MathJax2Image] 正在安装Playwright系统依赖...")
        self._install_attempted = True

        success = await self._install_deps()
        if success:
            logger.info("[MathJax2Image] Playwright依赖安装成功")
            self._installed = True
            return True
        else:
            logger.error("[MathJax2Image] Playwright依赖安装失败")
            self._log_manual_install_instructions()
            return False

    async def _install_deps(self) -> bool:
        """执行依赖安装"""
        try:
            # 方法1: 使用 playwright install-deps
            process = await asyncio.create_subprocess_exec(
                "playwright",
                "install-deps",
                "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=300,  # 5分钟超时
            )

            if process.returncode == 0:
                return True

            logger.warning(
                f"[MathJax2Image] playwright install-deps 失败: {stderr.decode()}"
            )

            # 方法2: 尝试 apt-get (Debian/Ubuntu)
            return await self._try_apt_install()

        except FileNotFoundError:
            logger.warning("[MathJax2Image] playwright 命令不可用，尝试apt安装")
            return await self._try_apt_install()
        except asyncio.TimeoutError:
            logger.error("[MathJax2Image] 依赖安装超时(5分钟)")
            return False
        except Exception as e:
            logger.error(f"[MathJax2Image] 依赖安装异常: {e}")
            return False

    async def _try_apt_install(self) -> bool:
        """尝试使用apt安装依赖"""
        packages = [
            "libnss3",
            "libnspr4",
            "libatk1.0-0",
            "libatk-bridge2.0-0",
            "libdrm2",
            "libxkbcommon0",
            "libatspi2.0-0",
            "libxcomposite1",
            "libxdamage1",
            "libxfixes3",
            "libxrandr2",
            "libgbm1",
            "libpango-1.0-0",
            "libcairo2",
            "libasound2",
        ]

        try:
            # 更新包索引
            update_proc = await asyncio.create_subprocess_exec(
                "apt-get",
                "update",
                "-qq",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(update_proc.wait(), timeout=120)

            # 安装包
            install_proc = await asyncio.create_subprocess_exec(
                "apt-get",
                "install",
                "-y",
                "-qq",
                *packages,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(install_proc.communicate(), timeout=300)

            if install_proc.returncode == 0:
                return True

            logger.warning(f"[MathJax2Image] apt安装失败: {stderr.decode()}")
            return False

        except Exception as e:
            logger.warning(f"[MathJax2Image] apt安装异常: {e}")
            return False

    def _log_manual_install_instructions(self):
        """记录手动安装说明"""
        logger.error(
            "[MathJax2Image] 自动安装失败，请手动安装依赖:\n"
            "  Debian/Ubuntu:\n"
            "    sudo apt-get update\n"
            "    sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 "
            "libatk-bridge2.0-0 libdrm2 libxkbcommon0 libatspi2.0-0 "
            "libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 "
            "libpango-1.0-0 libcairo2 libasound2\n"
            "  或使用:\n"
            "    playwright install-deps chromium"
        )
