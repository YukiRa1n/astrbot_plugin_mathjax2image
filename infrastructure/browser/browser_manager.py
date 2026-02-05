"""
浏览器管理器
管理Playwright浏览器实例的生命周期
"""
import asyncio
import traceback
from typing import Optional

from playwright.async_api import async_playwright, Browser, Playwright

from astrbot.api import logger
from ...domain.errors import BrowserError


class BrowserManager:
    """浏览器管理器 - 管理Playwright浏览器生命周期"""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def get_browser(self) -> Browser:
        """获取或创建浏览器实例（线程安全）"""
        async with self._lock:
            try:
                if self._browser is None or not self._browser.is_connected():
                    logger.info("[MathJax2Image] 正在启动浏览器...")
                    if self._playwright is None:
                        self._playwright = await async_playwright().start()
                        logger.debug("[MathJax2Image] Playwright 已启动")

                    self._browser = await self._playwright.chromium.launch(
                        headless=True,
                        args=[
                            '--disable-web-security',
                            '--allow-file-access-from-files',
                            '--disable-features=VizDisplayCompositor'
                        ]
                    )
                    logger.info("[MathJax2Image] 浏览器实例已创建")
                return self._browser

            except Exception as e:
                logger.error(f"[MathJax2Image] 浏览器启动失败: {type(e).__name__}: {e}")
                logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
                raise BrowserError(f"浏览器启动失败: {e}")

    async def close(self) -> None:
        """关闭浏览器和Playwright"""
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.warning(f"[MathJax2Image] 关闭浏览器时出错: {e}")
                finally:
                    self._browser = None

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning(f"[MathJax2Image] 关闭Playwright时出错: {e}")
                finally:
                    self._playwright = None

            logger.info("[MathJax2Image] 浏览器资源已释放")

    @property
    def is_connected(self) -> bool:
        """检查浏览器是否已连接"""
        return self._browser is not None and self._browser.is_connected()
