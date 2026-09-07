"""Apply a narrowly versioned optimization to the bundled upstream TeX worker."""

import hashlib
from pathlib import Path

WORKER_SHA256 = "45532e94a8aab00edd7a76ddd12f035c1570978be2a87931caed2b2c5c4bc9a7"


WORKER_REPLACEMENTS = {
    b'o=new Uint8Array(await c("core.dump.gz"),0,65536*B.pages)': b"o=await globalThis.__loadTexSnapshot(`${a}/core.dump.gz`,n.default.Inflate,65536*B.pages)",
    b"new Uint8Array(n.buffer,0,65536*B.pages).set(o.slice(0))": b"globalThis.__restoreTexSnapshot(n.buffer,o)",
    b"async texify(t,e){": b"async texify(t,e){globalThis.__texProfile={started:performance.now(),packageMs:0,packageHits:0,packageMisses:0,negativeHits:0};",
    b"throw new Error(`Unable to load ${A}. File not available.`)": b"throw Object.assign(new Error(`Unable to load ${A}. File not available.`),{status:t.status})",
    b"B.setFileLoader(c)": b"B.setFileLoader(globalThis.__profiledTexLoader(c))",
    b"await B.executeAsync(a.instance.exports);": b"globalThis.__texProfile.setupMs=performance.now()-globalThis.__texProfile.started;"
    b"const executionStart=performance.now();await B.executeAsync(a.instance.exports);"
    b"globalThis.__texProfile.executionMs=performance.now()-executionStart;"
    b"const svgStart=performance.now();",
    b"return await(0,A.dvi2html)(async function*(){yield s.Buffer.from(w)}(),h),Q": b"return await(0,A.dvi2html)(async function*(){yield s.Buffer.from(w)}(),h),"
    b"globalThis.__texProfile.svgMs=performance.now()-svgStart,"
    b"globalThis.__texProfile.totalMs=performance.now()-globalThis.__texProfile.started,Q",
}


def optimize_tikz_worker(source: bytes) -> bytes:
    """Use sparse snapshots, compiled modules and bounded package reuse.

    Args:
        source: The unmodified beta24 run-tex.js response body.

    Returns:
        Patched JavaScript for the exact audited build; unknown builds are unchanged.
    """
    if hashlib.sha256(source).hexdigest() != WORKER_SHA256:
        return source
    if any(source.count(original) != 1 for original in WORKER_REPLACEMENTS):
        return source
    bootstrap = (
        Path(__file__).resolve().parents[2] / "static/tikz_worker_bootstrap.js"
    ).read_bytes()
    for original, replacement in WORKER_REPLACEMENTS.items():
        source = source.replace(original, replacement)
    return bootstrap + b"\n" + source
