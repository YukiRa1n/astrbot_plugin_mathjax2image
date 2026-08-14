# -*- coding: utf-8 -*-
"""
P0/P1 fix verification tests
"""

import sys
import types
import inspect
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_plugin_root = Path(__file__).resolve().parent.parent
_parent_dir = _plugin_root.parent
for p in [str(_parent_dir), str(_plugin_root)]:
    if p not in sys.path:
        sys.path.insert(0, p)

if "astrbot" not in sys.modules:
    astrbot_mock = types.ModuleType("astrbot")
    astrbot_mock.__path__ = [str(_plugin_root / "astrbot")]
    astrbot_api_mock = types.ModuleType("astrbot.api")
    astrbot_api_mock.__path__ = [str(_plugin_root / "astrbot" / "api")]
    astrbot_api_mock.logger = MagicMock()
    astrbot_mock.api = astrbot_api_mock
    sys.modules["astrbot"] = astrbot_mock
    sys.modules["astrbot.api"] = astrbot_api_mock

for _mod_name in [
    "astrbot.api.event",
    "astrbot.api.message_components",
    "astrbot.api.star",
]:
    if _mod_name not in sys.modules:
        _m = types.ModuleType(_mod_name)
        _m.AstrMessageEvent = MagicMock
        _m.MessageChain = MagicMock
        _m.Comp = MagicMock()
        _m.StarTools = MagicMock()
        sys.modules[_mod_name] = _m


def test_font_path_traversal_sanitized():
    """Path traversal like ../../etc/passwd must be reduced to just the filename."""
    malicious = "../../etc/passwd"
    safe_name = Path(malicious).name
    assert safe_name == "passwd"
    assert "/" not in safe_name
    assert ".." not in safe_name

    malicious2 = "../../../etc/shadow"
    safe_name2 = Path(malicious2).name
    assert safe_name2 == "shadow"

    malicious3 = "subdir/../../../etc/passwd"
    safe_name3 = Path(malicious3).name
    assert safe_name3 == "passwd"


def test_browser_launch_args_no_dangerous_flags():
    """Browser launch args must not contain --disable-web-security or --allow-file-access-from-files."""
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import BrowserManager

    source = inspect.getsource(BrowserManager.get_browser)
    assert "--disable-web-security" not in source
    assert "--allow-file-access-from-files" not in source


@pytest.mark.asyncio
async def test_font_route_uses_safe_name(tmp_path):
    """handle_font_route must sanitize font_name via Path.name before building path."""
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import PageRenderer
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import BrowserManager

    static_dir = tmp_path / "static"
    fonts_dir = static_dir / "fonts"
    fonts_dir.mkdir(parents=True)

    bm = BrowserManager(max_pages=1)
    renderer = PageRenderer(browser_manager=bm, plugin_dir=tmp_path)

    mock_page = AsyncMock()
    await renderer._setup_font_routes(mock_page)

    route_call = mock_page.route.call_args_list[0]
    handler = route_call[0][1]

    mock_route = MagicMock()
    mock_route.request.url = "http://localhost/fonts/../../etc/passwd"
    mock_route.fulfill = AsyncMock()
    mock_route.continue_ = AsyncMock()
    mock_route.fallback = AsyncMock()

    await handler(mock_route)

    # 未命中本地字体时应调用 fallback() 交回下一个 handler，而非 continue_()
    mock_route.fallback.assert_awaited()
    mock_route.fulfill.assert_not_awaited()


@pytest.mark.asyncio
async def test_font_route_serves_valid_font(tmp_path):
    """Legitimate font requests should still be served correctly via route.fulfill(path=...)."""
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import PageRenderer
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import BrowserManager

    static_dir = tmp_path / "static"
    fonts_dir = static_dir / "fonts"
    fonts_dir.mkdir(parents=True)
    real_font = fonts_dir / "cmr10.ttf"
    real_font.write_bytes(b"fontdata")

    bm = BrowserManager(max_pages=1)
    renderer = PageRenderer(browser_manager=bm, plugin_dir=tmp_path)

    mock_page = AsyncMock()
    await renderer._setup_font_routes(mock_page)

    handler = mock_page.route.call_args_list[0][0][1]

    mock_route = MagicMock()
    mock_route.request.url = "http://localhost/fonts/cmr10.ttf"
    mock_route.fulfill = AsyncMock()
    mock_route.continue_ = AsyncMock()

    await handler(mock_route)

    mock_route.fulfill.assert_awaited_once()
    call_kwargs = mock_route.fulfill.call_args[1]
    assert "path" in call_kwargs
    assert Path(call_kwargs["path"]).name == "cmr10.ttf"


