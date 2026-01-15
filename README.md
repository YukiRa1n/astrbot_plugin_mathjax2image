# MathJax2Image

将 Markdown/MathJax 内容渲染为精美图片的 AstrBot 插件。

## 命令

| 命令 | 说明 |
|------|------|
| `/math <主题>` | 调用 LLM 生成数学文章，支持 LaTeX 公式渲染 |
| `/art <主题>` | 调用 LLM 生成普通文章 |
| `/render <内容>` | 直接渲染 Markdown/LaTeX 内容为图片 |

**示例：**
```
/math 勾股定理的证明
/art 人工智能的发展历程
/render $E=mc^2$ 是爱因斯坦的质能方程
```

## 安装

### 1. 安装依赖
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. MathJax 自动安装
插件首次加载时会自动下载 MathJax 离线包（约 1.1MB），无需手动操作。

如果自动下载失败，可手动下载：
1. 访问 https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js
2. 保存到 `static/mathjax/tex-chtml.js`

## 特性

- **LaTeX 公式渲染** - 支持行内公式 `$...$` 和独立公式 `$$...$$`
- **Markdown 智能预处理** - 自动修复格式问题，确保正确渲染
- **代码块行号** - 代码块自动显示行号，长代码自动换行
- **可配置背景色** - 支持自定义模板背景颜色
- **离线渲染** - MathJax 本地运行，无需外部 CDN

## 效果展示

### 数学公式

![数学公式示例](examples/math_example.png)

### 代码块

![代码块示例](examples/code_example.png)

## 配置

在 AstrBot 插件配置中可设置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `background_color` | 模板背景颜色 | `#FDFBF0` |
| `math_system_prompt` | 数学文章提示词 | 内置默认 |
| `article_system_prompt` | 普通文章提示词 | 内置默认 |

## 支持

[AstrBot 帮助文档](https://astrbot.app)
