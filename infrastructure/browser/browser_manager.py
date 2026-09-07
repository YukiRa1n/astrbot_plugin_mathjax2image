"""
浏览器管理器
基于 Playwright 的高可靠无内存泄露浏览器页面池 (Page Pool)
支持在热重载或事件循环(asyncio loop)改变时的自动重连自愈。
"""

import asyncio
import sys

from playwright.async_api import (
    APIRequestContext,
    Browser,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")
from ...domain.errors import BrowserError

_browser_install_locks = {
    engine: asyncio.Lock() for engine in ("chromium", "firefox", "webkit")
}


async def _install_browser(engine: str) -> None:
    """Install a missing browser only after an actual launch failure.

    Args:
        engine: Playwright browser type name.

    Raises:
        BrowserError: The installer fails or exceeds its time budget.
    """
    async with _browser_install_locks[engine]:
        command = [sys.executable, "-m", "playwright", "install", engine]
        if engine == "chromium":
            command.append("--only-shell")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise BrowserError(f"Installing Playwright {engine} timed out") from exc
        if process.returncode != 0:
            detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
            raise BrowserError(
                f"Unable to install Playwright {engine}: {detail.strip()}"
            )
        logger.info("[MathJax2Image] Playwright %s installation completed", engine)


class BrowserManager:
    """浏览器管理器 - 管理 Playwright 页面池与生命周期"""

    def __init__(
        self,
        max_pages: int = 2,
        engine: str = "chromium",
        cdp_url: str = "",
        auto_install_browser: bool = False,
        page_wait_timeout: float = 30.0,
        max_idle_pages: int = 1,
        idle_timeout: float = 30.0,
    ):
        if engine not in {"chromium", "firefox", "webkit"}:
            raise ValueError(f"不支持的浏览器引擎: {engine}")
        if cdp_url and engine != "chromium":
            raise ValueError("CDP 后端仅支持 Chromium")
        self.max_pages = max(1, max_pages)
        self.engine = engine
        self.cdp_url = cdp_url.strip()
        self.auto_install_browser = bool(auto_install_browser)
        self._page_wait_timeout = max(0.001, float(page_wait_timeout))
        self._max_idle_pages = min(self.max_pages, max(0, int(max_idle_pages)))
        self._idle_timeout = max(0.0, float(idle_timeout))
        self._idle_deadline = 0.0
        self._idle_timer = None
        self._idle_cleanup_task = None
        self._playwright: Playwright | None = None
        self.request_context: APIRequestContext | None = None
        self._browser: Browser | None = None
        self._owns_browser = True
        self._pool = asyncio.Queue()
        self._page_available = asyncio.Event()
        self._configured_pages: set[Page] = set()
        self._resident_pages: set[Page] = set()
        self._owned_contexts = set()
        self._active_pages_count = 0
        self._lock = asyncio.Lock()
        self._startup_lock = asyncio.Lock()
        self._loop = None  # 绑定时的 asyncio 事件循环
        self._closed = False
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
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        browser = self._browser
        playwright = self._playwright
        request_context = self.request_context
        self.request_context = None
        owns = self._owns_browser
        contexts = tuple(self._owned_contexts)
        self._browser = None
        self._playwright = None
        self._pool = asyncio.Queue()
        self._page_available = asyncio.Event()
        self._configured_pages = set()
        self._resident_pages.clear()
        self._owned_contexts.clear()
        self._active_pages_count = 0

        # Best-effort close (old loop may already be dead)
        if browser is not None and owns:
            await self._best_effort_close(browser, "close")
        if not owns:
            for context in contexts:
                await self._best_effort_close(context, "close")
        if request_context is not None:
            await self._best_effort_close(request_context, "dispose")
        if playwright is not None:
            await self._best_effort_close(playwright, "stop")

    @staticmethod
    def _launch_options(engine: str) -> dict:
        # Playwright already supplies background-task and extension switches.
        # Its default headless channel selects the separate Chromium shell.
        return {"headless": True}

    async def get_browser(self) -> Browser:
        """获取或创建浏览器实例（自愈并兼容 Loop 重启）"""
        current_loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current_loop
        elif self._loop != current_loop:
            await self._force_cleanup_loop_resources()
            self._loop = current_loop

        async with self._startup_lock:
            if self._closed:
                raise BrowserError("BrowserManager is closed")
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            logger.info("[MathJax2Image] Starting Playwright %s", self.engine)
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            if self.request_context is None:
                self.request_context = await self._playwright.request.new_context()
            if self.cdp_url:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    self.cdp_url
                )
                self._owns_browser = False
            else:
                browser_type = getattr(self._playwright, self.engine)
                options = self._launch_options(self.engine)
                try:
                    # Let Playwright resolve the matching headless executable.
                    # chromium.executable_path points to the full browser, even
                    # when launch(headless=True) uses the smaller headless shell.
                    self._browser = await browser_type.launch(**options)
                except PlaywrightError as exc:
                    if "Executable doesn't exist" not in str(exc):
                        raise
                    if not self.auto_install_browser:
                        shell_flag = (
                            " --only-shell" if self.engine == "chromium" else ""
                        )
                        raise BrowserError(
                            f"Playwright {self.engine} is unavailable. Install it with: "
                            f"{sys.executable} -m playwright install {self.engine}{shell_flag}"
                        ) from exc
                    await _install_browser(self.engine)
                    self._browser = await browser_type.launch(**options)
                self._owns_browser = True
            return self._browser

    async def _create_page(self, browser: Browser, width: int, height: int) -> Page:
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            service_workers="block",
            accept_downloads=False,
        )
        try:
            page = await context.new_page()
            self._owned_contexts.add(context)
            return page
        except BaseException:
            await context.close()
            raise

    async def _dispose_page(self, page: Page) -> None:
        self._configured_pages.discard(page)
        self._resident_pages.discard(page)
        self._owned_contexts.discard(page.context)
        try:
            await page.context.close()
        except Exception:
            try:
                await page.close()
            except Exception:
                pass

    async def acquire_page(self, width: int, height: int) -> tuple[Page, bool]:
        """
        从池中拿取一个 Page

        Returns:
            (page, has_been_setup) - page 实例和是否已配置过路由和注入脚本的标记
        """
        deadline = asyncio.get_running_loop().time() + self._page_wait_timeout
        while True:
            async with self._lock:
                if self._closed:
                    raise BrowserError("BrowserManager is closed")
                browser = await self.get_browser()
                while not self._pool.empty():
                    page = self._pool.get_nowait()
                    try:
                        if page.is_closed():
                            raise BrowserError("Pooled page is closed")
                        if page.viewport_size != {"width": width, "height": height}:
                            await page.set_viewport_size(
                                {"width": width, "height": height}
                            )
                        return page, page in self._configured_pages
                    except BaseException as exc:
                        self._active_pages_count = max(0, self._active_pages_count - 1)
                        await self._dispose_page(page)
                        self._page_available.set()
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                if self._active_pages_count < self.max_pages:
                    page = await self._create_page(browser, width, height)
                    self._active_pages_count += 1
                    return page, False
                # Clear under the same lock used when disposing occupied pages.
                self._page_available.clear()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise BrowserError("Timed out waiting for available page in pool")
            try:
                await asyncio.wait_for(self._page_available.wait(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise BrowserError(
                    "Timed out waiting for available page in pool"
                ) from exc

    async def release_page(self, page: Page, exception_occurred: bool = False) -> None:
        """Return a healthy page, keeping only the configured number warm.

        Args:
            page: Leased page to release.
            exception_occurred: Whether the page must be discarded.
        """
        if page is None:
            return
        discard = (
            exception_occurred
            or page.is_closed()
            or self._closed
            or self._max_idle_pages == 0
        )
        try:
            if not discard:
                if page in self._resident_pages:
                    await page.evaluate("() => window.__clearRenderContent()")
                else:
                    await page.goto("about:blank")
                async with self._lock:
                    discard = self._closed or self._pool.qsize() >= self._max_idle_pages
                    if not discard:
                        self._configured_pages.add(page)
                        self._pool.put_nowait(page)
                        self._page_available.set()
                        if self._idle_timeout > 0:
                            loop = asyncio.get_running_loop()
                            self._idle_deadline = loop.time() + self._idle_timeout
                            if self._idle_timer is not None:
                                self._idle_timer.cancel()

                            def expire():
                                self._idle_timer = None
                                self._idle_cleanup_task = asyncio.create_task(
                                    self._expire_idle_pages()
                                )

                            self._idle_timer = loop.call_later(
                                self._idle_timeout, expire
                            )
        except asyncio.CancelledError:
            discard = True
            raise
        except Exception as exc:
            discard = True
            logger.warning(
                "[MathJax2Image] Discarding page after release failure: %s", exc
            )
        finally:
            if discard:
                try:
                    await self._dispose_page(page)
                finally:
                    async with self._lock:
                        self._active_pages_count = max(0, self._active_pages_count - 1)
                        self._page_available.set()

    async def _expire_idle_pages(self) -> None:
        """Release idle renderer processes while retaining shared asset responses."""
        async with self._lock:
            if self._closed or asyncio.get_running_loop().time() < self._idle_deadline:
                return
            while not self._pool.empty():
                page = self._pool.get_nowait()
                self._active_pages_count = max(0, self._active_pages_count - 1)
                await self._dispose_page(page)
            self._page_available.set()

    async def close(self) -> None:
        """关闭所有浏览器资源并清空池"""
        self._closed = True
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        if self._idle_cleanup_task is not None:
            await self._idle_cleanup_task
        self._page_available.set()
        async with self._lock, self._startup_lock:
            while not self._pool.empty():
                page = self._pool.get_nowait()
                try:
                    await self._dispose_page(page)
                except Exception:
                    pass

            # 关闭所有活跃 context（CDP 共享模式下 browser.close 不负责）
            if self._browser and not self._owns_browser:
                try:
                    for ctx in tuple(self._owned_contexts):
                        try:
                            await ctx.close()
                        except Exception:
                            pass
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

            if self.request_context is not None:
                await self._best_effort_close(self.request_context, "dispose")
                self.request_context = None

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning(f"[MathJax2Image] 停止 Playwright 时出错: {e}")
                finally:
                    self._playwright = None

            self._active_pages_count = 0
            self._configured_pages = set()
            self._resident_pages.clear()
            self._owned_contexts.clear()
            self._loop = None
            logger.info("[MathJax2Image] 浏览器共享页面池已完全销毁")

    @property
    def is_connected(self) -> bool:
        """检查浏览器是否已连接"""
        return self._browser is not None and self._browser.is_connected()
