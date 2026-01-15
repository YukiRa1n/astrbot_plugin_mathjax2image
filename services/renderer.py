"""
MathJax 渲染器模块
支持浏览器实例复用和并发安全
"""
import asyncio
import re
import tempfile
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
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
                logger.info("浏览器实例已创建")
            return self._browser

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

            # 检测代码块边界（``` 或 ~~~）
            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_code_block = not in_code_block
                result.append(line)
                continue

            # 代码块内部不做任何处理
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

            # 检查是否是列表项（无序: - 或 *，有序: 1. 2. 等）
            is_unordered = bool(re.match(r'^[-*]\s+', stripped))
            is_ordered = bool(re.match(r'^\d+\.\s+', stripped))
            is_list_item = is_unordered or is_ordered

            # 如果是标题或列表项，且前一行不是空行，则添加空行
            if (is_heading or is_list_item) and result:
                prev_line = result[-1].strip()
                prev_is_unordered = bool(re.match(r'^[-*]\s+', prev_line))
                prev_is_ordered = bool(re.match(r'^\d+\.\s+', prev_line))
                prev_is_list = prev_is_unordered or prev_is_ordered
                # 标题前必须空行，列表项前如果不是列表也要空行
                if prev_line and (is_heading or not prev_is_list):
                    result.append('')

            result.append(line)

        return '\n'.join(result)

    def _convert_markdown_to_html(self, md_text: str) -> str:
        """将 Markdown 转换为完整 HTML"""
        # 预处理Markdown，修复格式问题
        md_text = self._preprocess_markdown(md_text)

        html_body = markdown.markdown(
            md_text,
            extensions=['fenced_code', 'tables']
        )

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

        return full_html

    async def render(self, content: str) -> Path:
        """渲染内容为图片，返回图片路径"""
        html_content = self._convert_markdown_to_html(content)
        logger.info("Markdown 转 HTML 完成")

        # 使用唯一文件名避免并发冲突
        output_dir = StarTools.get_data_dir('astrbot_plugin_mathjax2image')
        output_path = output_dir / f"render_{uuid.uuid4().hex[:8]}.png"

        await self._render_html_to_image(html_content, output_path)
        return output_path

    async def _render_html_to_image(self, html: str, output: Path) -> None:
        """使用 Playwright 渲染 HTML 并截图"""
        # 创建临时 HTML 文件
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False, encoding='utf-8'
        ) as tmp:
            tmp.write(html)
            tmp_path = tmp.name

        try:
            browser = await self._get_browser()
            page = await browser.new_page(viewport={'width': 1150, 'height': 2000})

            try:
                await page.goto(
                    f"file://{tmp_path}",
                    wait_until='domcontentloaded',
                    timeout=60000
                )

                # 等待 MathJax 渲染完成
                try:
                    await page.wait_for_function(
                        "() => window.mathJaxReady === true",
                        timeout=10000
                    )
                    logger.info("MathJax 渲染完成")
                except Exception as e:
                    logger.warning(f"MathJax 等待超时: {e}")

                # 调整视口高度并截图
                height = await page.evaluate("document.body.scrollHeight")
                await page.set_viewport_size({'width': 1150, 'height': height})
                await page.screenshot(
                    path=str(output),
                    full_page=True,
                    scale='device',
                    timeout=60000
                )
                logger.info(f"截图已保存: {output}")

            finally:
                await page.close()

        finally:
            Path(tmp_path).unlink(missing_ok=True)
