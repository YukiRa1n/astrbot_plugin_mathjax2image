"""Tolerate the LaTeX/Markdown habits LLMs bring to the render_math tool."""

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_mathjax2image.domain.errors import RenderError
from astrbot_plugin_mathjax2image.infrastructure.converter import (
    LatexPreprocessor,
    ListConverter,
    MarkdownConverter,
    MermaidConverter,
    TableConverter,
    TikzConverter,
    TikzPlotConverter,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "templates/template.html"
TIKZ = "\\begin{tikzpicture}\n\\draw (0,0) -- (1,0);\n\\end{tikzpicture}"


def _body(source: str) -> str:
    preprocessor = LatexPreprocessor(
        TikzConverter(TikzPlotConverter()),
        ListConverter(),
        TableConverter(),
        MermaidConverter(),
    )
    html = MarkdownConverter(TEMPLATE).convert_to_html(preprocessor.preprocess(source))
    return re.search(r'<main class="render-content">([\s\S]*?)</main>', html).group(1)


def test_document_preamble_is_dropped():
    body = _body(
        "\\documentclass{article}\n\\usepackage{amsmath,tikz}\n"
        "\\usetikzlibrary{arrows.meta}\n\\begin{document}\n正文 $x$\n\\end{document}"
    )
    for leftover in ("documentclass", "usepackage", "usetikzlibrary", "document}"):
        assert leftover not in body
    assert "正文" in body


def test_preamble_commands_inside_prose_are_kept():
    body = _body("用 \\usetikzlibrary{calc} 加载库，\\maketitlex 不是命令")
    assert "usetikzlibrary{calc}" in body
    assert "maketitlex" in body


@pytest.mark.parametrize("label", ["tikz", "TikZ"])
def test_tikz_fence_is_rendered_as_a_figure(label):
    body = _body(f"```{label}\n{TIKZ}\n```")
    assert 'class="tikz-diagram"' in body
    assert "<pre" not in body


@pytest.mark.parametrize("label", ["latex", "tex", ""])
def test_other_fences_keep_tikz_as_code(label):
    body = _body(f"```{label}\n{TIKZ}\n```")
    assert 'class="tikz-diagram"' not in body
    assert "<pre" in body


def test_tikz_fence_without_environment_stays_code():
    body = _body("```tikz\n\\draw (0,0) -- (1,0);\n```")
    assert 'class="tikz-diagram"' not in body


def test_preamble_inside_code_blocks_is_untouched():
    body = _body("```latex\n\\documentclass{article}\n\\usepackage{tikz}\n```")
    text = re.sub(r"<[^>]+>", "", body)
    assert "\\documentclass{article}" in text
    assert "\\usepackage{tikz}" in text


@pytest.mark.asyncio
async def test_tikz_compile_error_explains_the_usual_cause():
    from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

    orchestrator = MagicMock()
    orchestrator.render = AsyncMock(
        side_effect=RenderError("渲染失败: TikZ渲染失败：1 个图编译错误")
    )
    handler = LLMToolHandler(orchestrator, MagicMock())

    result = await handler.handle_render_math(MagicMock(), TIKZ)

    assert "渲染失败" in result
    assert "中文" in result


@pytest.mark.asyncio
async def test_sent_result_tells_the_model_not_to_repeat_latex(tmp_path, monkeypatch):
    from astrbot_plugin_mathjax2image.handlers import llm_tool_handler

    # 测试桩里的 AstrBot 消息组件不完整，替换为 Mock 只验证返回给模型的结果
    monkeypatch.setattr(llm_tool_handler, "Comp", MagicMock())
    monkeypatch.setattr(llm_tool_handler, "MessageChain", MagicMock())

    image = tmp_path / "render.png"
    image.write_bytes(b"png")
    orchestrator = MagicMock()
    orchestrator.render = AsyncMock(return_value=image)
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    handler = llm_tool_handler.LLMToolHandler(orchestrator, context)

    result = await handler.handle_render_math(MagicMock(), "$x$")

    assert result == llm_tool_handler.IMAGE_SENT_RESULT
    assert "已发送" in result and "LaTeX" in result
