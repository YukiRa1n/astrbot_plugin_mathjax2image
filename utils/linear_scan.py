"""Linear-time delimiter scanners.

The converters used to locate paired delimiters with lazy regexes of the form
``open [\\s\\S]*? close``.  When the closing token is missing, such a pattern
costs O(n) at *every* opening occurrence, so a message that is nothing but
opening delimiters (```\\begin{tikzpicture}```` x N, backticks x N, ...) burns
O(n^2) time on the event loop and freezes the whole bot.

Every scanner here walks the text once and never re-scans a region, so total
work is O(n) regardless of how unbalanced the input is.
"""

from __future__ import annotations

from bisect import bisect_right

# Inline code spans never need more ticks than this. Bounding the tick count
# also bounds the work for a pathological run of a single delimiter.
INLINE_TICKS_MAX = 8
# A fence run longer than this is scanned only up to the cap: the fence is then
# reduced to three ticks, which matches identically but keeps the pass linear.
FENCE_SCAN_MAX = 64


def _occurrences(text: str, token: str) -> list[int]:
    """All start offsets of ``token`` in ``text``, in ascending order."""
    offsets: list[int] = []
    cursor = 0
    while True:
        found = text.find(token, cursor)
        if found < 0:
            return offsets
        offsets.append(found)
        cursor = found + 1


def find_pairs(
    text: str,
    open_token: str,
    close_token: str,
    *,
    allow_newline: bool = True,
) -> list[tuple[int, int]]:
    """Locate non-overlapping ``open_token ... close_token`` spans, left to right.

    Semantics match a non-greedy regex over the same pair. Returns half-open
    ``(start, end)`` offsets in ascending order.

    A closing token is claimed at most once. When the delimiters nest (``$$``
    inside ``$$``, as in display math), that is what the lazy regex did too:
    ``$$a$$ ... $$b$$`` yields two spans rather than one wide span.

    Args:
        text: Haystack.
        open_token: Literal opening delimiter.
        close_token: Literal closing delimiter.
        allow_newline: When False, a match may not span a line break (mirrors
            ``open .*? close`` where ``.`` does not match ``\\n``).

    Returns:
        List of ``(start, end)`` spans, never overlapping.
    """
    spans: list[tuple[int, int]] = []
    if not open_token or not close_token or open_token in close_token:
        return spans if not open_token or not close_token else _overlapping_pairs(
            text, open_token, close_token, allow_newline
        )
    opens = _occurrences(text, open_token)
    if not opens:
        return spans
    closes = _occurrences(text, close_token)
    # Drop closers wholly contained in the first opener, then walk both lists
    # together; the head of `closes` is a usable candidate for `opens[0]`
    # because a closer cannot start at or before its own opener.
    while closes and closes[0] < opens[0] + len(open_token):
        closes.pop(0)
    cursor = 0
    for start in opens:
        if start < cursor:
            continue  # consumed by the previous pair
        index = bisect_right(closes, start)
        while index < len(closes) and closes[index] < start + len(open_token):
            index += 1
        if index >= len(closes):
            break
        close = closes.pop(index)
        if not allow_newline:
            line_end = text.find("\n", start)
            if line_end != -1 and close > line_end:
                continue  # never matches, so the opening pair is dropped
        end = close + len(close_token)
        spans.append((start, end))
        cursor = end
    return spans


