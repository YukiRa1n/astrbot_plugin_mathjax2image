"""Compare fixed numeric kernel prototypes; NumPy is only a benchmark dependency."""

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path


def main():
    """Measure equivalent scalar, factored and NumPy surface kernels."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=15)
    args = parser.parse_args()
    import numpy as np

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from astrbot_plugin_mathjax2image.utils.safe_eval import compile_math_expression

    results = []
    for name, n, m in [
        ("surface-40", 40, 40),
        ("surface-80", 80, 80),
        ("torus", 48, 24),
    ]:
        torus = name == "torus"
        xs = [360 * i / (n - 1) if torus else -3 + 6 * i / (n - 1) for i in range(n)]
        ys = [360 * i / (m - 1) if torus else -3 + 6 * i / (m - 1) for i in range(m)]
        expressions = (
            ["(2+0.6*cos(y))*cos(x)", "(2+0.6*cos(y))*sin(x)", "0.6*sin(y)"]
            if torus
            else ["x", "y", "exp(-0.18*(x*x+y*y))*sin(deg(3*x))*cos(deg(2*y))"]
        )
        functions = [
            compile_math_expression(e, ("x", "y"), trig_degrees=True)
            for e in expressions
        ]

        def scalar():
            return [tuple(f(x, y) for f in functions) for y in ys for x in xs]

        def factored():
            if torus:
                sx = [math.sin(math.radians(x)) for x in xs]
                cx = [math.cos(math.radians(x)) for x in xs]
                radius = [2 + 0.6 * math.cos(math.radians(y)) for y in ys]
                height = [0.6 * math.sin(math.radians(y)) for y in ys]
                return [
                    (r * c, r * s, z)
                    for r, z in zip(radius, height)
                    for c, s in zip(cx, sx)
                ]
            xx = [math.exp(-0.18 * x * x) * math.sin(3 * x) for x in xs]
            yy = [math.exp(-0.18 * y * y) * math.cos(2 * y) for y in ys]
            return [(x, y, a * b) for y, b in zip(ys, yy) for x, a in zip(xs, xx)]

        def vectorized():
            x, y = np.meshgrid(xs, ys)
            if torus:
                u, v = np.deg2rad(x), np.deg2rad(y)
                r = 2 + 0.6 * np.cos(v)
                return np.column_stack(
                    (
                        (r * np.cos(u)).ravel(),
                        (r * np.sin(u)).ravel(),
                        (0.6 * np.sin(v)).ravel(),
                    )
                ).tolist()
            z = np.exp(-0.18 * (x * x + y * y)) * np.sin(3 * x) * np.cos(2 * y)
            return np.column_stack((x.ravel(), y.ravel(), z.ravel())).tolist()

        reference = np.asarray(scalar())
        timings, errors = {}, {}
        for label, kernel in [
            ("scalar", scalar),
            ("factored", factored),
            ("numpy", vectorized),
        ]:
            actual = np.asarray(kernel())
            error = float(np.max(np.abs(actual - reference)))
            if not np.allclose(actual, reference, rtol=1e-12, atol=1e-12):
                raise RuntimeError(f"{name}/{label} changed numeric results")
            samples = []
            for _ in range(max(1, args.runs)):
                started = time.perf_counter()
                kernel()
                samples.append((time.perf_counter() - started) * 1000)
            timings[label] = round(statistics.median(samples), 4)
            errors[label] = error
        result = {
            "case": name,
            "points": n * m,
            "median_ms": timings,
            "max_absolute_error": errors,
        }
        results.append(result)
        print(json.dumps(result), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
