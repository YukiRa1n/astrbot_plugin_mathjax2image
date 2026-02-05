"""
LLM工具处理器
处理 render_math, send_image 等LLM工具调用
"""
import asyncio
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp

if TYPE_CHECKING:
    from ..application import RenderOrchestrator
    from ..infrastructure.converter import LatexPreprocessor


class LLMToolHandler:
    """LLM工具处理器"""

    def __init__(
        self,
        render_orchestrator: "RenderOrchestrator",
        latex_preprocessor: "LatexPreprocessor",
        context,
    ):
        self._render_orchestrator = render_orchestrator
        self._latex_preprocessor = latex_preprocessor
        self._context = context

        # 最近渲染的图片状态
        self._last_rendered_image: Optional[Path] = None
        self._render_success: bool = False

    async def handle_render_math(
        self, event: AstrMessageEvent, content: str
    ) -> str:
        """处理 render_math 工具调用

        将Markdown/LaTeX/TikZ内容渲染为图片
        """
        if not content:
            return "错误：content 参数不能为空"

        try:
            # 直接渲染（render_orchestrator内部会进行预处理）
            image_path = await self._render_orchestrator.render(content)

            if image_path and image_path.exists():
                logger.info(f"[MathJax2Image] LLM工具渲染成功: {image_path}")
                # 等待文件系统同步
                await asyncio.sleep(0.5)
                # 保存状态
                self._last_rendered_image = image_path
                self._render_success = True
                return "渲染成功，图片已生成。请调用 send_image 工具发送图片。"
            else:
                self._last_rendered_image = None
                self._render_success = False
                return "渲染失败: 图片未生成"

        except Exception as e:
            logger.error(f"[MathJax2Image] LLM工具渲染失败: {e}")
            self._last_rendered_image = None
            self._render_success = False
            return f"渲染失败: {str(e)}"

    async def handle_send_image(self, event: AstrMessageEvent) -> str:
        """处理 send_image 工具调用

        发送最近渲染的图片给用户
        """
        if not self._render_success:
            return "没有可发送的图片，请先使用 render_math 成功渲染内容"

        if self._last_rendered_image is None:
            return "没有可发送的图片，请先使用 render_math 渲染内容"

        if not self._last_rendered_image.exists():
            return f"图片文件不存在: {self._last_rendered_image}"

        try:
            chain = [Comp.Image.fromFileSystem(str(self._last_rendered_image))]
            await self._context.send_message(
                event.unified_msg_origin,
                MessageChain(chain)
            )
            # 重置标记
            self._render_success = False
            return f"图片已发送: {self._last_rendered_image.name}"
        except Exception as e:
            logger.error(f"[MathJax2Image] 发送图片失败: {e}")
            return f"发送图片失败: {str(e)}"

    @property
    def last_rendered_image(self) -> Optional[Path]:
        """获取最近渲染的图片路径"""
        return self._last_rendered_image

    @property
    def has_pending_image(self) -> bool:
        """是否有待发送的图片"""
        return self._render_success and self._last_rendered_image is not None
