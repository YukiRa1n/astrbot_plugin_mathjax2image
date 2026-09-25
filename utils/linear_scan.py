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

import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable

# Runs of a single code-fence delimiter, located in C so the scan does not pay
# Python-level per-character cost.
_DELIMITER_RUN = re.compile(r"`+|~+")

#: Environments whose spans are treated as math by ``scan_math_blocks``.
_MATH_ENVIRONMENTS = frozenset(
    """
    equation* equation align* align alignat* alignat
    gather* gather multline* multline flalign* flalign
    CD numcases subnumcases
    """.split()
)
# One match per \begin{X} / \end{X} boundary, so all environments are collected
# in a single pass instead of one scan each.
_ENV_BOUNDARY = re.compile(r"\\(?P<kind>begin|end)\{(?P<name>[^}]*)\}")

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
    # Walk both lists with monotonic cursors. `closes` must never be shifted:
    # `pop(0)` costs O(len(closes)) and `pop(index)` costs O(len(closes)-index),
    # which made an ordinary document such as `\(x\)` repeated -- or many
    # closers sitting before a single opener -- quadratic despite the module's
    # O(n) contract. Closers are claimed strictly left to right, so a single
    # lower bound is enough and the list is left intact.
    #
    # `first_usable` replaces the old drain of closers contained in the first
    # opener (`closes[0] < opens[0] + len(open_token)`): a closer cannot start
    # at or before its own opener, so everything left of that bound is dead.
    first_usable = bisect_left(closes, opens[0] + len(open_token))
    cursor = 0
    for start in opens:
        if start < cursor:
            continue  # consumed by the previous pair
        index = bisect_right(closes, start, first_usable)
        while index < len(closes) and closes[index] < start + len(open_token):
            index += 1
        if index >= len(closes):
            break
        close = closes[index]
        # Claimed by this opener in every branch below, exactly like the old
        # `closes.pop(index)` -- including the `allow_newline` rejection.
        first_usable = index + 1
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

    Delimiter runs are located with ``re.finditer`` so the per-character work
    happens in C: a hand-written per-character Python loop costs several times
    more than the C scanner it replaced on ordinary text, which showed up as a
    measurable regression on prose-heavy documents.
    """
    spans: list[tuple[int, int]] = []
    length = len(text)
    consumed_until = 0
    #: End of the line that ``line_limit`` was computed for. Reused by every
    #: delimiter run on the same line: re-finding the newline per run made a
    #: single long line of inline spans quadratic.
    line_limit = -1
    for match in _DELIMITER_RUN.finditer(text):
        start = match.start()
        if start < consumed_until:
            continue  # already part of a block body or an inline span
        char = match.group(0)[0]
        run_length = match.end() - start

        if run_length >= 3:
            # Fenced block: "<fence>[rest of line]\n<body><same fence>".
            # Only three ticks are needed: a longer run matches identically.
            line_end = text.find("\n", match.end())
            if line_end != -1:
                fence = char * 3
                close = text.find(fence, line_end + 1)
                if close != -1:
                    end = close + len(fence)
                    spans.append((start, end))
                    consumed_until = end
                    continue  # inner delimiters are part of the block body

        if char == "`":
            # Inline spans: `+<no newline>`+, tick run bounded to stay O(1).
            if line_limit < start:
                found = text.find("\n", start)
                line_limit = length if found == -1 else found
            limit = line_limit
            ticks = min(run_length, INLINE_TICKS_MAX)
            position = start
            while ticks and position < match.end():
                token = char * ticks
                close = text.find(token, position + ticks)
                if close != -1 and close + ticks <= limit:
                    end = close + ticks
                    spans.append((position, end))
                    position = end
                    consumed_until = end
                else:
                    break
    return spans


def scan_math_blocks(text: str) -> list[tuple[int, int]]:
    """Locate math spans in one pass: ``\\[\\]``, ``\\(\\)``, ``$$``, ``$``, environments.

    Mirrors ``MarkdownConverter._extract_math_blocks``. The previous regex chain
    was applied sequentially and each pattern paid O(n) per opening occurrence
    when its closer was missing, so a message of repeated ``\\(`` cost O(n^2).
    """
    spans: list[tuple[int, int]] = []
    # Each find_pairs call costs a substring scan even when the delimiter is
    # absent, and the environment list alone is 15 pairs. `str.__contains__` is
    # a C-level scan that skips those calls outright, which matters on plain
    # prose where none of these tokens appear.
    for open_token, close_token, allow_newline in (
        ("\\[", "\\]", True),
        ("\\(", "\\)", True),
        ("$$", "$$", False),
        ("$", "$", False),
    ):
        if open_token in text:
            spans.extend(
                find_pairs(text, open_token, close_token, allow_newline=allow_newline)
            )
    if "\\begin{" in text and "\\end{" in text:
        # One pass collects every \begin{X}/\end{X} offset; pairing then walks
        # two short lists per environment. Calling find_pairs once per
        # environment would re-scan the whole document fifteen times.
        begins: dict[str, list[int]] = {}
        ends: dict[str, list[int]] = {}
        for match in _ENV_BOUNDARY.finditer(text):
            environment = match.group("name")
            if environment not in _MATH_ENVIRONMENTS:
                continue
            bucket = begins if match.group("kind") == "begin" else ends
            bucket.setdefault(environment, []).append(match.start())
        for environment, starts in begins.items():
            closes = ends.get(environment)
            if not closes:
                continue
            # Same rule as find_pairs: each opening takes the nearest end that
            # follows it and has not been claimed yet.
            cursor = 0
            for start in starts:
                while cursor < len(closes) and closes[cursor] <= start:
                    cursor += 1
                if cursor >= len(closes):
                    break
                spans.append((start, closes[cursor] + len(environment) + len("\\end{}")))
                cursor += 1
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


#: Command whose declaration ``strip_usepackage_declarations`` removes.
_USEPACKAGE = "\\usepackage"


def _char_positions(text: str, char: str) -> list[int]:
    """Every offset of ``char`` in ``text``, ascending (C-level ``str.find``)."""
    positions: list[int] = []
    at = text.find(char)
    while at != -1:
        positions.append(at)
        at = text.find(char, at + 1)
    return positions


def _first_after(positions: list[int], offset: int) -> int:
    """First recorded offset strictly greater than ``offset``, else -1."""
    index = bisect_right(positions, offset)
    return positions[index] if index < len(positions) else -1


def strip_usepackage_declarations(text: str, collect: Callable[[str], None]) -> str:
    """Remove ``\\usepackage{...}`` / ``\\usepackage[...]{...}`` declarations.

    ``collect`` receives the brace argument of each matched declaration and may
    raise to reject it (the caller validates the package allowlist).

    This replaces
    ``re.sub(r"\\\\usepackage(?:\\[([^\\]]*)\\])?\\{([^{}]+)\\}")``. Its
    ``[^\\]]*`` / ``[^{}]+`` rescan the rest of the document at every
    ``\\usepackage`` whose closer is absent, so a 100 KB message of repeated
    ``\\usepackage[`` cost about 148 seconds. Closer offsets are collected once
    and looked up with ``bisect``, so each declaration costs O(log n) and no
    region is ever rescanned.

    Args:
        text: Document text.
        collect: Called with the brace argument of each matched declaration.

    Returns:
        ``text`` with every matched declaration removed.
    """
    if _USEPACKAGE not in text:
        return text
    brackets = _char_positions(text, "]")
    opening = _char_positions(text, "{")
    closing = _char_positions(text, "}")
    pieces: list[str] = []
    cursor = 0
    length = len(text)
    while True:
        start = text.find(_USEPACKAGE, cursor)
        if start < 0:
            break
        after = start + len(_USEPACKAGE)
        body_start = after
        options: str | None = None
        # `[^\]]*` stops only at `]`, so the optional group needs one after it.
        if after < length and text[after] == "[":
            bracket_end = _first_after(brackets, after)
            if bracket_end != -1:
                options = text[after + 1 : bracket_end]
                body_start = bracket_end + 1
        end = -1
        if body_start < length and text[body_start] == "{":
            brace_end = _first_after(closing, body_start)
            first_open = _first_after(opening, body_start)
            # `[^{}]+` needs at least one character and may not cross a `{`;
            # `brace_end` is the first `}` after the opener, so no `}` can sit
            # inside the candidate body.
            if brace_end > body_start + 1 and (
                first_open == -1 or first_open > brace_end
            ):
                end = brace_end + 1
        if end == -1:
            # Not a declaration: keep the command text and resume after it, like
            # a regex engine advancing past a failed match.
            pieces.append(text[cursor:after])
            cursor = after
            continue
        if options is not None:
            raise ValueError("MathJax package options are not supported")
        collect(text[body_start + 1 : end - 1])
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
