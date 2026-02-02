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

    def _fix_tikz_comments(self, text: str) -> str:
        """修复 TikZ 代码中注释与 \end{tikzpicture} 同行导致的渲染失败

        问题：当注释与 \end{tikzpicture} 在同一行时，TikZJax 可能无法正确解析结束标记
        解决：强制在 \end{tikzpicture} 前添加换行符
        """
        # 处理 \end{tikzpicture} 前有注释的情况
        # 例如：% 注释\end{tikzpicture} -> % 注释\n\end{tikzpicture}
        text = re.sub(
            r'(%[^\n]*?)\\end\{tikzpicture\}',
            r'\1\n\\end{tikzpicture}',
            text
        )
        # 处理 \end{tikzcd} 的情况
        text = re.sub(
            r'(%[^\n]*?)\\end\{tikzcd\}',
            r'\1\n\\end{tikzcd}',
            text
        )
        return text

    def _preprocess_markdown(self, text: str) -> str:
        """预处理Markdown，自动修复常见格式问题"""
        # 处理转义字符：将字面的\n转换为真实换行符
        text = text.replace('\\n', '\n')
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
        md_text = self._fix_tikz_comments(md_text)
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

        # 先提取代码块和数学公式
        md_text = re.sub(r'```[\s\S]*?```', substitute_code, md_text)
        md_text = re.sub(r'\\\[[\s\S]*?\\\]', substitute_math, md_text)
        md_text = re.sub(r'\\\([\s\S]*?\\\)', substitute_math, md_text)
        md_text = re.sub(r'\$\$.*?\$\$', substitute_math, md_text, flags=re.DOTALL)
        md_text = re.sub(r'\$.*?\$', substitute_math, md_text)

        # Markdown 转换
        html_body = markdown.markdown(
            md_text,
            extensions=['fenced_code', 'tables', 'nl2br']
        )

        # 替换回数学公式
        for i, block in enumerate(math_blocks):
            placeholder = f"MATHBLOCK{i}MATHBLOCK"
            html_body = html_body.replace(placeholder, block)

        # 替换回代码块（手动转换为 HTML）
        for i, block in enumerate(code_blocks):
            placeholder = f"CODEBLOCK{i}CODEBLOCK"
            # 去掉前后的 ```
            content = block.strip('`')

            # 尝试解析语言标识：```language\ncode 或 ```code
            if '\n' in content:
                # 多行格式：language\ncode
                parts = content.split('\n', 1)
                language = parts[0].strip()
                code_content = parts[1] if len(parts) > 1 else ''
            else:
                # 单行格式：直接是代码内容
                language = ''
                code_content = content

            # 生成 HTML
            lang_class = f' class="language-{language}"' if language else ''
            code_html = f'<pre><code{lang_class}>{code_content}</code></pre>'
            html_body = html_body.replace(placeholder, code_html)

        template_path = self._plugin_dir / "templates" / "template.html"
        static_dir = self._plugin_dir / "static"

        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        full_html = html_template.replace("{{CONTENT}}", html_body)
        full_html = full_html.replace("--bg-color: #FDFBF0;", f"--bg-color: {self._bg_color};")

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
                    # 根据文件扩展名设置 content_type
                    content_type = 'font/otf' if font_path.suffix == '.otf' else 'font/ttf'
                    await route.fulfill(
                        body=font_data,
                        content_type=content_type
                    )
                    return

                await route.continue_()

            await page.route("**/*.ttf", handle_font_route)
            await page.route("**/*.otf", handle_font_route)
            logger.info("[MathJax2Image] 字体路由已设置（ttf + otf）")

            # 监听所有网络请求，记录失败的请求
            page.on("request", lambda req: logger.debug(f"[Request] {req.url}"))
            page.on("response", lambda resp: (
                logger.error(f"[Failed Response] {resp.status} {resp.url}") if resp.status >= 400 else None
            ))

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
                        # TikZJax 编译需要时间，必须等到有实际内容才算成功
                        try:
                            result = await page.wait_for_function(
                                """() => {
                                    const container = document.querySelector('.tikz-diagram');
                                    if (!container) return null;

                                    const svg = container.querySelector('svg');
                                    if (!svg) return null;

                                    // 只检查真正的图形内容元素（排除加载占位符）
                                    const paths = svg.querySelectorAll('path').length;
                                    const lines = svg.querySelectorAll('line').length;
                                    const texts = svg.querySelectorAll('text').length;
                                    const polygons = svg.querySelectorAll('polygon').length;
                                    const polylines = svg.querySelectorAll('polyline').length;

                                    const totalElements = paths + lines + texts + polygons + polylines;

                                    // 至少要有 1 个实际图形元素（不包括 rect 和 circle，它们可能是占位符）
                                    if (totalElements >= 1) {
                                        return {
                                            success: true,
                                            count: totalElements,
                                            details: { paths, lines, texts, polygons, polylines }
                                        };
                                    }

                                    // 检查 script 标签是否还在（还在编译中）
                                    const script = container.querySelector('script[type="text/tikz"]');
                                    if (script) {
                                        return null; // 继续等待
                                    }

                                    // script 已移除但没有内容，可能编译失败
                                    return null;
                                }""",
                                timeout=300000  # 300 秒超时（5分钟）
                            )
                            tikz_result = await result.json_value()
                            if tikz_result and tikz_result.get('success'):
                                logger.info(f"[MathJax2Image] TikZ SVG 渲染完成，元素数: {tikz_result.get('count', 0)}")
                                logger.info(f"[MathJax2Image] 元素详情: {tikz_result.get('details', {})}")
                            else:
                                logger.error("[MathJax2Image] TikZ 渲染结果异常，没有生成有效内容")
                                raise Exception("TikZ 渲染失败：SVG 中没有实际图形元素（可能是绘图太复杂或使用了不支持的特性）")
                        except Exception as e:
                            logger.error(f"[MathJax2Image] 等待 TikZ 渲染超时或失败: {e}")
                            # 检查是否真的有内容
                            svg_check = await page.evaluate('''
                                () => {
                                    const svg = document.querySelector('.tikz-diagram svg');
                                    if (!svg) return { hasContent: false, reason: 'No SVG found' };
                                    const paths = svg.querySelectorAll('path').length;
                                    const lines = svg.querySelectorAll('line').length;
                                    const texts = svg.querySelectorAll('text').length;
                                    const total = paths + lines + texts;
                                    return { hasContent: total > 0, total, paths, lines, texts };
                                }
                            ''')
                            logger.error(f"[MathJax2Image] SVG 内容检查: {svg_check}")
                            if not svg_check.get('hasContent'):
                                error_msg = (
                                    f"TikZ 渲染失败：{svg_check.get('reason', 'SVG 为空')}。\n"
                                    f"可能原因：\n"
                                    f"1. 绘图太复杂（建议降低 samples 值，如 samples=15）\n"
                                    f"2. 使用了 TikZJax 不支持的特性（如复杂的 3D 图形、某些 pgfplots 选项）\n"
                                    f"3. TikZ 代码中包含中文字符（TikZJax 不支持）\n"
                                    f"4. 代码语法错误"
                                )
                                raise Exception(error_msg)

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
