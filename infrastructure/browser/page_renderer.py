"""
页面渲染器
将HTML渲染为图片
"""

import asyncio
import uuid
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api import logger
from ...domain.errors import RenderError

if TYPE_CHECKING:
    from .browser_manager import BrowserManager


class PageRenderer:
    """页面渲染器 - 将HTML渲染为图片截图"""

    def __init__(
        self,
        browser_manager: "BrowserManager",
        plugin_dir: Path,
        viewport_width: int = 1150,
        viewport_height: int = 2000,
        mathjax_timeout: int = 10000,
        tikz_timeout: int = 300000,
        screenshot_timeout: int = 60000,
    ):
        self._browser_manager = browser_manager
        self._plugin_dir = plugin_dir
        self._viewport_width = viewport_width
        self._viewport_height = viewport_height
        self._mathjax_timeout = mathjax_timeout
        self._tikz_timeout = tikz_timeout
        self._screenshot_timeout = screenshot_timeout

    async def render_to_image(self, html: str, output: Path) -> None:
        """将HTML渲染为图片"""
        temp_dir = self._plugin_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
        tmp_path = temp_dir / f"temp_{uuid.uuid4().hex[:8]}.html"

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

        try:
            browser = await self._browser_manager.get_browser()
            page = await browser.new_page(
                viewport={
                    "width": self._viewport_width,
                    "height": self._viewport_height,
                }
            )

            await page.add_init_script(inject_script)
            await self._setup_font_routes(page)
            self._setup_logging(page)

            try:
                await self._load_and_wait(page, html_path)
                await self._take_screenshot(page, output)
            finally:
                await page.close()

        except Exception as e:
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise RenderError(f"渲染失败: {e}")

    def _get_inject_script(self) -> str:
        """获取注入脚本"""
        return """
        setInterval(function() {
            document.querySelectorAll('.tikz-diagram svg').forEach(function(svg) {
                svg.style.position = 'relative';
                svg.style.display = 'block';
                svg.style.margin = '20px auto';
                svg.style.border = 'none';
                svg.style.padding = '0';
            });
        }, 500);
        """

    async def _setup_font_routes(self, page) -> None:
        """设置字体路由"""
        static_dir = self._plugin_dir / "static"

        async def handle_font_route(route):
            url = route.request.url
            font_path = None

            if "/bakoma/ttf/" in url:
                font_name = url.split("/bakoma/ttf/")[-1]
                font_path = static_dir / "bakoma" / "ttf" / font_name
            elif "/fonts/" in url:
                font_name = url.split("/fonts/")[-1]
                font_path = static_dir / "fonts" / font_name

            if font_path and font_path.exists():
                with open(font_path, "rb") as f:
                    font_data = f.read()
                content_type = "font/otf" if font_path.suffix == ".otf" else "font/ttf"
                await route.fulfill(body=font_data, content_type=content_type)
                return

            await route.continue_()

        await page.route("**/*.ttf", handle_font_route)
        await page.route("**/*.otf", handle_font_route)

    def _setup_logging(self, page) -> None:
        """设置页面日志"""
        page.on(
            "console", lambda msg: logger.debug(f"[Browser] {msg.type}: {msg.text}")
        )
        page.on("pageerror", lambda err: logger.error(f"[Browser Error] {err}"))

    async def _load_and_wait(self, page, html_path: Path) -> None:
        """加载页面并等待渲染完成"""
        await page.goto(
            f"file://{html_path}", wait_until="domcontentloaded", timeout=60000
        )

        # 等待MathJax
        try:
            await page.wait_for_function(
                "() => window.mathJaxReady === true", timeout=self._mathjax_timeout
            )
            logger.debug("[MathJax2Image] MathJax 渲染完成")
        except Exception as e:
            logger.warning(f"[MathJax2Image] MathJax 等待超时: {e}")

        # 检查TikZ
        await self._wait_for_tikz(page)

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
                    const container = document.querySelector('.tikz-diagram');
                    if (!container) return null;

                    const svg = container.querySelector('svg');
                    if (!svg) return null;

                    const paths = svg.querySelectorAll('path').length;
                    const lines = svg.querySelectorAll('line').length;
                    const texts = svg.querySelectorAll('text').length;
                    const polygons = svg.querySelectorAll('polygon').length;
                    const polylines = svg.querySelectorAll('polyline').length;

                    const totalElements = paths + lines + texts + polygons + polylines;

                    if (totalElements >= 1) {
                        return { success: true, count: totalElements };
                    }

                    const script = container.querySelector('script[type="text/tikz"]');
                    if (script) return null;

                    return null;
                }""",
                timeout=self._tikz_timeout,
            )
            tikz_result = await result.json_value()
            if tikz_result and tikz_result.get("success"):
                logger.info(
                    f"[MathJax2Image] TikZ渲染完成，元素数: {tikz_result.get('count', 0)}"
                )
            else:
                raise RenderError("TikZ渲染失败：SVG中没有有效图形元素")

        except Exception as e:
            logger.warning(f"[MathJax2Image] TikZ渲染检查: {e}")

        # 额外等待确保字体加载
        await asyncio.sleep(2)

    async def _take_screenshot(self, page, output: Path) -> None:
        """截取页面截图"""
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": self._viewport_width, "height": height})

        logger.info(f"[MathJax2Image] 截图中，高度: {height}px")
        await page.screenshot(
            path=str(output), full_page=True, timeout=self._screenshot_timeout
        )
        logger.info(f"[MathJax2Image] 截图已保存: {output}")
