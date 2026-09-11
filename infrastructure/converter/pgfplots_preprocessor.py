"""Precompute bounded PGFplots surface grids while retaining native TeX styling."""

import math
import re

from ...domain.errors import PreprocessError
from ...utils.safe_eval import compile_math_expression


def _group(source: str, start: int) -> tuple[str, int]:
    """Read a balanced TeX option, expression, or coordinate group.

    Args:
        source: TeX source string.
        start: Index of the opening delimiter.

    Returns:
        Group contents and the index immediately after the closing delimiter.

    Raises:
        ValueError: The group is unbalanced.
    """
    opening = source[start]
    closing = {"[": "]", "{": "}", "(": ")"}[opening]
    depth = 1
    for index in range(start + 1, len(source)):
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1
            if depth == 0:
                return source[start + 1 : index], index + 1
    raise ValueError("Unbalanced plot group")


def _split(source: str) -> list[str]:
    """Split top-level options or parametric coordinates without splitting calls."""
    parts, stack, start = [], [], 0
    pairs = {"[": "]", "{": "}", "(": ")"}
    for index, char in enumerate(source):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == "," and not stack:
            parts.append(source[start:index].strip())
            start = index + 1
    parts.append(source[start:].strip())
    return parts


class PgfplotsPreprocessor:
    """Sample supported expression surfaces once in Python, without subsampling."""

    def __init__(self, max_points: int = 6400, budget: list[int] | None = None):
        self.max_points = max_points
        # Caller-owned point budget shared across every picture in a document.
        # Without it, each picture gets a fresh allowance and a message can
        # multiply the configured ceiling by its picture count.
        self._budget = budget if budget is not None else [max_points]
        # Points already spent by earlier pictures in this document.
        self._spent = max(0, max_points - self._budget[0])

    def convert(self, source: str) -> str:
        """Replace simple expression surfaces with an equivalent coordinate mesh.

        Args:
            source: One TikZ picture, optionally containing multiple axes.

        Returns:
            TeX with supported grids precomputed. Custom macros and styles retain
            native PGFplots evaluation instead of having their semantics guessed.

        Raises:
            PreprocessError: A known grid exceeds the per-picture point budget.
        """
        # Arbitrary style/macro expansion belongs to TeX, not this numeric path.
        semantic_source = re.sub(
            r"\\pgfplotsset\s*\{\s*compat\s*=\s*(?:1\.\d+|newest)\s*\}",
            "",
            source,
        )
        if any(
            token in semantic_source
            for token in (
                "\\pgfplotsset",
                "\\tikzset",
                "/.style",
                "\\def",
                "\\pgfmathdeclarefunction",
                "\\foreach",
            )
        ):
            return source
        token = re.compile(
            r"\\begin\{(?P<begin>tikzpicture|axis)\}|\\end\{axis\}|\\addplot\s*3\+?"
        )
        cursor = 0
        inherited, picture = {}, {}
        in_axis = False
        edits = []
        used = self._spent
        while match := token.search(source, cursor):
            cursor = match.end()
            line_start = source.rfind("\n", 0, match.start()) + 1
            if re.search(r"(?<!\\)%", source[line_start : match.start()]):
                continue
            try:
                options = ""
                while cursor < len(source) and source[cursor].isspace():
                    cursor += 1
                if cursor < len(source) and source[cursor] == "[":
                    options, cursor = _group(source, cursor)
                parsed = {}
                for item in _split(options):
                    key, _, value = item.partition("=")
                    key = " ".join(
                        key.strip()
                        .removeprefix("/pgfplots/")
                        .removeprefix("/tikz/")
                        .split()
                    )
                    parsed[key] = value.strip().strip("{}")
                if match.group("begin"):
                    if match.group("begin") == "tikzpicture":
                        picture = parsed
                    else:
                        inherited = {**picture, **parsed}
                        in_axis = True
                    continue
                if match.group(0).startswith("\\end"):
                    in_axis = False
                    continue
                if not in_axis:
                    continue
                settings = {**inherited, **parsed}
                if (
                    not ({"surf", "mesh"} & settings.keys())
                    and settings.get("samples y") != "1"
                ):
                    continue
                if any(
                    key in settings
                    for key in (
                        "samples at",
                        "filter point",
                        "execute at begin plot",
                        "execute at end plot",
                    )
                ):
                    continue
                n = int(settings.get("samples", "25"))
                m = int(settings.get("samples y", str(n)))
                if n < 2 or m < 1:
                    continue
                while cursor < len(source) and source[cursor].isspace():
                    cursor += 1
                if source[cursor] == "{":
                    expression, end = _group(source, cursor)
                    expressions = [
                        settings.get("variable", "x"),
                        settings.get("variable y", "y"),
                        expression,
                    ]
                elif source[cursor] == "(":
                    expression, end = _group(source, cursor)
                    expressions = [
                        item.strip().strip("{}") for item in _split(expression)
                    ]
                    if len(expressions) != 3:
                        continue
                else:
                    continue
                while end < len(source) and source[end].isspace():
                    end += 1
                if end >= len(source) or source[end] != ";":
                    continue
                cursor = end + 1
                names = tuple(
                    settings.get(key, default).lstrip("\\")
                    for key, default in [("variable", "x"), ("variable y", "y")]
                )
                if names[0] == names[1] or any(
                    not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in names
                ):
                    continue
                degree_mode = (
                    settings.get(
                        "trig format plots", settings.get("trig format", "deg")
                    )
                    == "deg"
                )
                evaluate = []
                for expression in expressions:
                    expression = re.sub(
                        r"\\(" + "|".join((*names, "pi")) + r")(?![A-Za-z])",
                        r"\1",
                        expression,
                    )
                    expression = re.sub(r"\blog\s*\(", "log10(", expression).replace(
                        "^", "**"
                    )
                    evaluate.append(
                        compile_math_expression(
                            expression, names, trig_degrees=degree_mode
                        )
                    )
                domains = []
                for domain in [
                    settings.get("domain", "-5:5"),
                    settings.get(
                        "y domain",
                        settings.get("domain y", settings.get("domain", "-5:5")),
                    ),
                ]:
                    bounds = domain.split(":")
                    if len(bounds) != 2:
                        raise ValueError("Unsupported domain")
                    limits = [
                        compile_math_expression(bound.replace("^", "**"))()
                        for bound in bounds
                    ]
                    if not all(math.isfinite(value) for value in limits):
                        raise ValueError("Unsupported domain")
                    domains.append(limits)
                if domains[1][0] == domains[1][1]:
                    m = 1
                count = n * m
                if used + count > self.max_points:
                    raise PreprocessError(
                        f"3D 曲面需要 {used + count} 个采样点，超过当前上限 {self.max_points}；请调高 plot_max_points 或拆分图形"
                    )
                # Charge the shared allowance so later pictures see less.
                self._budget[0] = max(0, self._budget[0] - count)
                self._spent = used + count
                x_values = [
                    domains[0][0] + (domains[0][1] - domains[0][0]) * i / (n - 1)
                    for i in range(n)
                ]
                y_values = [
                    domains[1][0] + (domains[1][1] - domains[1][0]) * j / (m - 1)
                    if m > 1
                    else domains[1][0]
                    for j in range(m)
                ]
                ordering = settings.get("mesh/ordering", "x varies")
                if ordering in ("x varies", "rowwise"):
                    grid = ((x, y) for y in y_values for x in x_values)
                elif ordering in ("y varies", "colwise"):
                    grid = ((x, y) for x in x_values for y in y_values)
                else:
                    continue
                coordinates = [
                    "("
                    + ",".join(f"{function(x, y):.9g}" for function in evaluate)
                    + ")"
                    for x, y in grid
                ]
                extra = f"mesh/rows={m},mesh/cols={n},mesh/ordering={ordering}"
                # Generated finite literals need no TeX expression parser.
                if not any("nan" in point for point in coordinates):
                    extra += ",plot coordinates/math parser=false"
                plot_options = (
                    options.rstrip(" ,") + "," + extra if options.strip() else extra
                )
                replacement = (
                    match.group(0)
                    + "["
                    + plot_options
                    + "] coordinates {\n"
                    + "\n".join(coordinates)
                    + "\n};"
                )
                edits.append((match.start(), end + 1, replacement))
                used += count
            except (ValueError, IndexError):
                continue
        for start, end, replacement in reversed(edits):
            source = source[:start] + replacement + source[end:]
        return source
