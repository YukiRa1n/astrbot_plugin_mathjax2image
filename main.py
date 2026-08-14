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
from .handlers import CommandHandler, LLMToolHandler
from .utils.security import validate_cdp_url


def _safe_bool(value) -> bool:
    """安全布尔解析：只接受布尔值或明确的 true/false 字符串，其他一律视为 False。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return False


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

        logger.info("[MathJax2Image] 插件已加载 v3.1.0")

    def _init_components(self):
        """初始化组件 - 依赖注入"""
        allow_remote_cdp = _safe_bool(self.config.get("allow_remote_cdp", False))
        try:
            browser_cdp_url = validate_cdp_url(
                self.config.get("browser_cdp_url", ""),
                allow_remote=allow_remote_cdp,
            )
        except ValueError as e:
            logger.warning(f"[MathJax2Image] Invalid browser_cdp_url, ignoring: {e}")
            browser_cdp_url = ""

        def _cfg_int(key: str, default: int) -> int:
            """int 配置项安全转换，非法值回退默认。"""
            try:
                return int(self.config.get(key, default))
            except (TypeError, ValueError):
                return default

        # 校验浏览器引擎，非法值回退 chromium
        raw_engine = str(self.config.get("browser_engine", "chromium") or "chromium").strip().lower()
        browser_engine = raw_engine if raw_engine in ("chromium", "firefox", "webkit") else "chromium"
        if browser_engine != raw_engine:
            logger.warning(f"[MathJax2Image] 无效的 browser_engine '{raw_engine}'，回退为 chromium")

        self._render_orchestrator = RenderOrchestrator(
            plugin_dir=self._plugin_dir,
            bg_color=self._bg_color,
            browser_engine=browser_engine,
            browser_cdp_url=browser_cdp_url,
            browser_max_pages=_cfg_int("browser_max_pages", 2),
            auto_install_browser=_safe_bool(self.config.get("auto_install_browser", False)),
            max_screenshot_height=_cfg_int("max_screenshot_height", 16000),
            max_screenshot_pixels=_cfg_int("max_screenshot_pixels", 40_000_000),
            tikz_timeout=_cfg_int("tikz_timeout", 60000),
            mathjax_timeout=_cfg_int("mathjax_timeout", 10000),
            mermaid_timeout=_cfg_int("mermaid_timeout", 15000),
            fail_on_mathjax_timeout=_safe_bool(
                self.config.get("fail_on_mathjax_timeout", False)
            ),
            max_concurrent_renders=_cfg_int("max_concurrent_renders", 2),
        )

        # LLM编排器
        self._llm_orchestrator = LLMOrchestrator(
            context=self.context,
            provider_id=self._provider_id,
        )

        # 命令处理器
        self._command_handler = CommandHandler(
            render_orchestrator=self._render_orchestrator,
            llm_orchestrator=self._llm_orchestrator,
            math_prompt=self._math_prompt,
            article_prompt=self._article_prompt,
        )

        # LLM工具处理器
        self._llm_tool_handler = LLMToolHandler(
            render_orchestrator=self._render_orchestrator,
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
    async def llm_render_math(
        self, event: AstrMessageEvent, content: str, auto_send: bool = True
    ) -> str:
        """【数学与图形渲染工具】将 Markdown/LaTeX/TikZ/Mermaid 内容渲染为图片并发送。

        默认渲染成功后立即把图片发送给用户(一步到位,无需再调其他工具)。
        若 auto_send=False,则仅保存图片,需再调用 send_image 发送。

        ⚠️ 格式要求：
        1. 数学公式用 $$ 包裹(独立)或 $...$(行内),例如 $$f(x) = x^2$$
        2. 普通文字直接写,不要用 $$ 包裹
        3. TikZ 绘图直接用 \\begin{tikzpicture}...\\end{tikzpicture}
        4. Mermaid 图表用 ```mermaid ... ``` 代码块

        正确示例：
        ```
        导数的定义：

        $$f'(x) = \\lim_{h \\to 0} \\frac{f(x+h) - f(x)}{h}$$

        对于 $f(x) = x^2$：

        $$f'(x) = 2x$$
        ```

        支持内容类型：
        - 数学公式(MathJax): 行内 $...$ 和独立 $$...$$(支持 boldsymbol/mathtools 等)
        - TikZ 绘图: 常用库已自动加载(calc/positioning/arrows.meta/intersections/
          decorations/patterns/angles/matrix/3d/trees/mindmap/automata 等 60+ 库)
        - pgfplots 图表(axis/addplot)、tikz-cd 交换图
        - Mermaid 流程图、时序图等
        - Markdown 文本(标题/列表/表格/代码块)

        不支持(会返回明确错误): circuitikz、chemfig、graphicx 等 TikZJax
        未内置的宏包,请用 TikZ 原生命令替代。

        Args:
            content(string): Required. Markdown/LaTeX/TikZ/Mermaid 内容,公式用 $$ 包裹
            auto_send(bool): Optional. 渲染后是否立即发送图片,默认 True

        Returns:
            string: 渲染+发送结果(成功或失败原因)
        """
        return await self._llm_tool_handler.handle_render_math(
            event, content, auto_send=auto_send
        )

    @filter.llm_tool(name="send_image")
    async def llm_send_image(self, event: AstrMessageEvent) -> str:
        """发送最近渲染的图片给用户。

        仅在 render_math 以 auto_send=False 调用后使用(此时图片已保存未发送)。
        默认 render_math 会直接发送,无需调用本工具。

        Returns:
            string: 发送结果
        """
        return await self._llm_tool_handler.handle_send_image(event)

    # ==================== 生命周期 ====================

    async def terminate(self):
        """插件卸载时清理资源"""
        await self._llm_tool_handler.close()
        await self._render_orchestrator.close()
        logger.info("[MathJax2Image] 插件已卸载")
