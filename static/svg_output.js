// Normalize PGF paint scopes before allocating browser DOM nodes.
// Transform, clipping, opacity, IDs and style scopes remain in their original order.
window.__compactSvgOutput = function(source) {
    const paint = new Set(['fill', 'fill-rule', 'fill-opacity', 'stroke',
        'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit',
        'stroke-dasharray', 'stroke-dashoffset', 'stroke-opacity', 'color']);
    const tokens = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[[\s\S]*?\]\]>|<\/?[\w:.-]+\b(?:[^"'<>]|"[^"]*"|'[^']*')*>/g;
    const stack = [];
    const output = [];
    let inherited = Object.create(null);
    let cursor = 0;
    let depth = 0;
    let dropped = 0;
    let roots = 0;
    const started = performance.now();
    for (const match of source.matchAll(tokens)) {
        const gap = source.slice(cursor, match.index);
        if (gap.includes('<')) return null;
        output.push(gap);
        const token = match[0];
        cursor = match.index + token.length;
        if (token.startsWith('<?') || token.startsWith('<!')) {
            // XML declarations are not part of an HTML SVG fragment.
            if (!token.startsWith('<?xml') && !token.startsWith('<!--')) output.push(token);
            continue;
        }
        const closing = token.startsWith('</');
        const name = token.match(/^<\/?([\w:.-]+)/)[1];
        if (closing) {
            const frame = stack.pop();
            if (!frame || frame.name !== name) return null;
            inherited = frame.inherited;
            if (!frame.drop) { output.push(token); depth--; }
            continue;
        }
        const selfClosing = token.endsWith('/>');
        const end = selfClosing ? '/>' : '>';
        const raw = token.slice(name.length + 1, -end.length);
        const attributes = Object.create(null);
        const attributePattern = /([\w:.-]+)\s*=\s*("[^"]*"|'[^']*')/g;
        if (raw.replace(attributePattern, '').trim()) return null;
        for (const attribute of raw.matchAll(attributePattern)) {
            if (attribute[1] in attributes) return null;
            attributes[attribute[1]] = attribute[2];
        }
        const drop = name === 'g' && Object.keys(attributes).every(key => paint.has(key));
        const previous = inherited;
        if (drop) {
            inherited = Object.assign(Object.create(null), inherited);
            for (const [key, value] of Object.entries(attributes)) {
                if (value !== '"inherit"' && value !== "'inherit'") inherited[key] = value;
            }
            dropped++;
        } else {
            if (!stack.length) {
                if (name !== 'svg' || ++roots > 1) return null;
            }
            for (const [key, value] of Object.entries(inherited)) {
                if (!(key in attributes) || attributes[key] === '"inherit"' || attributes[key] === "'inherit'") {
                    attributes[key] = value;
                }
            }
            output.push('<' + name + Object.entries(attributes).map(([key, value]) =>
                ' ' + key + '=' + value).join('') + end);
            inherited = Object.create(null);
            if (!selfClosing && ++depth > 256) return null;
        }
        if (selfClosing) inherited = previous;
        else stack.push({name, drop, inherited: previous});
    }
    if (stack.length || roots !== 1 || source.slice(cursor).includes('<')) return null;
    output.push(source.slice(cursor));
    const result = output.join('').trimStart();
    window.__svgStats = window.__svgStats || [];
    window.__svgStats.push({inputBytes: source.length, outputBytes: result.length,
        droppedGroups: dropped, milliseconds: performance.now() - started});
    return result;
};
