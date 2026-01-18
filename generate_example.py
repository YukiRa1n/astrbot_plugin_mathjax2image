"""生成TikZ示例图"""
import asyncio
import re
import uuid
from pathlib import Path
from playwright.async_api import async_playwright

PLUGIN_DIR = Path(__file__).parent

tikz_content = r"""\begin{tikzpicture}[scale=1.8]
  \draw[gray, rounded corners] (-0.8,-1.2) rectangle (0.8,1.2);
  \node at (0,1.5) {$\mathcal{C}$};
  \node (X) at (0,0.5) {$X$};
  \node (Y) at (0,-0.5) {$Y$};
  \draw[->] (X) -- (Y) node[midway,left] {$f$};

  \draw[gray, rounded corners] (3,-1.5) rectangle (6,1.5);
  \node at (4.5,1.8) {$\mathcal{D}$};

  \node (FX) at (3.5,0.7) {$F(X)$};
  \node (FY) at (3.5,-0.7) {$F(Y)$};

  \node (GX) at (5.5,0.7) {$G(X)$};
  \node (GY) at (5.5,-0.7) {$G(Y)$};

  \draw[->] (FX) -- (FY) node[midway,left] {$F(f)$};
  \draw[->] (GX) -- (GY) node[midway,right] {$G(f)$};
  \draw[->, blue, dashed] (FX) -- (GX) node[midway,above] {$\alpha_X$};
  \draw[->, blue, dashed] (FY) -- (GY) node[midway,below] {$\alpha_Y$};

  \draw[->, red, bend left=20] (0.9,0.3) to node[above] {$F$} (3.4,0.5);
  \draw[->, orange, bend right=20] (0.9,-0.3) to node[below] {$G$} (3.4,-0.5);
\end{tikzpicture}"""

template_path = PLUGIN_DIR / "templates" / "template.html"
with open(template_path, "r", encoding="utf-8") as f:
    html_template = f.read()

# 转换TikZ
def convert_block(match):
    tikz_code = match.group(0)
    full_tikz = f"""\\usepackage{{amsfonts}}
\\usepackage{{amssymb}}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""
    return f'<div class="tikz-diagram"><script type="text/tikz">\n{full_tikz}\n</script></div>'

content = re.sub(r'\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}', convert_block, tikz_content)
html = html_template.replace("{{CONTENT}}", content)

async def main():
    examples_dir = PLUGIN_DIR / "examples"
    examples_dir.mkdir(exist_ok=True)
    output_path = examples_dir / "tikz_example.png"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1150, 'height': 2000})

        temp_dir = PLUGIN_DIR / "temp"
        temp_dir.mkdir(exist_ok=True)
        tmp_path = temp_dir / f"temp_{uuid.uuid4().hex[:8]}.html"

        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(html)

        await page.goto(f"file://{tmp_path}")

        await page.wait_for_function("() => window.mathJaxReady === true", timeout=10000)
        await asyncio.sleep(15)

        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({'width': 1150, 'height': height})

        await page.screenshot(path=str(output_path))
        await browser.close()

        tmp_path.unlink(missing_ok=True)
        print(f"示例图已生成: {output_path}")

if __name__ == "__main__":
    asyncio.run(main())
