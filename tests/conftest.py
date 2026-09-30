"""Fixtures for testing the real render admission and scheduling pipeline.

需要真实 Playwright 浏览器的用例标了 ``browser``（见文件末尾）：浏览器缺失时
整条跳过，而不是报 ``Executable doesn't exist`` 的失败，把真正的回归淹没在
环境噪声里。用 ``-m "not browser"`` 也可以主动跳过。
"""

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_plugin_root = Path(__file__).resolve().parents[1]
_parent_dir = _plugin_root.parent
for _p in [str(_parent_dir), str(_plugin_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

if "astrbot" not in sys.modules:
    astrbot_mock = types.ModuleType("astrbot")
    astrbot_mock.__path__ = [str(_plugin_root / "astrbot")]
    astrbot_api_mock = types.ModuleType("astrbot.api")
    astrbot_api_mock.__path__ = [str(_plugin_root / "astrbot" / "api")]
    astrbot_api_mock.logger = MagicMock()
    astrbot_api_mock.AstrBotConfig = MagicMock
    astrbot_mock.api = astrbot_api_mock
    sys.modules["astrbot"] = astrbot_mock
    sys.modules["astrbot.api"] = astrbot_api_mock

for _mod_name in [
    "astrbot.api.event",
    "astrbot.api.message_components",
    "astrbot.api.star",
]:
    if _mod_name not in sys.modules:
        _m = types.ModuleType(_mod_name)
        _m.AstrMessageEvent = MagicMock
        _m.MessageChain = MagicMock
        _m.Comp = MagicMock()
        _m.StarTools = MagicMock()
        _m.Context = MagicMock
        _m.Star = MagicMock
        _m.register = MagicMock()
        _m.filter = MagicMock()
        sys.modules[_mod_name] = _m


@pytest.fixture
def render_engine_factory(tmp_path, monkeypatch):
    """Build a renderer with real scheduling and isolated output files.

    Args:
        tmp_path: Test output directory.
        monkeypatch: Scoped replacements for host integration.

    Returns:
        Factory accepting RenderOrchestrator configuration keywords.
    """
    from astrbot_plugin_mathjax2image.application.render_orchestrator import (
        RenderOrchestrator,
    )

    monkeypatch.setattr(
        "astrbot_plugin_mathjax2image.application.render_orchestrator.StarTools.get_data_dir",
        lambda _name: tmp_path,
    )

    def create(**settings):
        engine = RenderOrchestrator(Path(__file__).parents[1], **settings)
        engine._dependency_installer.check_and_install = AsyncMock(return_value=True)
        engine._latex_preprocessor.preprocess = lambda content: content
        engine._markdown_converter.convert_to_html = lambda content, _bg: content
        return engine

    return create


_SKIP_REASON = (
    "需要 Playwright 浏览器：python -m playwright install chromium --only-shell"
)


def _browser_available() -> bool:
    """真的启动一次无头 chromium；失败即视为本机没有可用浏览器。

    只检查 ``chromium.executable_path`` 会漏判：那条路径指向完整 chromium，
    而 ``--only-shell`` 装的是 headless shell，两者不是同一个文件。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config, items):
    marked = [item for item in items if item.get_closest_marker("browser")]
    if not marked or _browser_available():
        return
    for item in marked:
        item.add_marker(pytest.mark.skip(reason=_SKIP_REASON))
