"""
渲染编排器
编排完整的渲染流程
"""
import uuid
import traceback
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import StarTools

from ..domain.errors import RenderError, DependencyError
from ..infrastructure.browser import (
    PlaywrightDependencyInstaller,
    BrowserManager,
    PageRenderer,
)
from ..infrastructure.converter import (
    TikzPlotConverter,
    TikzConverter,
    ListConverter,
    TableConverter,
    LatexPreprocessor,
    MarkdownConverter,
    MermaidConverter,
)


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

    def __init__(self, plugin_dir: Path, bg_color: str = "#FDFBF0"):
        self._plugin_dir = plugin_dir
        self._bg_color = bg_color

        # 依赖安装器
        self._dependency_installer = PlaywrightDependencyInstaller()

        # 浏览器管理
        self._browser_manager = BrowserManager()

        # 页面渲染器
        self._page_renderer = PageRenderer(
            browser_manager=self._browser_manager,
            plugin_dir=plugin_dir,
        )

        # 转换器组合
        plot_converter = TikzPlotConverter()
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
            template_path=plugin_dir / "templates" / "template.html"
        )

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
        """
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {len(content)}")

        try:
            # 1. 检查并安装依赖
            if not await self._dependency_installer.check_and_install():
                raise DependencyError(
                    "Playwright系统依赖未安装",
                    install_command="playwright install-deps chromium"
                )

            # 2. LaTeX预处理（如果需要）
            if skip_preprocess:
                processed = content
            else:
                processed = self._latex_preprocessor.preprocess(content)
                logger.debug("[MathJax2Image] LaTeX预处理完成")

            # 3. Markdown转HTML
            html_content = self._markdown_converter.convert_to_html(
                processed, self._bg_color
            )
            logger.debug("[MathJax2Image] Markdown转HTML完成")

            # 4. 生成输出路径
            output_dir = StarTools.get_data_dir('astrbot_plugin_mathjax2image')
            output_path = output_dir / f"render_{uuid.uuid4().hex[:8]}.png"

            # 5. 渲染为图片
            await self._page_renderer.render_to_image(html_content, output_path)

            logger.info(f"[MathJax2Image] 渲染成功: {output_path}")
            return output_path

        except (DependencyError, RenderError):
            raise
        except Exception as e:
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise RenderError(f"渲染失败: {e}")

    async def close(self) -> None:
        """释放资源"""
        await self._browser_manager.close()
        logger.info("[MathJax2Image] 编排器资源已释放")

    def set_bg_color(self, color: str) -> None:
        """设置背景颜色"""
        self._bg_color = color
