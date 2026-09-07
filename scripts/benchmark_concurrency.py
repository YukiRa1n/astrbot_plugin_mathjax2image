"""Measure unique render throughput, queue latency, and browser RSS by concurrency."""

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path


async def main() -> None:
    """Run warmed independent renders against the selected plugin checkout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin-dir", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--profile", choices=["plugin", "stock"], default="plugin")
    args = parser.parse_args()
    if args.jobs < 1 or any(value < 1 for value in args.concurrency):
        parser.error("jobs and concurrency must be positive")
    sys.path.insert(0, str(args.plugin_dir.resolve().parent))
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
    from PIL import Image

    logging.disable(logging.CRITICAL)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for concurrency in args.concurrency:
        manager = BrowserManager(max_pages=concurrency)
        if args.profile == "stock":
            manager._launch_options = lambda engine: {"headless": True}
        renderer = PageRenderer(
            manager,
            args.plugin_dir,
            mathjax_timeout=30000,
            fail_on_mathjax_timeout=True,
        )
        converter = MarkdownConverter(args.plugin_dir / "templates/template.html")
        root = psutil.Process()
        peak_rss = 0
        sampling = True

        async def sample():
            nonlocal peak_rss
            while sampling:
                total = 0
                for process in root.children(recursive=True):
                    try:
                        total += process.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                peak_rss = max(peak_rss, total)
                await asyncio.sleep(0.05)

        sampler = asyncio.create_task(sample())
        gate = asyncio.Semaphore(concurrency)
        active, peak_active = 0, 0
        original_screenshot = renderer._take_screenshot

        async def verified_screenshot(page, output):
            content = await page.evaluate(
                "() => document.querySelector('.render-content')?.textContent || document.body.textContent"
            )
            if output.stem not in content:
                raise AssertionError("Concurrent page content was mixed")
            errors = await page.locator("mjx-merror, [data-mjx-error]").count()
            if errors or await page.locator("mjx-container").count() != 1:
                raise AssertionError("Math was not correctly rendered")
            await original_screenshot(page, output)

        renderer._take_screenshot = verified_screenshot

        async def render_one(label):
            nonlocal active, peak_active
            started = time.perf_counter()
            async with gate:
                active += 1
                peak_active = max(peak_active, active)
                try:
                    text = (
                        f"# {label}\n\n并发渲染测试。Each page has independent content.\n\n"
                        + r"$$\int_0^1 x^2\,dx=\frac{1}{3}$$"
                    )
                    html = converter.convert_to_html(text)
                    output = args.output / f"{label}.png"
                    await renderer.render_to_image(html, output)
                    with Image.open(output) as image:
                        image.verify()
                finally:
                    active -= 1
            return (time.perf_counter() - started) * 1000

        try:
            started = time.perf_counter()
            await manager.get_browser()
            launch_ms = (time.perf_counter() - started) * 1000
            # Load each isolated page, fonts, and MathJax before timing throughput.
            await asyncio.gather(
                *(render_one(f"warm-{concurrency}-{i}") for i in range(concurrency))
            )
            peak_rss = 0
            peak_active = 0
            started = time.perf_counter()
            samples = await asyncio.gather(
                *(render_one(f"job-{concurrency}-{i}") for i in range(args.jobs))
            )
            wall = time.perf_counter() - started
            result = {
                "concurrency": concurrency,
                "jobs": args.jobs,
                "launch_ms": round(launch_ms, 2),
                "wall_s": round(wall, 3),
                "images_per_s": round(args.jobs / wall, 3),
                "median_latency_ms_including_queue": round(
                    statistics.median(samples), 2
                ),
                "max_latency_ms_including_queue": round(max(samples), 2),
                "driver_browser_peak_rss_mib": round(peak_rss / 2**20, 1),
                "peak_active": peak_active,
                "errors": 0,
            }
            results.append(result)
            print(json.dumps(result), flush=True)
        finally:
            await manager.close()
            sampling = False
            await sampler
    payload = {
        "profile": args.profile,
        "plugin_dir": str(args.plugin_dir.resolve()),
        "results": results,
    }
    (args.output / "concurrency.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