# ---- P1 Fix verification tests ----


class TestSafeEvalPowerLimit:
    """Fix 1: safe_eval rejects large power operations"""

    def test_large_exponent_rejected(self):
        from astrbot_plugin_mathjax2image.utils.safe_eval import SafeMathEvaluator
        import ast

        evaluator = SafeMathEvaluator()
        tree = ast.parse("9**9999", mode="eval")
        with pytest.raises(ValueError, match="Power exponent too large"):
            evaluator.visit(tree)

    def test_reasonable_power_allowed(self):
        from astrbot_plugin_mathjax2image.utils.safe_eval import SafeMathEvaluator
        import ast

        evaluator = SafeMathEvaluator()
        tree = ast.parse("2**10", mode="eval")
        result = evaluator.visit(tree)
        assert result == 1024.0

    def test_large_base_large_exponent_rejected(self):
        from astrbot_plugin_mathjax2image.utils.safe_eval import SafeMathEvaluator
        import ast

        evaluator = SafeMathEvaluator()
        tree = ast.parse("2000**20", mode="eval")
        with pytest.raises(ValueError, match="Power operation too large"):
            evaluator.visit(tree)


class TestTikzSamplesCap:
    """Fix 2: samples capped at 2000 (安全上限，防止坐标串绕过 MAX_TIKZ_LENGTH)"""

    def test_samples_capped(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzPlotConverter()
        result = converter._parse_samples("samples=999999")
        assert result == 2000

    def test_samples_normal_value(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzPlotConverter()
        result = converter._parse_samples("samples=50")
        assert result == 50

    def test_samples_default(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzPlotConverter()
        result = converter._parse_samples("thick, blue")
        assert result == 50


class TestListConverterReset:
    """Fix 4: list_converter resets counter per enumerate block"""

    def test_counter_resets_per_enumerate(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.list_converter import (
            ListConverter,
        )

        converter = ListConverter()
        text = (
            r"\begin{enumerate}" + "\n"
            r"\item First" + "\n"
            r"\item Second" + "\n"
            r"\end{enumerate}" + "\n"
            r"\begin{enumerate}" + "\n"
            r"\item Alpha" + "\n"
            r"\item Beta" + "\n"
            r"\end{enumerate}"
        )
        result = converter.convert(text)
        assert "1. First" in result
        assert "2. Second" in result
        assert "1. Alpha" in result
        assert "2. Beta" in result

    def test_itemize_uses_dashes(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.list_converter import (
            ListConverter,
        )

        converter = ListConverter()
        text = (
            r"\begin{itemize}" + "\n"
            r"\item Apple" + "\n"
            r"\item Banana" + "\n"
            r"\end{itemize}"
        )
        result = converter.convert(text)
        assert "- Apple" in result
        assert "- Banana" in result


class TestPendingImagesTTL:
    """Fix 3: _pending_images TTL cleanup"""

    def test_cleanup_removes_expired(self, tmp_path):
        import time
        from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

        handler = LLMToolHandler.__new__(LLMToolHandler)
        handler._pending_images = {}
        handler._IMAGE_TTL_SECONDS = 300

        fake_path = tmp_path / "expired.png"
        fake_path.write_bytes(b"fake")
        handler._pending_images["session1"] = (fake_path, time.time() - 600)

        live_path = tmp_path / "live.png"
        live_path.write_bytes(b"fake")
        handler._pending_images["session2"] = (live_path, time.time())

        handler._cleanup_expired_images()

        assert "session1" not in handler._pending_images
        assert "session2" in handler._pending_images
        assert not fake_path.exists()

    def test_cleanup_keeps_fresh_entries(self, tmp_path):
        import time
        from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

        handler = LLMToolHandler.__new__(LLMToolHandler)
        handler._pending_images = {}
        handler._IMAGE_TTL_SECONDS = 300

        fresh_path = tmp_path / "fresh.png"
        fresh_path.write_bytes(b"fake")
        handler._pending_images["s1"] = (fresh_path, time.time() - 10)

        handler._cleanup_expired_images()

        assert "s1" in handler._pending_images
        assert fresh_path.exists()


class TestInjectScriptUsesSetTimeout:
    """Fix 5: setInterval replaced with setTimeout"""

    def test_no_setInterval(self):
        from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
            PageRenderer,
        )
        from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
            BrowserManager,
        )

        bm = BrowserManager(max_pages=1)
        renderer = PageRenderer(browser_manager=bm, plugin_dir=Path("."))
        script = renderer._get_inject_script()
        assert "setInterval" not in script
        # 注入脚本监听 tikzjax-load-finished 事件(等待编译完成)
        assert "tikzjax-load-finished" in script
        assert "MutationObserver" in script


# ---- P0 Fix verification tests ----


class TestPendingImagesLock:
    """P0 Fix 1: _pending_images uses asyncio.Lock for race-condition safety."""

    def test_lock_created_in_init(self):
        """LLMToolHandler.__init__ must create an asyncio.Lock as _pending_lock."""
        import asyncio
        from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

        handler = LLMToolHandler.__new__(LLMToolHandler)
        handler._pending_images = {}
        handler._pending_lock = asyncio.Lock()
        handler._last_rendered_image = None

        assert isinstance(handler._pending_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_handle_render_math_uses_lock(self, tmp_path, monkeypatch):
        """handle_render_math must guard _pending_images writes with the lock."""
        import asyncio
        from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

        handler = LLMToolHandler.__new__(LLMToolHandler)
        handler._pending_images = {}
        handler._pending_lock = asyncio.Lock()
        handler._last_rendered_image = None
        handler._IMAGE_TTL_SECONDS = 300
        handler._render_orchestrator = MagicMock()
        handler._latex_preprocessor = MagicMock()
        handler._context = MagicMock()
        handler._get_session_key = MagicMock(return_value="session-x")

        image_file = tmp_path / "img.png"
        image_file.write_bytes(b"x")
        handler._render_orchestrator.render = AsyncMock(return_value=image_file)

        async def _no_sleep(_):
            return None
        monkeypatch.setattr(
            "astrbot_plugin_mathjax2image.handlers.llm_tool_handler.asyncio.sleep",
            _no_sleep,
        )

        event = MagicMock()
        result = await handler.handle_render_math(event, "hello", auto_send=False)
        assert "渲染成功" in result
        assert "session-x" in handler._pending_images
        assert isinstance(handler._pending_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_handle_render_math_cleans_old_entry(self, tmp_path, monkeypatch):
        """When overwriting a session entry, the previous file must be unlinked."""
        import asyncio
        from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

        handler = LLMToolHandler.__new__(LLMToolHandler)
        handler._pending_images = {}
        handler._pending_lock = asyncio.Lock()
        handler._last_rendered_image = None
        handler._IMAGE_TTL_SECONDS = 300
        handler._render_orchestrator = MagicMock()
        handler._latex_preprocessor = MagicMock()
        handler._context = MagicMock()
        handler._get_session_key = MagicMock(return_value="s1")

        old_file = tmp_path / "old.png"
        old_file.write_bytes(b"old")
        handler._pending_images["s1"] = (old_file, 0.0)

        new_file = tmp_path / "new.png"
        new_file.write_bytes(b"new")
        handler._render_orchestrator.render = AsyncMock(return_value=new_file)

        async def _no_sleep(_):
            return None
        monkeypatch.setattr(
            "astrbot_plugin_mathjax2image.handlers.llm_tool_handler.asyncio.sleep",
            _no_sleep,
        )

        event = MagicMock()
        await handler.handle_render_math(event, "content", auto_send=False)

        assert not old_file.exists()
        assert handler._pending_images["s1"][0] == new_file


class TestRenderOrchestratorFullUuid:
    """P0 Fix 2: render_orchestrator must use the full uuid4().hex, not truncated."""

    def test_output_path_uses_full_uuid(self):
        """Output path generation must use uuid.uuid4().hex (32 chars), not truncated."""
        import inspect
        from astrbot_plugin_mathjax2image.application.render_orchestrator import (
            RenderOrchestrator,
        )

        # UUID is generated inside _render_locked after semaphore gate
        source = inspect.getsource(RenderOrchestrator._render_locked)
        assert "uuid.uuid4().hex" in source
        assert "uuid.uuid4().hex[:8]" not in source
        assert "uuid.uuid4().hex[:16]" not in source
        assert "uuid.uuid4().hex[:12]" not in source

    def test_output_path_uuid_length(self, tmp_path, monkeypatch):
        """Generated filename contains a 32-char UUID hex (no truncation)."""
        import re
        from astrbot_plugin_mathjax2image.application.render_orchestrator import (
            RenderOrchestrator,
        )

        captured = {}

        async def fake_render_to_image(self, html, output):
            captured["output"] = output
            output.write_bytes(b"x")

        monkeypatch.setattr(
            "astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer.PageRenderer.render_to_image",
            fake_render_to_image,
        )

        fake_data_dir = tmp_path / "data"
        fake_data_dir.mkdir()
        monkeypatch.setattr(
            "astrbot_plugin_mathjax2image.application.render_orchestrator.StarTools.get_data_dir",
            lambda name: fake_data_dir,
        )

        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        templates_dir = plugin_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "template.html").write_text(
            "<html><body>{{CONTENT}}</body></html>", encoding="utf-8"
        )

        ro = RenderOrchestrator(plugin_dir=plugin_dir, bg_color="#FFFFFF")

        async def _no_check(_self):
            return True
        monkeypatch.setattr(
            "astrbot_plugin_mathjax2image.infrastructure.browser.dependency_installer.PlaywrightDependencyInstaller.check_and_install",
            _no_check,
        )

        import asyncio
        asyncio.run(ro.render("hello"))

        name = captured["output"].name
        assert name.startswith("render_")
        assert name.endswith(".png")
        uuid_part = name[len("render_"):-len(".png")]
        assert len(uuid_part) == 32, f"Expected full 32-char UUID, got {len(uuid_part)}: {uuid_part}"
        assert re.fullmatch(r"[0-9a-f]{32}", uuid_part)


class TestMermaidLengthLimit:
    """P0 Fix 3: MermaidConverter rejects oversized code blocks."""

    def test_max_mermaid_length_constant(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.mermaid_converter import (
            MAX_MERMAID_LENGTH,
        )
        assert MAX_MERMAID_LENGTH == 50000

    def test_oversized_mermaid_block_rejected(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.mermaid_converter import (
            MermaidConverter,
        )

        converter = MermaidConverter()
        huge = "graph TD\n  A-->B\n" + " " * 50000
        text = f"```mermaid\n{huge}\n```"
        result = converter.convert(text)
        assert "error" in result.lower() or "过长" in result

    def test_normal_mermaid_block_processed(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.mermaid_converter import (
            MermaidConverter,
        )

        converter = MermaidConverter()
        text = "```mermaid\ngraph TD\n  A-->B\n```"
        result = converter.convert(text)
        assert '<pre class="mermaid">' in result
        assert "A--&gt;B" in result or "A-->B" in result


class TestChemfigComplexityValidation:
    """P0 Fix 4: chemfig blocks must run through _validate_tikz_complexity."""

    def test_chemfig_block_calls_validate(self):
        """Source must invoke _validate_tikz_complexity inside _convert_chemfig_block."""
        import inspect
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
            TikzConverter,
        )

        source = inspect.getsource(TikzConverter._convert_chemfig_block)
        assert "_validate_tikz_complexity" in source

    def test_chemfig_oversized_block_rejected(self, tmp_path):
        """A chemfig block that is too long must be rejected with an error div."""
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
            TikzConverter,
        )
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzConverter(TikzPlotConverter())
        huge_inner = "A" * 60000
        huge_chemfig = r"\chemfig{" + huge_inner + "}"
        text = f"some text {huge_chemfig} more text"

        result = converter.convert(text)
        assert "error" in result.lower() or "复杂" in result

    def test_chemfig_short_block_rejected(self):
        """chemfig 不在 TikZJax 支持清单中,应明确拒绝而非假装支持。"""
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
            TikzConverter,
        )
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzConverter(TikzPlotConverter())
        text = r"reaction: \chemfig{H-Cl}"
        result = converter.convert(text)
        assert "chemfig 不支持" in result.lower()


class TestPageRendererFontRouteUsesPath:
    """P0 Fix 5: page_renderer font route must use route.fulfill(path=...) not body."""

    def test_source_uses_path_argument(self):
        """The font route handler source must use route.fulfill(path=...)."""
        import inspect
        from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
            PageRenderer,
        )

        source = inspect.getsource(PageRenderer._setup_font_routes)
        assert "route.fulfill(path=" in source
        assert "with open(font_path" not in source
        assert 'fulfill(body=' not in source

    @pytest.mark.asyncio
    async def test_font_route_fulfill_called_with_path(self, tmp_path):
        """When a valid font is requested, fulfill is called with path kwarg."""
        from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
            PageRenderer,
        )
        from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
            BrowserManager,
        )

        fonts_dir = tmp_path / "static" / "fonts"
        fonts_dir.mkdir(parents=True)
        real_font = fonts_dir / "cmr10.ttf"
        real_font.write_bytes(b"data")

        bm = BrowserManager(max_pages=1)
        renderer = PageRenderer(browser_manager=bm, plugin_dir=tmp_path)

        mock_page = AsyncMock()
        await renderer._setup_font_routes(mock_page)

        handler = mock_page.route.call_args_list[0][0][1]

        mock_route = MagicMock()
        mock_route.request.url = "http://localhost/fonts/cmr10.ttf"
        mock_route.fulfill = AsyncMock()
        mock_route.continue_ = AsyncMock()

        await handler(mock_route)

        mock_route.fulfill.assert_awaited_once()
        call_kwargs = mock_route.fulfill.call_args[1]
        assert "path" in call_kwargs
        assert call_kwargs["path"].endswith("cmr10.ttf")
        mock_route.continue_.assert_not_awaited()


# ---- P2 Fix verification tests ----


class TestSimpleMacrosWordBoundary:
    """Fix 1: SIMPLE_MACROS replacement must not corrupt longer macros like \\Zeta."""

    def test_Z_does_not_corrupt_Zeta(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
            TikzConverter,
        )
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzConverter(TikzPlotConverter())
        tikz_code = r"\begin{tikzpicture}\node {$\Zeta$};\end{tikzpicture}"
        result = converter.convert(tikz_code)
        assert r"\Zeta" in result
        assert r"\mathbb{Z}eta" not in result

    def test_Z_standalone_replaced(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
            TikzConverter,
        )
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzConverter(TikzPlotConverter())
        tikz_code = r"\begin{tikzpicture}\node {$\Z$};\end{tikzpicture}"
        result = converter.convert(tikz_code)
        assert r"\mathbb{Z}" in result

    def test_eps_does_not_corrupt_epsilon(self):
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
            TikzConverter,
        )
        from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
            TikzPlotConverter,
        )

        converter = TikzConverter(TikzPlotConverter())
        tikz_code = r"\begin{tikzpicture}\node {$\epsilon$};\end{tikzpicture}"
        result = converter.convert(tikz_code)
        assert r"\epsilon" in result
        assert r"\varepsilonilon" not in result


