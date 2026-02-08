"""
AstrBot MathJax2Image 插件
将 Markdown/MathJax 内容渲染为图片

洋葱架构重构版本 v3.0

阅读提示（主流程一览）：
命令(/math|/art|/render)
  → CommandHandler 处理输入与提示词
  → LLMOrchestrator 生成文本（/math、/art）
  → RenderOrchestrator 统一预处理与渲染
  → Playwright 截图输出图片
"""

from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

from .application import RenderOrchestrator, LLMOrchestrator
from .infrastructure.converter import (
    TikzPlotConverter,
    TikzConverter,
    ListConverter,
    TableConverter,
    LatexPreprocessor,
)
from .handlers import CommandHandler, LLMToolHandler


@register(
    "astrbot_plugin_mathjax2image",
    "Willixrain",
    "调用 LLM 生成支持 MathJax 渲染的文章图片",
    "3.1.0",
)
class MathJax2ImagePlugin(Star):
    """MathJax 转图片插件 - 洋葱架构版本"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._plugin_dir = Path(__file__).resolve().parent

        # 加载配置
        self._bg_color = config.get("background_color", "#FDFBF0")
        self._math_prompt = config.get("math_system_prompt", "")
        self._article_prompt = config.get("article_system_prompt", "")

        llm_settings = config.get("llm_settings", {}) or {}
        self._provider_id = llm_settings.get("provider_id", "")

        # 依赖注入 - 创建组件
        self._init_components()

        logger.info("[MathJax2Image] 插件已加载 v3.0 (洋葱架构)")

    def _init_components(self):
        """初始化组件 - 依赖注入"""
        # 渲染编排器
        self._render_orchestrator = RenderOrchestrator(
            plugin_dir=self._plugin_dir,
            bg_color=self._bg_color,
        )

        # LLM编排器
        self._llm_orchestrator = LLMOrchestrator(
            context=self.context,
            provider_id=self._provider_id,
        )

        # LaTeX预处理器 (复用渲染器内部的转换器)
        plot_converter = TikzPlotConverter()
        tikz_converter = TikzConverter(plot_converter)
        list_converter = ListConverter()
        table_converter = TableConverter()

        self._latex_preprocessor = LatexPreprocessor(
            tikz_converter=tikz_converter,
            list_converter=list_converter,
            table_converter=table_converter,
        )

        # 命令处理器
        self._command_handler = CommandHandler(
            render_orchestrator=self._render_orchestrator,
            llm_orchestrator=self._llm_orchestrator,
            latex_preprocessor=self._latex_preprocessor,
            math_prompt=self._math_prompt,
            article_prompt=self._article_prompt,
        )

        # LLM工具处理器
        self._llm_tool_handler = LLMToolHandler(
            render_orchestrator=self._render_orchestrator,
            latex_preprocessor=self._latex_preprocessor,
            context=self.context,
        )

    # ==================== 命令处理 ====================

    @filter.command("math")
    async def cmd_math_article(self, event: AstrMessageEvent, content: str = ""):
        """生成数学文章并渲染为图片"""
        async for result in self._command_handler.handle_math(event, content):
            yield result

    @filter.command("art")
    async def cmd_article(self, event: AstrMessageEvent, content: str = ""):
        """生成普通文章并渲染为图片"""
        async for result in self._command_handler.handle_article(event, content):
            yield result

    @filter.command("render")
    async def cmd_render_direct(self, event: AstrMessageEvent, content: str = ""):
        """直接渲染 Markdown/MathJax 内容为图片"""
        async for result in self._command_handler.handle_render(event, content):
            yield result

    # ==================== LLM 工具 ====================

    @filter.llm_tool(name="render_math")
    async def llm_render_math(self, event: AstrMessageEvent, content: str) -> str:
        """【数学与图形渲染工具】将 Markdown/LaTeX/TikZ 内容渲染为图片。

        ⚠️ 关键格式要求（必须严格遵守）：
        1. 所有数学公式必须用 $$ 包裹，例如：$$f(x) = x^2$$
        2. 普通文字直接写，不要用 $$ 包裹
        3. 每个独立公式单独一行，用 $$ 包裹
        4. TikZ代码直接使用 \\begin{tikzpicture}...\\end{tikzpicture}
        5. Mermaid图表使用 ```mermaid ... ``` 代码块

        正确示例：
        ```
        导数的定义：

        $$f'(x) = \\lim_{h \\to 0} \\frac{f(x+h) - f(x)}{h}$$

        对于 $f(x) = x^2$：

        $$f'(x) = 2x$$
        ```

        支持内容类型：
        - 数学公式（MathJax）：行内 $...$ 和独立 $$...$$
        - TikZ 绘图、circuitikz、chemfig、tikz-cd、pgfplots
        - Mermaid 流程图、时序图等

        Args:
            content(string): Required. Markdown/LaTeX/TikZ 格式内容，数学公式必须用$$包裹

        Returns:
            string: 渲染结果
        """
        return await self._llm_tool_handler.handle_render_math(event, content)

    @filter.llm_tool(name="send_image")
    async def llm_send_image(self, event: AstrMessageEvent) -> str:
        """发送最近渲染的数学图片给用户。

        在每次 render_math 渲染完成后调用此工具发送图片。
        发送后可以继续用文字讲解，或继续渲染下一个公式。

        Returns:
            string: 发送结果
        """
        return await self._llm_tool_handler.handle_send_image(event)

    # ==================== 生命周期 ====================

    async def terminate(self):
        """插件卸载时清理资源"""
        await self._render_orchestrator.close()
        logger.info("[MathJax2Image] 插件已卸载")
