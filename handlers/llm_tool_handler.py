"""
LLM工具处理器
处理 render_math, send_image 等LLM工具调用
"""

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp

from ..application.render_orchestrator import MAX_RENDER_LENGTH
from ..utils.artifacts import consume_artifact, remove_artifact

if TYPE_CHECKING:
    from ..application import RenderOrchestrator


class LLMToolHandler:
    """LLM工具处理器"""

    _IMAGE_TTL_SECONDS = 300

    def __init__(
        self,
        render_orchestrator: "RenderOrchestrator",
        context,
    ):
        self._render_orchestrator = render_orchestrator
        self._context = context

        self._pending_images: dict[str, tuple[Path, float]] = {}
        self._pending_lock = asyncio.Lock()
        self._last_rendered_image: Optional[Path] = None

    async def handle_render_math(self, event: AstrMessageEvent, content: str) -> str:
        """处理 render_math 工具调用

        将Markdown/LaTeX/TikZ内容渲染为图片
        """
        if not content:
            return "错误：content 参数不能为空"

        if len(content) > MAX_RENDER_LENGTH:
            return f"渲染内容过长，最大支持 {MAX_RENDER_LENGTH} 字符"

        session_key = self._get_session_key(event)

        try:
            # 直接渲染（render_orchestrator内部会进行预处理）
            image_path = await self._render_orchestrator.render(content)

            if image_path and image_path.exists():
                logger.info(f"[MathJax2Image] LLM工具渲染成功: {image_path}")
                # 保存状态（加锁避免并发竞争，_last_rendered_image 也在锁内写）
                async with self._pending_lock:
                    old_entry = self._pending_images.pop(session_key, None)
                    if old_entry:
                        old_path, _ = old_entry
                        remove_artifact(old_path)
                    self._pending_images[session_key] = (image_path, time.time())
                    self._last_rendered_image = image_path
                return "渲染成功，图片已生成。请调用 send_image 工具发送图片。"
            else:
                remove_artifact(image_path)
                return "渲染失败: 图片未生成"

        except Exception as e:
            logger.error(f"[MathJax2Image] LLM工具渲染失败: {e}")
            return f"渲染失败: {str(e)}"

    async def handle_send_image(self, event: AstrMessageEvent) -> str:
        """处理 send_image 工具调用

        发送最近渲染的图片给用户
        """
        session_key = self._get_session_key(event)

        async with self._pending_lock:
            self._cleanup_expired_images()
            entry = self._pending_images.get(session_key)
            if not entry:
                return "没有可发送的图片,请先使用 render_math 渲染内容"
            image_path, _ = self._pending_images.pop(session_key)

        if not image_path.exists():
            self._last_rendered_image = None
            return f"图片文件不存在: {image_path}"

        try:
            image_name = image_path.name
            image_bytes = await consume_artifact(image_path)
            chain = [Comp.Image.fromBytes(image_bytes)]
            await self._context.send_message(
                event.unified_msg_origin, MessageChain(chain)
            )
            return f"图片已发送: {image_name}"
        except Exception as e:
            logger.error(f"[MathJax2Image] 发送图片失败: {e}")
            return f"发送图片失败: {str(e)}"
        finally:
            remove_artifact(image_path)
            self._last_rendered_image = None

    def _cleanup_expired_images(self) -> None:
        """清理过期的图片缓存"""
        now = time.time()
        expired = [
            k for k, (_, ts) in self._pending_images.items()
            if now - ts > self._IMAGE_TTL_SECONDS
        ]
        for k in expired:
            path, _ = self._pending_images.pop(k)
            remove_artifact(path)

    async def close(self) -> None:
        """回收所有尚未发送的渲染产物。"""
        async with self._pending_lock:
            entries = list(self._pending_images.values())
            self._pending_images.clear()
            self._last_rendered_image = None
        for path, _ in entries:
            remove_artifact(path)

    def _get_session_key(self, event: AstrMessageEvent) -> str:
        """获取会话隔离键。

        同一群聊共享 unified_msg_origin，仅用 origin 会导致不同用户的
        渲染结果互相覆盖（旧图被删除、send_image 无图可发）。
        因此附带 sender 维度作为隔离键。
        """
        origin = getattr(event, "unified_msg_origin", None) or ""
        sender = ""
        try:
            sender = str(event.get_sender_id() or "")
        except Exception:
            sender = ""
        if origin and sender:
            return f"{origin}|{sender}"
        return origin or sender or str(id(event))

    @property
    def last_rendered_image(self) -> Optional[Path]:
        """获取最近渲染的图片路径"""
        return self._last_rendered_image

    @property
    def has_pending_image(self) -> bool:
        """是否有待发送的图片"""
        return bool(self._pending_images)