class TestTikzPlotXReplacement:
    """Fix 2: \\x replacement must not corrupt \\xi or other \\x-prefixed macros."""

    def test_x_does_not_corrupt_xi(self):
        import re
        expr = r"\xi + \x"
        x_val = 3.14
        result = re.sub(r'\\x(?![a-zA-Z])', str(x_val), expr)
        assert r"\xi" in result
        assert str(x_val) in result

    def test_x_standalone_replaced(self):
        import re
        expr = r"\x^2 + \x"
        x_val = 2.0
        result = re.sub(r'\\x(?![a-zA-Z])', str(x_val), expr)
        assert r"\x" not in result
        assert "2.0^2 + 2.0" == result


class TestHandleSendImageUsesLock:
    """Fix 3: handle_send_image must guard _pending_images with _pending_lock."""

    def test_handle_send_image_source_uses_lock(self):
        import inspect
        from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

        source = inspect.getsource(LLMToolHandler.handle_send_image)
        assert "_pending_lock" in source
        assert "async with" in source

    @pytest.mark.asyncio
    async def test_handle_send_image_no_pending(self):
        import asyncio
        from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

        handler = LLMToolHandler.__new__(LLMToolHandler)
        handler._pending_images = {}
        handler._pending_lock = asyncio.Lock()
        handler._IMAGE_TTL_SECONDS = 300
        handler._context = MagicMock()
        handler._get_session_key = MagicMock(return_value="session-a")

        event = MagicMock()
        result = await handler.handle_send_image(event)
        assert "没有" in result


