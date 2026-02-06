"""
命令处理器
处理 /math, /art, /render 命令
"""

import traceback
from typing import TYPE_CHECKING, AsyncIterator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp

if TYPE_CHECKING:
    from ..application import RenderOrchestrator, LLMOrchestrator
    from ..infrastructure.converter import LatexPreprocessor


class CommandHandler:
    """命令处理器"""

    def __init__(
        self,
        render_orchestrator: "RenderOrchestrator",
        llm_orchestrator: "LLMOrchestrator",
        latex_preprocessor: "LatexPreprocessor",
        math_prompt: str,
        article_prompt: str,
    ):
        self._render_orchestrator = render_orchestrator
        self._llm_orchestrator = llm_orchestrator
        self._latex_preprocessor = latex_preprocessor
        self._math_prompt = math_prompt
        self._article_prompt = article_prompt

    def set_prompts(self, math_prompt: str = None, article_prompt: str = None):
        """更新提示词"""
        if math_prompt is not None:
            self._math_prompt = math_prompt
        if article_prompt is not None:
            self._article_prompt = article_prompt

    async def handle_math(self, event: AstrMessageEvent, content: str) -> AsyncIterator:
        """处理 /math 命令"""
        math_content = self._extract_command_content(event, "math")

        if not math_content:
            yield event.plain_result("请提供文章主题，例如: /math 勾股定理")
            return

        yield event.plain_result("正在生成数学文章...")
        logger.info(f"[MathJax2Image] /math 内容长度: {len(math_content)}")

        llm_result = await self._llm_orchestrator.call_llm(
            math_content, self._math_prompt
        )
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        # 直接传给渲染器，由渲染器统一预处理
        async for result in self._render_and_send(event, llm_result):
            yield result

    async def handle_article(
        self, event: AstrMessageEvent, content: str
    ) -> AsyncIterator:
        """处理 /art 命令"""
        art_content = self._extract_command_content(event, "art")

        if not art_content:
            yield event.plain_result("请提供文章主题，例如: /art 人工智能")
            return

        yield event.plain_result("正在生成文章...")
        logger.info(f"[MathJax2Image] /art 内容长度: {len(art_content)}")

        llm_result = await self._llm_orchestrator.call_llm(
            art_content, self._article_prompt
        )
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        # 直接传给渲染器，由渲染器统一预处理
        async for result in self._render_and_send(event, llm_result):
            yield result

    async def handle_render(
        self, event: AstrMessageEvent, content: str
    ) -> AsyncIterator:
        """处理 /render 命令"""
        render_content = self._extract_command_content(event, "render")

        if not render_content:
            yield event.plain_result("请提供要渲染的内容，例如: /render $E=mc^2$")
            return

        logger.info(f"[MathJax2Image] /render 内容长度: {len(render_content)}")

        # 直接传给渲染器，由渲染器统一预处理
        async for result in self._render_and_send(event, render_content):
            yield result

    async def _render_and_send(
        self, event: AstrMessageEvent, content: str
    ) -> AsyncIterator:
        """渲染内容并发送图片"""
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {len(content)}")

        try:
            image_path = await self._render_orchestrator.render(content)

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
        """从完整消息中提取命令后的内容"""
        full_msg = event.get_message_str()
        content = ""

        for prefix in [f"/{cmd_name} ", f"{cmd_name} "]:
            if prefix in full_msg:
                content = full_msg.split(prefix, 1)[1]
                break

        return content.strip()
