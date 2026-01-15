"""
AstrBot MathJax2Image 插件
将 Markdown/MathJax 内容渲染为图片
"""
import re
import traceback
from pathlib import Path
from typing import Optional
import urllib.request

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig

from .services.renderer import MathJaxRenderer


@register(
    "astrbot_plugin_mathjax2image",
    "Willixrain",
    "调用 LLM 生成支持 MathJax 渲染的文章图片",
    "2.0.0"
)
class MathJax2ImagePlugin(Star):
    """MathJax 转图片插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 加载背景颜色配置
        self.bg_color = config.get("background_color", "#FDFBF0")
        self.renderer = MathJaxRenderer(bg_color=self.bg_color)

        # 加载提示词配置（默认值与 _conf_schema.json 保持一致）
        self.math_prompt = config.get("math_system_prompt", "")
        self.article_prompt = config.get("article_system_prompt", "")

        # 确保 MathJax 已安装
        self._ensure_mathjax_installed()

    def _ensure_mathjax_installed(self) -> None:
        """检查并自动下载 MathJax"""
        plugin_dir = Path(__file__).resolve().parent
        mathjax_file = plugin_dir / "static" / "mathjax" / "tex-chtml.js"

        if mathjax_file.exists():
            logger.info(f"MathJax 已安装: {mathjax_file}")
            return

        mathjax_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info("首次使用，正在下载 MathJax...")

        try:
            url = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"
            urllib.request.urlretrieve(url, mathjax_file)
            size_kb = mathjax_file.stat().st_size / 1024
            logger.info(f"MathJax 下载成功！({size_kb:.2f} KB)")
        except Exception as e:
            logger.error(f"MathJax 下载失败: {e}")
            logger.error("请手动下载并保存到 static/mathjax/tex-chtml.js")

    async def _call_llm(
        self,
        user_input: str,
        system_prompt: str
    ) -> Optional[str]:
        """统一的 LLM 调用方法"""
        logger.info(f"[MathJax2Image] 开始调用 LLM，主题: {user_input[:50]}...")

        try:
            provider = self.context.get_using_provider()
            if provider is None:
                logger.error("[MathJax2Image] LLM provider 未配置或不可用")
                return None

            logger.debug(f"[MathJax2Image] 使用 provider: {type(provider).__name__}")

            contexts = [{"role": "user", "content": user_input}]
            response = await provider.text_chat(
                system_prompt=system_prompt,
                prompt="以下是文章围绕的话题",
                contexts=contexts,
            )

            if response is None:
                logger.error("[MathJax2Image] LLM 返回空响应")
                return None

            if not response.completion_text:
                logger.error("[MathJax2Image] LLM 返回内容为空")
                return None

            logger.info(f"[MathJax2Image] LLM 调用成功，响应长度: {len(response.completion_text)}")
            return self._filter_think_tags(response.completion_text)

        except Exception as e:
            logger.error(f"[MathJax2Image] LLM 调用失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            return None

    def _filter_think_tags(self, text: Optional[str]) -> Optional[str]:
        """过滤 LLM 响应中的 <think> 标签"""
        if not text:
            return None
        return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL)

    async def _render_and_send(
        self,
        event: AstrMessageEvent,
        content: str
    ):
        """渲染内容并发送图片"""
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {len(content)}")

        try:
            image_path = await self.renderer.render(content)

            if not image_path.exists():
                logger.error(f"[MathJax2Image] 图片文件不存在: {image_path}")
                yield event.plain_result("图片生成失败，请检查日志。")
                return

            logger.info(f"[MathJax2Image] 图片生成成功: {image_path}")
            chain = [Comp.Image.fromFileSystem(str(image_path))]
            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            yield event.plain_result(f"渲染失败: {e}")

    def _extract_command_content(self, event: AstrMessageEvent, cmd_name: str) -> str:
        """从完整消息中提取命令后的内容（避免空格截断问题）"""
        full_msg = event.get_message_str()
        content = ""

        # 支持 /cmd 和 cmd 两种格式
        for prefix in [f"/{cmd_name} ", f"{cmd_name} "]:
            if prefix in full_msg:
                content = full_msg.split(prefix, 1)[1]
                break

        return content.strip()

    @filter.command("math")
    async def cmd_math_article(self, event: AstrMessageEvent, content: str = ""):
        """生成数学文章并渲染为图片"""
        # 从完整消息中提取内容
        math_content = self._extract_command_content(event, "math")

        if not math_content:
            yield event.plain_result("请提供文章主题，例如: /math 勾股定理")
            return

        yield event.plain_result("正在生成数学文章...")
        logger.info(f"[MathJax2Image] /math 内容长度: {len(math_content)}")

        llm_result = await self._call_llm(math_content, self.math_prompt)
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        async for result in self._render_and_send(event, llm_result):
            yield result

    @filter.command("art")
    async def cmd_article(self, event: AstrMessageEvent, content: str = ""):
        """生成普通文章并渲染为图片"""
        # 从完整消息中提取内容
        art_content = self._extract_command_content(event, "art")

        if not art_content:
            yield event.plain_result("请提供文章主题，例如: /art 人工智能")
            return

        logger.info(f"[MathJax2Image] /art 内容长度: {len(art_content)}")

        llm_result = await self._call_llm(art_content, self.article_prompt)
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        async for result in self._render_and_send(event, llm_result):
            yield result

    @filter.command("render")
    async def cmd_render_direct(self, event: AstrMessageEvent, content: str = ""):
        """直接渲染 Markdown/MathJax 内容为图片"""
        # 从完整消息中提取内容
        render_content = self._extract_command_content(event, "render")

        if not render_content:
            yield event.plain_result("请提供要渲染的内容，例如: /render $E=mc^2$")
            return

        logger.info(f"[MathJax2Image] /render 内容长度: {len(render_content)}")

        # 预处理：将 LaTeX 文本命令转换为 Markdown
        processed = self._preprocess_latex_text(render_content)

        async for result in self._render_and_send(event, processed):
            yield result

    def _preprocess_latex_text(self, text: str) -> str:
        """将 LaTeX 文本命令转换为 Markdown 格式"""
        # \textbf{...} -> **...** (支持跨行)
        text = re.sub(r'\\textbf\{([\s\S]*?)\}', r'**\1**', text)
        # \textit{...} -> *...* (支持跨行)
        text = re.sub(r'\\textit\{([\s\S]*?)\}', r'*\1*', text)
        # \emph{...} -> *...* (支持跨行)
        text = re.sub(r'\\emph\{([\s\S]*?)\}', r'*\1*', text)
        return text

    async def terminate(self):
        """插件卸载时清理资源"""
        await self.renderer.close()
        logger.info("MathJax2Image 插件已卸载")
