"""Compare installed native TeX distributions on fixed, trusted showcase inputs."""

import argparse
import asyncio
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path


async def main():
    """Measure native compilation, SVG conversion, memory, and preview output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tinytex-bin", type=Path)
    parser.add_argument("--miktex-bin", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output-format", choices=["dvi", "pdf"], default="dvi")
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=["ml_surface", "torus"],
        default=["ml_surface", "torus"],
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=["latex-coordinates", "lua-expression", "lua-coordinates"],
    )
    parser.add_argument(
        "--pdf-rasterizer",
        type=Path,
        help="Optional pdftoppm executable for direct PDF-to-PNG measurement",
    )
    args = parser.parse_args()
    if args.pdf_rasterizer and args.output_format != "pdf":
        parser.error("--pdf-rasterizer requires --output-format pdf")
    if not args.tinytex_bin and not args.miktex_bin:
        parser.error("Specify at least one installed TeX binary directory")
    import psutil

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    from astrbot_plugin_mathjax2image.infrastructure.browser import (
        BrowserManager,
        PageRenderer,
    )
    from astrbot_plugin_mathjax2image.infrastructure.converter.pgfplots_preprocessor import (
        PgfplotsPreprocessor,
    )

    logging.disable(logging.CRITICAL)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    distributions = [
        (name, path.resolve())
        for name, path in [("tinytex", args.tinytex_bin), ("miktex", args.miktex_bin)]
        if path
    ]
    variants = [
        ("latex-coordinates", "latex", True),
        ("lua-expression", "lualatex", False),
        ("lua-coordinates", "lualatex", True),
    ]
    manager = BrowserManager(max_pages=1, idle_timeout=0)
    renderer = PageRenderer(manager, root, image_cache_max_mb=0)
    results = []
    try:
        for distribution, binary_dir in distributions:
            env = os.environ.copy()
            env.update(
                {
                    "PATH": str(binary_dir) + os.pathsep + env.get("PATH", ""),
                    "LANG": "C",
                    "LC_ALL": "C",
                    "LC_CTYPE": "C",
                    "openin_any": "p",
                    "openout_any": "p",
                }
            )
            suffix = ".exe" if os.name == "nt" else ""
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            for variant, engine, precompute in variants:
                if args.variants and variant not in args.variants:
                    continue
                command_name = (
                    "pdflatex"
                    if engine == "latex" and args.output_format == "pdf"
                    else engine
                )
                executable = binary_dir / (command_name + suffix)
                svg_executable = binary_dir / ("dvisvgm" + suffix)
                if not executable.exists() or (
                    not args.pdf_rasterizer and not svg_executable.exists()
                ):
                    raise FileNotFoundError(
                        f"Missing native executables in {binary_dir}"
                    )
                version_bytes = await asyncio.to_thread(
                    subprocess.check_output,
                    [str(executable), "--version"],
                    env=env,
                    creationflags=flags,
                )
                version = version_bytes.decode(errors="replace").splitlines()[0]
                for case in args.cases:
                    work = args.output / distribution / variant / case
                    work.mkdir(parents=True, exist_ok=True)
                    code = (root / "examples" / f"{case}.md").read_text(
                        encoding="utf-8"
                    )
                    started = time.perf_counter()
                    if precompute:
                        code = PgfplotsPreprocessor().convert(code)
                        if "coordinates {" not in code:
                            raise RuntimeError("Expected precomputed coordinates")
                    preprocess_ms = (time.perf_counter() - started) * 1000
                    # Force comparable classic fonts and reject hidden Lua fallbacks.
                    preamble = r"""\documentclass[tikz,border=0pt]{standalone}
