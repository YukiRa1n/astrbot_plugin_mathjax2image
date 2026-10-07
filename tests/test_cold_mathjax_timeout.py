"""Regression tests for cold-page MathJax startup timing."""

import pytest
from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
    BrowserManager,
)
from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
    PageRenderer,
)


@pytest.mark.asyncio
@pytest.mark.browser
async def test_cold_page_waits_for_delayed_mathjax_ready(tmp_path):
    manager = BrowserManager(max_pages=1)
    renderer = PageRenderer(manager, tmp_path, mathjax_timeout=50)
    output = tmp_path / "delayed-mathjax.png"
    html = """<!doctype html>
    <html><head><script>
      window.mathJaxRequired = true;
      window.mathJaxReady = false;
      setTimeout(() => { window.mathJaxReady = true; }, 250);
    </script></head><body><main>$$x$$</main></body></html>"""

    try:
        complete = await renderer._render_uncached(html, output)
        assert complete is True
        assert output.is_file()
    finally:
        await manager.close()
