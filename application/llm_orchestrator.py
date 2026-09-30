"""
LLM编排器
管理与LLM的交互
"""

import traceback
from typing import Any

from astrbot.api import logger


def _load_provider_type() -> Any:
    """惰性取得宿主的 ``ProviderType`` 枚举；宿主结构不同则返回 None。"""
    try:
        from astrbot.core.provider.entities import ProviderType
    except Exception:
        return None
    return ProviderType


class LLMOrchestrator:
    """
    LLM编排器

    负责与LLM提供商的交互，包括：
    1. 获取LLM提供商
    2. 调用LLM生成内容
    3. 过滤响应中的特殊标签
    """

    def __init__(self, context: Any, provider_id: str = ""):
        self._context = context
        self._provider_id = provider_id

    async def call_llm(
        self, user_input: str, system_prompt: str, umo: str = ""
    ) -> str | None:
        """调用LLM生成内容

        Args:
            user_input: 用户输入
            system_prompt: 系统提示词
            umo: 会话来源 ID，用于取该会话偏好的提供商

        Returns:
            LLM响应文本，失败时返回None
        """
        logger.debug(f"[MathJax2Image] 开始调用LLM，输入长度: {len(user_input)}")

        try:
            provider = self._get_provider(umo)
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

            logger.info(
                f"[MathJax2Image] LLM调用成功，响应长度: {len(response.completion_text)}"
            )
            return self._filter_think_tags(response.completion_text)

        except Exception as e:
            logger.error(f"[MathJax2Image] LLM调用失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            return None

    def _get_provider(self, umo: str = "") -> Any | None:
        """获取LLM提供商

        Args:
            umo: 会话来源 ID；缺省时退回宿主的默认对话提供商
        """
        provider_mgr = getattr(self._context, "provider_manager", None)
        if not provider_mgr:
            return None

        # 优先使用配置的提供商
        if self._provider_id and hasattr(provider_mgr, "inst_map"):
            provider = provider_mgr.inst_map.get(self._provider_id)
            if provider:
                logger.info(f"[MathJax2Image] 使用配置的提供商: {self._provider_id}")
                return provider

        # 没有配置或未找到，使用当前会话的提供商
        return self._session_provider(provider_mgr, umo)

    def _session_provider(self, provider_mgr: Any, umo: str) -> Any | None:
        """按宿主约定取对话提供商。

        ``ProviderManager.get_using_provider(provider_type, umo=None)`` 的
        ``provider_type`` 必须是 ``ProviderType`` 枚举成员：传 ``None`` 会落进它的
        ``else`` 分支，抛 ``ValueError("Unknown provider type: None")``，兜底路径就
        永远拿不到提供商。优先用宿主公开的 ``Context.get_using_provider(umo)``，
        它内部正是 ``get_using_provider(ProviderType.CHAT_COMPLETION, umo)``。
        """
        session_umo = umo or None

        get_using_provider = getattr(self._context, "get_using_provider", None)
        if callable(get_using_provider):
            try:
                return get_using_provider(session_umo)
            except Exception as e:
                logger.warning(
                    f"[MathJax2Image] 会话提供商不可用: {type(e).__name__}: {e}"
                )

        provider_type = _load_provider_type()
        if provider_type is not None:
            try:
                return provider_mgr.get_using_provider(
                    provider_type.CHAT_COMPLETION, session_umo
                )
            except Exception as e:
                logger.warning(
                    f"[MathJax2Image] 获取默认提供商失败: {type(e).__name__}: {e}"
                )

        # 宿主 API 都不可用时的最后兜底：第一个已注册的对话提供商
        insts = getattr(provider_mgr, "provider_insts", None) or []
        return insts[0] if insts else None

    def _filter_think_tags(self, text: str | None) -> str | None:
        """过滤LLM响应中的<think>标签。

        用前向扫描代替 ``re.sub(r"<think>.*?</think>\\s*", ...)``：惰性 ``.*?``
        在找不到闭合标签时，每个 ``<think>`` 都会把剩余文本扫一遍，一段
        100 KB 的响应里塞满未闭合标签就要 6.6 秒。
        """
        if not text:
            return None
        closing = "</think>"
        pieces: list[str] = []
        position = 0
        length = len(text)
        while True:
            start = text.find("<think>", position)
            if start < 0:
                break
            end = text.find(closing, start + len("<think>"))
            if end < 0:
                # 这个标签之后没有闭合标记，更靠后的标签也不可能有。
                break
            pieces.append(text[position:start])
            position = end + len(closing)
            # 与 ``\\s*`` 一致：吃掉闭合标签后的空白。
            while position < length and text[position].isspace():
                position += 1
        pieces.append(text[position:])
        return "".join(pieces)

    def set_provider_id(self, provider_id: str) -> None:
        """设置提供商ID"""
        self._provider_id = provider_id