\usepackage[OT1]{fontenc}
\renewcommand{\rmdefault}{cmr}
\renewcommand{\sfdefault}{cmss}
\renewcommand{\ttdefault}{cmtt}
\usepackage{amsmath,amssymb,pgfplots}
\pgfplotsset{compat=1.16,LUA}
\begin{document}
""".replace(
                        "LUA",
                        "lua backend=true,lua debug=compileerror"
                        if engine == "lualatex"
                        else "lua backend=false",
                    )
                    (work / "plot.tex").write_text(
                        preamble + code + "\n\\end{document}\n", encoding="utf-8"
                    )
                    compile_command = [
                        str(executable),
                        "--halt-on-error",
                        "--interaction=nonstopmode",
                    ]
                    compile_command += (
                        ["--disable-write18", "--disable-installer"]
                        if distribution == "miktex"
                        else ["--no-shell-escape"]
                    )
                    if engine == "lualatex":
                        compile_command.append("--output-format=" + args.output_format)
                    compile_command.append("plot.tex")
                    conversion_command = [
                        str(svg_executable),
                        "--no-fonts",
                        "--exact-bbox",
                        "plot." + args.output_format,
                        "-o",
                        "plot.svg",
                    ]
                    if args.output_format == "pdf":
                        conversion_command.insert(1, "--pdf")
                    if args.pdf_rasterizer:
                        conversion_command = [
                            str(args.pdf_rasterizer.resolve()),
                            "-png",
                            "-singlefile",
                            "-scale-to-x",
                            "1102",
                            "-scale-to-y",
                            "-1",
                            "plot.pdf",
                            "plot",
                        ]
                    runs = []
                    cold = None
                    failure = None
                    for repetition in range(max(1, args.runs) + 1):
                        # The first run warms format/font/file-system caches.
                        record = {"preprocess_ms": round(preprocess_ms, 3)}
                        for stage, command in [
                            ("tex", compile_command),
                            ("conversion", conversion_command),
                        ]:
                            cpu = {}
                            peak = 0
                            started = time.perf_counter()
                            with (work / f"{stage}-{repetition}.txt").open("wb") as log:
                                process = await asyncio.to_thread(
                                    subprocess.Popen,
                                    command,
                                    cwd=work,
                                    env=env,
                                    stdout=log,
                                    stderr=subprocess.STDOUT,
                                    creationflags=flags,
                                )
                                tracked = psutil.Process(process.pid)
                                while process.poll() is None:
                                    try:
                                        rss = 0
                                        for child in [
                                            tracked,
                                            *tracked.children(recursive=True),
                                        ]:
                                            try:
                                                times = child.cpu_times()
                                                cpu[
                                                    (child.pid, child.create_time())
                                                ] = times.user + times.system
                                                rss += child.memory_info().rss
                                            except (
                                                psutil.NoSuchProcess,
                                                psutil.AccessDenied,
                                            ):
                                                pass
                                        peak = max(peak, rss)
                                    except psutil.NoSuchProcess:
                                        pass
                                    if time.perf_counter() - started > 120:
                                        for child in tracked.children(recursive=True):
                                            child.kill()
                                        process.kill()
                                        process.wait()
                                        raise TimeoutError(
                                            f"{distribution}/{variant}/{case}/{stage}"
                                        )
                                    await asyncio.sleep(0.01)
                                record[stage + "_ms"] = round(
                                    (time.perf_counter() - started) * 1000, 2
                                )
                                record[stage + "_cpu_s"] = round(sum(cpu.values()), 4)
                                record[stage + "_peak_rss_mib"] = round(peak / 2**20, 2)
                            if process.returncode:
                                failure = f"{stage} exited with {process.returncode}: {work / f'{stage}-{repetition}.txt'}"
                                break
                        if failure:
                            break
                        if repetition:
                            runs.append(record)
                        else:
                            cold = record
                    result = {
                        "distribution": distribution,
                        "variant": variant,
                        "case": case,
                        "version": version,
                        "format": args.output_format,
                        "conversion": "png" if args.pdf_rasterizer else "svg",
                        "runs": runs,
                        "cold_run": cold,
                    }
                    if failure:
                        result["error"] = failure
                    else:
                        log = (work / "plot.log").read_text(errors="replace")
                        if (
                            engine == "lualatex"
                            and "ActivatingLUAbackend" not in re.sub(r"\s+", "", log)
                        ):
                            raise RuntimeError(
                                f"Lua activation was not confirmed: {work}"
                            )
                        result["lua_active"] = engine == "lualatex"
                        result["median_native_ms"] = round(
                            statistics.median(
                                r["tex_ms"] + r["conversion_ms"] + r["preprocess_ms"]
                                for r in runs
                            ),
                            2,
                        )
                        result["peak_native_rss_mib"] = max(
                            max(r["tex_peak_rss_mib"], r["conversion_peak_rss_mib"])
                            for r in runs
                        )
                        if args.pdf_rasterizer:
                            if not (work / "plot.png").exists():
                                raise RuntimeError("PDF rasterizer produced no image")
                        else:
                            svg = (work / "plot.svg").read_text(encoding="utf-8")
                            svg = re.sub(
                                r"<\?xml[\s\S]*?\?>|<!--[\s\S]*?-->", "", svg
                            ).strip()
                            html = (
                                "<html><head><style>body{margin:24px;background:white}svg{display:block;width:100%;height:auto}</style></head><body>"
                                + svg
                                + "<script>window.mathJaxRequired=false;</script></body></html>"
                            )
                            await renderer.render_to_image(html, work / "plot.png")
                    results.append(result)
                    (args.output / "native.json").write_text(
                        json.dumps(results, indent=2), encoding="utf-8"
                    )
                    print(
                        json.dumps({k: v for k, v in result.items() if k != "runs"}),
                        flush=True,
                    )
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
