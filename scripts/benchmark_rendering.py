"""Benchmark the complete renderer with cold and warm page reuse.

Run with --plugin-dir to compare a separate checkout using identical inputs.
"""

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path


async def main():
    """Render representative content and print elapsed times and image sizes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin-dir", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--unique",
        action="store_true",
        help="Bypass image reuse while retaining the asset cache",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--cases", nargs="+", default=["plain", "math", "packages", "mermaid", "tikz"]
    )
    args = parser.parse_args()
    sys.path.insert(0, str(args.plugin_dir.resolve().parent))
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
    from PIL import Image

    logging.basicConfig(level=logging.ERROR)
    try:
        from astrbot.api import logger

        logger.setLevel(logging.ERROR)
    except (ImportError, AttributeError):
        pass
    args.output.mkdir(parents=True, exist_ok=True)
    manager = BrowserManager(max_pages=2)
    renderer = PageRenderer(
        manager,
        args.plugin_dir.resolve(),
        mathjax_timeout=30000,
        fail_on_mathjax_timeout=True,
    )
    preprocessor = LatexPreprocessor(
        TikzConverter(TikzPlotConverter()),
        ListConverter(),
        TableConverter(),
        MermaidConverter(),
    )
    converter = MarkdownConverter(args.plugin_dir / "templates/template.html")
    cases = {
        "plain": "# 渲染测试\n\n中文与 English 混排。\n\n| 内容 | 数值 |\n| --- | --- |\n| 示例 | 42 |",
        "math": r"# 数学公式"
        + "\n\n"
        + r"$$\int_{-\infty}^{\infty}e^{-x^2}\,dx=\sqrt{\pi}$$",
        "packages": r"""\usepackage{physics,mhchem,cancel,upgreek,centernot}
# 扩展包测试
$$\dv[2]{x}{t}+\pdv{f}{x}+\qty(\frac{a}{b})$$
$$\ce{2H2 + O2 -> 2H2O} \qquad \cancel{x}+\upalpha \centernot\implies y$$
$$\braket{\psi}{\phi}$$""",
        "braket": r"\usepackage{braket} $$\Braket{\psi | \phi}$$",
        "extensions": r"""\usepackage{ams,amscd,bbox,boldsymbol,cancel,cases,centernot,color,empheq,enclose,extpfeil,gensymb,mathtools,mhchem,newcommand,textcomp,textmacros,unicode,upgreek,verb}
\begin{align}a&=b\\c&=d\end{align}
$$a\xmapsto{f}b \qquad \bbox[5px,border:1px solid gray]{\boldsymbol{x}}$$""",
        "mermaid": "```mermaid\nflowchart LR\n A[输入] --> B[渲染] --> C[图片]\n```",
        "tikz": r"""\begin{tikzpicture}
\node (A) at (0,0) {A};
\node (B) at (2,0) {B};
\draw[->] (A) -- (B);
\end{tikzpicture}""",
    }
    results = {}
    try:
        for name in args.cases:
            samples = []
            state = {}
            original_screenshot = renderer._take_screenshot

            async def capture(page, output):
                nonlocal state
                await original_screenshot(page, output)
                state = await page.evaluate("""() => ({
                    math: document.querySelectorAll('mjx-container').length,
                    errors: Array.from(document.querySelectorAll('mjx-merror')).map(n => n.textContent),
                    undefined: Array.from(document.querySelectorAll('[data-mjx-error]')).map(n => n.textContent),
                    mermaid: document.querySelectorAll('pre.mermaid svg').length,
                    tikz: document.querySelectorAll('.tikz-diagram svg').length
                })""")

            renderer._take_screenshot = capture
            try:
                for index in range(max(1, args.runs) + 1):
                    output = args.output / f"{name}-{index}.png"
                    started = time.perf_counter()
                    html = converter.convert_to_html(
                        preprocessor.preprocess(cases[name])
                    )
                    if args.unique:
                        html += f"<!-- sample {index} -->"
                    await renderer.render_to_image(html, output)
                    samples.append(round((time.perf_counter() - started) * 1000, 2))
                    print(f"{name} {index}: {samples[-1]} ms", flush=True)
                with Image.open(output) as image:
                    dimensions = image.size
                results[name] = {
                    "cold_ms": samples[0],
                    "warm_ms": samples[1:],
                    "median_warm_ms": statistics.median(samples[1:]),
                    "dimensions": dimensions,
                    "bytes": output.stat().st_size,
                    "state": state,
                }
            except Exception as exc:
                results[name] = {"error": str(exc), "samples_ms": samples}
                print(f"{name}: {exc}", flush=True)
            finally:
                renderer._take_screenshot = original_screenshot
    finally:
        await manager.close()
    result = json.dumps(results, ensure_ascii=False, indent=2)
    (args.output / "benchmark.json").write_text(result, encoding="utf-8")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
