"""
LLM编排器
管理与LLM的交互
"""
import re
import traceback
from typing import Optional, Any

from astrbot.api import logger


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
        self,
        user_input: str,
        system_prompt: str
    ) -> Optional[str]:
        """调用LLM生成内容

        Args:
            user_input: 用户输入
            system_prompt: 系统提示词

        Returns:
            LLM响应文本，失败时返回None
        """
        logger.info(f"[MathJax2Image] 开始调用LLM，主题: {user_input[:50]}...")

        try:
            provider = self._get_provider()
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

            logger.info(f"[MathJax2Image] LLM调用成功，响应长度: {len(response.completion_text)}")
            return self._filter_think_tags(response.completion_text)

        except Exception as e:
            logger.error(f"[MathJax2Image] LLM调用失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            return None

    def _get_provider(self) -> Optional[Any]:
        """获取LLM提供商"""
        provider_mgr = getattr(self._context, "provider_manager", None)
        if not provider_mgr:
            return None

        provider = None

        # 优先使用配置的提供商
        if self._provider_id and hasattr(provider_mgr, "inst_map"):
            provider = provider_mgr.inst_map.get(self._provider_id)
            if provider:
                logger.info(f"[MathJax2Image] 使用配置的提供商: {self._provider_id}")

        # 如果没有配置或未找到，使用当前会话的提供商
        if not provider:
            provider = provider_mgr.get_using_provider(None, None)

        return provider

    def _filter_think_tags(self, text: Optional[str]) -> Optional[str]:
        """过滤LLM响应中的<think>标签"""
        if not text:
            return None
        return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL)

    def set_provider_id(self, provider_id: str) -> None:
        """设置提供商ID"""
        self._provider_id = provider_id
