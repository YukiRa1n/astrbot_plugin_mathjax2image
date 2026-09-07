"""Generate the README showcase using the actual plugin rendering pipeline."""

import argparse
import asyncio
import json
import logging
import sys
from collections import deque
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--names",
        nargs="+",
        default=[
            "math",
            "chemistry",
            "physics",
            "ml_surface",
            "torus",
            "neural_network",
        ],
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    from astrbot_plugin_mathjax2image.infrastructure.browser import (
        BrowserManager,
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

    logging.disable(logging.CRITICAL)
    manager = BrowserManager(max_pages=1)
    renderer = PageRenderer(
        manager,
        root,
        tikz_timeout=120000,
        mathjax_timeout=30000,
        fail_on_mathjax_timeout=True,
    )
    preprocess = LatexPreprocessor(
        TikzConverter(TikzPlotConverter()),
        ListConverter(),
        TableConverter(),
        MermaidConverter(),
    )
    converter = MarkdownConverter(root / "templates/template.html")
    messages = deque(maxlen=30)
    renderer._setup_logging = lambda page: page.on(
        "console", lambda message: messages.append(message.text[:1200])
    )
    original = renderer._take_screenshot

    async def verify(page, output):
        errors = await page.locator(
            "mjx-merror,[data-mjx-error],.error"
        ).all_text_contents()
        if errors:
            raise RuntimeError(str(errors))
        await original(page, output)

    renderer._take_screenshot = verify
    try:
        for name in args.names:
            source = (root / "examples" / f"{name}.md").read_text(encoding="utf-8")
            html = converter.convert_to_html(
                await asyncio.to_thread(preprocess.preprocess, source), "#FFFFFF"
            )
            output = root / "examples" / f"{name}.png"
            try:
                await renderer.render_to_image(html, output)
            except Exception:
                print("\n".join(messages), flush=True)
                raise
            with Image.open(output) as image:
                print(
                    json.dumps(
                        {
                            "name": name,
                            "size": image.size,
                            "bytes": output.stat().st_size,
                        }
                    ),
                    flush=True,
                )
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
