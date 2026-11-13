"""配置值归一化。

刻意不依赖 ``astrbot``：决定"哪个设置值可用"的规则应当能脱离宿主框架单测。
"""

from __future__ import annotations


def safe_bool(value, default: bool = False) -> bool:
    """Safely parse a boolean setting without importing the host framework.

    Args:
        value: Raw config value. Accepts ``bool`` and explicit truthy strings
            (``true``, ``1``, ``yes``, ``on``); ``None`` falls back to
            ``default`` and other non-string/non-bool values return ``False``.
        default: Value returned when ``value`` is ``None``.

    Returns:
        Normalized boolean setting.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return False


def safe_int(value, default: int) -> int:
    """int 配置项安全转换，非法值（含 NaN/Infinity 的 OverflowError）回退默认。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def normalized_choice(value, default: str) -> str:
    """把枚举类配置项归一为小写 token；None/空串回退默认值。"""
    if value is None or value == "":
        value = default
    return str(value).strip().lower()
