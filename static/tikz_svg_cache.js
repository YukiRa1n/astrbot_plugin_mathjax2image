// Called after compilation and before screenshot sizing changes the SVG.
async ({hits, native, budget}) => {
    const main = document.querySelector('main.render-content');
    const sources = new Map();
    const compiled = {};
    const serializer = new XMLSerializer();
    for (const wrapper of main.querySelectorAll('.tikz-diagram[data-tikz-source]')) {
        const index = wrapper.dataset.tikzSource;
        const svg = wrapper.querySelector('svg');
        if (!svg) throw new Error('Missing compiled TikZ SVG');
        sources.set(index, svg);
        // A transient native failure falls back to WASM. Never publish that
        // result under a native key; duplicates in this document can still use it.
        if (budget > 0 && (!native || svg.dataset.tikzBackend === 'native')) {
            const xml = serializer.serializeToString(svg);
            if (xml.length <= budget) {
                compiled[index] = xml;
                budget -= xml.length;
            }
        }
    }

    // The normal TikZJax loader installs its fonts together with the worker.
    // A cache-only page needs those same fonts without starting the compiler.
    if (!native && hits.some(Boolean)) {
        const font = window.__engineSources?.tikzjax?.find(([tag]) => tag === 'link');
        if (font && !Array.from(document.styleSheets).some(s => s.href === font[1])) {
            await new Promise((resolve, reject) => {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.onload = resolve;
                link.onerror = () => { link.remove(); reject(new Error('TikZ font load failed')); };
                link.href = font[1];
                document.head.appendChild(link);
            });
        }
    }
    const parser = new DOMParser();
    for (const wrapper of main.querySelectorAll('[data-tikz-copy]')) {
        const index = wrapper.dataset.tikzCopy;
        let source = sources.get(index);
        if (!source) {
            source = parser.parseFromString(hits[index], 'image/svg+xml').documentElement;
            if (source.localName !== 'svg' || source.namespaceURI !== 'http://www.w3.org/2000/svg') {
                throw new Error('Invalid cached TikZ SVG');
            }
            sources.set(index, source);
        }
        wrapper.className = 'tikz-diagram';
        wrapper.replaceChildren(document.importNode(source, true));
    }

    const diagrams = main.querySelectorAll('[data-tikz-source] svg, [data-tikz-copy] svg');
    if (main.querySelectorAll('.tikz-diagram').length > 1) {
        const docPrefix = 'tikz-' + crypto.getRandomValues(new Uint32Array(3)).join('-') + '-';
        diagrams.forEach((svg, position) => {
            const ids = new Map();
            const prefix = docPrefix + position + '-';
            for (const element of [svg, ...svg.querySelectorAll('[id]')]) {
                if (!element.id) continue;
                const replacement = prefix + ids.size;
                ids.set(element.id, replacement);
                element.id = replacement;
            }
            if (!ids.size) return;
            const urls = text => text.replace(/url\(\s*(['"]?)#([^)'"\s]+)\1\s*\)/g,
                (all, quote, id) => ids.has(id) ? 'url(#' + ids.get(id) + ')' : all);
            for (const el of [svg, ...svg.querySelectorAll('*')]) {
                for (const attr of el.attributes) {
                    const value = attr.value;
                    if (attr.localName === 'href' && value.startsWith('#') && ids.has(value.slice(1))) {
                        attr.value = '#' + ids.get(value.slice(1));
                    } else if (value.includes('url(')) {
                        attr.value = urls(value);
                    } else if (attr.name === 'aria-labelledby' || attr.name === 'aria-describedby') {
                        attr.value = value.split(/\s+/).map(id => ids.get(id) || id).join(' ');
                    }
                }
                if (el.localName === 'style') {
                    el.textContent = urls(el.textContent).replace(/([^{}]+)\{/g, selector =>
                        selector.replace(/#([\w-]+)/g, (all, id) => ids.has(id) ? '#' + ids.get(id) : all));
                }
            }
        });
    }
    return compiled;
}
