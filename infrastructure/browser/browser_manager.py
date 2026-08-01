"""
浏览器管理器
基于 Playwright 的高可靠无内存泄露浏览器页面池 (Page Pool)
支持在热重载或事件循环(asyncio loop)改变时的自动重连自愈。
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import async_playwright, Browser, Playwright, Page

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")
from ...domain.errors import BrowserError

_browser_install_locks = {
    engine: asyncio.Lock() for engine in ("chromium", "firefox", "webkit")
}
_installed_browsers: set[str] = set()


async def _ensure_browser_installed(
    engine: str = "chromium",
    *,
    allow_install: bool = False,
):
    """确保所选 Playwright 浏览器已安装。"""
    if engine in _installed_browsers:
        return

    async with _browser_install_locks[engine]:
        if engine in _installed_browsers:
            return
        try:
            async with async_playwright() as playwright:
                executable = Path(getattr(playwright, engine).executable_path)
                if not executable.is_file():
                    raise FileNotFoundError(executable)
            _installed_browsers.add(engine)
            logger.info(f"[MathJax2Image] Playwright {engine} 已就绪")
            return
        except Exception as exc:
            if not allow_install:
                raise BrowserError(
                    f"Playwright {engine} is unavailable. Install it during deployment with: "
                    f"{sys.executable} -m playwright install {engine}"
                ) from exc
            launch_failure = exc

        logger.info(f"[MathJax2Image] 正在安装 Playwright {engine}...")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "playwright",
            "install",
            engine,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            # 带超时安装，防止网络挂起永久阻塞整个渲染管线
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=300
            )
        except asyncio.TimeoutError:
            logger.error(
                f"[MathJax2Image] 安装 Playwright {engine} 超时（300s），终止安装进程"
            )
            try:
                process.kill()
            except Exception:
                pass
            try:
                await process.wait()
            except Exception:
                pass
            raise BrowserError(f"安装 Playwright {engine} 超时") from launch_failure
        if process.returncode != 0:
            detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
            raise BrowserError(
                f"无法安装 Playwright {engine}: {detail.strip()}"
            ) from launch_failure

        _installed_browsers.add(engine)
        logger.info(f"[MathJax2Image] Playwright {engine} 安装完成")


class BrowserManager:
    """浏览器管理器 - 管理 Playwright 页面池与生命周期"""

    def __init__(
        self,
        max_pages: int = 2,
        engine: str = "chromium",
        cdp_url: str = "",
        auto_install_browser: bool = False,
    ):
        if engine not in {"chromium", "firefox", "webkit"}:
            raise ValueError(f"不支持的浏览器引擎: {engine}")
        if cdp_url and engine != "chromium":
            raise ValueError("CDP 后端仅支持 Chromium")
        self.max_pages = max(1, max_pages)
        self.engine = engine
        self.cdp_url = cdp_url.strip()
        self.auto_install_browser = bool(auto_install_browser)
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._owns_browser = True
        self._pool = asyncio.Queue()
        self._configured_pages: set[Page] = set()
        self._active_pages_count = 0
        self._lock = asyncio.Lock()
        self._loop = None  # 绑定时的 asyncio 事件循环
        logger.info(
            f"[MathJax2Image] BrowserManager 初始化完成，池页面上限: {max_pages}"
        )

    async def _best_effort_close(self, obj, method_name: str) -> None:
        """尽力关闭浏览器/playwright 资源，旧事件循环可能已失效。"""
        try:
            fn = getattr(obj, method_name, None)
            if fn is not None:
                result = fn()
                if asyncio.iscoroutine(result):
                    try:
                        await asyncio.wait_for(result, timeout=2.0)
                    except Exception:
                        pass
        except Exception:
            pass

    async def _force_cleanup_loop_resources(self):
        """当事件循环改变时强行清理僵尸连接"""
        logger.info("[MathJax2Image] 检测到运行 Loop 改变，强行重置浏览器页面池")
        browser = self._browser
        playwright = self._playwright
        owns = self._owns_browser
        self._browser = None
        self._playwright = None
        self._pool = asyncio.Queue()
        self._configured_pages = set()
        self._active_pages_count = 0

        # Best-effort close (old loop may already be dead)
        if browser is not None and owns:
            await self._best_effort_close(browser, "close")
        if playwright is not None:
            await self._best_effort_close(playwright, "stop")

    @staticmethod
    def _launch_options(engine: str) -> dict:
        options = {"headless": True}
        if engine == "chromium":
            options["args"] = [
                "--disable-features=VizDisplayCompositor",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--js-flags=--max-old-space-size=512",
            ]
        return options

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

            if self._playwright is None:
                if not self.cdp_url:
                    await _ensure_browser_installed(
                        self.engine,
                        allow_install=self.auto_install_browser,
                    )
                self._playwright = await async_playwright().start()
                logger.debug("[MathJax2Image] Playwright 已启动")

            if self.cdp_url:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    self.cdp_url
                )
                self._owns_browser = False
                logger.info(f"[MathJax2Image] 已连接共享 CDP: {self.cdp_url}")
            else:
                browser_type = getattr(self._playwright, self.engine)
                self._browser = await browser_type.launch(
                    **self._launch_options(self.engine)
                )
                self._owns_browser = True
                logger.info(
                    f"[MathJax2Image] Playwright {self.engine} 进程已创建"
                )
        return self._browser

    async def _create_page(self, browser: Browser, width: int, height: int) -> Page:
        context = await browser.new_context(
            viewport={"width": width, "height": height}
        )
        try:
            return await context.new_page()
        except Exception:
            await context.close()
            raise

    async def _dispose_page(self, page: Page) -> None:
        self._configured_pages.discard(page)
        try:
            await page.context.close()
        except Exception:
            try:
                await page.close()
            except Exception:
                pass

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
                        self._configured_pages.discard(page)
                        continue
                    await page.set_viewport_size({"width": width, "height": height})
                    has_been_setup = page in self._configured_pages
                    return page, has_been_setup
                except Exception as e:
                    logger.warning(
                        f"[MathJax2Image] 从池中拿取的 Page 健康度检查失败，予以舍弃: {e}"
                    )
                    self._active_pages_count = max(0, self._active_pages_count - 1)
                    try:
                        await self._dispose_page(page)
                    except Exception:
                        pass

            # 2. 如果池空且活动页数未达上限，则创建新页
            if self._active_pages_count < self.max_pages:
                logger.info(
                    f"[MathJax2Image] 创建新渲染 Page (活动页数: {self._active_pages_count + 1})"
                )
                try:
                    page = await self._create_page(browser, width, height)
                    self._active_pages_count += 1
                    return page, False
                except Exception as e:
                    logger.error(f"[MathJax2Image] 创建 Page 失败: {e}")
                    raise BrowserError(f"创建 Page 失败: {e}")

        # 3. Block-wait for a page to be released back to pool
        logger.debug("[MathJax2Image] 页面池全部满载忙碌，正在阻塞等待空闲归还...")
        max_retries = 3
        for _ in range(max_retries):
            try:
                page = await asyncio.wait_for(self._pool.get(), timeout=30.0)
            except asyncio.TimeoutError:
                raise BrowserError("Timed out waiting for available page in pool")
            try:
                if page.is_closed():
                    async with self._lock:
                        self._active_pages_count = max(0, self._active_pages_count - 1)
                    continue
                await page.set_viewport_size({"width": width, "height": height})
                has_been_setup = page in self._configured_pages
                return page, has_been_setup
            except Exception:
                async with self._lock:
                    self._active_pages_count = max(0, self._active_pages_count - 1)
                try:
                    await self._dispose_page(page)
                except Exception:
                    pass
        raise BrowserError("Failed to acquire page after retries")

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
                await self._dispose_page(page)
            except Exception:
                pass
            return

        try:
            # DOM 净化
            await page.goto("about:blank")
            self._configured_pages.add(page)
            self._pool.put_nowait(page)
            logger.debug("[MathJax2Image] 页面已完成自净并归还至页面池")
        except Exception as e:
            logger.warning(f"[MathJax2Image] 归还页面至页面池出错，进行销毁: {e}")
            async with self._lock:
                self._active_pages_count = max(0, self._active_pages_count - 1)
            try:
                await self._dispose_page(page)
            except Exception:
                pass

    async def close(self) -> None:
        """关闭所有浏览器资源并清空池"""
        async with self._lock:
            while not self._pool.empty():
                page = self._pool.get_nowait()
                try:
                    await self._dispose_page(page)
                except Exception:
                    pass

            if self._browser and self._owns_browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.warning(f"[MathJax2Image] 关闭浏览器时出错: {e}")
                finally:
                    self._browser = None
            else:
                self._browser = None

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning(f"[MathJax2Image] 停止 Playwright 时出错: {e}")
                finally:
                    self._playwright = None

            self._active_pages_count = 0
            self._configured_pages = set()
            self._loop = None
            logger.info("[MathJax2Image] 浏览器共享页面池已完全销毁")

    @property
    def is_connected(self) -> bool:
        """检查浏览器是否已连接"""
        return self._browser is not None and self._browser.is_connected()
