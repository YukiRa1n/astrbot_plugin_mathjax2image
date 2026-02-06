"""
工具层 - AOP装饰器
日志、超时、重试等横切关注点
"""

import asyncio
import functools
import time
from typing import Callable, TypeVar

from astrbot.api import logger

T = TypeVar("T")


def log_execution(func: Callable[..., T]) -> Callable[..., T]:
    """日志装饰器 - 记录函数执行"""

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs) -> T:
        func_name = func.__name__
        logger.debug(f"[MathJax2Image] {func_name} 开始执行")
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            elapsed = time.time() - start_time
            logger.debug(f"[MathJax2Image] {func_name} 执行完成，耗时: {elapsed:.2f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"[MathJax2Image] {func_name} 执行失败，耗时: {elapsed:.2f}s, 错误: {e}"
            )
            raise

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs) -> T:
        func_name = func.__name__
        logger.debug(f"[MathJax2Image] {func_name} 开始执行")
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            logger.debug(f"[MathJax2Image] {func_name} 执行完成，耗时: {elapsed:.2f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"[MathJax2Image] {func_name} 执行失败，耗时: {elapsed:.2f}s, 错误: {e}"
            )
            raise

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper


def with_timeout(timeout_ms: int):
    """超时装饰器"""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs), timeout=timeout_ms / 1000
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"{func.__name__} 超时 ({timeout_ms}ms)")

        return wrapper

    return decorator


def retry(max_attempts: int = 3, delay_ms: int = 1000):
    """重试装饰器"""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"[MathJax2Image] {func.__name__} 第{attempt + 1}次尝试失败: {e}, "
                            f"将在 {delay_ms}ms 后重试"
                        )
                        await asyncio.sleep(delay_ms / 1000)
            raise last_error

        return wrapper

    return decorator
