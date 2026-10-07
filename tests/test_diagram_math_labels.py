"""Regression coverage for AMS commands and mixed flowchart labels."""

import html
import json
import re

import pytest
from astrbot_plugin_mathjax2image.infrastructure.converter import (
    MermaidConverter,
    TikzConverter,
    TikzPlotConverter,
)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (r"$x \in \mathbb{R}^d$", r"$$x \in \mathbb{R}^d$$"),
        (r"$W_1$: 升维到 $4d$", r"$$W_1\text{: 升维到 }4d$$"),
        (r"$\sigma(\cdot)$ 非线性", r"$$\sigma(\cdot)\text{ 非线性}$$"),
        (r"$W_2$: 降回 $d$", r"$$W_2\text{: 降回 }d$$"),
        (r"输出 $\in \mathbb{R}^d$", r"$$\text{输出 }\in \mathbb{R}^d$$"),
        (r"输入 $x$", r"$$\text{输入 }x$$"),
        (
            r"输出 $x' = x + \mathrm{Sub}(x)$",
            r"$$\text{输出 }x' = x + \mathrm{Sub}(x)$$",
        ),
        (r"$x<y$", r"$$x<y$$"),
        (r"rate 50% & item_1: $x$", r"$$\text{rate 50\% \& item\_1: }x$$"),
        (r"{#} ^~ $x$", r"$$\text{\{\#\} \textasciicircum{}\textasciitilde{} }x$$"),
    ],
)
def test_flowchart_normalizes_a_complete_quoted_label(label, expected):
    source = f'```mermaid\nflowchart LR\n A["{label}"] --> B["plain"]\n```'
    converted = html.unescape(MermaidConverter().convert(source))
    assert f'A["{expected}"]' in converted
    assert 'B["plain"]' in converted


@pytest.mark.parametrize(
    "label",
    [
        "plain",
        r"$$x^2$$",
        r"$x$ and $$y$$",
        r"Cost \$5",
        "Cost $5",
        "$x$ $",
        "$5 and $10",
        r"$x$<br/>$y$",
    ],
)
def test_ambiguous_or_native_labels_are_preserved(label):
    source = f'```mermaid\nflowchart LR\n A["{label}"]\n```'
    converted = html.unescape(MermaidConverter().convert(source))
    assert f'A["{label}"]' in converted


def test_normalized_labels_are_idempotent():
    source = 'flowchart LR\n A["输入 $x$"] -->|"权重 $w$"| B["$y$"]'
    normalized = MermaidConverter._normalize_math_labels(source)
    assert MermaidConverter._normalize_math_labels(normalized) == normalized
    assert '|"$$\\text{权重 }w$$"|' in normalized


def test_frontmatter_comments_and_style_are_untouched():
    source = """---
title: 'A["$x$"]'
---
%%{init: {"theme": "base"}}%%
flowchart LR
%% A["$x$"]
 A["输入 $x$"] %% B["$y$"]
style A stroke:#fff
classDef math fill:#eee;
click A "https://example.com/$x$"
"""
    converted = html.unescape(
        MermaidConverter().convert("```mermaid\n" + source + "```")
    )
    assert "title: 'A[\"$x$\"]'" in converted
    assert '%% A["$x$"]' in converted
    assert '%% B["$y$"]' in converted
    assert 'A["$$\\text{输入 }x$$"]' in converted
    assert 'click A "https://example.com/$x$"' in converted


def test_other_diagram_types_do_not_change_math_syntax():
    source = '```mermaid\nsequenceDiagram\n A->>B: "$x$"\n```'
    assert '"$x$"' in html.unescape(MermaidConverter().convert(source))


def test_tikz_worker_loads_ams_packages_for_mathbb():
    source = r"\begin{tikzpicture}\node {$x\in\mathbb{R}^d$};\end{tikzpicture}"
    converted = TikzConverter(TikzPlotConverter()).convert(source)
    packages = json.loads(re.search(r"data-tex-packages='([^']+)'", converted).group(1))
    assert {"amsmath", "amsfonts", "amssymb"} <= packages.keys()
    assert r"\mathbb{R}" in converted
