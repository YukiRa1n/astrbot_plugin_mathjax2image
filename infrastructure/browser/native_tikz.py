"""Optional TeX Live/TinyTeX compiler; no distribution is bundled or installed."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from ...domain.errors import RenderError
from ..converter.tikz_converter import TikzConverter

_TIKZ_SCRIPT = re.compile(
    r'<script\b(?=[^>]*\btype="text/tikz")([^>]*)>(.*?)</script>', re.S
)
# A name blocklist cannot hold: the original one omitted `\file_input:n` (expl3)
# and `\@@input`, and `openin_any=p` does not stop absolute paths on every TeX
# engine. Two structural rules replace guesswork about individual names:
#   * any expl3-style name (contains `_` or `:`) is refused wholesale, which
#     covers the whole \file_*/\ior_*/\tl_* file and IO namespace at once;
#   * any name starting with `@` is refused, which covers TeX internals such as
#     \@@input that the old blocklist missed.
# A short explicit list still covers plain-TeX file and process primitives.
_DENIED_TEX_COMMANDS = frozenset(
    """
    input include includeonly includegraphics openin openout closein closeout
    read readline write message errmessage immediate special catcode csname
    endcsname directlua latelua scantokens everyjob everyeof everypar
    newread newwrite loop repeat usepackage requirepackage documentclass
    afterassignment aftergroup expandafter noexpand futurelet
    pdfobj pdffiledump pdfximage pdfcatalog pdfannot pdfextension pdfunescapehex
    primitive ifx ifnum while
    """.split()
)
# Control *symbols* (backslash + one non-letter) that drawing code may use.
_ALLOWED_TEX_SYMBOLS = frozenset("\\{}%$&_#@,;:! -'\"|./^~=*+<>()[?")
_BEGIN_ENVIRONMENT = re.compile(r"\\begin\{([^}]*)\}")
_ALLOWED_ENVIRONMENTS = frozenset(
    """
    tikzpicture tikzcd scope axis document semiverbatim picture pgfpicture
    """.split()
)
# The converter only ever writes these three data-* attributes onto a block.
_ALLOWED_BLOCK_ATTRIBUTES = frozenset(
    {"data-tex-packages", "data-tikz-libraries", "data-disable-cache"}
)
# How much of a failed stage log may travel back to the requester. The tail of
# a TeX log can contain file contents pulled in via \input-family primitives,
# so only a short machine-level summary is exposed, never the raw tail.
_FAILURE_TAIL_BYTES = 2000
_FAILURE_DETAIL_CHARS = 200


class NativeTikzRenderer:
    """Compile existing validated TikZ blocks with an external TeX Live installation."""

    def __init__(self, binary_dir: str = ""):
        self.binary_dir = (
            Path(binary_dir).expanduser().resolve() if binary_dir else None
        )

    @staticmethod
    def _screen_block_attributes(source: str) -> bool:
        """Allow only the attributes the converter itself emits.

        The previous regex merely looked for known-bad names, so a crafted block
        (``data-tikz-libraries='x} \\file_input:n{...}'``) parsed as trusted.
        """
        pattern = r"([^\s=/>]+)\s*=\s*(\"([^\"]*)\"|'([^']*)')"
        for match in re.finditer(pattern, source):
            name = match.group(1).lower()
            value = match.group(3) if match.group(3) is not None else match.group(4)
            if name == "type":
                if value != "text/tikz":
                    return False
                continue
            if name not in _ALLOWED_BLOCK_ATTRIBUTES:
                return False
            # Values become TeX preamble or a worker concatenation; keep them to
            # the characters a package/library list can legitimately contain.
            if not re.fullmatch(r"[A-Za-z0-9_,.\-\[\]:{}\" ]*", value):
                return False
        # Whatever is left after removing balanced attributes must be whitespace.
        return not re.sub(pattern, "", source).strip()

    @staticmethod
    def _reject_unsafe_tex(code: str) -> None:
        """Reject file/process/metaprogramming TeX by structure, not by name list.

        Args:
            code: Body of one ``text/tikz`` block.

        Raises:
            RenderError: The code contains a file, process, or metaprogramming
                primitive, an unknown environment, or a TeX character escape.
                Legitimate diagrams only ever fail here through an unusual macro;
                the caller then falls back to the WASM backend.
        """
        index, length = 0, len(code)
        while index < length:
            if code[index] != "\\":
                index += 1
                continue
            index += 1
            if index >= length:
                break
            char = code[index]
            if char == "@":
                # \@ internals and the \@@input alias of \input.
                raise RenderError(
                    "Native TikZ rejected a file, process, or metaprogramming command"
                )
            if not (char.isascii() and char.isalpha()):
                # Control symbol: only the small set TikZ syntax uses.
                if char not in _ALLOWED_TEX_SYMBOLS:
                    raise RenderError(
                        "Native TikZ rejected an unexpected TeX control symbol"
                    )
                index += 1
                continue
            start = index
            while index < length and code[index].isascii() and (
                code[index].isalnum() or code[index] == "_"
            ):
                index += 1
            name = code[start:index]
            # An expl3 verb is a name immediately followed by `:` — that covers
            # the whole \file_*/\ior_*/\tl_* file and IO namespace (\file_input:n,
            # \file_get:nnN, ...) without rejecting ordinary `\my_style` macros.
            if index < length and code[index] == ":":
                raise RenderError(
                    "Native TikZ rejected a file, process, or metaprogramming command"
                )
            # \openout1 / \read2 carry a register number.
            if name.lower().rstrip("0123456789") in _DENIED_TEX_COMMANDS:
                raise RenderError(
                    "Native TikZ rejected a file, process, or metaprogramming command"
                )
        for match in _BEGIN_ENVIRONMENT.finditer(code):
            if match.group(1) not in _ALLOWED_ENVIRONMENTS:
                raise RenderError(
                    "Native TikZ rejected an environment outside the drawing allowlist"
                )
        if "^^" in code:
            raise RenderError("Native TikZ rejected a TeX character escape")

    async def render_html(self, html: str, timeout: float) -> str:
        """Replace TikZ scripts with SVGs within one bounded compilation deadline.

        Args:
            html: HTML produced by the plugin converters.
            timeout: Total time allowed for all native diagrams, in seconds.

        Returns:
            HTML with native SVGs and without the TikZJax loader.

        Raises:
            RenderError: A dependency, input, or compilation is invalid.
        """
        try:
            return await asyncio.wait_for(self._compile_html(html), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RenderError("Native TikZ compilation timed out") from exc

    async def _compile_html(self, html: str) -> str:
        """Build isolated documents and insert their path-based SVG output.

        Args:
            html: Converted document containing TikZ scripts.

        Returns:
            HTML containing the compiled diagrams.

        Raises:
            RenderError: Required binaries or packages are missing, or input is rejected.
        """
        matches = list(_TIKZ_SCRIPT.finditer(html))
        if not matches:
            return html
        # A block the native path must refuse (unknown command, unexpected
        # attribute) is not a render failure: PageRenderer falls back to the
        # WASM backend when RenderError is raised, so legitimate diagrams that
        # merely use an unlisted macro still render.

        executables = []
        for name in ("latex", "dvisvgm"):
            executable = (
                self.binary_dir / (name + (".exe" if os.name == "nt" else ""))
                if self.binary_dir
                else Path(shutil.which(name) or "")
            )
            if not executable.is_file():
                raise RenderError(
                    f"Native TikZ requires {name}; configure native_tex_bin"
                )
            executables.append(str(executable.resolve()))
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "TMP",
                "TEMP",
                "TMPDIR",
                "SYSTEMDRIVE",
            }
        }
        env.update(
            {
                "openin_any": "p",
                "openout_any": "p",
                "shell_escape": "f",
                "LANG": "C",
                "LC_ALL": "C",
                "MKTEXPK": "0",
                "MKTEXTFM": "0",
            }
        )
        env["PATH"] = (
            str(Path(executables[0]).parent) + os.pathsep + env.get("PATH", "")
        )
        replacements = []
        for index, match in enumerate(matches):
            attrs = {}
            parser = HTMLParser()
            parser.handle_starttag = lambda tag, values: attrs.update(values)
            parser.feed("<script" + match.group(1) + ">")
            code = match.group(2)
            if not self._screen_block_attributes(match.group(1)):
                raise RenderError("Native TikZ rejected an unexpected block attribute")
            self._reject_unsafe_tex(code)
            try:
                packages = json.loads(attrs.get("data-tex-packages", "{}"))
                libraries = [
                    x for x in attrs.get("data-tikz-libraries", "").split(",") if x
                ]
                if not isinstance(packages, dict) or any(packages.values()):
                    raise ValueError("Package options are not supported")
                if (
                    set(packages) - TikzConverter.SUPPORTED_PACKAGES
                    or set(libraries) - TikzConverter.SUPPORTED_LIBRARIES
                ):
                    raise ValueError("Unsupported package or library")
            except (ValueError, TypeError) as exc:
                raise RenderError(f"Invalid native TikZ preamble: {exc}") from exc
            preamble = (
                r"\documentclass[tikz,border=0pt]{standalone}" + "\n"
                r"\usepackage[OT1]{fontenc}" + "\n"
                r"\renewcommand{\rmdefault}{cmr}\renewcommand{\sfdefault}{cmss}\renewcommand{\ttdefault}{cmtt}"
                + "\n"
                r"\usepackage{amsmath,amssymb}" + "\n"
            )
            preamble += "".join("\\usepackage{" + p + "}\n" for p in packages)
            if libraries:
                preamble += "\\usetikzlibrary{" + ",".join(libraries) + "}\n"
            with tempfile.TemporaryDirectory(
                prefix="astrbot-native-tikz-"
            ) as directory:
                work = Path(directory)
                (work / "plot.tex").write_text(
                    preamble + "\\begin{document}\n" + code + "\n\\end{document}\n",
                    encoding="utf-8",
                )
                await self._run(
                    [
                        executables[0],
                        "--no-shell-escape",
                        "--halt-on-error",
                        "--interaction=nonstopmode",
                        "plot.tex",
                    ],
                    work,
                    env,
                )
                await self._run(
                    [
                        executables[1],
                        "--no-fonts",
                        "--no-styles",
                        "--exact-bbox",
                        "--no-mktexmf",
                        "plot.dvi",
                        "-o",
                        "plot.svg",
                    ],
                    work,
                    env,
                )
                output = work / "plot.svg"
                if not output.is_file() or output.stat().st_size > 16 * 1024 * 1024:
                    raise RenderError("Native TikZ produced no SVG or exceeded 16 MiB")
                try:
                    svg = ET.fromstring(output.read_bytes())
                except ET.ParseError as exc:
                    raise RenderError("Native TikZ produced malformed SVG") from exc
                if svg.tag != "{http://www.w3.org/2000/svg}svg":
                    raise RenderError("Native TikZ output is not SVG")
                # Avoid collisions between glyph IDs from different diagrams.
                ids = {
                    node.attrib["id"]: f"native{index}-{node.attrib['id']}"
                    for node in svg.iter()
                    if "id" in node.attrib
                }
                for node in svg.iter():
                    if node.tag.rsplit("}", 1)[-1] in {
                        "script",
                        "foreignObject",
                        "image",
                    }:
                        raise RenderError(
                            "Native SVG contains unsupported active or external content"
                        )
                    for key, value in list(node.attrib.items()):
                        local = key.rsplit("}", 1)[-1]
                        if local.startswith("on") or (
                            local == "href" and not value.startswith("#")
                        ):
                            raise RenderError(
                                "Native SVG contains an external reference"
                            )
                        if key == "id":
                            value = ids[value]
                        elif value.startswith("#") and value[1:] in ids:
                            value = "#" + ids[value[1:]]
                        value = re.sub(
                            r"url\(#([^)]*)\)",
                            lambda m: "url(#" + ids.get(m[1], m[1]) + ")",
                            value,
                        )
                        node.set(key, value)
                ET.register_namespace("", "http://www.w3.org/2000/svg")
                ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
                replacements.append(ET.tostring(svg, encoding="unicode"))
        for match, svg in reversed(list(zip(matches, replacements))):
            html = html[: match.start()] + svg + html[match.end() :]
        return re.sub(
            r'<(?:script|link)[^>]+(?:src|href)="[^" ]*/@drgrice1/tikzjax[^" ]+"[^>]*>(?:</script>)?',
            "",
            html,
        )

    async def _run(self, command: list[str], work: Path, env: dict) -> None:
        """Run one compiler stage and reap it on cancellation.

        Args:
            command: Executable and arguments, without a shell.
            work: Private temporary directory.
            env: Restricted child environment.

        Raises:
            RenderError: The compiler stage exits unsuccessfully.
        """
        # A file avoids unbounded PIPE buffering for verbose TeX failures.
        with (work / "stage.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=work,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                await process.wait()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        if process.returncode:
            # Summarise rather than forward the log tail. The tail is shaped by
            # attacker-controlled TeX and can carry file contents pulled in via
            # \input-family primitives straight back to the requester.
            with (work / "stage.log").open("rb") as log:
                log.seek(max(0, (work / "stage.log").stat().st_size - _FAILURE_TAIL_BYTES))
                tail = log.read().decode(errors="replace")
            first_error = ""
            for line in tail.splitlines():
                if line.startswith("!"):
                    first_error = line.lstrip("! ").strip()
                    break
            summary = (first_error or "compilation failed")[:_FAILURE_DETAIL_CHARS]
            raise RenderError(
                f"Native TikZ {Path(command[0]).name} failed "
                f"({process.returncode}): {summary}"
            )
