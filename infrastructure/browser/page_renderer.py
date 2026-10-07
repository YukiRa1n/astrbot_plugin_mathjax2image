"""
页面渲染器
将HTML渲染为图片
"""

import asyncio
import hashlib
import os
import re
import tempfile
import time
import traceback
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ...domain.errors import RenderError
from ...utils.async_tasks import run_in_thread
from .asset_disk_cache import AssetDiskCache
from .native_tikz import NativeTikzRenderer
from .tikz_worker import optimize_tikz_worker

_GOTO_TIMEOUT_MS = 60_000
_MATH_REQUIRED_DECLARATION = re.compile(r"window\.mathJaxRequired = (true|false);")
_EXTRA_MARGIN_MS = 10_000  # 截图等余量
_CACHEABLE_SUFFIXES = (
    ".js",
    ".css",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".wasm",
    ".gz",
)
_TIKZ_WORKER_SUFFIX = "/@drgrice1/tikzjax@1.0.0-beta24/dist/run-tex.js"
_MEMORY_ASSET_TTL_S = 3600
_MAX_MEMORY_ASSETS = 256
_MAX_MEMORY_ASSET_BYTES = 32 * 1024 * 1024
# 浏览器下载同一资源期间，并发请求最多等待这么久；页面中途关闭等导致
# 下载事件不再到来时，过期的登记会被新请求接管
_PENDING_TTL_S = 60.0

try:
    from astrbot.api import logger
except ModuleNotFoundError:  # pragma: no cover - standalone test support
    import logging

    logger = logging.getLogger("astrbot")

if TYPE_CHECKING:
    from .browser_manager import BrowserManager


@dataclass
class _CachedResource:
    """An immutable cached asset body; eviction never affects a delivery."""

    body: bytes
    headers: dict[str, str]
    created: float
    size: int

    async def fulfill(self, route) -> None:
        """Deliver the asset to an intercepted request."""
        await route.fulfill(body=self.body, headers=self.headers)


