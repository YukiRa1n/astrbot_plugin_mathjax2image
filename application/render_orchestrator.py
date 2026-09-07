"""
渲染编排器
编排完整的渲染流程
"""

import asyncio
import traceback
import uuid
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import StarTools

from ..domain.errors import DependencyError, RenderError
from ..infrastructure.browser import (
    BrowserManager,
    PageRenderer,
    PlaywrightDependencyInstaller,
)
from ..infrastructure.converter import (
    LatexPreprocessor,
    ListConverter,
    MarkdownConverter,
    MermaidConverter,
    TableConverter,
    TikzConverter,
    TikzPlotConverter,
)
from ..utils.artifacts import cleanup_stale_artifacts, remove_artifact

MAX_RENDER_LENGTH = 100000  # 渲染内容最大长度（字符）


class RenderOrchestrator:
    """
    渲染编排器

    Pipeline:
    content ──► preprocess ──► convert ──► render ──► image

    1. 检查依赖 (dependency_installer)
    2. LaTeX预处理 (latex_preprocessor)
    3. Markdown转HTML (markdown_converter)
    4. 页面渲染截图 (page_renderer)
    """

    def __init__(
        self,
        plugin_dir: Path,
        bg_color: str = "#FDFBF0",
        browser_engine: str = "chromium",
        browser_cdp_url: str = "",
        browser_max_pages: int = 2,
        auto_install_browser: bool = False,
        max_screenshot_height: int = 16000,
        max_screenshot_pixels: int = 40_000_000,
        tikz_timeout: int = 60000,
        mathjax_timeout: int = 10000,
        mermaid_timeout: int = 15000,
        fail_on_mathjax_timeout: bool = False,
        max_concurrent_renders: int = 2,
        max_queued_renders: int = 8,
        render_queue_timeout: int = 30000,
        browser_max_idle_pages: int = 1,
        browser_idle_timeout: int = 30,
        resource_cache_max_mb: int = 64,
        image_cache_max_mb: int = 8,
        precompute_pgfplots: bool = True,
        plot_max_points: int = 6400,
        max_concurrent_tikz: int = 1,
        typography: dict | None = None,
        resident_engines: bool = True,
        tikz_backend: str = "wasm",
        native_tex_bin: str = "",
    ):
        self._plugin_dir = plugin_dir
        self._bg_color = bg_color
        self._render_limit = min(
            max(1, int(max_concurrent_renders)), max(1, int(browser_max_pages))
        )
        self._render_semaphore = asyncio.Semaphore(self._render_limit)
        self._max_pending_renders = self._render_limit + max(0, int(max_queued_renders))
        self._pending_renders = 0
        self._queue_timeout = max(1, int(render_queue_timeout)) / 1000.0
        self._closed = False

        # 依赖安装器
        self._dependency_installer = PlaywrightDependencyInstaller()

        # 浏览器管理
        self._browser_manager = BrowserManager(
            max_pages=browser_max_pages,
            engine=browser_engine,
            cdp_url=browser_cdp_url,
            auto_install_browser=auto_install_browser,
            page_wait_timeout=self._queue_timeout,
            max_idle_pages=browser_max_idle_pages,
            idle_timeout=browser_idle_timeout,
        )

        # 页面渲染器
        self._page_renderer = PageRenderer(
            browser_manager=self._browser_manager,
            plugin_dir=plugin_dir,
            max_screenshot_height=max_screenshot_height,
            max_screenshot_pixels=max_screenshot_pixels,
            tikz_timeout=tikz_timeout,
            mathjax_timeout=mathjax_timeout,
            mermaid_timeout=mermaid_timeout,
            fail_on_mathjax_timeout=fail_on_mathjax_timeout,
            resource_cache_max_mb=resource_cache_max_mb,
            image_cache_max_mb=image_cache_max_mb,
            max_concurrent_tikz=max_concurrent_tikz,
            resident_engines=resident_engines,
            tikz_backend=tikz_backend,
            native_tex_bin=native_tex_bin,
        )

        # 转换器组合
        plot_converter = TikzPlotConverter(
            precompute_pgfplots=precompute_pgfplots, max_plot_points=plot_max_points
        )
        tikz_converter = TikzConverter(plot_converter)
        list_converter = ListConverter()
        table_converter = TableConverter()
        mermaid_converter = MermaidConverter()

        self._latex_preprocessor = LatexPreprocessor(
            tikz_converter=tikz_converter,
            list_converter=list_converter,
            table_converter=table_converter,
            mermaid_converter=mermaid_converter,
        )

        self._markdown_converter = MarkdownConverter(
            template_path=plugin_dir / "templates" / "template.html",
            typography=typography,
        )
        self._artifacts_cleaned = False

    async def render(self, content: str, skip_preprocess: bool = False) -> Path:
        """渲染内容为图片

        Args:
            content: Markdown/LaTeX/TikZ内容
            skip_preprocess: 是否跳过预处理（如果内容已经预处理过）

        Returns:
            生成的图片路径

        Raises:
            DependencyError: 依赖安装失败
            RenderError: 渲染失败

        Pipeline（逻辑顺序）：
        1) 依赖检查与安装
        2) LaTeX 预处理（文本命令、TikZ、表格、Mermaid）
        3) Markdown → HTML
        4) Playwright 渲染 → 截图
        """
        content_len = len(content)
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {content_len}")

        if content_len > MAX_RENDER_LENGTH:
            logger.warning(
                f"[MathJax2Image] 内容过长被拒绝，长度: {content_len}, 限制: {MAX_RENDER_LENGTH}"
            )
            raise RenderError(f"渲染内容过长，最大支持 {MAX_RENDER_LENGTH} 字符")

        if self._closed:
            raise RenderError("渲染引擎已关闭")
        # Admission is atomic until the first await on this event loop.
        if self._pending_renders >= self._max_pending_renders:
            raise RenderError("渲染队列已满，请稍后重试")
        self._pending_renders += 1
        acquired = False
        try:
            try:
                if self._render_semaphore.locked():
                    await asyncio.wait_for(
                        self._render_semaphore.acquire(), timeout=self._queue_timeout
                    )
                else:
                    await self._render_semaphore.acquire()
            except asyncio.TimeoutError as exc:
                raise RenderError("等待渲染超时，请稍后重试") from exc
            acquired = True
            if self._closed:
                raise RenderError("渲染引擎已关闭")
            return await self._render_locked(content, skip_preprocess)
        finally:
            if acquired:
                self._render_semaphore.release()
            self._pending_renders -= 1

    async def _render_locked(self, content: str, skip_preprocess: bool) -> Path:
        output_path = None
        try:
            # 1. 检查并安装依赖
            if not await self._dependency_installer.check_and_install():
                raise DependencyError(
                    "Playwright系统依赖未安装",
                    install_command="playwright install-deps chromium",
                )

            # 2. LaTeX预处理（纯 CPU 密集，放到线程池避免阻塞事件循环）
            if skip_preprocess:
                processed = content
            else:
                processed = await asyncio.to_thread(
                    self._latex_preprocessor.preprocess, content
                )
                logger.debug("[MathJax2Image] LaTeX预处理完成")

            # 3. Markdown转HTML（同步 CPU 密集，放到线程池避免阻塞事件循环）
            html_content = await asyncio.to_thread(
                self._markdown_converter.convert_to_html,
                processed,
                self._bg_color,
            )
            logger.debug("[MathJax2Image] Markdown转HTML完成")

            # 4. 生成输出路径
            output_dir = StarTools.get_data_dir("astrbot_plugin_mathjax2image")
            output_dir.mkdir(parents=True, exist_ok=True)
            if not self._artifacts_cleaned:
                removed = cleanup_stale_artifacts(output_dir)
                self._artifacts_cleaned = True
                if removed:
                    logger.info(f"[MathJax2Image] 已清理 {removed} 个过期渲染文件")
            output_path = output_dir / f"render_{uuid.uuid4().hex}.png"

            # 5. 渲染为图片
            await self._page_renderer.render_to_image(html_content, output_path)

            logger.info(f"[MathJax2Image] 渲染成功: {output_path}")
            return output_path

        except asyncio.CancelledError:
            remove_artifact(output_path)
            raise
        except Exception as e:
            remove_artifact(output_path)
            if isinstance(e, (DependencyError, RenderError)):
                raise
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise RenderError(f"渲染失败: {e}")

    async def close(self) -> None:
        """释放资源"""
        self._closed = True
        await self._browser_manager.close()
        logger.info("[MathJax2Image] 编排器资源已释放")

    def set_bg_color(self, color: str) -> None:
        """设置背景颜色"""
        self._bg_color = color
