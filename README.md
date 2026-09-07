# MathJax2Image

将 Markdown、LaTeX 公式、TikZ / PGFplots 和 Mermaid 图表渲染为 PNG 的 AstrBot 插件。

[安装](#安装) · [命令](#命令) · [效果展示](#效果展示) · [扩展包](#扩展包) · [配置](#配置) · [性能验证](#性能验证)

> 喜欢的话可以给个 Star 喵，有问题欢迎提 Issue/PR。

## 特性

- **公式与图表**：支持行内 / 独立公式、化学反应式、物理公式、TikZ 二维与三维图形、Mermaid。
- **Markdown 排版**：支持标题、列表、表格和带行号的代码块，可配置背景色、正文字号及标题比例。
- **按需加载与缓存**：只加载内容需要的引擎，复用浏览器资源和近期渲染结果。
- **并发与内存控制**：合并相同请求，限制排队及重型 TikZ 并发，自动回收空闲页面。
- **密集绘图优化**：预计算支持的函数采样，保留网格精度，修复深层 SVG 的着色与裁剪。

## 安装

### 1. 安装依赖

```bash
pip install -r requirements.txt
python -m playwright install chromium --only-shell
```

Linux 系统依赖应在部署阶段安装，插件运行时不会执行 `apt-get`：

```bash
playwright install-deps chromium
```

### 2. CDN 资源

MathJax 3.2.2、TikZJax beta24 和 Mermaid 10.9.3 使用固定版本 CDN；字体来自阿里云 OSS。首次使用仍需联网，后续在插件进程内复用资源。

## 命令

- `/math <主题>` - 调用 LLM 生成数学文章，支持 LaTeX 公式渲染
- `/art <主题>` - 调用 LLM 生成普通文章
- `/render <内容>` - 直接渲染 Markdown/LaTeX 内容为图片

**示例：**

```text
/math 勾股定理的证明
/art 人工智能的发展历程
/render $E=mc^2$ 是爱因斯坦的质能方程
```

### Mermaid

支持 ```` ```mermaid ```` 代码块；CDN 优先 unpkg，失败回退 jsdelivr。离线环境需自行保证浏览器可访问 Mermaid 脚本。

## 效果展示

以下图片均由当前插件实际渲染。点击示例标题查看完整源码，复制内容后加上 `/render` 即可使用。示例使用白底；损失曲面为合成函数，网络图为结构示意。

| 公式与排版 | TikZ 绘图 |
| --- | --- |
| [数学：谱分解、高斯积分与 Hessian](examples/math.md)<br>![数学公式](examples/math.png) | [非凸损失曲面：48 × 48 采样](examples/ml_surface.md)<br>![蓝色非凸损失曲面](examples/ml_surface.png) |
| [化学：反应、离子与平衡](examples/chemistry.md)<br>![化学公式](examples/chemistry.png) | [参数圆环：48 × 24 采样](examples/torus.md)<br>![参数圆环](examples/torus.png) |
| [物理：场方程、量子态与阻尼振子](examples/physics.md)<br>![物理公式](examples/physics.png) | [全连接网络：4–6–6–3](examples/neural_network.md)<br>![神经网络结构](examples/neural_network.png) |

重新生成全部示例：`python scripts/render_showcase.py`（需要浏览器及 CDN 网络）。

## 扩展包

普通扩展通过 MathJax 自动加载，例如 `\ce{H2O}`、`\cancel{x}`。也可在公式前声明包：

```latex
\usepackage{physics,mhchem,cancel,upgreek,centernot}
$$\dv[2]{x}{t} + \pdv{f}{x}$$
$$\ce{2H2 + O2 -> 2H2O}$$
$$\cancel{x} + \upalpha \centernot\implies y$$
```

显式声明支持：`ams`、`amscd`、`bbox`、`boldsymbol`、`braket`、`cancel`、`cases`、`centernot`、`color`、`empheq`、`enclose`、`extpfeil`、`gensymb`、`mathtools`、`mhchem`、`newcommand`、`physics`、`textcomp`、`textmacros`、`unicode`、`upgreek`、`verb`。

常见 LaTeX 包名也可使用：`amsmath` / `amsfonts` / `amssymb` 映射到 `ams`，`bm` 映射到 `boldsymbol`，`xcolor` 映射到 `color`。不支持的包和带选项的 `\usepackage[...]{...}` 会明确报错。

`physics` 会改变部分命令语法，须显式声明（或在公式中使用 `\require{physics}`）；此时使用完整的导数、括号与向量命令，并移除冲突的简化宏。`physics` 和 `braket` 不能同时声明：前者使用 `\braket{a}{b}`，后者使用 `\braket{a|b}`。

这不是完整 TeX 安装，不能加载任意 CTAN 包；TikZ 可用包仍受 TikZJax 编译环境限制，`circuitikz` 仍不支持。参见 [MathJax 3.2 扩展文档](https://docs.mathjax.org/en/v3.2/input/tex/extensions.html)。

### TikZ / PGFplots 支持清单

TikZ 与 MathJax 使用不同引擎。下面是当前 TikZJax 加载白名单，不表示每个库的全部高级功能都已逐项测试。常用依赖会自动检测；需要显式指定时，把声明写在 `tikzpicture` 环境内，插件会提取并加载：

```latex
\begin{tikzpicture}
\usepackage{pgfplots}
\usetikzlibrary{arrows.meta,positioning}
\pgfplotsset{compat=1.16}
\begin{axis}[view={40}{40}]
\addplot3[surf,shader=flat,samples=25,domain=-2:2] {x^2+y^2};
\end{axis}
\end{tikzpicture}
```

**可加载宏包（17 个）：** `amsbsy`、`amsfonts`、`amsgen`、`amsmath`、`amsopn`、`amssymb`、`amstext`、`array`、`etoolbox`、`expl3`、`hf-tikz`、`ifthen`、`pgfcalendar`、`pgfplots`、`tikz-3dplot`、`tikz-cd`、`xparse`。

<details>
<summary>展开完整 TikZ 库清单（75 个）</summary>

`3d`、`angles`、`animations`、`arrows`、`arrows.meta`、`automata`、`babel`、`backgrounds`、`bending`、`calc`、`calendar`、`cd`、`chains`、`circuits`、`circuits.ee`、`circuits.ee.IEC`、`circuits.logic`、`circuits.logic.CDH`、`circuits.logic.IEC`、`circuits.logic.US`、`datavisualization`、`datavisualization.3d`、`datavisualization.barcharts`、`datavisualization.formats.functions`、`datavisualization.polar`、`datavisualization.sparklines`、`decorations`、`decorations.footprints`、`decorations.fractals`、`decorations.markings`、`decorations.pathmorphing`、`decorations.pathreplacing`、`decorations.shapes`、`decorations.text`、`er`、`fadings`、`fit`、`fixedpointarithmetic`、`folding`、`fpu`、`graphs`、`graphs.standard`、`intersections`、`lindenmayersystems`、`math`、`matrix`、`mindmap`、`patterns`、`patterns.meta`、`perspective`、`petri`、`plothandlers`、`plotmarks`、`positioning`、`quotes`、`rdf`、`scopes`、`shadings`、`shadows`、`shapes`、`shapes.arrows`、`shapes.callouts`、`shapes.gates.logic.IEC`、`shapes.gates.logic.US`、`shapes.geometric`、`shapes.misc`、`shapes.multipart`、`shapes.symbols`、`snakes`、`spy`、`svg.path`、`through`、`trees`、`turtle`、`views`。

</details>

### 支持边界

- 普通公式中的 `\usepackage` 仅支持上方 MathJax 清单；TikZ 内的宏包与库仅支持 TikZ 清单。没有列出的扩展均不承诺支持，无法穷举整个 CTAN 的不支持包。
- 常见不支持项包括 `chemfig`、`circuitikz`、`siunitx`、`unicode-math`、`fontspec`、`biblatex` 和完整 `documentclass` 文档编译。化学反应式使用 `mhchem` 的 `\ce`，单位可使用 `\pu`；TikZ 自带的 `circuits.*` 库不等于 `circuitikz`。
- 不提供本地 TeX、Lua、gnuplot、shell-escape、外部数据文件或 externalization 工作流。TikZ 中文标签缺少相应 TeX 字体支持，示例使用英文；中文说明放在 Markdown 正文中。
- 输出是静态 PNG，不能旋转三维曲面或播放动画；`animations` 在加载白名单中也不改变输出格式。
- 本次曲面示例使用 `shader=flat` / `shader=faceted`。`shader=interp` 在当前 TikZJax beta24 圆环实测中编译失败，暂不承诺支持。
- 圆环使用 `compat=1.16`、`axis equal image` 和显式视角保持几何比例；网格线用于呈现完整表面。不要用独立的 x/y/z 投影向量随意替代三维视角。原生计算与预计算已对照，采样网格完整保留。
- 密集 PGFplots 输出可能包含超过 512 层的 SVG 分组。插件将 TikZJax 的 SVG 插入改为 XML 解析，避免 HTML 解析器压平深层分组后丢失颜色、线条和变换继承。再合并冗余的颜色与坐标变换分组，避免浏览器因过深嵌套崩溃；保留裁剪、透明度等有独立语义的分组，不减少采样点。

## 配置

在 AstrBot 插件配置页修改，保存后重载插件生效。

### 常用设置

| 参数 | 作用 |
| --- | --- |
| `background_color` | 背景色，默认 `#FDFBF0` |
| `math_system_prompt` / `article_system_prompt` | `/math` 和 `/art` 的生成提示词 |
| `tikz_timeout` / `mathjax_timeout` / `mermaid_timeout` | 各引擎渲染超时，单位毫秒 |
| `fail_on_mathjax_timeout` | MathJax 超时是否拒绝出图 |
| `max_screenshot_height` / `max_screenshot_pixels` | 截图尺寸上限 |

### 字体与标题比例

在插件配置的 `typography` 分组内设置以下字段。标题字号等于正文大小乘以对应比例：

| 字段 | 默认值 | 范围 |
| --- | --- | --- |
| `body_font_size` | 36 px | 16–64 |
| `h1_scale` | 1.55 | 1–3 |
| `h2_scale` | 1.25 | 1–2.5 |
| `h3_scale` | 1.10 | 1–2 |
| `line_height` | 1.7 | 1.2–2.2 |

例如正文 32 px、一级标题比例 1.5，对应标题 48 px。四至六级标题为正文大小，使用字重区分。此配置控制 Markdown 排版；TikZ 图内文字通过 TeX 的 `font`、`scale` 等选项调整。

绘图采样与 TikZ 并发见[密集曲线与三维曲面性能](#密集曲线与三维曲面性能)；浏览器选择、页面池、缓存和排队参数见 [Playwright 引擎定制](#playwright-引擎定制)。

## 密集曲线与三维曲面性能

曲线表达式先检查白名单语法并编译一次，再重复求值，不执行 Python `eval` / `exec`。支持的 PGFplots 函数曲面和参数曲面在 Python 预计算坐标，TeX 继续负责投影、遮挡排序、网格与颜色，减少浏览器内 TeX 数值计算。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `precompute_pgfplots` | `true` | 为可识别的 `addplot3` 表达式预计算网格 |
| `plot_max_points` | 6400 | 每幅图可预计算的三维采样点预算 |
| `max_concurrent_tikz` | 1 | 同时执行的重型 TikZ 渲染数，普通公式仍可并行 |

保持 `samples × samples y` 的原始点数、端点和遍历顺序；超过预算会报错，不悄悄降低三维精度。普通二维 `draw plot` 每条曲线最多 2000 点、每幅图最多 4000 次采样求值。自定义宏、样式和不能可靠解析的表达式回退原生 TeX；这种情况下预计算点数限制并不是原生 TeX 的总工作量限制，仍由超时约束。仅设置兼容版本的 `pgfplotsset` 不影响优化。

同一台 Windows 机器、预热资源、关闭图片结果缓存的单次对照如下；CPU 为 Python/浏览器进程树累计时间，不是 CPU 占用百分比：

| 场景 | 原耗时 → 优化后 | 原 CPU 秒 → 优化后 |
| --- | --- | --- |
| 2000 点二维曲线 | 13.08 → 11.68 s | 17.34 → 14.86 |
| 25 × 25 曲面 | 16.19 → 9.58 s | 20.44 → 12.11 |
| 40 × 40 曲面 | 37.79 → 18.82 s | 48.83 → 24.33 |
| 36 × 20 参数圆环 | 20.31 → 12.26 s | 25.75 → 15.38 |

这是开发机对照，不是所有硬件的保证；表中圆环为性能用例，画廊采用更细的 48 × 24 网格。高密度曲面仍有 TeX 路径和遮挡排序成本，不能无限增大点数。参见 [PGFplots 性能说明](https://tikz.dev/pgfplots/optimization) 和 [三维网格规则](https://tikz.dev/pgfplots/reference-3dplots)。

复现（压测额外需要 `psutil`、`Pillow`）：

```bash
python scripts/benchmark_heavy.py --output ./heavy-output --cases dense-curve surface-25 surface-40 torus
```

脚本保存源 TeX、HTML、SVG、PNG 和时间/CPU/内存 JSON，可用 `--plugin-dir` 指向另一个插件版本做对照。对更密集网格可显式选择 `surface-80`；默认不会执行该重负载用例。

## Playwright 引擎定制

通过 `browser_engine` 选择 `chromium`、`firefox` 或 `webkit`，默认 `chromium`。

默认使用 Playwright 自带的 Chromium Headless Shell（Playwright >= 1.49），只需安装精简无头浏览器，不需要完整 Chrome、Firefox 或 WebKit。插件直接启动匹配版本的 Shell，不额外启动驱动检查完整浏览器路径。保留 Playwright 官方启动参数，不再重复覆盖 Chromium 的默认开关。参见 [官方 Headless Shell 文档](https://playwright.dev/python/docs/browsers#chromium-headless-shell)。

一个常驻浏览器搭配隔离页面池：每个渲染任务独占页面和 BrowserContext，任务结束后默认最多保留 1 个空闲页面，多余页面立即关闭；空闲 30 秒后回收剩余页面，释放对应渲染进程。静态资源缓存持有独立 APIRequestContext 中的响应，命中时直接向浏览器复用响应，避免反复通过 Python/Node 管道传输大字体；缓存淘汰时释放对应响应。页面销毁不会清掉其他页面使用的资源缓存。

可配置的运行参数：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `browser_max_pages` | 2 | 浏览器页面上限 |
| `max_concurrent_renders` | 2 | 运行任务上限；实际并发取两项较小值 |
| `max_queued_renders` | 8 | 额外允许排队的任务数；0 表示繁忙时立即返回 |
| `render_queue_timeout` | 30000 | 排队超时，单位毫秒；不改变引擎自身的渲染超时 |
| `browser_max_idle_pages` | 1 | 最多保留的空闲页面；0 表示归还时立即释放 |
| `browser_idle_timeout` | 30 | 空闲页面回收时间，单位秒；0 关闭定时回收 |
| `resource_cache_max_mb` | 64 | 静态资源缓存容量，单位 MiB；0 关闭 |
| `image_cache_max_mb` | 8 | 图片缓存容量，单位 MiB；0 关闭 |

页面回收后仍保留浏览器进程及独立资源缓存，重新出图无需再下载已缓存的字体或脚本。缓存容量限制不等于浏览器总内存限制；优先压低峰值内存时，可把运行并发调为 1。

2 路适合兼顾吞吐和内存；可用下方压测工具对比 4 路。只增大排队数不会提高渲染吞吐。无需为每条消息启动浏览器，也无需为每个任务新建 Python 进程。已有 `browser_cdp_url` 仍可连接共享浏览器；当前优化不依赖外部浏览器服务。

### 其他浏览器与共享实例

Chromium 是默认推荐。Firefox/WebKit 使用前需执行：

```bash
playwright install firefox webkit
```

可运行相同模板、视口和截图参数的可重复基准：

```bash
python scripts/benchmark_browsers.py --runs 5
```

若多个插件需要截图，建议连接同一个外部 Chromium，并将 `browser_cdp_url` 设置为 `http://127.0.0.1:9222`。CDP 端口不要暴露到公网；`allow_remote_cdp` 默认关闭，非本机地址需显式开启。

## 性能验证

以下脚本用于本机对照；并发和内存测试额外需要 `psutil`、`Pillow`。首次联网下载与资源预热后的稳定渲染应分开比较。

### 页面复用与图片缓存

```bash
python scripts/benchmark_rendering.py --output ./benchmark-output --runs 5 --unique
python scripts/benchmark_rendering.py --output ./benchmark-cached --runs 5
```

第一条使用不同内容测页面池和资源缓存的热启动耗时，第二条重复相同内容测图片结果缓存。输出包含耗时、图片尺寸和公式错误节点；支持 `--plugin-dir` 指定另一份插件代码作对照。

### 并发吞吐

```bash
python scripts/benchmark_concurrency.py --output ./concurrency-output --jobs 12 --concurrency 1 2 4
```

页面预热后使用不同内容绕过图片缓存，检查公式与页面内容隔离，记录吞吐、含排队的延迟，以及驱动 / 浏览器进程 RSS 合计。RSS 可能重复计入共享内存，不代表独占内存。

### 空闲内存与唤醒延迟

```bash
python scripts/benchmark_memory.py --output ./memory-output
```

对比保留两个页面与回收空闲页面的策略，记录 RSS、唤醒耗时及新增下载数。测试将回收时间缩短到 1 秒，正式配置默认 30 秒。

## 支持

[AstrBot 帮助文档](https://astrbot.app)

## 致谢

- [obsidian-tikzjax](https://github.com/artisticat1/obsidian-tikzjax) - 提供支持 AMS 字体的 TikZJax 实现
- [MathJax](https://www.mathjax.org/) - 数学公式渲染引擎
- [BaKoMa Fonts](http://www.ctan.org/pkg/bakoma-fonts) - TeX 字体
