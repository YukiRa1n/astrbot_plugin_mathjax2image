# -*- coding: utf-8 -*-
"""Shared security helpers for mathjax2image."""

from __future__ import annotations

from urllib.parse import urlparse


#: Hosts that reach the local machine without leaving it. ``0.0.0.0`` is the
#: IPv4 unspecified address (a bind target), not a loopback address, so it is
#: deliberately absent: connecting to it can reach a local service on some
#: stacks and must go through ``allow_remote``.
LOCAL_CDP_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def validate_cdp_url(url: str, allow_remote: bool = False) -> str:
    """Validate a CDP endpoint URL; default loopback-only."""
    cleaned = (url or "").strip()
    if not cleaned:
        return ""

    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError(
            f"CDP URL scheme must be http/https/ws/wss, got: {parsed.scheme or '(empty)'}"
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("CDP URL must include a hostname")

    if not allow_remote and host not in LOCAL_CDP_HOSTS:
        raise ValueError(
            f"CDP host '{host}' is not loopback. "
            "Enable allow_remote_cdp only on trusted networks."
        )

    return cleaned
