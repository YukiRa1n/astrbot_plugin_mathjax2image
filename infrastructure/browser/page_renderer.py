"""
页面渲染器
将HTML渲染为图片
"""

import asyncio
import uuid
import traceback
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")
from ...domain.errors import RenderError

if TYPE_CHECKING:
    from .browser_manager import BrowserManager


class PageRenderer:
    """页面渲染器 - 将HTML渲染为图片截图"""

    _ALLOWED_REMOTE_HOSTS = frozenset(
        {
            "astrbot-plugins.oss-cn-hangzhou.aliyuncs.com",
            "unpkg.com",
            "cdn.jsdelivr.net",
        }
    )

    def __init__(
        self,
        browser_manager: "BrowserManager",
        plugin_dir: Path,
        viewport_width: int = 1150,
        viewport_height: int = 2000,
        mathjax_timeout: int = 10000,
        tikz_timeout: int = 60000,
        mermaid_timeout: int = 15000,
        screenshot_timeout: int = 60000,
        max_screenshot_height: int = 16000,
        max_screenshot_pixels: int = 40_000_000,
        fail_on_mathjax_timeout: bool = False,
    ):
        self._browser_manager = browser_manager
        self._plugin_dir = plugin_dir
        self._viewport_width = viewport_width
        self._viewport_height = viewport_height
        self._mathjax_timeout = mathjax_timeout
        self._tikz_timeout = tikz_timeout
        self._mermaid_timeout = mermaid_timeout
        self._screenshot_timeout = screenshot_timeout
        self._max_screenshot_height = max_screenshot_height
        self._max_screenshot_pixels = max_screenshot_pixels
        self._fail_on_mathjax_timeout = fail_on_mathjax_timeout
        # 当前渲染页面的 file URI（安全边界：仅放行此 URI 的本地文件加载）
        self._current_page_uri: str | None = None

    async def render_to_image(self, html: str, output: Path) -> None:
        """将HTML渲染为图片"""
        import tempfile

        # 使用系统临时目录（插件目录可能因 pip 安装到 site-packages 而只读）
        temp_dir = Path(tempfile.gettempdir()) / "astrbot_mathjax2image"
        temp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = temp_dir / f"temp_{uuid.uuid4().hex}.html"

        logger.info(f"[MathJax2Image] HTML 临时文件: {tmp_path}")

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html)

        try:
            await self._do_render(tmp_path, output)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def _do_render(self, html_path: Path, output: Path) -> None:
        """执行实际的渲染操作"""
        inject_script = self._get_inject_script()
        page = None
        exception_occurred = False

        try:
            page, has_been_setup = await self._browser_manager.acquire_page(
                self._viewport_width, self._viewport_height
            )

            if not has_been_setup:
                await page.add_init_script(inject_script)
                await self._setup_network_policy(page)
                await self._setup_font_routes(page)
                self._setup_logging(page)

            # 记录当前渲染页面的 file URI，网络策略据此放行自身资源
            self._current_page_uri = html_path.resolve().as_uri()
            # 单次渲染整体超时，防止恶意/超长内容长时间占满页面池
            # (goto 60s + MathJax 10s + Mermaid 15s + TikZ 60s 串行最坏约 145s)
            overall_timeout = (
                self._mathjax_timeout
                + self._tikz_timeout
                + self._mermaid_timeout
                + 70_000  # goto + screenshot 余量
            )
            try:
                await asyncio.wait_for(
                    self._load_and_wait(page, html_path),
                    timeout=overall_timeout,
                )
                await self._take_screenshot(page, output)
            except asyncio.TimeoutError:
                raise RenderError(
                    f"渲染总时长超过限制 {overall_timeout / 1000:.0f}s"
                )

        except Exception as e:
            exception_occurred = True
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise RenderError(f"渲染失败: {e}")
        finally:
            if page is not None:
                await self._browser_manager.release_page(page, exception_occurred)

    def _get_inject_script(self) -> str:
        """获取注入脚本"""
        return """
        setTimeout(function() {
            document.querySelectorAll('.tikz-diagram svg').forEach(function(svg) {
                svg.style.position = 'relative';
                svg.style.display = 'block';
                svg.style.margin = '20px auto';
                svg.style.border = 'none';
                svg.style.padding = '0';
            });
        }, 1000);
        """

    async def _setup_font_routes(self, page) -> None:
        """设置字体路由"""
        static_dir = self._plugin_dir / "static"

        async def handle_font_route(route):
            url = route.request.url
            font_path = None

            if "/bakoma/ttf/" in url:
                raw_name = url.split("/bakoma/ttf/")[-1]
                safe_name = Path(raw_name).name
                font_path = static_dir / "bakoma" / "ttf" / safe_name
            elif "/fonts/" in url:
                raw_name = url.split("/fonts/")[-1]
                safe_name = Path(raw_name).name
                font_path = static_dir / "fonts" / safe_name

            if font_path and font_path.exists() and font_path.is_file():
                await route.fulfill(path=str(font_path))
                return

            await route.continue_()

        await page.route("**/*.ttf", handle_font_route)
        await page.route("**/*.otf", handle_font_route)

    async def _setup_network_policy(self, page) -> None:
        """只允许本地页面和模板所需 CDN，避免渲染内容触发任意出站请求。"""

        async def handle_request(route):
            parsed = urlparse(route.request.url)
            if parsed.scheme == "file":
                # 安全边界：只放行当前渲染页面自身的 file URI。
                # 攻击者可通过 Markdown 图片语法构造 file:/// 引用，若全部放行
                # 即可读取服务器本地文件（图片/SVG）并回传给请求者。
                if self._current_page_uri and route.request.url == self._current_page_uri:
                    await route.continue_()
                    return
                logger.warning(
                    "[MathJax2Image] 已阻止渲染页面加载本地文件: %s",
                    route.request.url,
                )
                await route.abort("blockedbyclient")
                return
            if parsed.scheme in {"about", "data", "blob"}:
                await route.continue_()
                return
            if parsed.scheme == "https" and parsed.hostname in self._ALLOWED_REMOTE_HOSTS:
                await route.continue_()
                return
            logger.warning(
                "[MathJax2Image] 已阻止渲染页面访问非白名单资源: %s",
                route.request.url,
            )
            await route.abort("blockedbyclient")

        await page.route("**/*", handle_request)

    def _setup_logging(self, page) -> None:
        """设置页面日志"""
        page.on(
            "console", lambda msg: logger.debug(f"[Browser] {msg.type}: {msg.text}")
        )
        page.on("pageerror", lambda err: logger.error(f"[Browser Error] {err}"))

    async def _load_and_wait(self, page, html_path: Path) -> None:
        """加载页面并等待渲染完成"""
        await page.goto(
            html_path.resolve().as_uri(),
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # 等待MathJax
        try:
            await page.wait_for_function(
                "() => window.mathJaxReady === true", timeout=self._mathjax_timeout
            )
            logger.debug("[MathJax2Image] MathJax 渲染完成")
        except Exception as e:
            logger.warning(f"[MathJax2Image] MathJax 等待超时: {e}")
            if self._fail_on_mathjax_timeout:
                raise RenderError(f"MathJax 渲染超时: {e}")

        # 等待 Mermaid（若页面含 mermaid 块）
        await self._wait_for_mermaid(page)

        # 检查TikZ
        await self._wait_for_tikz(page)

    async def _wait_for_mermaid(self, page) -> None:
        """等待 Mermaid 渲染完成（无图则跳过）"""
        try:
            count = await page.evaluate(
                "() => document.querySelectorAll('pre.mermaid').length"
            )
        except Exception:
            return
        if not count:
            return

        logger.info(f"[MathJax2Image] 检测到 {count} 个 Mermaid 图")
        try:
            await page.wait_for_function(
                "() => window.mermaidReady === true",
                timeout=self._mermaid_timeout,
            )
            logger.debug("[MathJax2Image] Mermaid 渲染完成")
        except Exception as e:
            logger.warning(f"[MathJax2Image] Mermaid 等待超时: {e}")

    async def _wait_for_tikz(self, page) -> None:
        """等待TikZ渲染完成"""
        tikz_count = await page.evaluate(
            "() => document.querySelectorAll('.tikz-diagram').length"
        )

        if tikz_count == 0:
            return

        logger.info(f"[MathJax2Image] 检测到 {tikz_count} 个TikZ图")

        try:
            result = await page.wait_for_function(
                """() => {
                    const containers = Array.from(document.querySelectorAll('.tikz-diagram'));
                    if (containers.length === 0) return null;

                    let totalElements = 0;
                    for (const container of containers) {
                        const svg = container.querySelector('svg');
                        if (!svg) return null;

                        const selectors = 'path,line,text,polygon,polyline,circle,ellipse,rect';
                        const elementCount = svg.querySelectorAll(selectors).length;
                        if (elementCount < 1) return null;

                        totalElements += elementCount;
                    }

                    return {
                        success: true,
                        diagrams: containers.length,
                        count: totalElements
                    };
                }""",
                timeout=self._tikz_timeout,
            )
            tikz_result = await result.json_value()
            if tikz_result and tikz_result.get("success"):
                logger.info(
                    "[MathJax2Image] TikZ渲染完成，"
                    f"图数: {tikz_result.get('diagrams', 0)}, "
                    f"元素数: {tikz_result.get('count', 0)}"
                )
            else:
                raise RenderError("TikZ渲染失败：SVG中没有有效图形元素")

        except Exception as e:
            logger.error(f"[MathJax2Image] TikZ渲染失败或超时: {e}")
            raise RenderError(f"TikZ渲染失败或超时: {e}")

        # 额外等待确保字体加载
        await asyncio.sleep(0.15)

    async def _take_screenshot(self, page, output: Path) -> None:
        """截取页面截图"""
        metrics = await page.evaluate(
            """() => ({
                width: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
                height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
            })"""
        )
        width = max(1, int(metrics["width"]))
        height = max(1, int(metrics["height"]))
        pixels = width * height

        if height > self._max_screenshot_height:
            raise RenderError(
                f"页面高度 {height}px 超过限制 {self._max_screenshot_height}px"
            )
        if pixels > self._max_screenshot_pixels:
            raise RenderError(
                f"截图像素 {pixels} 超过限制 {self._max_screenshot_pixels}"
            )

        await page.set_viewport_size(
            {"width": self._viewport_width, "height": height}
        )
        await page.evaluate(
            "() => document.fonts ? document.fonts.ready : Promise.resolve()"
        )

        logger.info(
            f"[MathJax2Image] 截图中，尺寸: {width}x{height}px，像素: {pixels}"
        )
        await page.screenshot(
            path=str(output), full_page=True, timeout=self._screenshot_timeout
        )
        logger.info(f"[MathJax2Image] 截图已保存: {output}")