class PageRenderer:
    """页面渲染器 - 将HTML渲染为图片截图"""

    _ALLOWED_REMOTE_HOSTS = frozenset(
        {
            "astrbot-plugins.oss-cn-hangzhou.aliyuncs.com",
            "unpkg.com",
            "cdn.jsdelivr.net",
        }
    )

    def __init__(
        self,
        browser_manager: "BrowserManager",
        plugin_dir: Path,
        viewport_width: int = 1150,
        viewport_height: int = 1,
        mathjax_timeout: int = 10000,
        tikz_timeout: int = 60000,
        mermaid_timeout: int = 15000,
        screenshot_timeout: int = 60000,
        max_screenshot_height: int = 16000,
        max_screenshot_pixels: int = 40_000_000,
        fail_on_mathjax_timeout: bool = False,
        resource_cache_max_mb: int = 64,
        image_cache_max_mb: int = 8,
        max_concurrent_tikz: int = 1,
        resident_engines: bool = True,
        compact_svg: bool = True,
        optimize_worker: bool = True,
        tikz_backend: str = "wasm",
        native_tex_bin: str = "",
        render_semaphore: asyncio.Semaphore | None = None,
        render_queue_timeout: float = 30.0,
        asset_cache_dir: Path | None = None,
        asset_disk_cache_max_mb: int = 256,
    ):
        if tikz_backend not in {"wasm", "native"}:
            raise ValueError("tikz_backend must be wasm or native")
        # 跨重启保留 CDN 静态资源；未提供目录或预算为 0 时只用内存缓存
        disk_cache = (
            AssetDiskCache(asset_cache_dir, int(asset_disk_cache_max_mb) * 1024 * 1024)
            if asset_cache_dir is not None
            else None
        )
        self._disk_cache = disk_cache if disk_cache and disk_cache.enabled else None
        self._native_tikz = (
            NativeTikzRenderer(native_tex_bin) if tikz_backend == "native" else None
        )
        self._optimize_worker = optimize_worker
        self._compact_svg = compact_svg
        self._resident_engines = resident_engines
        self._browser_manager = browser_manager
        self._plugin_dir = plugin_dir
        self._viewport_width = viewport_width
        self._viewport_height = viewport_height
        self._mathjax_timeout = mathjax_timeout
        self._tikz_timeout = tikz_timeout
        self._mermaid_timeout = mermaid_timeout
        self._screenshot_timeout = screenshot_timeout
        self._max_screenshot_height = max_screenshot_height
        self._max_screenshot_pixels = max_screenshot_pixels
        self._fail_on_mathjax_timeout = fail_on_mathjax_timeout
        # 当前渲染页面的 file URI（安全边界：仅放行此 URI 的本地文件加载）
        # 当前渲染页面的 file URI，按 page 隔离（网络策略只放行各自页面自身资源）
        self._current_page_uris: dict = {}
        # 进程级 CDN 资源缓存(URL → bytes),避免跨页重复下载 MathJax/TikZJax。
        # 只在事件循环线程上访问，不需要锁。
        self._cdn_cache: OrderedDict[str, _CachedResource] = OrderedDict()
        self._cdn_cache_bytes = 0
        self._cdn_cache_limit = max(0, int(resource_cache_max_mb)) * 1024 * 1024
        # URL → (浏览器下载完成的通知, 登记时间)
        self._cdn_pending: dict[str, tuple[asyncio.Future, float]] = {}
        # 已监听下载事件的浏览器上下文（上下文关闭后自动移除）
        self._watched_contexts: weakref.WeakSet = weakref.WeakSet()
        self._image_cache = OrderedDict()
        self._image_cache_bytes = 0
        self._image_cache_limit = max(0, int(image_cache_max_mb)) * 1024 * 1024
        self._image_pending: dict[bytes, asyncio.Future] = {}
        self._tikz_slots = asyncio.Semaphore(max(1, int(max_concurrent_tikz)))
        self._render_semaphore = render_semaphore
        self._render_queue_timeout = render_queue_timeout

    async def render_to_image(self, html: str, output: Path) -> None:
        """Render HTML, reusing recent successful images and concurrent work.

        Args:
            html: Complete HTML document.
            output: Independent output file owned by the caller.
        """
        key = hashlib.sha256(html.encode("utf-8")).digest()
        while True:
            cached = self._image_cache.pop(key, None)
            if cached is not None:
                if time.monotonic() - cached[1] < 300:
                    self._image_cache[key] = cached
                    await run_in_thread(output.write_bytes, cached[0])
                    return
                self._image_cache_bytes -= len(cached[0])
            pending = self._image_pending.get(key)
            if pending is None:
                break
            # A cancelled waiter must not cancel another caller's rendering.
            body = await asyncio.shield(pending)
            if body is not None:
                await run_in_thread(output.write_bytes, body)
                return

        pending = asyncio.get_running_loop().create_future()
        self._image_pending[key] = pending
        try:
            complete = await self._render_uncached(html, output)
            if complete and output.stat().st_size <= self._image_cache_limit:
                body = await run_in_thread(output.read_bytes)
                while self._image_cache and (
                    self._image_cache_bytes + len(body) > self._image_cache_limit
                    or len(self._image_cache) >= 64
                ):
                    _, evicted = self._image_cache.popitem(last=False)
                    self._image_cache_bytes -= len(evicted[0])
                self._image_cache[key] = (body, time.monotonic())
                self._image_cache_bytes += len(body)
                pending.set_result(body)
        except Exception as exc:
            # Concurrent callers share this attempt's failure; later calls retry.
            pending.set_exception(exc)
            pending.exception()
            raise
        finally:
            if not pending.done():
                pending.set_result(None)
            self._image_pending.pop(key, None)

    async def _render_uncached(self, html: str, output: Path) -> bool:
        """Render a private temporary HTML file and remove it after use.

        Args:
            html: Complete HTML document.
            output: Destination PNG path.

        Returns:
            Whether rendering completed without a timeout fallback.
        """
        heavy = 'type="text/tikz"' in html
        render_acquired = False
        if heavy:
            try:
                await asyncio.wait_for(
                    self._tikz_slots.acquire(), timeout=self._tikz_timeout / 1000.0
                )
            except asyncio.TimeoutError as exc:
                raise RenderError("TikZ 编译队列等待超时，请稍后重试") from exc
        try:
            if self._render_semaphore is not None:
                try:
                    if self._render_semaphore.locked():
                        await asyncio.wait_for(
                            self._render_semaphore.acquire(),
                            timeout=self._render_queue_timeout,
                        )
                    else:
                        await self._render_semaphore.acquire()
                except asyncio.TimeoutError as exc:
                    raise RenderError("等待渲染超时，请稍后重试") from exc
                render_acquired = True
            if heavy and self._native_tikz is not None:
                try:
                    html = await self._native_tikz.render_html(
                        html, self._tikz_timeout / 1000.0
                    )
                except (RenderError, OSError, ValueError) as exc:
                    logger.warning(
                        "[MathJax2Image] Native TikZ unavailable; falling back to WASM: %s",
                        exc,
                    )
            # 使用系统临时目录（插件目录可能因 pip 安装到 site-packages 而只读）
            # 使用 mkstemp 确保文件权限为 0600，避免其他本地用户读取渲染内容
            temp_dir = Path(tempfile.gettempdir()) / "astrbot_mathjax2image"
            temp_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path_str = tempfile.mkstemp(suffix=".html", dir=str(temp_dir))
            tmp_path = Path(tmp_path_str)

            logger.info(f"[MathJax2Image] HTML 临时文件: {tmp_path}")

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(html)
                return await self._do_render(tmp_path, output)
            finally:
                tmp_path.unlink(missing_ok=True)
        finally:
            if render_acquired:
                self._render_semaphore.release()
            if heavy:
                self._tikz_slots.release()

    async def _do_render(self, html_path: Path, output: Path) -> bool:
        """执行实际的渲染操作"""
        inject_script = self._get_inject_script()
        page = None
        exception_occurred = False

        try:
            page, has_been_setup = await self._browser_manager.acquire_page(
                self._viewport_width, self._viewport_height
            )

            if not has_been_setup:
                await page.add_init_script(inject_script)
                await self._setup_network_policy(page)
                await self._setup_font_routes(page)
                self._setup_logging(page)

            # 记录当前渲染页面的 file URI，网络策略据此放行自身资源
            self._current_page_uris[page] = html_path.resolve().as_uri()
            # 单次渲染整体超时，防止恶意/超长内容长时间占满页面池
            # (goto + MathJax + Mermaid + TikZ 串行最坏约 145s)
            overall_timeout_ms = (
                self._mathjax_timeout
                + self._tikz_timeout
                + self._mermaid_timeout
                + _GOTO_TIMEOUT_MS
                + _EXTRA_MARGIN_MS
            )
            overall_timeout_s = overall_timeout_ms / 1000.0
            try:
                complete = await asyncio.wait_for(
                    self._load_and_wait(page, html_path),
                    timeout=overall_timeout_s,
                )
                await asyncio.wait_for(
                    self._take_screenshot(page, output),
                    timeout=self._screenshot_timeout / 1000.0
                    + _EXTRA_MARGIN_MS / 1000.0,
                )
                return complete
            except asyncio.TimeoutError:
                # 超时取消的页面可能处于中间态，标记异常以便销毁而非干净回收
                exception_occurred = True
                raise RenderError(f"渲染总时长超过限制 {overall_timeout_s:.0f}s")

        except asyncio.CancelledError:
            exception_occurred = True
            raise
        except Exception as e:
            exception_occurred = True
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise RenderError(f"渲染失败: {e}")
        finally:
            if page is not None:
                self._current_page_uris.pop(page, None)
                await self._browser_manager.release_page(page, exception_occurred)

    def _get_inject_script(self) -> str:
        """获取注入脚本。

        TikZJax 渲染是异步的：先把 script 替换成 loading spinner SVG，
        编译完成后派发 ``tikzjax-load-finished`` 事件并替换为最终 SVG。
        这里监听该事件并计数，供 ``_wait_for_tikz`` 判断"全部编译完成"。
        同时记录 ``img-not-found``(编译失败)以便立即报错。
        """
        optimizer = ""
        if self._compact_svg:
            optimizer = (
                Path(__file__).resolve().parents[2] / "static/svg_output.js"
            ).read_text(encoding="utf-8")
        return (
            optimizer
            + "\n"
            + """
        (function() {
            window.__tikzFinished = 0;
            window.__tikzFailed = 0;
            // TikZJax inserts SVG with the HTML fragment parser, whose depth
            // limit flattens dense PGFplots groups and corrupts inherited paint.
            // Parse SVG as XML so every transform and color scope survives.
            const parseFragment = Range.prototype.createContextualFragment;
            Range.prototype.createContextualFragment = function(markup) {
                if (typeof markup === 'string') {
                    const source = markup.trimStart();
                    if ((source.startsWith('<svg') || source.startsWith('<?xml')) &&
                        source.includes('<g')) {
                        const compact = window.__compactSvgOutput && window.__compactSvgOutput(source);
                        if (compact) return parseFragment.call(this, compact);
                        const parsed = new DOMParser().parseFromString(source, 'image/svg+xml');
                        if (parsed.documentElement.localName === 'svg' &&
                            !parsed.querySelector('parsererror')) {
                            const inherited = new Set([
                                'fill', 'fill-rule', 'fill-opacity', 'stroke',
                                'stroke-width', 'stroke-linecap', 'stroke-linejoin',
                                'stroke-miterlimit', 'stroke-dasharray',
                                'stroke-dashoffset', 'stroke-opacity', 'color'
                            ]);
                            // Deep XML is valid, but also exceeds Chromium's
                            // layout stack. Collapse only presentation scopes;
                            // retain IDs, clips, opacity, styles and other groups.
                            const pending = Array.from(parsed.documentElement.children);
                            while (pending.length) {
                                const node = pending.pop();
                                const children = Array.from(node.children);
                                if (node.localName === 'g' &&
                                    Array.from(node.attributes).every(attribute =>
                                        inherited.has(attribute.name) || attribute.name === 'transform')) {
                                    const matrix = node.transform.baseVal.consolidate()?.matrix;
                                    for (const child of children) {
                                        for (const attribute of node.attributes) {
                                            if (inherited.has(attribute.name) && !child.hasAttribute(attribute.name)) {
                                                child.setAttribute(attribute.name, attribute.value);
                                            }
                                        }
                                        // Definition coordinates are resolved at
                                        // their use site, not at this ancestor.
                                        if (matrix && child.transform &&
                                            !['clipPath', 'mask', 'pattern', 'marker',
                                              'symbol', 'defs'].includes(child.localName)) {
                                            const local = child.transform.baseVal.consolidate()?.matrix;
                                            const combined = local ? matrix.multiply(local) : matrix;
                                            child.setAttribute('transform', 'matrix(' +
                                                [combined.a, combined.b, combined.c, combined.d,
                                                 combined.e, combined.f].join(' ') + ')');
                                        }
                                    }
                                    const parent = node.parentNode;
                                    while (node.firstChild) parent.insertBefore(node.firstChild, node);
                                    node.remove();
                                }
                                pending.push(...children);
                            }
                            const depths = [[parsed.documentElement, 0]];
                            while (depths.length) {
                                const [node, depth] = depths.pop();
                                if (depth > 256) {
                                    window.__tikzFailed++;
                                    throw new Error('SVG contains too many non-collapsible nested groups');
                                }
                                for (const child of node.children) depths.push([child, depth + 1]);
                            }
                            const fragment = document.createDocumentFragment();
                            fragment.appendChild(document.importNode(parsed.documentElement, true));
                            return fragment;
                        }
                    }
                }
                return parseFragment.call(this, markup);
            };
            document.addEventListener('tikzjax-load-finished', function() {
                window.__tikzFinished++;
            });
            // 编译失败: TikZJax 插入 <img src="//invalid.site/img-not-found.png">
            // 注意: add_init_script 在文档解析前运行, DOM 尚未创建,
            // MutationObserver.observe 会抛 "must be an instance of Node"。
            // 必须等 DOMContentLoaded 后再安装 observer。
            function installObserver() {
                var checkFailed = function() {
                    var bad = document.querySelectorAll('.tikz-diagram img[src*="invalid.site"], .tikz-diagram img[src*="img-not-found"]');
                    if (bad.length > 0) window.__tikzFailed = bad.length;
                };
                var imgObserver = new MutationObserver(checkFailed);
                imgObserver.observe(document.documentElement, {childList: true, subtree: true});
                checkFailed();
            }
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', installObserver);
            } else {
                installObserver();
            }
            // 页面 load 后确保样式修复执行
            window.addEventListener('load', function() {
                document.querySelectorAll('.tikz-diagram svg').forEach(function(svg) {
                    svg.style.position = 'relative';
                    svg.style.display = 'block';
                    svg.style.margin = '20px auto';
                    svg.style.border = 'none';
                    svg.style.padding = '0';
                });
            });
        })();
        """
        )

    async def _setup_font_routes(self, page) -> None:
        """设置字体路由"""
        static_dir = self._plugin_dir / "static"

        async def handle_font_route(route):
            url = route.request.url
            font_path = None

            for marker, subdir in (
                ("/bakoma/ttf/", static_dir / "bakoma" / "ttf"),
                ("/fonts/", static_dir / "fonts"),
            ):
                if marker in url:
                    font_path = subdir / Path(url.split(marker)[-1]).name
                    break

            if font_path and font_path.exists() and font_path.is_file():
                await route.fulfill(path=str(font_path))
                return

            # 未命中本地字体时用 fallback() 交回下一个 handler（网络策略），
            # 而不是 continue_()（直接发网，绕过网络策略白名单）。
            await route.fallback()

        await page.route("**/*.ttf", handle_font_route)
        await page.route("**/*.otf", handle_font_route)

    async def _setup_network_policy(self, page) -> None:
        """只允许本地页面和模板所需 CDN，避免渲染内容触发任意出站请求。"""

        async def handle_request(route):
            parsed = urlparse(route.request.url)
            if parsed.scheme == "file":
                # 安全边界：只放行当前渲染页面自身的 file URI。
                # 攻击者可通过 Markdown 图片语法构造 file:/// 引用，若全部放行
                # 即可读取服务器本地文件（图片/SVG）并回传给请求者。
                page_uri = self._current_page_uris.get(page)
                if page_uri and route.request.url == page_uri:
                    await route.continue_()
                    return
                logger.warning(
                    "[MathJax2Image] 已阻止渲染页面加载本地文件: %s",
                    route.request.url,
                )
                await route.abort("blockedbyclient")
                return
            if parsed.scheme in {"about", "data", "blob"}:
                await route.continue_()
                return
            if (
                parsed.scheme == "https"
                and parsed.hostname in self._ALLOWED_REMOTE_HOSTS
            ):
                # Routing disables Chromium's HTTP cache. Cache only static GET
                # assets, with a byte budget and TTL, across isolated pages.
                if self._is_cacheable_asset(
                    route.request
                ) and await self._serve_cached_resource(route):
                    return
                # 未命中时由浏览器自己下载（其 DNS/连接回退远快于驱动端
                # 请求），下载完成后由 _capture_response 写入缓存。
                await route.continue_()
                return
            logger.warning(
                "[MathJax2Image] 已阻止渲染页面访问非白名单资源: %s",
                route.request.url,
            )
            await route.abort("blockedbyclient")

        await page.route("**/*", handle_request)
        self._watch_asset_downloads(page.context)

    @classmethod
    def _is_cacheable_asset(cls, request) -> bool:
        """Whether a request is an allowlisted static GET asset worth caching."""
        parsed = urlparse(request.url)
        return (
            request.method == "GET"
            and parsed.scheme == "https"
            and parsed.hostname in cls._ALLOWED_REMOTE_HOSTS
            and not parsed.query
            and parsed.path.endswith(_CACHEABLE_SUFFIXES)
        )

    def _watch_asset_downloads(self, context) -> None:
        """Capture browser-downloaded assets once per browser context.

        Worker requests (TikZ TeX snapshot and package files) belong to the
        context too, so they are captured as well.
        """
        if context in self._watched_contexts:
            return
        try:
            self._watched_contexts.add(context)
            context.on(
                "requestfinished",
                lambda request: asyncio.ensure_future(self._capture_response(request)),
            )
            context.on(
                "requestfailed",
                lambda request: self._release_pending(request.url),
            )
        except Exception as exc:  # pragma: no cover - mocked or closed contexts
            logger.debug("[MathJax2Image] 无法监听资源下载: %s", exc)

    async def _serve_cached_resource(self, route) -> bool:
        """Fulfill a static request from the memory or disk asset cache.

        On a miss the caller lets the browser download the asset; the first
        miss registers a pending download so concurrent requests for the same
        URL wait for it instead of downloading again.

        Args:
            route: An already allowlisted static GET request.

        Returns:
            Whether the request was fulfilled from a cached response.
        """
        if self._cdn_cache_limit == 0:
            return False
        url = route.request.url
        cached = self._lookup_resource(url)
        if cached is None:
            pending = self._cdn_pending.get(url)
            if pending is not None and time.monotonic() - pending[1] < _PENDING_TTL_S:
                try:
                    await asyncio.wait_for(asyncio.shield(pending[0]), _PENDING_TTL_S)
                except asyncio.TimeoutError:
                    pass
                cached = self._lookup_resource(url)
        if cached is None and self._disk_cache is not None:
            hit = await run_in_thread(self._disk_cache.get, url)
            if hit is not None:
                # 重启后从磁盘恢复，免去重新下载（首个资源常需数秒）
                cached = self._store_resource(url, *hit)
        if cached is not None:
            await cached.fulfill(route)
            return True
        pending = self._cdn_pending.get(url)
        if pending is None or time.monotonic() - pending[1] >= _PENDING_TTL_S:
            self._cdn_pending[url] = (
                asyncio.get_running_loop().create_future(),
                time.monotonic(),
            )
        return False

    def _lookup_resource(self, url: str) -> "_CachedResource | None":
        """Return a fresh memory entry and mark it most recently used."""
        cached = self._cdn_cache.pop(url, None)
        if cached is None:
            return None
        if time.monotonic() - cached.created >= _MEMORY_ASSET_TTL_S:
            self._cdn_cache_bytes -= cached.size
            return None
        self._cdn_cache[url] = cached
        return cached

    def _store_resource(
        self, url: str, body: bytes, content_type: str
    ) -> "_CachedResource":
        """Keep a downloaded asset in memory within the byte and entry budgets.

        The TikZ worker is stored patched; the disk copy stays unpatched so a
        plugin update can change the patch.
        """
        if self._optimize_worker and url.endswith(_TIKZ_WORKER_SUFFIX):
            body = optimize_tikz_worker(body)
        resource = _CachedResource(
            body,
            {"content-type": content_type, "access-control-allow-origin": "*"},
            time.monotonic(),
            len(body),
        )
        previous = self._cdn_cache.pop(url, None)
        if previous is not None:
            self._cdn_cache_bytes -= previous.size
        if resource.size > min(self._cdn_cache_limit, _MAX_MEMORY_ASSET_BYTES):
            return resource
        while self._cdn_cache and (
            self._cdn_cache_bytes + resource.size > self._cdn_cache_limit
            or len(self._cdn_cache) >= _MAX_MEMORY_ASSETS
        ):
            _, evicted = self._cdn_cache.popitem(last=False)
            self._cdn_cache_bytes -= evicted.size
        self._cdn_cache[url] = resource
        self._cdn_cache_bytes += resource.size
        return resource

    async def _capture_response(self, request) -> None:
        """Store an asset the browser downloaded after a cache miss."""
        url = request.url
        if url not in self._cdn_pending:
            return
        try:
            response = await request.response()
            if response is None or response.status != 200:
                return
            body = await response.body()
            content_type = response.headers.get(
                "content-type", "application/octet-stream"
            )
            if self._disk_cache is not None:
                await run_in_thread(self._disk_cache.put, url, body, content_type)
            self._store_resource(url, body, content_type)
        except Exception as exc:
            logger.debug("[MathJax2Image] 缓存已下载资源失败: %s: %s", url, exc)
        finally:
            self._release_pending(url)

    def _release_pending(self, url: str) -> None:
        """Wake requests waiting for a browser download of ``url``."""
        pending = self._cdn_pending.pop(url, None)
        if pending is not None and not pending[0].done():
            pending[0].set_result(None)

    def _setup_logging(self, page) -> None:
        """设置页面日志"""
        page.on(
            "console", lambda msg: logger.debug(f"[Browser] {msg.type}: {msg.text}")
        )
        page.on("pageerror", lambda err: logger.error(f"[Browser Error] {err}"))

    async def _load_and_wait(self, page, html_path: Path) -> bool:
        """加载页面并等待渲染完成"""
        html = await asyncio.to_thread(html_path.read_text, encoding="utf-8")
        content = re.search(r'<main class="render-content">([\s\S]*?)</main>', html)
        resident_key = ""
        math_required = None
        if (
            self._resident_engines
            and content
            and "window.__replaceRenderContent =" in html
        ):
            # Global TeX declarations can persist in MathJax. Reload these jobs
            # and never retain their state for the following document.
            stateful_math = re.search(
                r"\\(?:[egx]?def|let|global|(?:re)?newcommand|providecommand|"
                r"DeclareMathOperator|(?:re)?newenvironment|newtheorem)\b",
                content.group(1),
            )
            if not stateful_math:
                shell = html[: content.start(1)] + html[content.end(1) :]
                shell += str('class="mermaid"' in content.group(1))
                # 是否需要公式只影响本次排版，不应让公式/TikZ/纯文本文档
                # 之间切换时重新加载页面；复用时单独传入页面
                declaration = _MATH_REQUIRED_DECLARATION.search(shell)
                if declaration:
                    math_required = declaration.group(1) == "true"
                    shell = shell[: declaration.start()] + shell[declaration.end() :]
                resident_key = hashlib.sha256(shell.encode()).hexdigest()
        reused = False
        if resident_key:
            reused = await page.evaluate(
                "key => window.__residentKey === key", resident_key
            )
        if reused:
            await page.evaluate(
                """([content, required]) => {
                    if (required !== null) window.mathJaxRequired = required;
                    return window.__replaceRenderContent(content);
                }""",
                [content.group(1), math_required],
            )
        else:
            await page.goto(
                html_path.resolve().as_uri(),
                wait_until="domcontentloaded",
                timeout=_GOTO_TIMEOUT_MS,
            )
        await page.evaluate("key => { window.__residentKey = key; }", resident_key)

        # 等待MathJax(仅当页面含公式时,无公式跳过避免白等 CDN 加载)
        try:
            has_math = await page.evaluate(
                """() => {
                    if (typeof window.mathJaxRequired === 'boolean') return window.mathJaxRequired;
                    const text = document.body.innerHTML;
                    return /\\$\\$[\\s\\S]*?\\$\\$|\\$[^\\$\\n]+\\$|\\\\\\([\\s\\S]*?\\\\\\)|\\\\\\[[\\s\\S]*?\\\\\\]/.test(text);
                }"""
            )
        except Exception:
            has_math = True
        complete = True
        if has_math:
            mathjax_wait_timeout = (
                self._mathjax_timeout
                if reused
                else max(self._mathjax_timeout, _GOTO_TIMEOUT_MS)
            )
            try:
                await page.wait_for_function(
                    "() => window.mathJaxReady === true",
                    timeout=mathjax_wait_timeout,
                )
                logger.debug("[MathJax2Image] MathJax 渲染完成")
            except Exception as e:
                complete = False
                logger.warning(f"[MathJax2Image] MathJax 等待超时: {e}")
                if self._fail_on_mathjax_timeout:
                    raise RenderError(f"MathJax 渲染超时: {e}")

        # 等待 Mermaid（若页面含 mermaid 块）
        await self._wait_for_mermaid(page)

        # 检查TikZ
        await self._wait_for_tikz(page)
        if complete and resident_key:
            self._browser_manager._resident_pages.add(page)
        else:
            self._browser_manager._resident_pages.discard(page)
        return complete

    async def _wait_for_mermaid(self, page) -> None:
        """等待 Mermaid 渲染完成（无图则跳过）"""
        try:
            count = await page.evaluate(
                "() => document.querySelectorAll('pre.mermaid').length"
            )
        except Exception:
            return
        if not count:
            return

        logger.info(f"[MathJax2Image] 检测到 {count} 个 Mermaid 图")
        try:
            await page.wait_for_function(
                "() => window.mermaidReady === true || !!window.mermaidError",
                timeout=self._mermaid_timeout,
            )
            error = await page.evaluate("() => window.mermaidError || null")
            if error:
                raise RenderError(f"Mermaid rendering failed: {error}")
            logger.debug("[MathJax2Image] Mermaid rendering complete")
        except Exception as e:
            raise RenderError(f"Mermaid rendering failed or timed out: {e}") from e

    async def _wait_for_tikz(self, page) -> None:
        """等待TikZ渲染完成。

        TikZJax 异步编译：先显示 spinner SVG，完成后派发
        ``tikzjax-load-finished`` 事件。等待"finished 数 >= 容器数"，
        避免把 spinner 误判为完成内容(旧逻辑轮询 SVG 元素，1 秒就
        误判成功，截图拍到 spinner)。
        """
        tikz_count = await page.evaluate(
            "() => document.querySelectorAll('.tikz-diagram').length"
        )

        if tikz_count == 0:
            return

        logger.info(f"[MathJax2Image] 检测到 {tikz_count} 个TikZ图")

        try:
            result = await page.wait_for_function(
                """(count) => {
                    if (window.__tikzFailed) return {failed: window.__tikzFailed};
                    // 编译失败检测: TikZJax 插入 invalid 图片(不依赖注入状态,
                    // 直接查询 DOM,更可靠)
                    const bad = document.querySelectorAll('.tikz-diagram img[src*="invalid.site"], .tikz-diagram img[src*="img-not-found"]');
                    if (bad.length > 0) {
                        return {failed: bad.length};
                    }
                    // 完成判定: TikZJax 渲染的 SVG 是 <svg><g ...>...</g></svg>
                    // (1 个 g 子元素),而 loading spinner 是
                    // <rect fill-opacity="0.2"> + <circle> x2 + <animate>
                    // (3+ 子元素)。用 "有 g 且无 spinner 特征" 判断,
                    // 不能用子元素数量(真实内容可能就是 1 个 g)。
                    const containers = Array.from(document.querySelectorAll('.tikz-diagram'));
                    if (containers.length === 0) return null;
                    let totalElements = 0;
                    for (const container of containers) {
                        const svg = container.querySelector('svg');
                        if (!svg) return null;
                        // spinner 特征: 半透明黑色圆角矩形 + SMIL 动画。改用选择器：
                        // 把整段 SVG 序列化成字符串再查子串的写法，每一轮轮询都要重做
                        // 一次，几十 KB 的图在 60 s 等待里开销很可观。
                        if (svg.querySelector('animate') &&
                            svg.querySelector('[fill-opacity="0.2"]')) return null;
                        // Collapsed SVG paint scopes may leave no group nodes.
                        if (!svg.querySelector('path,line,text,circle,ellipse,rect,polygon,polyline,use')) return null;
                        totalElements += svg.querySelectorAll('path,line,text,circle,ellipse,rect,polygon,polyline,g,use').length;
                    }
                    return {success: true, diagrams: containers.length, count: totalElements};
                }""",
                arg=tikz_count,
                timeout=self._tikz_timeout,
            )
            tikz_result = await result.json_value()
            if tikz_result and tikz_result.get("failed"):
                raise RenderError(f"TikZ渲染失败：{tikz_result['failed']} 个图编译错误")
            if tikz_result and tikz_result.get("success"):
                logger.info(
                    "[MathJax2Image] TikZ渲染完成，"
                    f"图数: {tikz_result.get('diagrams', 0)}, "
                    f"元素数: {tikz_result.get('count', 0)}"
                )
            else:
                raise RenderError("TikZ渲染失败：未收到完成事件")

        except RenderError:
            raise
        except Exception as e:
            logger.error(f"[MathJax2Image] TikZ渲染失败或超时: {e}")
            raise RenderError(f"TikZ渲染失败或超时: {e}")

        # 截图前已 await document.fonts.ready(_take_screenshot 内),
        # 无需额外固定 sleep(每图省约 150ms)

    async def _take_screenshot(self, page, output: Path) -> None:
        """截取页面截图"""
        # Measure only after fonts and images settle. A small viewport removes
        # the initial 2000px minimum from short documents without changing width.
        if page.viewport_size != {"width": self._viewport_width, "height": 1}:
            await page.set_viewport_size({"width": self._viewport_width, "height": 1})
        await page.evaluate("""async () => {
            // Change layout dimensions instead of a transform that overlaps text.
            for (const svg of document.querySelectorAll('.tikz-diagram svg')) {
                const rect = svg.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    const factor = Math.min(2, svg.parentElement.clientWidth / rect.width);
                    svg.style.width = (rect.width * factor) + 'px';
                    svg.style.height = (rect.height * factor) + 'px';
                }
            }
            await document.fonts.ready;
            await Promise.all(Array.from(document.images, image =>
                image.decode().catch(() => {})));
        }""")
        metrics = await page.evaluate(
            """() => ({
                width: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
                height: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
            })"""
        )
        width = max(1, int(metrics["width"]))
        height = max(1, int(metrics["height"]))
        pixels = width * height

        if height > self._max_screenshot_height:
            raise RenderError(
                f"页面高度 {height}px 超过限制 {self._max_screenshot_height}px"
            )
        if pixels > self._max_screenshot_pixels:
            raise RenderError(
                f"截图像素 {pixels} 超过限制 {self._max_screenshot_pixels}"
            )

        logger.info(f"[MathJax2Image] 截图中，尺寸: {width}x{height}px，像素: {pixels}")
        await page.screenshot(
            path=str(output), full_page=True, timeout=self._screenshot_timeout
        )
        # 校验截图非空：WebKit 等引擎可能产生 0 字节截图
        if not output.exists() or output.stat().st_size == 0:
            raise RenderError("截图为空或文件未生成")
        logger.info(f"[MathJax2Image] 截图已保存: {output}")
