"""Persistent on-disk cache for allowlisted static CDN assets.

The renderer's in-memory asset cache is lost whenever AstrBot restarts or the
plugin reloads, so the first renders afterwards download MathJax, TikZJax and
dozens of font subsets again. On slow or misconfigured networks (for example a
proxy that black-holes IPv6, costing seconds per new connection) this dominates
render time. This cache keeps those immutable files on disk.

All methods are synchronous and meant to run in a worker thread.
"""

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

# npm 资源带精确版本号（如 mathjax@3.2.2、tikzjax@1.0.0-beta24）时内容不可变
_PINNED_NPM_PATH = re.compile(r"/(?:@[^/]+/)?[^/@]+@\d+\.\d+\.\d+[^/]*/")
_NPM_HOSTS = frozenset({"cdn.jsdelivr.net", "unpkg.com"})
# 未带版本号的资源（如 OSS 字体）可能被原地替换，只缓存有限时间
_UNPINNED_MAX_AGE = 7 * 24 * 3600


class AssetDiskCache:
    """Size-bounded, least-recently-used file cache keyed by URL."""

    def __init__(self, directory: Path, max_bytes: int):
        """Create the cache.

        Args:
            directory: Directory owned by this cache.
            max_bytes: Total size budget; oldest entries are evicted beyond it.
        """
        self._directory = Path(directory)
        self._max_bytes = max(0, int(max_bytes))
        self._total_bytes: int | None = None

    @property
    def enabled(self) -> bool:
        return self._max_bytes > 0

    @staticmethod
    def max_age(url: str) -> float | None:
        """Return the maximum age in seconds, or None for immutable assets."""
        parsed = urlparse(url)
        if parsed.hostname in _NPM_HOSTS and _PINNED_NPM_PATH.search(parsed.path):
            return None
        return _UNPINNED_MAX_AGE

    def _paths(self, url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._directory / f"{digest}.bin", self._directory / f"{digest}.json"

    def get(self, url: str) -> tuple[bytes, str] | None:
        """Return ``(body, content_type)`` for a fresh entry, else None."""
        if not self.enabled:
            return None
        body_path, meta_path = self._paths(url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("url") != url:
                return None
            max_age = self.max_age(url)
            if max_age is not None and time.time() - meta["stored"] > max_age:
                self._remove(body_path, meta_path)
                return None
            body = body_path.read_bytes()
            if len(body) != meta.get("size"):
                self._remove(body_path, meta_path)
                return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        # 以访问时间作为 LRU 依据；失败不影响命中
        try:
            os.utime(meta_path)
        except OSError:
            pass
        return body, str(meta.get("content_type") or "application/octet-stream")

    def put(self, url: str, body: bytes, content_type: str) -> bool:
        """Store a response body atomically, evicting old entries if needed."""
        size = len(body)
        if not self.enabled or size > self._max_bytes:
            return False
        body_path, meta_path = self._paths(url)
        meta = {
            "url": url,
            "content_type": content_type,
            "size": size,
            "stored": time.time(),
        }
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            previous = body_path.stat().st_size if body_path.exists() else 0
            self._atomic_write(body_path, body)
            # 元数据最后写入：读取方以元数据存在且大小一致为完整条目
            self._atomic_write(
                meta_path, json.dumps(meta, ensure_ascii=False).encode("utf-8")
            )
        except OSError:
            return False
        if self._total_bytes is not None:
            self._total_bytes += size - previous
        self._evict()
        return True

    def _atomic_write(self, path: Path, data: bytes) -> None:
        fd, tmp_name = tempfile.mkstemp(dir=str(self._directory), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _entries(self) -> list[tuple[float, int, Path, Path]]:
        """Return ``(last_used, size, body_path, meta_path)`` for each entry."""
        entries = []
        for meta_path in self._directory.glob("*.json"):
            body_path = meta_path.with_suffix(".bin")
            try:
                entries.append(
                    (
                        meta_path.stat().st_mtime,
                        body_path.stat().st_size,
                        body_path,
                        meta_path,
                    )
                )
            except OSError:
                continue
        return entries

    def _evict(self) -> None:
        if self._total_bytes is not None and self._total_bytes <= self._max_bytes:
            return
        # 只在超出预算（或首次统计）时扫描目录，条目数为数百量级
        entries = self._entries()
        self._total_bytes = sum(entry[1] for entry in entries)
        if self._total_bytes <= self._max_bytes:
            return
        for _, size, body_path, meta_path in sorted(entries):
            self._remove(body_path, meta_path)
            self._total_bytes -= size
            if self._total_bytes <= self._max_bytes:
                break

    @staticmethod
    def _remove(body_path: Path, meta_path: Path) -> None:
        # 先删元数据，避免读取方看到元数据却读到被删的正文
        meta_path.unlink(missing_ok=True)
        body_path.unlink(missing_ok=True)
