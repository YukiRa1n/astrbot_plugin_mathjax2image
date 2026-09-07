"""Compare idle-page memory and resumption latency with the asset cache retained."""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path


async def main():
    """Compare always-warm pages with the idle-page recycling policy."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plugin_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin_dir.parent))
    import psutil
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        MarkdownConverter,
    )

    logging.disable(logging.CRITICAL)
    args.output.mkdir(parents=True, exist_ok=True)
    root = psutil.Process()

    def rss():
        total = 0
        for process in root.children(recursive=True):
            try:
                total += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total / 2**20

    results = []
    for name, keep, timeout in [("always-warm", 2, 0), ("recycle-idle", 1, 1)]:
        manager = BrowserManager(max_pages=2, max_idle_pages=keep, idle_timeout=timeout)
        renderer = PageRenderer(
            manager, plugin_dir, mathjax_timeout=30000, fail_on_mathjax_timeout=True
        )
        converter = MarkdownConverter(plugin_dir / "templates/template.html")
        fetching = 0
        try:
            await manager.get_browser()
            original_get = manager.request_context.get

            async def get(*args, **kwargs):
                nonlocal fetching
                fetching += 1
                return await original_get(*args, **kwargs)

            manager.request_context.get = get

            async def render(index):
                html = converter.convert_to_html(
                    f"# {name} {index}\n\n中文字体与数学公式。\n\n"
                    + r"$$\int_0^1 x^2\,dx=\frac{1}{3}$$"
                )
                await renderer.render_to_image(
                    html, args.output / f"{name}-{index}.png"
                )

            await asyncio.gather(render(0), render(1))
            await asyncio.sleep(0.1)
            burst = rss()
            pages_after_burst = manager._active_pages_count
            await asyncio.sleep(1.2)
            idle = rss()
            pages_after_idle = manager._active_pages_count
            fetch_before_resume = fetching
            started = time.perf_counter()
            await render(2)
            resume = (time.perf_counter() - started) * 1000
            result = {
                "policy": name,
                "rss_after_burst_mib": round(burst, 1),
                "rss_idle_mib": round(idle, 1),
                "pages_after_burst": pages_after_burst,
                "pages_after_idle": pages_after_idle,
                "resume_ms": round(resume, 1),
                "downloads_on_resume": fetching - fetch_before_resume,
                "asset_cache_mib": round(renderer._cdn_cache_bytes / 2**20, 2),
            }
            results.append(result)
            print(json.dumps(result), flush=True)
        finally:
            await manager.close()
    (args.output / "memory.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
