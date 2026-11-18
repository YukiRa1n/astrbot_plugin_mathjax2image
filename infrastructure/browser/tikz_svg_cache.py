"""Bounded, renderer-local cache for compiled TikZ figures."""

import asyncio
import hashlib
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from ..converter.markdown_converter import iter_trusted_html_spans

_WRAPPER = '<div class="tikz-diagram">'
MATH_REQUIRED_DECLARATION = re.compile(r"window\.mathJaxRequired = (true|false);")
_DYNAMIC_TEX = re.compile(
    r"\\(?:time|today|day|month|year|pdfelapsedtime|pgfmathrandom\w*)\b|\b(?:rnd|rand|random)\b"
)


@dataclass
class FigureBatch:
    """The unique figures in one document and the misses owned by this render."""

    html: str
    keys: list[bytes]
    hits: list[str | None]
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    compiled: dict[str, str] = field(default_factory=dict)


class TikzSvgCache:
    """Share completed SVGs within one backend and coalesce overlapping renders."""

    def __init__(self, limit: int):
        self.limit = max(0, limit)
        self.size = 0
        self.entries: OrderedDict[bytes, tuple[bytes, float]] = OrderedDict()
        self.pending: dict[bytes, asyncio.Event] = {}

    async def prepare(
        self, html: str, start: int, end: int, timeout: float
    ) -> FigureBatch | None:
        """Replace hits and repeated figures before acquiring a compiler or page.

        Args:
            html: Converter-generated document.
            start: Start of the rendered main element's inner HTML.
            end: End of that inner HTML.
            timeout: Maximum time to wait for existing owners in seconds.

        Returns:
            Per-document cache state, or None for documents without reusable figures.
        """
        body = html[start:end]
        # Backend, binary path and optimizer versions are fixed for the lifetime
        # of this instance. The shell includes fonts, template and package config.
        # Formula typesetting in a caption does not change the standalone figure.
        shell = MATH_REQUIRED_DECLARATION.sub("", html[:start] + html[end:], count=1)
        shell_key = hashlib.sha256(shell.encode()).digest()
        pieces = []
        keys = []
        indices = {}
        cursor = 0
        for left, right in iter_trusted_html_spans(body):
            block = body[left:right]
            if not block.startswith(_WRAPPER) or _DYNAMIC_TEX.search(block):
                continue
            key = hashlib.sha256(shell_key + block.encode()).digest()
            index = indices.get(key)
            first = index is None
            if first:
                index = len(keys)
                indices[key] = index
                keys.append(key)
            pieces.append((body[cursor:left], index, block if first else None))
            cursor = right
        if not keys:
            return None

        # Wait before claiming *any* key, so documents [A,B] and [B,A]
        # cannot deadlock. No compiler/page slot is held while waiting.
        deadline = time.monotonic() + timeout
        while blockers := {self.pending[k] for k in keys if k in self.pending}:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in blockers)),
                deadline - time.monotonic(),
            )

        hits = []
        now = time.monotonic()
        for key in keys:
            entry = self.entries.pop(key, None)
            if entry is not None and now - entry[1] < 300:
                self.entries[key] = entry
                hits.append(entry[0].decode())
            else:
                if entry is not None:
                    self.size -= len(entry[0])
                hits.append(None)
        parts = [html[:start]]
        for prefix, index, block in pieces:
            parts.append(prefix)
            if block is not None and hits[index] is None:
                parts.append(
                    f'<div class="tikz-diagram" data-tikz-source="{index}">'
                    + block[len(_WRAPPER) :]
                )
            else:
                parts.append(
                    f'<div class="tikz-svg-slot" data-tikz-copy="{index}"></div>'
                )
        parts.extend((body[cursor:], html[end:]))
        batch = FigureBatch("".join(parts), keys, hits)
        for key, hit in zip(keys, hits):
            if hit is None:
                self.pending[key] = batch.ready
        return batch

    def finish(self, batch: FigureBatch, complete: bool) -> None:
        """Publish successful figures and wake waiters, including on cancellation."""
        try:
            if complete:
                for index, svg in batch.compiled.items():
                    key = batch.keys[int(index)]
                    payload = svg.encode()
                    if len(payload) > self.limit:
                        continue
                    while self.entries and (
                        len(self.entries) >= 128
                        or self.size + len(payload) > self.limit
                    ):
                        _, (removed, _) = self.entries.popitem(last=False)
                        self.size -= len(removed)
                    self.entries[key] = (payload, time.monotonic())
                    self.size += len(payload)
        finally:
            for key in batch.keys:
                if self.pending.get(key) is batch.ready:
                    del self.pending[key]
            batch.ready.set()
