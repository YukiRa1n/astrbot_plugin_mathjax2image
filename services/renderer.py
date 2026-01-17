"""
MathJax 渲染器模块
支持浏览器实例复用和并发安全
"""
import asyncio
import re
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Optional

import markdown
from playwright.async_api import async_playwright, Browser, Playwright
from astrbot.api import logger
from astrbot.api.star import StarTools


class MathJaxRenderer:
    """MathJax 渲染器，支持浏览器复用"""

    def __init__(self, bg_color: str = "#FDFBF0"):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._plugin_dir = Path(__file__).resolve().parent.parent
        self._bg_color = bg_color

    async def _get_browser(self) -> Browser:
        """获取或创建浏览器实例（线程安全）"""
        async with self._lock:
            try:
                if self._browser is None or not self._browser.is_connected():
                    logger.info("[MathJax2Image] 正在启动浏览器...")
                    if self._playwright is None:
                        self._playwright = await async_playwright().start()
                        logger.debug("[MathJax2Image] Playwright 已启动")
                    # 启动浏览器时添加参数以支持本地文件访问和 WebAssembly
                    self._browser = await self._playwright.chromium.launch(
                        headless=True,
                        args=[
                            '--disable-web-security',
                            '--allow-file-access-from-files',
                            '--disable-features=VizDisplayCompositor'
                        ]
                    )
                    logger.info("[MathJax2Image] 浏览器实例已创建")
                return self._browser
            except Exception as e:
                logger.error(f"[MathJax2Image] 浏览器启动失败: {type(e).__name__}: {e}")
                logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
                raise

    async def close(self) -> None:
        """关闭浏览器和 Playwright"""
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            logger.info("渲染器资源已释放")

    def _preprocess_markdown(self, text: str) -> str:
        """预处理Markdown，自动修复常见格式问题"""
        lines = text.split('\n')
        result = []
        in_code_block = False

        for i, line in enumerate(lines):
            stripped = line.strip()

            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_code_block = not in_code_block
                result.append(line)
                continue

            if in_code_block:
                result.append(line)
                continue

            heading_match = re.match(r'^(#{1,6})([^#\s])', stripped)
            if heading_match:
                stripped = heading_match.group(1) + ' ' + stripped[len(heading_match.group(1)):]
                line = stripped

            is_heading = bool(re.match(r'^#{1,6}\s+', stripped))
            is_unordered = bool(re.match(r'^[-*]\s+', stripped))
            is_ordered = bool(re.match(r'^\d+\.\s+', stripped))
            is_list_item = is_unordered or is_ordered

            if (is_heading or is_list_item) and result:
                prev_line = result[-1].strip()
                prev_is_unordered = bool(re.match(r'^[-*]\s+', prev_line))
                prev_is_ordered = bool(re.match(r'^\d+\.\s+', prev_line))
                prev_is_list = prev_is_unordered or prev_is_ordered
                if prev_line and (is_heading or not prev_is_list):
                    result.append('')

            result.append(line)

        return '\n'.join(result)

    def _convert_markdown_to_html(self, md_text: str) -> str:
        """将 Markdown 转换为完整 HTML"""
        md_text = self._preprocess_markdown(md_text)

        math_blocks = []
        def substitute_math(match):
            placeholder = f"MATHBLOCK{len(math_blocks)}MATHBLOCK"
            math_blocks.append(match.group(0))
            return placeholder

        code_blocks = []
        def substitute_code(match):
            placeholder = f"CODEBLOCK{len(code_blocks)}CODEBLOCK"
            code_blocks.append(match.group(0))
            return placeholder
        md_text = re.sub(r'```[\s\S]*?```', substitute_code, md_text)

        md_text = re.sub(r'\\\[[\s\S]*?\\\]', substitute_math, md_text)
        md_text = re.sub(r'\\\([\s\S]*?\\\)', substitute_math, md_text)
        md_text = re.sub(r'\$\$.*?\$\$', substitute_math, md_text, flags=re.DOTALL)
        md_text = re.sub(r'\$.*?\$', substitute_math, md_text)

        html_body = markdown.markdown(
            md_text,
            extensions=['fenced_code', 'tables', 'nl2br']
        )

        for i, block in enumerate(math_blocks):
            placeholder = f"MATHBLOCK{i}MATHBLOCK"
            html_body = html_body.replace(placeholder, block)

        for i, block in enumerate(code_blocks):
            placeholder = f"CODEBLOCK{i}CODEBLOCK"
            html_body = html_body.replace(placeholder, block)

        template_path = self._plugin_dir / "templates" / "template.html"
        static_dir = self._plugin_dir / "static"

        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        full_html = html_template.replace("{{CONTENT}}", html_body)
        full_html = full_html.replace("--bg-color: #FDFBF0;", f"--bg-color: {self._bg_color};")

        tikzjax_dir = static_dir / "tikzjax"
        tikzjax_js_path = tikzjax_dir / "tikzjax.js"

        if tikzjax_js_path.exists():
            with open(tikzjax_js_path, 'r', encoding='utf-8') as f:
                tikzjax_js_content = f.read()
            full_html = full_html.replace(
                '<script src="../static/tikzjax/tikzjax.js"></script>',
                f'<script>\n{tikzjax_js_content}\n</script>'
            )

        return full_html

    async def render(self, content: str) -> Path:
        """渲染内容为图片，返回图片路径"""
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {len(content)}")

        try:
            html_content = self._convert_markdown_to_html(content)
            logger.debug("[MathJax2Image] Markdown 转 HTML 完成")

            has_tikz_script = '<script type="text/tikz">' in html_content
            logger.info(f"[MathJax2Image] HTML 中包含 TikZ script: {has_tikz_script}")

            output_dir = StarTools.get_data_dir('astrbot_plugin_mathjax2image')
            output_path = output_dir / f"render_{uuid.uuid4().hex[:8]}.png"

            await self._render_html_to_image(html_content, output_path)
            return output_path

        except Exception as e:
            logger.error(f"[MathJax2Image] render 失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise

    async def _render_html_to_image(self, html: str, output: Path) -> None:
        """使用 Playwright 渲染 HTML 并截图"""
        temp_dir = self._plugin_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
        tmp_path = temp_dir / f"temp_{uuid.uuid4().hex[:8]}.html"

        logger.info(f"[MathJax2Image] HTML 临时文件: {tmp_path}")

        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(html)

        # 新版 tikzjax.js 是自包含的，不需要外部 wasm/gz 文件
        # 只需要 SVG 样式修复脚本
        inject_script = """
        // 修复 TikZ SVG 显示问题
        setInterval(function() {
            document.querySelectorAll('.tikz-diagram svg').forEach(function(svg) {
                svg.style.position = 'relative';
                svg.style.display = 'block';
                svg.style.margin = '20px auto';
                svg.style.border = 'none';
                svg.style.padding = '0';
            });
        }, 500);
        """

        try:
            logger.debug("[MathJax2Image] 获取浏览器实例...")
            browser = await self._get_browser()

            logger.debug("[MathJax2Image] 创建新页面...")
            page = await browser.new_page(viewport={'width': 1150, 'height': 2000})

            await page.add_init_script(inject_script)
            logger.info("[MathJax2Image] SVG 样式修复脚本已注入")

            # 添加字体文件路由拦截
            static_dir = self._plugin_dir / "static"

            async def handle_font_route(route):
                url = route.request.url
                logger.info(f"[MathJax2Image] 字体请求: {url}")

                font_path = None
                # 处理 bakoma 字体请求
                if '/bakoma/ttf/' in url:
                    font_name = url.split('/bakoma/ttf/')[-1]
                    font_path = static_dir / "bakoma" / "ttf" / font_name
                # 处理 fonts 目录字体请求 (EBGaramond, FiraCode 等)
                elif '/fonts/' in url:
                    font_name = url.split('/fonts/')[-1]
                    font_path = static_dir / "fonts" / font_name

                if font_path and font_path.exists():
                    logger.info(f"[MathJax2Image] 字体路径: {font_path}, 存在: True")
                    with open(font_path, 'rb') as f:
                        font_data = f.read()
                    logger.info(f"[MathJax2Image] 字体大小: {len(font_data)} bytes")
                    await route.fulfill(
                        body=font_data,
                        content_type='font/ttf'
                    )
                    return

                await route.continue_()

            await page.route("**/*.ttf", handle_font_route)
            logger.info("[MathJax2Image] 字体路由已设置")

            page.on("console", lambda msg: logger.info(f"[Browser Console] {msg.type}: {msg.text}"))
            page.on("pageerror", lambda err: logger.error(f"[Browser Error] {err}"))

            try:
                logger.debug(f"[MathJax2Image] 加载 HTML: {tmp_path}")
                await page.goto(
                    f"file://{tmp_path}",
                    wait_until='domcontentloaded',
                    timeout=60000
                )

                try:
                    await page.wait_for_function(
                        "() => window.mathJaxReady === true",
                        timeout=10000
                    )
                    logger.debug("[MathJax2Image] MathJax 渲染完成")
                except Exception as e:
                    logger.warning(f"[MathJax2Image] MathJax 等待超时: {e}")

                # 检查 TikZ 渲染状态
                try:
                    # 检测 .tikz-diagram 容器（我们生成的）
                    tikz_container_count = await page.evaluate(
                        "() => document.querySelectorAll('.tikz-diagram').length"
                    )
                    logger.info(f"[MathJax2Image] TikZ 容器数量: {tikz_container_count}")

                    if tikz_container_count > 0:
                        logger.info("[MathJax2Image] 等待 TikZ 渲染...")

                        # 等待 TikZ 渲染完成（成功生成 SVG 或编译失败）
                        # TikZJax 编译需要时间，不能在 script 移除后立即判断失败
                        try:
                            result = await page.wait_for_function(
                                """() => {
                                    const container = document.querySelector('.tikz-diagram');
                                    if (!container) return null;

                                    // 检查是否有 SVG 且有实际内容（至少5个元素才算真正渲染完成）
                                    const svg = container.querySelector('svg');
                                    if (svg) {
                                        const elements = svg.querySelectorAll('path, line, rect, text, circle, polygon, polyline');
                                        if (elements.length >= 5) return { success: true, count: elements.length };
                                    }

                                    // 检查 script 标签状态
                                    const script = container.querySelector('script[type="text/tikz"]');
                                    if (script) {
                                        return null; // script 还在，继续等待
                                    }

                                    // script 已移除，等待一小段时间让编译完成
                                    // 通过检查是否有任何子元素来判断
                                    if (svg && svg.children.length > 0) {
                                        return { success: true, count: svg.children.length };
                                    }

                                    // 没有 SVG 或 SVG 为空，可能编译失败
                                    return null;
                                }""",
                                timeout=45000  # 增加超时时间
                            )
                            tikz_result = await result.json_value()
                            if tikz_result and tikz_result.get('success'):
                                logger.info(f"[MathJax2Image] TikZ SVG 渲染完成，元素数: {tikz_result.get('count', 0)}")
                            else:
                                logger.warning("[MathJax2Image] TikZ 渲染结果异常")
                        except Exception as e:
                            logger.warning(f"[MathJax2Image] 等待 TikZ 渲染超时: {e}")

                        # 额外等待确保字体加载完成
                        await asyncio.sleep(2)

                        # 调试信息
                        svg_info = await page.evaluate('''
                            () => {
                                const svg = document.querySelector('.tikz-diagram svg');
                                if (!svg) return 'No SVG in .tikz-diagram';
                                const paths = svg.querySelectorAll('path').length;
                                const texts = svg.querySelectorAll('text').length;
                                const lines = svg.querySelectorAll('line').length;
                                return `SVG found: ${paths} paths, ${texts} texts, ${lines} lines`;
                            }
                        ''')
                        logger.info(f"[MathJax2Image] TikZ SVG 信息: {svg_info}")
                except Exception as e:
                    logger.warning(f"[MathJax2Image] TikZJax 检查异常: {e}")

                height = await page.evaluate("document.body.scrollHeight")
                await page.set_viewport_size({'width': 1150, 'height': height})

                logger.info(f"[MathJax2Image] 截图中，高度: {height}px")
                await page.screenshot(
                    path=str(output),
                    full_page=True,
                    scale='device',
                    timeout=60000
                )
                logger.info(f"[MathJax2Image] 截图已保存: {output}")

            finally:
                await page.close()

        except Exception as e:
            logger.error(f"[MathJax2Image] 截图失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise

        finally:
            Path(tmp_path).unlink(missing_ok=True)
