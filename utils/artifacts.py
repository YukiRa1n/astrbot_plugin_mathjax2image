"""渲染产物的读取与回收工具。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path


def remove_artifact(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def consume_artifact(path: Path) -> bytes:
    """读取文件内容，并确保临时产物被回收。"""
    try:
        return await asyncio.to_thread(path.read_bytes)
    finally:
        remove_artifact(path)


def cleanup_stale_artifacts(
    directory: Path,
    pattern: str = "render_*.png",
    max_age_seconds: int = 3600,
) -> int:
    if not directory.exists():
        return 0

    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in directory.glob(pattern):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
