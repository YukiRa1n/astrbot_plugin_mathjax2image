"""
命令处理器
处理 /math, /art, /render 命令
"""

import traceback
from typing import TYPE_CHECKING, AsyncIterator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp

from ..application.render_orchestrator import MAX_RENDER_LENGTH
from ..utils.artifacts import consume_artifact, remove_artifact

if TYPE_CHECKING:
    from ..application import RenderOrchestrator, LLMOrchestrator


# 安全限制常量
MAX_INPUT_LENGTH = 50000  # 最大输入长度（字符）


class CommandHandler:
    """命令处理器"""

    def __init__(
        self,
        render_orchestrator: "RenderOrchestrator",
        llm_orchestrator: "LLMOrchestrator",
        math_prompt: str,
        article_prompt: str,
    ):
        self._render_orchestrator = render_orchestrator
        self._llm_orchestrator = llm_orchestrator
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
        # /math 与 /art 最终进入同一条渲染管道，仅提示词不同
        math_content = self._extract_command_content(event, "math", content)

        if not math_content:
            yield event.plain_result("请提供文章主题，例如: /math 勾股定理")
            return

        # 输入长度验证
        if len(math_content) > MAX_INPUT_LENGTH:
            yield event.plain_result(
                f"输入内容过长（{len(math_content)} 字符），最大支持 {MAX_INPUT_LENGTH} 字符"
            )
            return

        yield event.plain_result("正在生成数学文章...")
        logger.info(f"[MathJax2Image] /math 请求，输入长度: {len(math_content)}")

        llm_result = await self._llm_orchestrator.call_llm(
            math_content, self._math_prompt
        )
        if not llm_result:
            yield event.plain_result(
                "文章生成失败：请检查是否已配置可用的 LLM 提供商，并查看日志"
            )
            return

        # 直接传给渲染器，由渲染器统一预处理
        async for result in self._render_and_send(event, llm_result):
            yield result

    async def handle_article(
        self, event: AstrMessageEvent, content: str
    ) -> AsyncIterator:
        """处理 /art 命令"""
        # /math 与 /art 最终进入同一条渲染管道，仅提示词不同
        art_content = self._extract_command_content(event, "art", content)

        if not art_content:
            yield event.plain_result("请提供文章主题，例如: /art 人工智能")
            return

        # 输入长度验证
        if len(art_content) > MAX_INPUT_LENGTH:
            yield event.plain_result(
                f"输入内容过长（{len(art_content)} 字符），最大支持 {MAX_INPUT_LENGTH} 字符"
            )
            return

        yield event.plain_result("正在生成文章...")
        logger.info(f"[MathJax2Image] /art 请求，输入长度: {len(art_content)}")

        llm_result = await self._llm_orchestrator.call_llm(
            art_content, self._article_prompt
        )
        if not llm_result:
            yield event.plain_result(
                "文章生成失败：请检查是否已配置可用的 LLM 提供商，并查看日志"
            )
            return

        # 直接传给渲染器，由渲染器统一预处理
        async for result in self._render_and_send(event, llm_result):
            yield result

    async def handle_render(
        self, event: AstrMessageEvent, content: str
    ) -> AsyncIterator:
        """处理 /render 命令"""
        render_content = self._extract_command_content(event, "render", content)

        if not render_content:
            yield event.plain_result("请提供要渲染的内容，例如: /render $E=mc^2$")
            return

        # 输入长度验证
        if len(render_content) > MAX_RENDER_LENGTH:
            yield event.plain_result(
                f"渲染内容过长（{len(render_content)} 字符），最大支持 {MAX_RENDER_LENGTH} 字符"
            )
            return

        logger.info(f"[MathJax2Image] /render 请求，输入长度: {len(render_content)}")

        # 直接传给渲染器，由渲染器统一预处理
        async for result in self._render_and_send(event, render_content):
            yield result

    async def _render_and_send(
        self, event: AstrMessageEvent, content: str
    ) -> AsyncIterator:
        """渲染内容并发送图片"""
        content_len = len(content)
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {content_len}")

        # 额外的渲染长度检查
        if content_len > MAX_RENDER_LENGTH:
            logger.warning(
                f"[MathJax2Image] 内容过长被拒绝，长度: {content_len}, 限制: {MAX_RENDER_LENGTH}"
            )
            yield event.plain_result(f"渲染内容过长，最大支持 {MAX_RENDER_LENGTH} 字符")
            return

        image_path = None
        try:
            image_path = await self._render_orchestrator.render(content)

            if not image_path.exists():
                logger.error(f"[MathJax2Image] 图片文件不存在: {image_path}")
                yield event.plain_result("图片生成失败，请检查日志。")
                return

            logger.info(f"[MathJax2Image] 图片生成成功: {image_path}")
            image_bytes = await consume_artifact(image_path)
            image_path = None
            chain = [Comp.Image.fromBytes(image_bytes)]
            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            yield event.plain_result(f"渲染失败: {e}")
        finally:
            remove_artifact(image_path)

    def _extract_command_content(
        self, event: AstrMessageEvent, cmd_name: str, framework_content: str = ""
    ) -> str:
        """提取命令后内容：优先框架注入参数，再解析完整消息。"""
        if framework_content and framework_content.strip():
            return framework_content.strip()

        full_msg = (event.get_message_str() or "").strip()
        if not full_msg:
            return ""

        lower_msg = full_msg.lower()
        cmd = cmd_name.lower()

        # 支持 /cmd 、自定义前缀、以及无空格紧跟参数
        # 1) 标准 "/cmd " 或 "/cmd\n"
        for prefix in (f"/{cmd} ", f"/{cmd}\n", f"/{cmd}\t"):
            idx = lower_msg.find(prefix)
            if idx != -1:
                return full_msg[idx + len(prefix) :].strip()

        # 2) 消息以 /cmd 开头且后面直接跟内容
        bare = f"/{cmd}"
        if lower_msg.startswith(bare):
            rest = full_msg[len(bare) :].lstrip(" \t\n:：")
            return rest.strip()

        # 3) 任意位置的 "cmd " 作为弱匹配（兼容无斜杠前缀）
        weak = f"{cmd} "
        idx = lower_msg.find(weak)
        if idx != -1:
            return full_msg[idx + len(weak) :].strip()

        return ""
