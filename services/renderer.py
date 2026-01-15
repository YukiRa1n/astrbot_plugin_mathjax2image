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
                    self._browser = await self._playwright.chromium.launch(headless=True)
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

            # 检测代码块边界
            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_code_block = not in_code_block
                result.append(line)
                continue

            if in_code_block:
                result.append(line)
                continue

            # 修复标题语法：##标题 -> ## 标题
            heading_match = re.match(r'^(#{1,6})([^#\s])', stripped)
            if heading_match:
                stripped = heading_match.group(1) + ' ' + stripped[len(heading_match.group(1)):]
                line = stripped

            # 检查是否是标题
            is_heading = bool(re.match(r'^#{1,6}\s+', stripped))

            # 检查是否是列表项
            is_unordered = bool(re.match(r'^[-*]\s+', stripped))
            is_ordered = bool(re.match(r'^\d+\.\s+', stripped))
            is_list_item = is_unordered or is_ordered

            # 如果是标题或列表项，且前一行不是空行，则添加空行
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
        # 预处理Markdown
        md_text = self._preprocess_markdown(md_text)

        # 保护数学公式，防止被 Markdown 渲染器解析（如 _ 变斜体）
        math_blocks = []
        def substitute_math(match):
            placeholder = f"MATHBLOCK{len(math_blocks)}MATHBLOCK"
            math_blocks.append(match.group(0))
            return placeholder

        # 1. 先保护代码块，避免代码块内的 $ 被误识别
        code_blocks = []
        def substitute_code(match):
            placeholder = f"CODEBLOCK{len(code_blocks)}CODEBLOCK"
            code_blocks.append(match.group(0))
            return placeholder
        md_text = re.sub(r'```[\s\S]*?```', substitute_code, md_text)

        # 2. 保护数学公式
        # 匹配 $$...$$ (多行) - 必须先匹配多行，再匹配单行
        md_text = re.sub(r'\$\$.*?\$\$', substitute_math, md_text, flags=re.DOTALL)
        # 匹配 $...$ (行内)
        md_text = re.sub(r'\$.*?\$', substitute_math, md_text)

        html_body = markdown.markdown(
            md_text,
            extensions=['fenced_code', 'tables', 'nl2br']
        )

        # 3. 还原数学公式
        for i, block in enumerate(math_blocks):
            placeholder = f"MATHBLOCK{i}MATHBLOCK"
            html_body = html_body.replace(placeholder, block)

        # 4. 还原代码块
        for i, block in enumerate(code_blocks):
            placeholder = f"CODEBLOCK{i}CODEBLOCK"
            html_body = html_body.replace(placeholder, block)

        template_path = self._plugin_dir / "templates" / "template.html"
        static_dir = self._plugin_dir / "static"

        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        full_html = html_template.replace("{{CONTENT}}", html_body)

        # 替换背景颜色
        full_html = full_html.replace("--bg-color: #FDFBF0;", f"--bg-color: {self._bg_color};")

        # 替换所有静态资源路径为绝对路径
        mathjax_url = (static_dir / "mathjax" / "tex-chtml.js").as_uri()
        full_html = full_html.replace("../static/mathjax/tex-chtml.js", mathjax_url)

        # 为 MathJax 动态加载组件提供本地路径前缀
        # 注意：MathJax 内部通过 relative_path 查找，这里需要确保 page 加载的是正确的 file:// 路径
        mathjax_base_url = (static_dir / "mathjax").as_uri()
        if not mathjax_base_url.endswith('/'):
            mathjax_base_url += '/'

        # 显式配置 MathJax 路径映射
        full_html = full_html.replace(
            "paths: {mathjax: '../static/mathjax'}",
            f"paths: {{mathjax: '{mathjax_base_url}'}}"
        )

        # 替换字体路径
        fonts_dir = static_dir / "fonts"
        full_html = full_html.replace(
            "../static/fonts/EBGaramond-Regular.ttf",
            (fonts_dir / "EBGaramond-Regular.ttf").as_uri()
        )
        full_html = full_html.replace(
            "../static/fonts/EBGaramond-SemiBold.ttf",
            (fonts_dir / "EBGaramond-SemiBold.ttf").as_uri()
        )
        full_html = full_html.replace(
            "../static/fonts/FiraCode-Regular.ttf",
            (fonts_dir / "FiraCode-Regular.ttf").as_uri()
        )
        full_html = full_html.replace(
            "../static/fonts/NotoSansSC-Regular.otf",
            (fonts_dir / "NotoSansSC-Regular.otf").as_uri()
        )

        return full_html

    async def render(self, content: str) -> Path:
        """渲染内容为图片，返回图片路径"""
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {len(content)}")

        try:
            html_content = self._convert_markdown_to_html(content)
            logger.debug("[MathJax2Image] Markdown 转 HTML 完成")

            output_dir = StarTools.get_data_dir('astrbot_plugin_mathjax2image')
            output_path = output_dir / f"render_{uuid.uuid4().hex[:8]}.png"
            logger.debug(f"[MathJax2Image] 输出路径: {output_path}")

            await self._render_html_to_image(html_content, output_path)
            return output_path

        except Exception as e:
            logger.error(f"[MathJax2Image] render 失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            raise

    async def _render_html_to_image(self, html: str, output: Path) -> None:
        """使用 Playwright 渲染 HTML 并截图"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False, encoding='utf-8'
        ) as tmp:
            tmp.write(html)
            tmp_path = tmp.name

        try:
            logger.debug("[MathJax2Image] 获取浏览器实例...")
            browser = await self._get_browser()

            logger.debug("[MathJax2Image] 创建新页面...")
            page = await browser.new_page(viewport={'width': 1150, 'height': 2000})

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

                height = await page.evaluate("document.body.scrollHeight")
                await page.set_viewport_size({'width': 1150, 'height': height})

                logger.debug(f"[MathJax2Image] 截图中，高度: {height}px")
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