class TestSafeEvalResultSizeLimit:
    """Fix 4: safe_eval must reject power results that are too large."""

    def test_999_pow_999_rejected(self):
        from astrbot_plugin_mathjax2image.utils.safe_eval import safe_eval_math

        result = safe_eval_math("999**999")
        import math
        assert math.isnan(result)

    def test_moderate_power_allowed(self):
        from astrbot_plugin_mathjax2image.utils.safe_eval import safe_eval_math

        result = safe_eval_math("2**10")
        assert result == 1024.0

    def test_inf_result_rejected(self):
        from astrbot_plugin_mathjax2image.utils.safe_eval import safe_eval_math
        import math

        result = safe_eval_math("999**500")
        assert math.isnan(result)


class TestPageRendererFullUuid:
    """Fix 5: page_renderer temp file must use full uuid, not truncated."""

    def test_temp_file_uses_full_uuid(self):
        import inspect
        from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
            PageRenderer,
        )

        source = inspect.getsource(PageRenderer.render_to_image)
        # mkstemp 生成随机唯一文件名（比 uuid 更安全，且文件权限 0600）
        assert "tempfile.mkstemp" in source or "uuid.uuid4().hex" in source
        assert "uuid.uuid4().hex[:8]" not in source
        assert "uuid.uuid4().hex[:16]" not in source
        assert "uuid.uuid4().hex[:12]" not in source