def _overlapping_pairs(
    text: str, open_token: str, close_token: str, allow_newline: bool
) -> list[tuple[int, int]]:
    """Fallback for tokens where the closer contains the opener (``$$``/``$$``)."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    length = len(text)
    while cursor < length:
        start = text.find(open_token, cursor)
        if start < 0:
            break
        close = text.find(close_token, start + len(open_token))
        if close < 0:
            break
        if not allow_newline:
            line_end = text.find("\n", start)
            if line_end != -1 and close > line_end:
                cursor = line_end + 1
                continue
        end = close + len(close_token)
        spans.append((start, end))
        cursor = end
    return spans


def substitute_spans(text: str, spans: list[tuple[int, int]], render) -> str:
    """Rebuild ``text`` replacing each span with ``render(index, span_text)``."""
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for index, (start, end) in enumerate(spans):
        parts.append(text[cursor:start])
        parts.append(render(index, text[start:end]))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def find_brace_arguments(text: str, command: str) -> list[tuple[int, int]]:
    """Locate ``command{...}`` spans with one level of nesting, in one pass.

    Mirrors ``command\\{(?:[^{}]|\\{(?:[^{}]|\\{[^{}]*\\})*\\})*\\}`` but walks
    the text linearly, so a long unclosed argument costs O(n) instead of O(n^2).
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    length = len(text)
    while True:
        start = text.find(command, cursor)
        if start < 0:
            return spans
        brace = start + len(command)
        if brace >= length or text[brace] != "{":
            cursor = brace
            continue
        depth = 0
        index = brace
        while index < length:
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    spans.append((start, index + 1))
                    cursor = index + 1
                    break
            index += 1
        else:
            return spans  # unbalanced: nothing after this can be a full command


def scan_fenced_code(text: str) -> list[tuple[int, int]]:
    """Locate fenced code blocks and inline code spans in a single pass.

    Mirrors the previous fenced/inline alternation: a fence needs a run of at
    least three backticks or tildes followed by a line break and a matching
    closer; inline spans use a bounded tick run and may not cross a line break.
    """
    spans: list[tuple[int, int]] = []
    length = len(text)
    cursor = 0
    while cursor < length:
        char = text[cursor]
        if char not in "`~":
            cursor += 1
            continue
        # Measure the run once and consume it as a unit. Re-entering the run
        # after an inner match would otherwise re-scan its tail, making a
        # single long delimiter run quadratic.
        run_end = cursor
        while run_end < length and text[run_end] == char:
            run_end += 1
        run_length = run_end - cursor

        if run_length >= 3:
            # Fenced block: "<fence>[rest of line]\n<body><same fence>".
            # Only three ticks are needed: a longer run matches identically.
            line_end = text.find("\n", run_end)
            if line_end != -1:
                fence = char * 3
                close = text.find(fence, line_end + 1)
                if close != -1:
                    spans.append((cursor, close + len(fence)))
                    cursor = close + len(fence)
                    continue

        if char == "`":
            # Inline spans: `+<no newline>`+, tick run bounded to stay O(1).
            line_end = text.find("\n", cursor)
            limit = length if line_end == -1 else line_end
            ticks = min(run_length, INLINE_TICKS_MAX)
            position = cursor
            while ticks and position < run_end:
                token = char * ticks
                close = text.find(token, position + ticks)
                if close != -1 and close + ticks <= limit:
                    spans.append((position, close + ticks))
                    position = close + ticks
                else:
                    break
            cursor = position if position > cursor else run_end
            continue

        cursor = run_end
    return spans


def scan_math_blocks(text: str) -> list[tuple[int, int]]:
    """Locate math spans in one pass: ``\\[\\]``, ``\\(\\)``, ``$$``, ``$``, environments.

    Mirrors ``MarkdownConverter._extract_math_blocks``. The previous regex chain
    was applied sequentially and each pattern paid O(n) per opening occurrence
    when its closer was missing, so a message of repeated ``\\(`` cost O(n^2).
    """
    spans: list[tuple[int, int]] = []
    for open_token, close_token, allow_newline in (
        ("\\[", "\\]", True),
        ("\\(", "\\)", True),
        ("$$", "$$", False),
        ("$", "$", False),
    ):
        spans.extend(find_pairs(text, open_token, close_token, allow_newline=allow_newline))
    for environment in (
        "equation*",
        "equation",
        "align*",
        "align",
        "alignat*",
        "alignat",
        "gather*",
        "gather",
        "multline*",
        "multline",
        "flalign*",
        "flalign",
        "CD",
        "numcases",
        "subnumcases",
    ):
        spans.extend(
            find_pairs(
                text,
                "\\begin{" + environment + "}",
                "\\end{" + environment + "}",
            )
        )
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
