"""
浏览器管理器
基于 Playwright 的高可靠无内存泄露浏览器页面池 (Page Pool)
支持在热重载或事件循环(asyncio loop)改变时的自动重连自愈。
"""

import asyncio
from typing import Optional, Tuple

from playwright.async_api import async_playwright, Browser, Playwright, Page

from astrbot.api import logger
from ...domain.errors import BrowserError

# 浏览器安装标记
_browser_installed = False


async def _ensure_browser_installed():
    """确保 Chromium 浏览器已安装"""
    global _browser_installed
    if _browser_installed:
        return

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            await browser.close()
        _browser_installed = True
        logger.info("[MathJax2Image] Chromium 浏览器已就绪")
    except Exception:
        logger.info("[MathJax2Image] 首次运行，正在自动安装 Chromium...")
        try:
            process = await asyncio.create_subprocess_exec(
                "playwright",
                "install",
                "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.wait()
            _browser_installed = True
            logger.info("[MathJax2Image] Chromium 安装完成")
        except Exception as e:
            logger.error(f"[MathJax2Image] 安装 Chromium 失败: {e}")
            raise BrowserError(
                "无法自动安装 Chromium，请手动运行: playwright install chromium"
            )


class BrowserManager:
    """浏览器管理器 - 管理 Playwright 页面池与生命周期"""

    def __init__(self, max_pages: int = 4):
        self.max_pages = max_pages
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._pool = asyncio.Queue()
        self._active_pages_count = 0
        self._lock = asyncio.Lock()
        self._loop = None  # 绑定时的 asyncio 事件循环
        logger.info(
            f"[MathJax2Image] BrowserManager 初始化完成，池页面上限: {max_pages}"
        )

    async def _force_cleanup_loop_resources(self):
        """当事件循环改变时强行清理僵尸连接"""
        logger.info("[MathJax2Image] 检测到运行 Loop 改变，强行重置浏览器页面池")
        self._browser = None
        self._playwright = None
        self._pool = asyncio.Queue()
        self._active_pages_count = 0

    async def get_browser(self) -> Browser:
        """获取或创建浏览器实例（自愈并兼容 Loop 重启）"""
        current_loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current_loop
        elif self._loop != current_loop:
            await self._force_cleanup_loop_resources()
            self._loop = current_loop

        if self._browser is None or not self._browser.is_connected():
            logger.info("[MathJax2Image] 正在启动浏览器实例...")
            await _ensure_browser_installed()

            if self._playwright is None:
                self._playwright = await async_playwright().start()
                logger.debug("[MathJax2Image] Playwright 已启动")

            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-web-security",
                    "--allow-file-access-from-files",
                    "--disable-features=VizDisplayCompositor",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--js-flags=--max-old-space-size=512",
                ],
            )
            logger.info("[MathJax2Image] 共享浏览器进程已成功创建")
        return self._browser

    async def acquire_page(self, width: int, height: int) -> Tuple[Page, bool]:
        """
        从池中拿取一个 Page

        Returns:
            (page, has_been_setup) - page 实例和是否已配置过路由和注入脚本的标记
        """
        async with self._lock:
            browser = await self.get_browser()

            # 1. 尝试从空闲池中拿取
            while not self._pool.empty():
                page = self._pool.get_nowait()
                try:
                    if page.is_closed():
                        self._active_pages_count = max(0, self._active_pages_count - 1)
                        continue
                    await page.set_viewport_size({"width": width, "height": height})
                    # 标识此页面是否已经设置过属性
                    has_been_setup = getattr(page, "_has_been_setup", False)
                    return page, has_been_setup
                except Exception as e:
                    logger.warning(
                        f"[MathJax2Image] 从池中拿取的 Page 健康度检查失败，予以舍弃: {e}"
                    )
                    self._active_pages_count = max(0, self._active_pages_count - 1)
                    try:
                        await page.close()
                    except Exception:
                        pass

            # 2. 如果池空且活动页数未达上限，则创建新页
            if self._active_pages_count < self.max_pages:
                logger.info(
                    f"[MathJax2Image] 创建新渲染 Page (活动页数: {self._active_pages_count + 1})"
                )
                try:
                    page = await browser.new_page(
                        viewport={"width": width, "height": height}
                    )
                    page._has_been_setup = False
                    self._active_pages_count += 1
                    return page, False
                except Exception as e:
                    logger.error(f"[MathJax2Image] 创建 Page 失败: {e}")
                    raise BrowserError(f"创建 Page 失败: {e}")

        # 3. 阻塞等待页面被释放归还
        logger.debug("[MathJax2Image] 页面池全部满载忙碌，正在阻塞等待空闲归还...")
        page = await self._pool.get()
        try:
            if page.is_closed():
                self._active_pages_count = max(0, self._active_pages_count - 1)
                return await self.acquire_page(width, height)
            await page.set_viewport_size({"width": width, "height": height})
            has_been_setup = getattr(page, "_has_been_setup", False)
            return page, has_been_setup
        except Exception:
            self._active_pages_count = max(0, self._active_pages_count - 1)
            try:
                await page.close()
            except Exception:
                pass
            return await self.acquire_page(width, height)

    async def release_page(self, page: Page, exception_occurred: bool = False) -> None:
        """归还或销毁 Page (DOM 自净化)"""
        if page is None:
            return

        if exception_occurred or page.is_closed():
            logger.warning(
                "[MathJax2Image] 渲染过程中发生异常或页面已被关闭，强制销毁并剔除"
            )
            async with self._lock:
                self._active_pages_count = max(0, self._active_pages_count - 1)
            try:
                await page.close()
            except Exception:
                pass
            return

        try:
            # DOM 净化
            await page.goto("about:blank")
            page._has_been_setup = True  # 下次复用该页面时，标记已装载过基础脚本
            self._pool.put_nowait(page)
            logger.debug("[MathJax2Image] 页面已完成自净并归还至页面池")
        except Exception as e:
            logger.warning(f"[MathJax2Image] 归还页面至页面池出错，进行销毁: {e}")
            async with self._lock:
                self._active_pages_count = max(0, self._active_pages_count - 1)
            try:
                await page.close()
            except Exception:
                pass

    async def close(self) -> None:
        """关闭所有浏览器资源并清空池"""
        async with self._lock:
            while not self._pool.empty():
                page = self._pool.get_nowait()
                try:
                    await page.close()
                except Exception:
                    pass

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
                    logger.warning(f"[MathJax2Image] 停止 Playwright 时出错: {e}")
                finally:
                    self._playwright = None

            self._active_pages_count = 0
            logger.info("[MathJax2Image] 浏览器共享页面池已完全销毁")

    @property
    def is_connected(self) -> bool:
        """检查浏览器是否已连接"""
        return self._browser is not None and self._browser.is_connected()