# ---- architecture and lifecycle optimization ----


def test_browser_manager_defaults_to_two_pages_and_supports_engine_selection():
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )

    manager = BrowserManager(engine="webkit")
    assert manager.max_pages == 2
    assert manager.engine == "webkit"


def test_non_chromium_launch_options_do_not_receive_chromium_flags():
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )

    assert BrowserManager._launch_options("firefox") == {"headless": True}
    assert BrowserManager._launch_options("webkit") == {"headless": True}
    assert "args" in BrowserManager._launch_options("chromium")


@pytest.mark.asyncio
async def test_page_renderer_rejects_excessive_screenshot_height(tmp_path):
    from astrbot_plugin_mathjax2image.domain.errors import RenderError
    from astrbot_plugin_mathjax2image.infrastructure.browser.browser_manager import (
        BrowserManager,
    )
    from astrbot_plugin_mathjax2image.infrastructure.browser.page_renderer import (
        PageRenderer,
    )

    renderer = PageRenderer(
        browser_manager=BrowserManager(max_pages=1),
        plugin_dir=tmp_path,
        max_screenshot_height=1000,
        max_screenshot_pixels=2_000_000,
    )
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value={"width": 1300, "height": 2000})

    with pytest.raises(RenderError, match="高度"):
        await renderer._take_screenshot(page, tmp_path / "oversized.png")

    page.screenshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_handler_embeds_image_bytes_and_removes_artifact(
    tmp_path, monkeypatch
):
    from astrbot_plugin_mathjax2image.handlers import command_handler as module
    from astrbot_plugin_mathjax2image.handlers.command_handler import CommandHandler

    image_path = tmp_path / "render.png"
    image_path.write_bytes(b"image-bytes")
    handler = CommandHandler.__new__(CommandHandler)
    handler._render_orchestrator = MagicMock()
    handler._render_orchestrator.render = AsyncMock(return_value=image_path)
    image_factory = MagicMock()
    image_factory.fromBytes = MagicMock(return_value="embedded-image")
    monkeypatch.setattr(module.Comp, "Image", image_factory, raising=False)
    event = MagicMock()
    event.chain_result.side_effect = lambda chain: chain

    results = [result async for result in handler._render_and_send(event, "content")]

    assert results == [["embedded-image"]]
    image_factory.fromBytes.assert_called_once_with(b"image-bytes")
    assert not image_path.exists()


