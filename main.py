"""
AstrBot MathJax2Image 插件
将 Markdown/MathJax 内容渲染为图片
"""
import re
import traceback
from pathlib import Path
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig

from .services.renderer import MathJaxRenderer


@register(
    "astrbot_plugin_mathjax2image",
    "Willixrain",
    "调用 LLM 生成支持 MathJax 渲染的文章图片",
    "2.0.0"
)
class MathJax2ImagePlugin(Star):
    """MathJax 转图片插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 加载背景颜色配置
        self.bg_color = config.get("background_color", "#FDFBF0")
        self.renderer = MathJaxRenderer(bg_color=self.bg_color)

        # 加载提示词配置（默认值与 _conf_schema.json 保持一致）
        self.math_prompt = config.get("math_system_prompt", "")
        self.article_prompt = config.get("article_system_prompt", "")

        # 确保 MathJax 已安装
        self._ensure_mathjax_installed()

    def _ensure_mathjax_installed(self) -> None:
        """检查 MathJax 是否已安装"""
        plugin_dir = Path(__file__).resolve().parent
        mathjax_file = plugin_dir / "static" / "mathjax" / "tex-chtml.js"

        if mathjax_file.exists():
            logger.info(f"MathJax 已安装: {mathjax_file}")
        else:
            logger.error("MathJax 未安装，请确保 static/mathjax/tex-chtml.js 存在")

    async def _call_llm(
        self,
        user_input: str,
        system_prompt: str
    ) -> Optional[str]:
        """统一的 LLM 调用方法"""
        logger.info(f"[MathJax2Image] 开始调用 LLM，主题: {user_input[:50]}...")

        try:
            provider = self.context.get_using_provider()
            if provider is None:
                logger.error("[MathJax2Image] LLM provider 未配置或不可用")
                return None

            logger.debug(f"[MathJax2Image] 使用 provider: {type(provider).__name__}")

            contexts = [{"role": "user", "content": user_input}]
            response = await provider.text_chat(
                system_prompt=system_prompt,
                prompt="以下是文章围绕的话题",
                contexts=contexts,
            )

            if response is None:
                logger.error("[MathJax2Image] LLM 返回空响应")
                return None

            if not response.completion_text:
                logger.error("[MathJax2Image] LLM 返回内容为空")
                return None

            logger.info(f"[MathJax2Image] LLM 调用成功，响应长度: {len(response.completion_text)}")
            return self._filter_think_tags(response.completion_text)

        except Exception as e:
            logger.error(f"[MathJax2Image] LLM 调用失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            return None

    def _filter_think_tags(self, text: Optional[str]) -> Optional[str]:
        """过滤 LLM 响应中的 <think> 标签"""
        if not text:
            return None
        return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL)

    async def _render_and_send(
        self,
        event: AstrMessageEvent,
        content: str
    ):
        """渲染内容并发送图片"""
        logger.info(f"[MathJax2Image] 开始渲染，内容长度: {len(content)}")

        try:
            image_path = await self.renderer.render(content)

            if not image_path.exists():
                logger.error(f"[MathJax2Image] 图片文件不存在: {image_path}")
                yield event.plain_result("图片生成失败，请检查日志。")
                return

            logger.info(f"[MathJax2Image] 图片生成成功: {image_path}")
            chain = [Comp.Image.fromFileSystem(str(image_path))]
            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"[MathJax2Image] 渲染失败: {type(e).__name__}: {e}")
            logger.error(f"[MathJax2Image] 堆栈信息:\n{traceback.format_exc()}")
            yield event.plain_result(f"渲染失败: {e}")

    def _extract_command_content(self, event: AstrMessageEvent, cmd_name: str) -> str:
        """从完整消息中提取命令后的内容（避免空格截断问题）"""
        full_msg = event.get_message_str()
        content = ""

        # 支持 /cmd 和 cmd 两种格式
        for prefix in [f"/{cmd_name} ", f"{cmd_name} "]:
            if prefix in full_msg:
                content = full_msg.split(prefix, 1)[1]
                break

        return content.strip()

    @filter.command("math")
    async def cmd_math_article(self, event: AstrMessageEvent, content: str = ""):
        """生成数学文章并渲染为图片"""
        # 从完整消息中提取内容
        math_content = self._extract_command_content(event, "math")

        if not math_content:
            yield event.plain_result("请提供文章主题，例如: /math 勾股定理")
            return

        yield event.plain_result("正在生成数学文章...")
        logger.info(f"[MathJax2Image] /math 内容长度: {len(math_content)}")

        llm_result = await self._call_llm(math_content, self.math_prompt)
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        # 预处理 LaTeX/TikZ 内容
        processed = self._preprocess_latex_text(llm_result)

        async for result in self._render_and_send(event, processed):
            yield result

    @filter.command("art")
    async def cmd_article(self, event: AstrMessageEvent, content: str = ""):
        """生成普通文章并渲染为图片"""
        # 从完整消息中提取内容
        art_content = self._extract_command_content(event, "art")

        if not art_content:
            yield event.plain_result("请提供文章主题，例如: /art 人工智能")
            return

        logger.info(f"[MathJax2Image] /art 内容长度: {len(art_content)}")

        llm_result = await self._call_llm(art_content, self.article_prompt)
        if not llm_result:
            yield event.plain_result("文章生成失败")
            return

        # 预处理 LaTeX/TikZ 内容
        processed = self._preprocess_latex_text(llm_result)

        async for result in self._render_and_send(event, processed):
            yield result

    @filter.command("render")
    async def cmd_render_direct(self, event: AstrMessageEvent, content: str = ""):
        """直接渲染 Markdown/MathJax 内容为图片"""
        # 从完整消息中提取内容
        render_content = self._extract_command_content(event, "render")

        if not render_content:
            yield event.plain_result("请提供要渲染的内容，例如: /render $E=mc^2$")
            return

        logger.info(f"[MathJax2Image] /render 内容长度: {len(render_content)}")

        # 预处理：将 LaTeX 文本命令转换为 Markdown
        processed = self._preprocess_latex_text(render_content)

        async for result in self._render_and_send(event, processed):
            yield result

    def _preprocess_latex_text(self, text: str) -> str:
        """将 LaTeX 文本命令转换为 Markdown 格式"""
        # \textbf{...} -> **...** (支持跨行)
        text = re.sub(r'\\textbf\{([\s\S]*?)\}', lambda m: f"**{m.group(1)}**", text)
        # \textit{...} -> *...* (支持跨行)
        text = re.sub(r'\\textit\{([\s\S]*?)\}', lambda m: f"*{m.group(1)}*", text)
        # \emph{...} -> *...* (支持跨行)
        text = re.sub(r'\\emph\{([\s\S]*?)\}', lambda m: f"*{m.group(1)}*", text)
        # \{ -> \lbrace, \} -> \rbrace (修复MathJax大括号渲染问题)
        text = text.replace(r'\{', r'\lbrace ')
        text = text.replace(r'\}', r'\rbrace ')
        # 自动检测集合表示法 {... \mid ...} 并添加大括号
        text = re.sub(r'(?<!\\)\{([^{}]*\\mid[^{}]*)\}', r'\\lbrace \1\\rbrace ', text)
        # 处理 LaTeX enumerate/itemize 环境
        text = self._convert_latex_lists(text)
        # 处理 LaTeX tabular 表格
        text = self._convert_latex_tables(text)
        # 处理 TikZ 绘图环境
        text = self._convert_tikz(text)
        return text

    def _convert_tikz(self, text: str) -> str:
        """将 tikzpicture 环境转换为 tikzjax 格式（支持多种 TikZ 库）"""

        def convert_tikz_block(match):
            tikz_code = match.group(0)

            # 简单宏替换
            simple_macros = {
                '\\Z': '\\mathbb{Z}',
                '\\N': '\\mathbb{N}',
                '\\Q': '\\mathbb{Q}',
                '\\R': '\\mathbb{R}',
                '\\C': '\\mathbb{C}',
                '\\F': '\\mathbb{F}',
                '\\P': '\\mathbb{P}',
                '\\A': '\\mathbb{A}',
                '\\eps': '\\varepsilon',
                '\\vphi': '\\varphi',
            }
            for macro, replacement in simple_macros.items():
                tikz_code = tikz_code.replace(macro, replacement)

            # 自动检测需要的包
            packages = ['amsfonts', 'amssymb']
            tikzlibraries = []

            # 检测 tikz-3dplot
            if 'tdplot' in tikz_code or '3d' in tikz_code.lower():
                packages.append('tikz-3dplot')

            # 检测 pgfplots
            if 'axis' in tikz_code or 'addplot' in tikz_code:
                packages.append('pgfplots')

            # 检测 circuitikz
            if 'circuitikz' in tikz_code or 'to[' in tikz_code:
                packages.append('circuitikz')

            # 检测 tikz-cd (交换图)
            if 'tikzcd' in tikz_code or 'arrow' in tikz_code:
                packages.append('tikz-cd')

            # 检测 arrows.meta (Stealth 箭头)
            if 'Stealth' in tikz_code or 'Latex' in tikz_code:
                tikzlibraries.append('arrows.meta')

            # 检测其他常用 TikZ 库
            if 'calc' in tikz_code or '($' in tikz_code:
                tikzlibraries.append('calc')

            # positioning 库 (检测 "of=" 语法)
            if 'positioning' in tikz_code or ' of=' in tikz_code or ' of ' in tikz_code:
                tikzlibraries.append('positioning')

            # shapes 库 (检测各种形状)
            if 'ellipse' in tikz_code or 'rectangle' in tikz_code or 'diamond' in tikz_code:
                tikzlibraries.append('shapes.geometric')
            if 'shapes' in tikz_code:
                tikzlibraries.append('shapes')

            # backgrounds 库
            if 'background' in tikz_code:
                tikzlibraries.append('backgrounds')

            # fit 库
            if 'fit=' in tikz_code:
                tikzlibraries.append('fit')

            logger.info(f"[MathJax2Image] TikZ 包: {packages}, 库: {tikzlibraries}")
            logger.info(f"[MathJax2Image] TikZ 代码: {tikz_code[:200]}...")

            # 构建 usepackage 和 usetikzlibrary 语句
            usepackages = '\n'.join([f'\\usepackage{{{pkg}}}' for pkg in packages])
            usetikzlibs = ''
            if tikzlibraries:
                usetikzlibs = f"\\usetikzlibrary{{{','.join(tikzlibraries)}}}"

            # 构建完整的 TikZ 文档
            full_tikz = f"""{usepackages}
{usetikzlibs}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""

            # 用 div 包装以便 CSS 精确选择
            return f'<div class="tikz-diagram"><script type="text/tikz">\n{full_tikz}\n</script></div>'

        # 调试：检查是否有 tikz 环境
        has_tikzpicture = r'\begin{tikzpicture}' in text
        has_tikzcd = r'\begin{tikzcd}' in text
        logger.info(f"[MathJax2Image] _convert_tikz: tikzpicture={has_tikzpicture}, tikzcd={has_tikzcd}")

        # 匹配 tikzpicture 环境
        text = re.sub(
            r'\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}',
            convert_tikz_block, text
        )

        # 匹配 tikzcd 环境
        text = re.sub(
            r'\\begin\{tikzcd\}[\s\S]*?\\end\{tikzcd\}',
            convert_tikz_block, text
        )

        # 调试：检查转换结果
        has_script = '<script type="text/tikz">' in text
        logger.info(f"[MathJax2Image] _convert_tikz: 转换后包含 script: {has_script}")

        return text

    def _convert_latex_lists(self, text: str) -> str:
        """将 LaTeX 列表环境转换为 Markdown 格式"""
        # 移除 \begin{enumerate}[...] 和 \end{enumerate}
        text = re.sub(r'\\begin\{enumerate\}(\[.*?\])?', '', text)
        text = re.sub(r'\\end\{enumerate\}', '', text)
        # 移除 \begin{itemize} 和 \end{itemize}
        text = re.sub(r'\\begin\{itemize\}', '', text)
        text = re.sub(r'\\end\{itemize\}', '', text)
        # 将 \item 转换为 Markdown 列表项
        lines = text.split('\n')
        result = []
        item_counter = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(r'\item'):
                item_counter += 1
                # 移除 \item 并添加编号
                content = re.sub(r'^\\item\s*', '', stripped)
                result.append(f"{item_counter}. {content}")
            else:
                result.append(line)
        return '\n'.join(result)

    def _convert_latex_tables(self, text: str) -> str:
        """将 LaTeX tabular 表格转换为 Markdown 格式"""
        # 移除 \begin{table}...\end{table} 包装，保留内容
        text = re.sub(r'\\begin\{table\}(\[.*?\])?', '', text)
        text = re.sub(r'\\end\{table\}', '', text)
        text = re.sub(r'\\centering', '', text)
        text = re.sub(r'\\caption\{.*?\}', '', text)

        # 处理 tabular 环境
        def convert_tabular(match):
            content = match.group(1)
            # 移除 \hline
            content = re.sub(r'\\hline\s*', '', content)
            # 按 \\ 分割行
            rows = re.split(r'\\\\\s*', content)
            md_rows = []
            for i, row in enumerate(rows):
                row = row.strip()
                if not row:
                    continue
                # 按 & 分割列
                cells = [c.strip() for c in row.split('&')]
                md_row = '| ' + ' | '.join(cells) + ' |'
                md_rows.append(md_row)
                # 第一行后添加分隔符
                if i == 0:
                    sep = '|' + '|'.join(['---'] * len(cells)) + '|'
                    md_rows.append(sep)
            return '\n'.join(md_rows)

        text = re.sub(
            r'\\begin\{tabular\}\{[^}]*\}([\s\S]*?)\\end\{tabular\}',
            convert_tabular, text
        )
        return text

    async def terminate(self):
        """插件卸载时清理资源"""
        await self.renderer.close()
        logger.info("MathJax2Image 插件已卸载")
