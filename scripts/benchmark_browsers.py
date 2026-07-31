"""Benchmark Playwright browser engines with representative plugin workloads."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import tempfile
import time
import types
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil
from PIL import Image, ImageStat
from playwright.async_api import BrowserType, Page, async_playwright


PLUGIN_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PLUGIN_DIR.parent
VOCAB_DIR = WORKSPACE_DIR / "astrbot_plugin_vocabcard"

if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))


class _NullLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


if "astrbot.api" not in sys.modules:
    astrbot_module = types.ModuleType("astrbot")
    astrbot_api_module = types.ModuleType("astrbot.api")
    astrbot_api_module.logger = _NullLogger()
    astrbot_module.api = astrbot_api_module
    sys.modules["astrbot"] = astrbot_module
    sys.modules["astrbot.api"] = astrbot_api_module


@dataclass
class CaseResult:
    name: str
    samples_ms: list[float]
    warmup_ms: float
    median_ms: float
    p95_ms: float
    output_width: int
    output_height: int
    output_bytes: int
    pixel_stddev: float
    feature_state: dict[str, int | bool]
    console_errors: list[str]
    error: str | None = None


@dataclass
class EngineResult:
    engine: str
    launch_ms: float
    peak_child_rss_mb: float
    cases: list[CaseResult]
    error: str | None = None


class ProcessTreeSampler:
    def __init__(self) -> None:
        self._root = psutil.Process()
        self._baseline = {p.pid for p in self._root.children(recursive=True)}
        self._peak_bytes = 0
        self._running = True

    async def run(self) -> None:
        while self._running:
            total = 0
            for process in self._root.children(recursive=True):
                if process.pid in self._baseline:
                    continue
                try:
                    total += process.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self._peak_bytes = max(self._peak_bytes, total)
            await asyncio.sleep(0.05)

    def stop(self) -> float:
        self._running = False
        return self._peak_bytes / (1024 * 1024)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def build_vocab_html() -> str:
    from astrbot_plugin_vocabcard.core.base_handler import WordEntry
    from astrbot_plugin_vocabcard.core.language_config import LanguageConfig
    from astrbot_plugin_vocabcard.languages.english.handler import (
        EnglishLanguageHandler,
    )

    lang_dir = VOCAB_DIR / "languages" / "english"
    config = LanguageConfig.from_json(lang_dir / "config.json")
    handler = EnglishLanguageHandler(config, lang_dir)
    word = WordEntry(
        word="architecture",
        phonetic="/ˈɑːrkɪtektʃər/",
        pos="n.",
        definition="架构；体系结构；建筑设计",
        example="A clear architecture keeps rendering predictable and efficient.",
    )
    background = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "width='1080' height='1350'%3E%3Crect fill='%231b263b' "
        "width='100%25' height='100%25'/%3E%3C/svg%3E"
    )
    return handler.render_card(
        word,
        bg_url=background,
        theme_color="#2F4F4F",
        bg_position="50% 50%",
    )


def build_math_html(content: str) -> str:
    from astrbot_plugin_mathjax2image.infrastructure.converter import (
        LatexPreprocessor,
        ListConverter,
        MarkdownConverter,
        MermaidConverter,
        TableConverter,
        TikzConverter,
        TikzPlotConverter,
    )

    preprocessor = LatexPreprocessor(
        tikz_converter=TikzConverter(TikzPlotConverter()),
        list_converter=ListConverter(),
        table_converter=TableConverter(),
        mermaid_converter=MermaidConverter(),
    )
    processed = preprocessor.preprocess(content)
    return MarkdownConverter(
        PLUGIN_DIR / "templates" / "template.html"
    ).convert_to_html(processed, "#FDFBF0")


async def wait_for_features(page: Page, name: str) -> dict[str, int | bool]:
    if name == "mathjax":
        await page.wait_for_function(
            "() => window.mathJaxReady === true", timeout=30000
        )
    elif name == "mermaid":
        await page.wait_for_function(
            "() => document.querySelectorAll('pre.mermaid svg').length > 0",
            timeout=5000,
        )
    elif name == "tikz":
        await page.wait_for_function(
            "() => document.querySelectorAll('.tikz-diagram svg').length > 0",
            timeout=90000,
        )
    return await page.evaluate(
        """() => ({
            mathJaxReady: window.mathJaxReady === true,
            mathNodes: document.querySelectorAll('mjx-container').length,
            tikzSvg: document.querySelectorAll('.tikz-diagram svg').length,
            mermaidSvg: document.querySelectorAll('pre.mermaid svg').length,
        })"""
    )


def inspect_image(path: Path) -> tuple[int, int, int, float]:
    with Image.open(path) as image:
        image.load()
        rgb = image.convert("RGB")
        stddev = statistics.mean(ImageStat.Stat(rgb).stddev)
        width, height = image.size
    size = path.stat().st_size
    if width < 100 or height < 100 or size < 1000 or stddev < 2:
        raise RuntimeError(
            f"blank or invalid image: {width}x{height}, {size} bytes, stddev={stddev:.2f}"
        )
    return width, height, size, stddev


async def render_case(
    page: Page,
    name: str,
    html_path: Path,
    output: Path,
) -> tuple[float, dict[str, int | bool]]:
    started = time.perf_counter()
    if name == "vocab":
        await page.set_viewport_size({"width": 432, "height": 540})
    else:
        await page.set_viewport_size({"width": 1150, "height": 2000})
    await page.goto(html_path.as_uri(), wait_until="domcontentloaded", timeout=60000)
    if name in {"mathjax", "mermaid", "tikz"}:
        state = await wait_for_features(page, name)
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 1150, "height": height})
        await page.screenshot(path=str(output), full_page=True)
    else:
        await page.wait_for_load_state("networkidle", timeout=10000)
        state = {"loaded": True}
        await page.screenshot(path=str(output), scale="device")
    return (time.perf_counter() - started) * 1000, state


async def benchmark_engine(
    browser_type: BrowserType,
    engine: str,
    cases: dict[str, str],
    runs: int,
    vocab_scale: int,
) -> EngineResult:
    sampler = ProcessTreeSampler()
    sample_task = asyncio.create_task(sampler.run())
    browser = None
    try:
        launch_started = time.perf_counter()
        browser = await browser_type.launch(headless=True)
        launch_ms = (time.perf_counter() - launch_started) * 1000

        results = []
        with tempfile.TemporaryDirectory(prefix=f"browser-bench-{engine}-") as tmp:
            tmp_dir = Path(tmp)
            for name, html in cases.items():
                viewport = (
                    {"width": 432, "height": 540}
                    if name == "vocab"
                    else {"width": 1150, "height": 2000}
                )
                context = await browser.new_context(
                    viewport=viewport,
                    device_scale_factor=vocab_scale if name == "vocab" else 1,
                )
                page = await context.new_page()
                html_path = tmp_dir / f"{name}.html"
                html_path.write_text(html, encoding="utf-8")
                warmup_path = tmp_dir / f"{name}-warmup.png"
                console_errors = []
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.on("pageerror", lambda error: console_errors.append(str(error)))
                try:
                    warmup_ms, last_state = await render_case(
                        page, name, html_path, warmup_path
                    )
                    samples = []
                    last_output = warmup_path
                    case_runs = runs if name in {"vocab", "mathjax"} else 1
                    for index in range(case_runs):
                        output = tmp_dir / f"{name}-{index}.png"
                        elapsed, last_state = await render_case(
                            page, name, html_path, output
                        )
                        samples.append(elapsed)
                        last_output = output

                    width, height, size, stddev = inspect_image(last_output)
                    results.append(
                        CaseResult(
                            name=name,
                            samples_ms=[round(value, 2) for value in samples],
                            warmup_ms=round(warmup_ms, 2),
                            median_ms=round(statistics.median(samples), 2),
                            p95_ms=round(_percentile(samples, 0.95), 2),
                            output_width=width,
                            output_height=height,
                            output_bytes=size,
                            pixel_stddev=round(stddev, 2),
                            feature_state=last_state,
                            console_errors=console_errors[-10:],
                        )
                    )
                except Exception as exc:
                    results.append(
                        CaseResult(
                            name=name,
                            samples_ms=[],
                            warmup_ms=0,
                            median_ms=0,
                            p95_ms=0,
                            output_width=0,
                            output_height=0,
                            output_bytes=0,
                            pixel_stddev=0,
                            feature_state={},
                            console_errors=console_errors[-10:],
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                finally:
                    await context.close()

        await browser.close()
        browser = None
        await asyncio.sleep(0.2)
        peak_mb = sampler.stop()
        await sample_task
        return EngineResult(
            engine=engine,
            launch_ms=round(launch_ms, 2),
            peak_child_rss_mb=round(peak_mb, 2),
            cases=results,
        )
    except Exception as exc:
        if browser is not None:
            await browser.close()
        peak_mb = sampler.stop()
        await sample_task
        return EngineResult(
            engine=engine,
            launch_ms=0,
            peak_child_rss_mb=round(peak_mb, 2),
            cases=[],
            error=f"{type(exc).__name__}: {exc}",
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--engines", nargs="+", default=["chromium", "firefox", "webkit"]
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--vocab-scale", type=int, default=3)
    args = parser.parse_args()

    cases = {
        "vocab": build_vocab_html(),
        "mathjax": build_math_html(
            r"""
# Browser rendering benchmark

For a Gaussian integral:

$$\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}$$

| Engine | Role |
| --- | --- |
| Browser | Layout and pixels |
| Plugin | Domain-specific readiness |
"""
        ),
        "mermaid": build_math_html(
            """```mermaid
flowchart LR
    A[Markdown] --> B[MathJax]
    B --> C[Screenshot]
```"""
        ),
        "tikz": build_math_html(
            r"""\begin{tikzpicture}
  \node (A) at (0,0) {A};
  \node (B) at (2,0) {B};
  \draw[->] (A) -- (B);
\end{tikzpicture}"""
        ),
    }
    results = []
    async with async_playwright() as playwright:
        for engine in args.engines:
            browser_type = getattr(playwright, engine)
            results.append(
                await benchmark_engine(
                    browser_type,
                    engine,
                    cases,
                    args.runs,
                    max(1, min(4, args.vocab_scale)),
                )
            )

    payload = {
        "runs": args.runs,
        "vocab_scale": args.vocab_scale,
        "engines": [asdict(result) for result in results],
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
