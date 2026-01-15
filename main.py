"""
AstrBot MathJax2Image 插件
将 Markdown/MathJax 内容渲染为图片
"""
import re
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
        self.renderer = MathJaxRenderer()

        # 加载配置
        self.math_prompt = config.get(
            "math_system_prompt",
            "写一篇文章，用 markdown 格式。数学公式用 MathJax 格式，"
            "反斜杠需要转义为双反斜杠，美元符号之间不要有中文字符。"
        )
        self.article_prompt = config.get(
            "article_system_prompt",
            "请生成一篇文章，使用 markdown 格式。"
        )

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
        try:
            contexts = [{"role": "user", "content": user_input}]
            response = await self.context.get_using_provider().text_chat(
                system_prompt=system_prompt,
                prompt="以下是文章围绕的话题",
                contexts=contexts,
            )
            return self._filter_think_tags(response.completion_text)
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
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
        try:
            image_path = await self.renderer.render(content)

            if not image_path.exists():
                logger.error(f"图片未生成: {image_path}")
                yield event.plain_result("图片生成失败，请检查日志。")
                return

            chain = [Comp.Image.fromFileSystem(str(image_path))]
            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"渲染失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

    @filter.command("mj2i")
    async def cmd_math_article(self, event: AstrMessageEvent, content: str = ""):
        """生成数学文章并渲染为图片"""
        if not content.strip():
            yield event.plain_result("请提供文章主题，例如: /mj2i 勾股定理")
            return

        yield event.plain_result("正在生成数学文章...")

        llm_result = await self._call_llm(content, self.math_prompt)
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        async for result in self._render_and_send(event, llm_result):
            yield result

    @filter.command("wz")
    async def cmd_article(self, event: AstrMessageEvent, content: str = ""):
        """生成普通文章并渲染为图片"""
        if not content.strip():
            yield event.plain_result("请提供文章主题，例如: /wz 人工智能")
            return

        llm_result = await self._call_llm(content, self.article_prompt)
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        async for result in self._render_and_send(event, llm_result):
            yield result

    @filter.command("m2i")
    async def cmd_render_direct(self, event: AstrMessageEvent, content: str = ""):
        """直接渲染 Markdown/MathJax 内容为图片"""
        if not content.strip():
            yield event.plain_result("请提供要渲染的内容，例如: /m2i $E=mc^2$")
            return

        # 转义反斜杠以正确渲染 LaTeX
        escaped_content = content.replace('\\', '\\\\')

        async for result in self._render_and_send(event, escaped_content):
            yield result

    async def terminate(self):
        """插件卸载时清理资源"""
        await self.renderer.close()
        logger.info("MathJax2Image 插件已卸载")
