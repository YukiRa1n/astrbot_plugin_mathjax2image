"""Fixtures for testing the real render admission and scheduling pipeline.

需要真实 Playwright 浏览器的用例标了 ``browser``（见文件末尾）：浏览器缺失时
整条跳过，而不是报 ``Executable doesn't exist`` 的失败，把真正的回归淹没在
环境噪声里。用 ``-m "not browser"`` 也可以主动跳过。
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest


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