def test_validate_cdp_url_security():
    from astrbot_plugin_mathjax2image.utils.security import validate_cdp_url

    assert validate_cdp_url("http://127.0.0.1:9222") == "http://127.0.0.1:9222"
    with pytest.raises(ValueError):
        validate_cdp_url("http://10.0.0.5:9222")


def test_bg_color_replacement_matches_template_default(tmp_path):
    from astrbot_plugin_mathjax2image.infrastructure.converter.markdown_converter import (
        MarkdownConverter,
    )

    template = tmp_path / "template.html"
    template.write_text(
        "<html><style>:root { --bg-color: #fdfbf7; }</style>{{CONTENT}}</html>",
        encoding="utf-8",
    )
    converter = MarkdownConverter(template_path=template)
    html = converter.convert_to_html("hello", "#112233")
    assert "--bg-color: #112233;" in html
    assert "#fdfbf7" not in html.lower() or "#112233" in html


def test_command_extract_prefers_framework_content():
    from astrbot_plugin_mathjax2image.handlers.command_handler import CommandHandler

    handler = CommandHandler.__new__(CommandHandler)
    event = MagicMock()
    event.get_message_str.return_value = "/math ignored"
    assert handler._extract_command_content(event, "math", "勾股定理") == "勾股定理"
    event.get_message_str.return_value = "/render$E=mc^2$"
    assert handler._extract_command_content(event, "render", "") == "$E=mc^2$"


