// Cache compiled code, while creating a fresh instance and memory for every job.
(() => {
    const instantiate = WebAssembly.instantiate.bind(WebAssembly);
    const modules = new WeakMap();
    globalThis.__mathjaxWasmCompiles = 0;
    WebAssembly.instantiate = async function(source, imports) {
        if (source instanceof WebAssembly.Module) return instantiate(source, imports);
        let pending = modules.get(source);
        if (!pending) {
            globalThis.__mathjaxWasmCompiles++;
            pending = WebAssembly.compile(source);
            modules.set(source, pending);
        }
        const module = await pending;
        return {module, instance: await instantiate(module, imports)};
    };
})();

// Fresh WASM memory is zero-initialized. Retain and restore only nonzero pages
// instead of keeping and copying a mostly empty 156 MiB TeX snapshot.
globalThis.__packTexSnapshot = function(input) {
    let source = input instanceof Uint8Array ? input : new Uint8Array(input);
    if (source.byteOffset % 4) source = source.slice();
    const words = new Uint32Array(source.buffer, source.byteOffset, source.byteLength >>> 2);
    const segments = [];
    let run = -1;
    for (let start = 0; start < source.length; start += 65536) {
        const end = Math.min(start + 65536, source.length);
        let nonzero = false;
        for (let i = start >>> 2; i < (end >>> 2); i++) {
            if (words[i] !== 0) { nonzero = true; break; }
        }
        if (!nonzero) {
            for (let i = (end >>> 2) * 4; i < end; i++) {
                if (source[i]) { nonzero = true; break; }
            }
        }
        if (nonzero && run < 0) run = start;
        if (!nonzero && run >= 0) {
            segments.push({offset: run, bytes: source.slice(run, start)});
            run = -1;
        }
    }
    if (run >= 0) segments.push({offset: run, bytes: source.slice(run)});
    globalThis.__texSnapshotStats = {originalBytes: source.length,
        retainedBytes: segments.reduce((total, part) => total + part.bytes.length, 0)};
    return {length: source.length, segments};
};
globalThis.__restoreTexSnapshot = function(buffer, snapshot) {
    if (buffer.byteLength < snapshot.length) throw new RangeError('TeX snapshot exceeds memory');
    const destination = new Uint8Array(buffer);
    for (const part of snapshot.segments) destination.set(part.bytes, part.offset);
};
globalThis.__loadTexSnapshot = async function(url, Inflate, expectedBytes) {
    const response = await fetch(url);
    if (!response.ok) throw new Error('Unable to load TeX snapshot');
    const reader = response.body.getReader();
    const inflate = new Inflate({chunkSize: 65536});
    const snapshot = {length: 0, segments: []};
    // Consume decompressed pages immediately; never assemble a full-size dump.
    inflate.onData = chunk => {
        if (snapshot.length + chunk.length > expectedBytes) throw new RangeError('Oversized TeX snapshot');
        const packed = __packTexSnapshot(chunk);
        for (const part of packed.segments) {
            snapshot.segments.push({offset: snapshot.length + part.offset, bytes: part.bytes});
        }
        snapshot.length += chunk.length;
    };
    try {
        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            inflate.push(value);
            if (inflate.err) throw new Error('Unable to decompress TeX snapshot');
        }
    } finally {
        reader.releaseLock();
    }
    if (!inflate.ended || snapshot.length !== expectedBytes) throw new Error('Incomplete TeX snapshot');
    globalThis.__texSnapshotStats = {originalBytes: snapshot.length,
        retainedBytes: snapshot.segments.reduce((sum, part) => sum + part.bytes.length, 0)};
    return snapshot;
};

// Keep bounded, immutable package bytes; each TeX job receives its own copy.
(() => {
    const cache = new Map();
    const missing = new Map();
    let bytes = 0;
    globalThis.__profiledTexLoader = loader => async function(name) {
        const started = performance.now();
        try {
            const absent = missing.get(name);
            if (absent && absent.expires > performance.now()) {
                globalThis.__texProfile.negativeHits++;
                throw Object.assign(new Error(absent.message), {status: 404});
            }
            missing.delete(name);
            const hit = cache.get(name);
            if (hit) {
                cache.delete(name); cache.set(name, hit);
                globalThis.__texProfile.packageHits++;
                return hit.slice();
            }
            globalThis.__texProfile.packageMisses++;
            const names = globalThis.__texProfile.packageMissNames ||= [];
            if (names.length < 8) names.push(name);
            let data;
            try {
                data = await loader(name);
            } catch (error) {
                if (error.status === 404 && name.startsWith('tex_files/')) {
                    if (missing.size >= 64) missing.delete(missing.keys().next().value);
                    missing.set(name, {message: String(error.message).slice(0, 300),
                        expires: performance.now() + 60000});
                }
                throw error;
            }
            if (name.startsWith('tex_files/') && data.byteLength <= 1024 * 1024) {
                while (cache.size && (bytes + data.byteLength > 4 * 1024 * 1024 || cache.size >= 128)) {
                    const key = cache.keys().next().value;
                    bytes -= cache.get(key).byteLength; cache.delete(key);
                }
                cache.set(name, data.slice()); bytes += data.byteLength;
            }
            return data;
        } finally {
            globalThis.__texProfile.packageMs += performance.now() - started;
            globalThis.__texProfile.packageCacheBytes = bytes;
        }
    };
})();
