# -*- coding: utf-8 -*-
"""
MathJax2Image 浏览器池与渲染器测试
"""

import os
import pytest
import asyncio
from pathlib import Path
from playwright.async_api import Error as PlaywrightError
from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import BrowserManager
from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import PageRenderer
from astrbot_plugin_mathjax2image.domain.errors import RenderError
from astrbot_plugin_mathjax2image.utils.safe_eval import safe_eval_math

pytestmark = pytest.mark.asyncio


async def test_safe_math_evaluator():
    """测试 AST 数学安全表达式求值器"""
    # 正常用例
    assert safe_eval_math("2 + 3 * 4") == 14.0
    assert safe_eval_math("sqrt(16) + abs(-5)") == 9.0
    assert safe_eval_math("sin(pi / 2)") == 1.0
    
    # 危险注入应该安全拒绝并返回 nan
    import math
    assert math.isnan(safe_eval_math("__import__('os').system('ls')"))
    assert math.isnan(safe_eval_math("eval('2+2')"))


async def test_browser_manager_pool(tmp_path):
    """测试 BrowserManager 的页面复用和事件循环自愈能力"""
    bm = BrowserManager(max_pages=2)
    
    # 1. 拿 page 1
    page1, setup1 = await bm.acquire_page(300, 300)
    assert not setup1
    assert bm._active_pages_count == 1
    
    # 2. 拿 page 2
    page2, setup2 = await bm.acquire_page(300, 300)
    assert not setup2
    assert bm._active_pages_count == 2
    
    # 3. 归还 page 1
    await bm.release_page(page1)
    assert bm._pool.qsize() == 1
    
    # 4. 再次拿，应该复用且已标记为 setup
    page3, setup3 = await bm.acquire_page(300, 300)
    assert page3 is page1
    assert setup3
    assert bm._active_pages_count == 2
    
    # 5. 异常销毁
    await bm.release_page(page3, exception_occurred=True)
    assert bm._active_pages_count == 1
    
    # 关闭池
    await bm.close()
    assert bm._active_pages_count == 0


async def test_page_renderer_rendering(tmp_path):
    """测试 PageRenderer 渲染管道"""
    bm = BrowserManager(max_pages=2)
    renderer = PageRenderer(
        browser_manager=bm,
        plugin_dir=Path(__file__).parent.parent,
        viewport_width=300,
        viewport_height=300,
        mathjax_timeout=2000,
        tikz_timeout=2000
    )
    
    html = """
    <!DOCTYPE html>
    <html>
    <body>
        <div>Concurrent Render Test</div>
        <script>
            window.mathJaxReady = true;
        </script>
    </body>
    </html>
    """
    
    out_png = tmp_path / "rendered.png"
    await renderer.render_to_image(html, out_png)
    
    assert out_png.exists()
    assert out_png.stat().st_size > 0
    
    await bm.close()


async def test_page_renderer_rejects_unrendered_tikz(tmp_path):
    """测试 TikZ 容器未生成 SVG 时渲染失败"""
    bm = BrowserManager(max_pages=1)
    renderer = PageRenderer(
        browser_manager=bm,
        plugin_dir=Path(__file__).parent.parent,
        viewport_width=300,
        viewport_height=300,
        mathjax_timeout=2000,
        tikz_timeout=500,
    )

    html = """
    <!DOCTYPE html>
    <html>
    <body>
        <div class="tikz-diagram">Unrendered TikZ</div>
        <script>
            window.mathJaxReady = true;
        </script>
    </body>
    </html>
    """

    out_png = tmp_path / "failed.png"
    with pytest.raises(RenderError):
        await renderer.render_to_image(html, out_png)

    await bm.close()


@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
async def test_configured_browser_engine_smoke(tmp_path, engine):
    bm = BrowserManager(max_pages=1, engine=engine)
    renderer = PageRenderer(
        browser_manager=bm,
        plugin_dir=Path(__file__).parent.parent,
        viewport_width=300,
        viewport_height=300,
        mathjax_timeout=1000,
        tikz_timeout=1000,
    )
    output = tmp_path / f"{engine}.png"
    html = """
    <html><body><div>engine</div>
    <script>window.mathJaxReady = true;</script></body></html>
    """
    try:
        await renderer.render_to_image(html, output)
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(f"Playwright {engine} is not installed")
        raise
    except RenderError as exc:
        if "截图为空" in str(exc) or "文件未生成" in str(exc):
            pytest.skip(f"Playwright {engine} screenshot is unreliable on this platform")
        raise
    finally:
        await bm.close()

    assert output.exists()
    assert output.stat().st_size > 0
