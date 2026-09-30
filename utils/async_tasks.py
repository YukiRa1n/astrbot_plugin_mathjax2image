"""Thread work whose lifetime remains bounded by its caller's resource slot."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


async def run_in_thread(function: Callable[..., _T], *args, **kwargs) -> _T:
    """Wait for running thread work before propagating caller cancellation.

    Args:
        function: Synchronous operation to execute in the default thread pool.
        *args: Positional arguments for the operation.
        **kwargs: Keyword arguments for the operation.

    Returns:
        The operation's result.

    Raises:
        asyncio.CancelledError: The caller was cancelled, after the worker exits.
        Exception: The operation failed while the caller was still active.
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Repeated cancellation must not release the caller's slot early either.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
