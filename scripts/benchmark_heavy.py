"""Benchmark difficult formulas and dense TikZ plots with wall/CPU measurements."""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path


async def main():
    """Render fixed workloads without relying on repeated-image cache hits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin-dir", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cases",
        nargs="+",
        default=["dense-curve", "surface-25", "surface-40", "torus"],
    )
    parser.add_argument("--timeout", type=int, default=90000)
    parser.add_argument("--no-resident", action="store_true")
    parser.add_argument("--no-compact-svg", action="store_true")
    parser.add_argument("--no-worker-opt", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.plugin_dir.resolve().parent))
    import psutil
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )
    from astrbot_plugin_mathjax2image.infrastructure.converter import (
        LatexPreprocessor,
        ListConverter,
        MarkdownConverter,
        MermaidConverter,
        TableConverter,
        TikzConverter,
        TikzPlotConverter,
    )

    logging.disable(logging.CRITICAL)
    args.output.mkdir(parents=True, exist_ok=True)
    manager = BrowserManager(max_pages=1)
    overrides = {}
    if args.no_resident:
        overrides["resident_engines"] = False
    if args.no_compact_svg:
        overrides["compact_svg"] = False
    if args.no_worker_opt:
        overrides["optimize_worker"] = False
    renderer = PageRenderer(
        manager,
        args.plugin_dir,
        tikz_timeout=args.timeout,
        mathjax_timeout=30000,
        fail_on_mathjax_timeout=True,
        **overrides,
    )
    preprocess = LatexPreprocessor(
        TikzConverter(TikzPlotConverter()),
        ListConverter(),
        TableConverter(),
        MermaidConverter(),
    )
    converter = MarkdownConverter(args.plugin_dir / "templates/template.html")
    cases = {
        "dense-curve": r"""\begin{tikzpicture}
\draw[domain=0:4,samples=2000,blue,thick] plot (\x,{exp(-\x^2)*(\x^4-6*\x^2+3)});
\draw[->] (0,0) -- (4.5,0);
\end{tikzpicture}""",
        "torus": r"""\begin{tikzpicture}
\begin{axis}[view={45}{28},width=11cm,height=8cm,axis lines=none]
\addplot3[surf,shader=flat,samples=36,samples y=20,domain=0:360,y domain=0:360,z buffer=sort]
({(2+0.6*cos(y))*cos(x)},{(2+0.6*cos(y))*sin(x)},{0.6*sin(y)});
\end{axis}
\end{tikzpicture}""",
    }
    for n in [25, 40, 80]:
        cases[f"surface-{n}"] = r"""\begin{tikzpicture}
\begin{axis}[view={45}{32},width=11cm,height=8cm,xlabel={$x$},ylabel={$y$},zlabel={$z$}]
\addplot3[surf,shader=flat,samples=SAMPLES,domain=-3:3,y domain=-3:3]
{exp(-0.18*(x^2+y^2))*sin(deg(3*x))*cos(deg(2*y))};
\end{axis}
\end{tikzpicture}""".replace("SAMPLES", str(n))
    process = psutil.Process()
    results = {}
    try:
        await manager.get_browser()
        # Warm TikZJax and pgfplots before the measured workloads.
        warm = cases["surface-25"].replace("samples=25", "samples=2")
        await renderer.render_to_image(
            converter.convert_to_html(preprocess.preprocess(warm)),
            args.output / "warm.png",
        )
        for name in args.cases:
            totals = {}
            peak_rss = 0
            running = True

            def collect():
                nonlocal peak_rss
                rss = 0
                for child in [process, *process.children(recursive=True)]:
                    try:
                        cpu = child.cpu_times()
                        totals[(child.pid, child.create_time())] = cpu.user + cpu.system
                        rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                peak_rss = max(peak_rss, rss)

            async def sample():
                while running:
                    collect()
                    await asyncio.sleep(0.05)

            collect()
            before = sum(totals.values())
            sampler = asyncio.create_task(sample())
            started = time.perf_counter()
            state = {}
            original = renderer._take_screenshot

            async def capture(page, output):
                nonlocal state
                svgs = await page.locator(".tikz-diagram svg").evaluate_all(
                    "(nodes)=>nodes.map(n=>n.outerHTML)"
                )
                if (
                    len(svgs) != 1
                    or not await page.locator(".tikz-diagram svg path").count()
                ):
                    raise RuntimeError("Expected a fully rendered TikZ SVG")
                state = {
                    "svg_count": len(svgs),
                    "svg_optimization": await page.evaluate("window.__svgStats || []"),
                    "worker_profiles": [
                        await worker.evaluate(
                            "({phases:globalThis.__texProfile,snapshot:globalThis.__texSnapshotStats})"
                        )
                        for worker in page.workers
                    ],
                    "wasm_compiles": [
                        await worker.evaluate("globalThis.__mathjaxWasmCompiles || 0")
                        for worker in page.workers
                    ],
                    "svg_bytes": sum(len(s.encode()) for s in svgs),
                }
                for i, svg in enumerate(svgs):
                    (args.output / f"{name}-{i}.svg").write_text(svg, encoding="utf-8")
                await original(page, output)

            renderer._take_screenshot = capture
            try:
                processed = await asyncio.to_thread(preprocess.preprocess, cases[name])
                preprocess_ms = (time.perf_counter() - started) * 1000
                html = converter.convert_to_html(processed)
                (args.output / f"{name}.tex").write_text(cases[name], encoding="utf-8")
                (args.output / f"{name}.html").write_text(html, encoding="utf-8")
                await renderer.render_to_image(html, args.output / f"{name}.png")
                collect()
                results[name] = {
                    "wall_ms": round((time.perf_counter() - started) * 1000, 2),
                    "preprocess_ms": round(preprocess_ms, 2),
                    "cpu_s_process_tree": round(sum(totals.values()) - before, 3),
                    "peak_rss_mib_process_tree": round(peak_rss / 2**20, 1),
                    "state": state,
                }
            except Exception as exc:
                results[name] = {
                    "error": str(exc),
                    "wall_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            finally:
                renderer._take_screenshot = original
                running = False
                await sampler
            print(name, json.dumps(results[name]), flush=True)
            (args.output / "heavy.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