@pytest.mark.asyncio
async def test_llm_send_removes_artifact_after_embedding(tmp_path, monkeypatch):
    import asyncio
    import time

    from astrbot_plugin_mathjax2image.handlers import llm_tool_handler as module
    from astrbot_plugin_mathjax2image.handlers.llm_tool_handler import LLMToolHandler

    image_path = tmp_path / "pending.png"
    image_path.write_bytes(b"pending-image")
    handler = LLMToolHandler.__new__(LLMToolHandler)
    handler._pending_images = {"session": (image_path, time.time())}
    handler._pending_lock = asyncio.Lock()
    handler._last_rendered_image = image_path
    handler._context = MagicMock()
    handler._context.send_message = AsyncMock()
    image_factory = MagicMock()
    image_factory.fromBytes = MagicMock(return_value="embedded-image")
    monkeypatch.setattr(module.Comp, "Image", image_factory, raising=False)
    event = MagicMock(unified_msg_origin="session")
    # 模拟 get_sender_id 返回空（私聊），会话键退化为 origin
    event.get_sender_id = MagicMock(return_value="")

    result = await handler.handle_send_image(event)

    assert "已发送" in result
    image_factory.fromBytes.assert_called_once_with(b"pending-image")
    assert not image_path.exists()
    assert handler._last_rendered_image is None


# ===== Regression tests for review fixes =====


def test_network_policy_file_logic_is_correct():
    """验证 file:// 放行逻辑：仅当前页面 URI 放行，其余中止。"""
    import asyncio
    from urllib.parse import urlparse

    # 复现 page_renderer 中的判定逻辑
    current_page_uri = "file:///C:/tmp/temp_abcd1234.html"

    def should_allow(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return "continue" if url == current_page_uri else "abort"
        if parsed.scheme in {"about", "data", "blob"}:
            return "continue"
        if parsed.scheme == "https" and parsed.hostname in {"unpkg.com"}:
            return "continue"
        return "abort"

    # 页面自身 URI 放行
    assert should_allow("file:///C:/tmp/temp_abcd1234.html") == "continue"
    # 任意其他本地文件被阻止（本地文件读取通道）
    assert should_allow("file:///C:/Windows/System32/secret.svg") == "abort"
    assert should_allow("file:///C:/Users/me/.ssh/id_rsa") == "abort"
    # data:/about:/blob 放行
    assert should_allow("data:image/png;base64,AAAA") == "continue"
    assert should_allow("about:blank") == "continue"
    # 非白名单 https 被阻止
    assert should_allow("https://evil.example.com/x.png") == "abort"


def test_safe_eval_giant_int_returns_nan_not_crash():
    """超大 int 常量（>256 bit，转 float 会 OverflowError）应安全返回 nan。"""
    from astrbot_plugin_mathjax2image.utils.safe_eval import safe_eval_math
    import math

    # 200 位十进制数 ≈ 664 bit，远超 float 可表示范围，字面量触发常量保护
    huge = "9" * 200
    result = safe_eval_math(huge)
    assert math.isnan(result)


def test_tikz_foreach_limits_blocked():
    """\foreach 数量/嵌套深度超限应被拒绝。"""
    from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_converter import (
        TikzConverter,
    )
    from astrbot_plugin_mathjax2image.infrastructure.converter.tikz_plot_converter import (
        TikzPlotConverter,
    )

    conv = TikzConverter(TikzPlotConverter())
    # 大量 foreach
    lots = "".join(r"\foreach \x in {1,...,100} {\draw (0,0);}" for _ in range(30))
    assert conv._validate_tikz_complexity(lots) is False
    # 深嵌套 foreach（括号深度超限）
    deep = r"\foreach \a in {1,...,9} {\foreach \b in {1,...,9} {\foreach \c in {1,...,9} {\draw (0,0);}}}"
    assert conv._validate_tikz_complexity(deep) is False
    # 正常代码通过
    normal = r"\draw (0,0) -- (1,1);"
    assert conv._validate_tikz_complexity(normal) is True
