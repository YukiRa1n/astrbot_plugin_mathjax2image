"""Check resident lifecycle, SVG output equivalence and versioned WASM patches."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from astrbot_plugin_mathjax2image.infrastructure.browser import (
    BrowserManager,
    PageRenderer,
    tikz_worker,
)
from astrbot_plugin_mathjax2image.infrastructure.converter import (
    MarkdownConverter,
    TikzConverter,
    TikzPlotConverter,
)

ROOT = Path(__file__).resolve().parents[1]
SHELL = """<!doctype html><html><body><main class="render-content">CONTENT</main>
<script>
window.boot = Math.random(); window.replacements = 0; window.mathJaxRequired = false;
window.__clearRenderContent = () => document.querySelector('main').replaceChildren();
window.__replaceRenderContent = content => {
    window.replacements++; document.querySelector('main').innerHTML = content;
};
</script></body></html>"""


@pytest.mark.asyncio
async def test_resident_content_is_cleared_and_idle_engine_is_disposed(tmp_path):
    manager = BrowserManager(max_pages=1, idle_timeout=0)
    renderer = PageRenderer(manager, ROOT, image_cache_max_mb=0)
    states = []

    async def capture(page, output):
        states.append(
            await page.evaluate(
                "({boot, replacements, text: document.querySelector('main').textContent})"
            )
        )
        output.write_bytes(b"verified")

    renderer._take_screenshot = capture
    try:
        await renderer.render_to_image(
            SHELL.replace("CONTENT", "first"), tmp_path / "one.png"
        )
        page = next(iter(manager._resident_pages))
        assert await page.locator("main").inner_text() == ""
        await renderer.render_to_image(
            SHELL.replace("CONTENT", "second"), tmp_path / "two.png"
        )
        assert states[0]["boot"] == states[1]["boot"]
        assert states[1]["text"] == "second"
        assert states[1]["replacements"] == 1
        manager._idle_timeout = 0.02
        await renderer.render_to_image(
            SHELL.replace("CONTENT", "third"), tmp_path / "three.png"
        )
        await asyncio.sleep(0.08)
        assert page.is_closed()
        assert not manager._resident_pages
        assert manager._active_pages_count == 0
        assert manager.request_context is not None
        await renderer.render_to_image(
            SHELL.replace("CONTENT", "fourth"), tmp_path / "four.png"
        )
        assert states[-1]["boot"] != states[0]["boot"]
        assert states[-1]["replacements"] == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_stateful_tex_and_changed_shell_reload(tmp_path):
    manager = BrowserManager(max_pages=1, idle_timeout=0)
    renderer = PageRenderer(manager, ROOT, image_cache_max_mb=0)
    boots = []

    async def capture(page, output):
        boots.append(await page.evaluate("window.boot"))
        output.write_bytes(b"verified")

    renderer._take_screenshot = capture
    try:
        for i, (content, shell) in enumerate(
            [
                ("first", SHELL),
                (r"\newcommand{\foo}{bar}", SHELL),
                ("third", SHELL),
                ("fourth", SHELL.replace("<body>", '<body style="color:blue">')),
            ]
        ):
            await renderer.render_to_image(
                shell.replace("CONTENT", content), tmp_path / f"{i}.png"
            )
        assert len(set(boots)) == 4
    finally:
        await manager.close()


def test_tikz_cache_control_remains_trusted_html():
    converter = MarkdownConverter(ROOT / "templates/template.html")
    block = TikzConverter(TikzPlotConverter()).convert(
        r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"
    )
    html = converter.convert_to_html(block)
    assert '<script type="text/tikz" data-disable-cache="true"' in html
    assert "&lt;script" not in html


def test_worker_patch_is_exactly_versioned(monkeypatch):
    unknown = b"unknown worker"
    assert tikz_worker.optimize_tikz_worker(unknown) is unknown
    source = b";".join(tikz_worker.WORKER_REPLACEMENTS)
    monkeypatch.setattr(
        tikz_worker, "WORKER_SHA256", hashlib.sha256(source).hexdigest()
    )
    patched = tikz_worker.optimize_tikz_worker(source)
    for replacement in tikz_worker.WORKER_REPLACEMENTS.values():
        assert replacement in patched
    assert b"WebAssembly.compile" in patched


@pytest.mark.asyncio
async def test_wasm_compiled_code_is_shared_but_instances_are_fresh():
    manager = BrowserManager(max_pages=1)
    try:
        page, _ = await manager.acquire_page(300, 200)
        await page.evaluate((ROOT / "static/tikz_worker_bootstrap.js").read_text())
        result = await page.evaluate("""async () => {
            const bytes = new Uint8Array([0,97,115,109,1,0,0,0]);
            const first = await WebAssembly.instantiate(bytes);
            const second = await WebAssembly.instantiate(bytes);
            const direct = await WebAssembly.instantiate(first.module);
            return {compiles: __mathjaxWasmCompiles, sameModule: first.module === second.module,
                differentInstances: first.instance !== second.instance,
                moduleInputWorks: direct instanceof WebAssembly.Instance};
        }""")
        assert result == {
            "compiles": 1,
            "sameModule": True,
            "differentInstances": True,
            "moduleInputWorks": True,
        }
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_sparse_snapshot_restores_all_bytes_without_retaining_source():
    manager = BrowserManager(max_pages=1)
    try:
        page, _ = await manager.acquire_page(300, 200)
        await page.evaluate((ROOT / "static/tikz_worker_bootstrap.js").read_text())
        result = await page.evaluate("""() => {
            const source = new Uint8Array(new ArrayBuffer(65536 * 6 + 4), 1, 65536 * 6 + 3);
            for (const i of [0,65535,65536,65537,65536*4,source.length-1]) source[i] = 123;
            const expected = source.slice();
            const packed = __packTexSnapshot(source);
            source.fill(0);
            const restored = new ArrayBuffer(expected.length + 10);
            __restoreTexSnapshot(restored, packed);
            const bytes = new Uint8Array(restored);
            return {equal: expected.every((value,index)=>bytes[index]===value),
                tailZero: bytes.slice(expected.length).every(value=>value===0),
                smaller: __texSnapshotStats.retainedBytes < expected.length};
        }""")
        assert result == {"equal": True, "tailZero": True, "smaller": True}
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_worker_package_cache_is_bounded_and_does_not_share_mutations():
    manager = BrowserManager(max_pages=1)
    try:
        page, _ = await manager.acquire_page(300, 200)
        await page.evaluate((ROOT / "static/tikz_worker_bootstrap.js").read_text())
        result = await page.evaluate("""async () => {
            globalThis.__texProfile = {packageMs:0,packageHits:0,packageMisses:0};
            let loads = 0;
            const loader = __profiledTexLoader(async name => {
                loads++; const data = new Uint8Array(40000); data[0] = 7; return data;
            });
            const first = await loader('tex_files/first.gz'); first[0] = 99;
            const second = await loader('tex_files/first.gz');
            const isolated = second[0] === 7 && loads === 1;
            for (let i=0;i<140;i++) await loader('tex_files/' + i + '.gz');
            const before = loads; await loader('tex_files/first.gz');
            return {isolated, evicted: loads === before + 1,
                bounded: __texProfile.packageCacheBytes <= 4*1024*1024};
        }""")
        assert result == {"isolated": True, "evicted": True, "bounded": True}
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", '<?xml version="1.0"?>\n<!-- generated -->\n'])
async def test_streamed_svg_matches_xml_path_with_clips_and_opacity(prefix):
    manager = BrowserManager(max_pages=1)
    source = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
    <g transform="translate(10 10)"><g fill="red"><g fill="blue">
    <clipPath id="clip"><rect width="25" height="30"/></clipPath>
    <g clip-path="url(#clip)" opacity=".5"><rect width="50" height="40"/></g>
    </g><rect x="30" width="10" height="10"/></g></g></svg>"""
    try:
        screenshots = []
        for fast in [False, True]:
            page, _ = await manager.acquire_page(100, 100)
            await page.set_content('<body style="margin:0;background:white"></body>')
            await page.evaluate(
                PageRenderer(manager, ROOT, compact_svg=fast)._get_inject_script()
            )
            await page.evaluate(
                "source => document.body.append(document.createRange().createContextualFragment(source))",
                source,
            )
            screenshots.append(await page.screenshot())
            await manager.release_page(page, exception_occurred=True)
        assert screenshots[0] == screenshots[1]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_worker_only_caches_missing_files_not_transient_failures():
    manager = BrowserManager(max_pages=1)
    try:
        page, _ = await manager.acquire_page(300, 200)
        await page.evaluate((ROOT / "static/tikz_worker_bootstrap.js").read_text())
        result = await page.evaluate("""async () => {
            globalThis.__texProfile = {packageMs:0,packageHits:0,packageMisses:0,negativeHits:0};
            let loads = 0;
            const loader = __profiledTexLoader(async name => {
                loads++; throw Object.assign(new Error('unavailable'), {status:name.includes('missing')?404:503});
            });
            for (const name of ['missing','missing','temporary','temporary']) {
                try { await loader('tex_files/' + name + '.gz'); } catch (_) {}
            }
            return {loads, negativeHits: __texProfile.negativeHits};
        }""")
        assert result == {"loads": 3, "negativeHits": 1}
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_streaming_snapshot_rejects_truncation_and_oversize():
    manager = BrowserManager(max_pages=1)
    try:
        page, _ = await manager.acquire_page(300, 200)
        await page.evaluate((ROOT / "static/tikz_worker_bootstrap.js").read_text())
        result = await page.evaluate("""async () => {
            globalThis.fetch = async () => new Response(new Uint8Array([1,2,3]));
            class Inflate {
                constructor() { this.ended = false; this.err = 0; }
                push() { this.onData(new Uint8Array([4,0,0])); this.ended = true; }
            }
            const valid = await __loadTexSnapshot('fixture', Inflate, 3);
            const data = new ArrayBuffer(3); __restoreTexSnapshot(data, valid);
            const errors = [];
            for (const expected of [2,4]) {
                try { await __loadTexSnapshot('fixture', Inflate, expected); }
                catch (error) { errors.push(error.message); }
            }
            return {bytes: Array.from(new Uint8Array(data)), errors};
        }""")
        assert result == {
            "bytes": [4, 0, 0],
            "errors": ["Oversized TeX snapshot", "Incomplete TeX snapshot"],
        }
    finally:
        await manager.close()
