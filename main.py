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

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .application import LLMOrchestrator, RenderOrchestrator
from .handlers import CommandHandler, LLMToolHandler
from .infrastructure.converter.markdown_converter import resolve_bg_color
from .utils.config import normalized_choice, safe_bool, safe_int
from .utils.security import validate_cdp_url

#: 枚举类配置项的合法取值，非法值一律回退第一个。
_BROWSER_ENGINES = ("chromium", "firefox", "webkit")
_TIKZ_BACKENDS = ("wasm", "native")


@register(
    "astrbot_plugin_mathjax2image",
    "Willixrain",
    "调用 LLM 生成支持 MathJax 渲染的文章图片",
    "3.2.1",
)
class MathJax2ImagePlugin(Star):
    """MathJax 转图片插件 - 洋葱架构版本"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._plugin_dir = Path(__file__).resolve().parent

        # 加载配置
        self._bg_color = resolve_bg_color(config.get("background_color"))
        self._math_prompt = config.get("math_system_prompt", "")
        self._article_prompt = config.get("article_system_prompt", "")

        llm_settings = config.get("llm_settings", {}) or {}
        if not isinstance(llm_settings, dict):
            logger.warning(
                "[MathJax2Image] llm_settings 不是对象，已忽略: %s",
                type(llm_settings).__name__,
            )
            llm_settings = {}
        self._provider_id = llm_settings.get("provider_id", "")

        # 依赖注入 - 创建组件
        self._init_components()

        logger.info("[MathJax2Image] 插件已加载 v3.2.1")

    def _init_components(self):
        """初始化组件 - 依赖注入"""
        allow_remote_cdp = safe_bool(self.config.get("allow_remote_cdp", False))
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
            return safe_int(self.config.get(key, default), default)

        # 校验浏览器引擎，非法值回退 chromium
        raw_engine = normalized_choice(
            self.config.get("browser_engine", "chromium"), "chromium"
        )
        browser_engine = raw_engine if raw_engine in _BROWSER_ENGINES else "chromium"
        if browser_engine != raw_engine:
            logger.warning(
                f"[MathJax2Image] 无效的 browser_engine '{raw_engine}'，回退为 chromium"
            )

        # 校验 TikZ 后端：非法值会让 PageRenderer 在构造期抛 ValueError，
        # 直接导致插件加载失败，因此与 browser_engine 一样先归一化。
        raw_backend = normalized_choice(
            self.config.get("tikz_backend", "wasm"), "wasm"
        )
        tikz_backend = raw_backend if raw_backend in _TIKZ_BACKENDS else "wasm"
        if tikz_backend != raw_backend:
            logger.warning(
                f"[MathJax2Image] 无效的 tikz_backend '{raw_backend}'，回退为 wasm"
            )

        self._render_orchestrator = RenderOrchestrator(
            plugin_dir=self._plugin_dir,
            bg_color=self._bg_color,
            browser_engine=browser_engine,
            browser_cdp_url=browser_cdp_url,
            browser_max_pages=_cfg_int("browser_max_pages", 2),
            auto_install_browser=safe_bool(
                self.config.get("auto_install_browser", False)
            ),
            max_screenshot_height=_cfg_int("max_screenshot_height", 16000),
            max_screenshot_pixels=_cfg_int("max_screenshot_pixels", 40_000_000),
            tikz_timeout=_cfg_int("tikz_timeout", 60000),
            mathjax_timeout=_cfg_int("mathjax_timeout", 10000),
            mermaid_timeout=_cfg_int("mermaid_timeout", 15000),
            fail_on_mathjax_timeout=safe_bool(
                self.config.get("fail_on_mathjax_timeout", False)
            ),
            max_concurrent_renders=_cfg_int("max_concurrent_renders", 2),
            max_queued_renders=_cfg_int("max_queued_renders", 8),
            render_queue_timeout=_cfg_int("render_queue_timeout", 30000),
            browser_max_idle_pages=_cfg_int("browser_max_idle_pages", 1),
            browser_idle_timeout=_cfg_int("browser_idle_timeout", 30),
            resource_cache_max_mb=_cfg_int("resource_cache_max_mb", 64),
            asset_disk_cache_max_mb=_cfg_int("asset_disk_cache_max_mb", 256),
            image_cache_max_mb=_cfg_int("image_cache_max_mb", 8),
            precompute_pgfplots=safe_bool(
                self.config.get("precompute_pgfplots", True)
            ),
            plot_max_points=_cfg_int("plot_max_points", 6400),
            max_concurrent_tikz=_cfg_int("max_concurrent_tikz", 1),
            tikz_backend=tikz_backend,
            native_tex_bin=str(self.config.get("native_tex_bin", "") or "").strip(),
            typography=self.config.get("typography", {}),
            resident_engines=safe_bool(self.config.get("resident_engines", True)),
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
        """把含公式、推导或图形的内容渲染成一张图片，并直接发送给用户。

        QQ、微信等聊天平台无法显示 LaTeX：在文字回复里写 $x^2$、\\frac{a}{b}
        会原样显示成难以阅读的源码。因此需要公式才能讲清楚的部分，应写成
        Markdown 交给本工具渲染，不要写在文字回复里。

        应当调用：
        - 回答包含分式、根式、积分、求和、极限、矩阵、方程组或多步推导，
          用纯文本难以阅读
        - 需要函数图像、几何图形、交换图、流程图，或带公式的表格
        - 用户要求写出公式、给出推导或画图

        不要调用：
        - 闲聊或与数学无关的回答
        - 只有一两个简单式子：直接用 Unicode 写在文字里即可，如 x² + 1、a/b、
          √2、≤、π、∑

        使用方式：
        - 一次回答通常只调用一次，把需要排版的内容连同必要的文字说明完整放进
          content，图片要能脱离聊天上下文独立看懂
        - 返回“图片已发送”后，文字回复只需一两句概括或引导，不要重复图片
          中的内容，也不要再输出任何 LaTeX
        - 返回“渲染失败”时，按提示修改后重试一次；仍失败则改用 Unicode
          纯文本回答

        content 就是普通 Markdown（标题、列表、表格、代码块均可）：
        - 公式：行内 $...$，独立 $$...$$（可跨多行）；\\(...\\)、\\[...\\]、
          align/cases 等环境也可直接写，\\text{中文} 可在公式中使用
        - 画图：直接写 \\begin{tikzpicture}...\\end{tikzpicture}，或放进 ```tikz
          代码块；支持 pgfplots、tikz-cd；不支持 circuitikz、chemfig
        - 流程图、时序图：```mermaid 代码块
        - 不需要写 \\documentclass、\\usepackage、\\usetikzlibrary，常用宏包与
          TikZ 库会自动加载

        必须遵守（否则渲染出错）：
        - 正文里的美元符号写成 \\$（如 \\$5），裸 $ 会被当成公式开头
        - TikZ 图内只能写英文或 $公式$，不能有中文，中文说明写在图外

        示例 content：
        ## 导数的定义
        $$f'(x) = \\lim_{h \\to 0} \\frac{f(x+h) - f(x)}{h}$$
        对 $f(x) = x^2$，有 $f'(x) = 2x$。

        Args:
            content(string): 要渲染的 Markdown 内容，可包含 $...$ / $$...$$ 公式、TikZ、Mermaid
            auto_send(bool): 渲染后是否立即发送图片，默认 true，一般不要修改；为 false 时需再调用 send_image 发送

        Returns:
            string: 渲染与发送的结果，失败时附带修改建议
        """
        return await self._llm_tool_handler.handle_render_math(
            event, content, auto_send=auto_send
        )

    @filter.llm_tool(name="send_image")
    async def llm_send_image(self, event: AstrMessageEvent) -> str:
        """发送最近一次用 render_math 渲染、但尚未发送的图片。

        只在 render_math 以 auto_send=false 调用之后使用。render_math 默认会
        直接发送图片，通常不需要调用本工具。

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
