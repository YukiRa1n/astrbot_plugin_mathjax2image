"""生成 TikZ 示例图 - 独立脚本"""
import asyncio
import re
import uuid
from pathlib import Path

import markdown
from playwright.async_api import async_playwright


PLUGIN_DIR = Path(__file__).parent


def convert_tikz(text: str) -> str:
    """将 tikzpicture/tikzcd 转换为 tikzjax 格式"""
    def convert_block(match):
        tikz_code = match.group(0)
        packages = ['amsfonts', 'amssymb']
        tikzlibraries = []

        if 'Stealth' in tikz_code or 'Latex' in tikz_code:
            tikzlibraries.append('arrows.meta')

        # tikzcd 需要 tikz-cd 包
        if 'tikzcd' in tikz_code:
            packages.append('tikz-cd')

        usepackages = '\n'.join([f'\\usepackage{{{pkg}}}' for pkg in packages])
        usetikzlibs = ''
        if tikzlibraries:
            usetikzlibs = f"\\usetikzlibrary{{{','.join(tikzlibraries)}}}"

        full_tikz = f"""{usepackages}
{usetikzlibs}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""

        return f'<div class="tikz-diagram"><script type="text/tikz">\n{full_tikz}\n</script></div>'

    # 匹配 tikzpicture
    text = re.sub(r'\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}', convert_block, text)
    # 匹配 tikzcd
    text = re.sub(r'\\begin\{tikzcd\}[\s\S]*?\\end\{tikzcd\}', convert_block, text)
    return text


def convert_markdown_to_html(md_text: str) -> str:
    """将 Markdown 转换为 HTML"""
    # 保护数学公式
    math_blocks = []
    def sub_math(m):
        placeholder = f"MATHBLOCK{len(math_blocks)}MATHBLOCK"
        math_blocks.append(m.group(0))
        return placeholder

    md_text = re.sub(r'\$\$.*?\$\$', sub_math, md_text, flags=re.DOTALL)
    md_text = re.sub(r'\$.*?\$', sub_math, md_text)

    # 转换 TikZ
    md_text = convert_tikz(md_text)

    # 转换 Markdown
    html_body = markdown.markdown(md_text, extensions=['fenced_code', 'tables'])

    # 恢复数学公式
    for i, block in enumerate(math_blocks):
        html_body = html_body.replace(f"MATHBLOCK{i}MATHBLOCK", block)

    # 读取模板
    template_path = PLUGIN_DIR / "templates" / "template.html"
    with open(template_path, "r", encoding="utf-8") as f:
        html_template = f.read()

    return html_template.replace("{{CONTENT}}", html_body)


async def render_to_image(content: str, output_path: Path):
    """渲染内容为图片"""
    html = convert_markdown_to_html(content)

    # 保存临时 HTML
    temp_dir = PLUGIN_DIR / "temp"
    temp_dir.mkdir(exist_ok=True)
    tmp_path = temp_dir / f"temp_{uuid.uuid4().hex[:8]}.html"

    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"HTML 临时文件: {tmp_path}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-web-security', '--allow-file-access-from-files']
        )
        page = await browser.new_page(viewport={'width': 1150, 'height': 2000})

        await page.goto(f"file://{tmp_path}", wait_until='domcontentloaded', timeout=60000)

        # 等待 MathJax
        try:
            await page.wait_for_function("() => window.mathJaxReady === true", timeout=10000)
            print("MathJax 渲染完成")
        except:
            print("MathJax 等待超时")

        # 等待 TikZ
        tikz_count = await page.evaluate("() => document.querySelectorAll('.tikz-diagram').length")
        print(f"TikZ 容器数量: {tikz_count}")

        if tikz_count > 0:
            try:
                await page.wait_for_function("""() => {
                    const svg = document.querySelector('.tikz-diagram svg');
                    if (!svg) return false;
                    return svg.querySelectorAll('path, line, text').length > 0;
                }""", timeout=30000)
                print("TikZ 渲染完成")
                await asyncio.sleep(2)
            except:
                print("TikZ 等待超时")

        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({'width': 1150, 'height': height})

        await page.screenshot(path=str(output_path), full_page=True)
        print(f"截图已保存: {output_path}")

        await browser.close()

    tmp_path.unlink(missing_ok=True)


async def main():
    tikz_content = r"""## TikZ 示例 - 自然变换

\begin{tikzpicture}[scale=1.8]
  % 左侧范畴 C
  \draw[gray, rounded corners] (-0.8,-1.2) rectangle (0.8,1.2);
  \node at (0,1.5) {$\mathcal{C}$};
  \node (X) at (0,0.5) {$X$};
  \node (Y) at (0,-0.5) {$Y$};
  \draw[->] (X) -- (Y) node[midway,left] {$f$};

  % 右侧范畴 D
  \draw[gray, rounded corners] (3,-1.5) rectangle (6,1.5);
  \node at (4.5,1.8) {$\mathcal{D}$};

  % F 映射的对象
  \node (FX) at (3.5,0.7) {$F(X)$};
  \node (FY) at (3.5,-0.7) {$F(Y)$};

  % G 映射的对象
  \node (GX) at (5.5,0.7) {$G(X)$};
  \node (GY) at (5.5,-0.7) {$G(Y)$};

  % 态射
  \draw[->] (FX) -- (FY) node[midway,left] {$F(f)$};
  \draw[->] (GX) -- (GY) node[midway,right] {$G(f)$};
  \draw[->, blue, dashed] (FX) -- (GX) node[midway,above] {$\alpha_X$};
  \draw[->, blue, dashed] (FY) -- (GY) node[midway,below] {$\alpha_Y$};

  % 函子箭头
  \draw[->, red, bend left=20] (0.9,0.3) to node[above] {$F$} (3.4,0.5);
  \draw[->, orange, bend right=20] (0.9,-0.3) to node[below] {$G$} (3.4,-0.5);
\end{tikzpicture}
"""

    output = PLUGIN_DIR / "examples" / "tikz_example.png"
    output.parent.mkdir(exist_ok=True)

    await render_to_image(tikz_content, output)
    print(f"完成: {output}")


if __name__ == "__main__":
    asyncio.run(main())
