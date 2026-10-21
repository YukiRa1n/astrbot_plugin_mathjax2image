# 大字体缓存修复与书籍主题交付

规范来源：C:/Users/29594/.codex/PLANS.md。
工作目录：D:/desktop/Github/AstrBot/YukiRa1n-AstrBot/data/plugins/astrbot_plugin_mathjax2image。

## 目标与范围

修复仓耳今楷 W04/W05 各约 18.9 MB 的字体下载后无法缓存的问题；验证首次写盘、并发合并、重建渲染器后不下载即可出图，然后提交并推送。包含用户已认可的 960px 长图书籍主题及先前未提交改动，不发布新 Release，不捆绑字体二进制。

## 现状与证据

当前 HEAD 为 07d0de9。先前书籍主题改动在 README、配置、转换器、渲染器、模板、两个测试和新增示例/授权说明中，全部保留。`_capture_response` 在浏览器 `requestfinished` 后调用 `response.body()`；日志出现 `Request content was evicted from inspector cache`（响应正文已被浏览器调试缓存清除）。实际字形检查通过，但字体未入缓存。

日志：D:/desktop/Github/AstrBot/.misaka-review-20261020/render-book-narrow.log。

## 实施方案

1. 为完整 TTF/OTF 字体增加路由直接下载、保存、交付路径，绕开 Chromium 调试响应缓存。其余资源继续现有浏览器下载方式。保持域名白名单，不自动跟随下载重定向，失败时允许浏览器回退；释放下载响应和等待者。
2. 将 jsDelivr 上完整 Git commit SHA 固定的资源视为不可变资源；分支和短 SHA 保持原有过期策略。保留磁盘容量淘汰机制。
3. 单元测试覆盖成功、并发、失败、取消和重启命中。实际用两个完整字体完成冷启动及新渲染器禁网验证，检查字形和图片。
4. 运行相关测试及全量回归，检查差异，提交推送并验证远端 SHA。

## 进展

- [x] 已检查代码、未提交改动和原始失败日志。
- [x] 修改前回归用例失败：首次字体请求只放行浏览器，未写入可复用缓存。命令 `python -m pytest tests/test_asset_disk_cache.py -k full_font_download -q`，退出码 1。
- [x] 完成路由字体下载、响应释放、重定向保留、失败回退和 commit 固定资源策略；`python -m pytest tests/test_asset_disk_cache.py -q`：33 passed，退出码 0。
- [x] 完成真实字体冷启动和禁网重启验证。两个字体合计 37,901,760 字节；冷启动下载 2 次，新浏览器/新渲染器断网启动下载 0 次；两张 PNG 的 SHA-256 一致。
- [x] 全量回归：`python -m pytest tests -q`，338 passed / 2 skipped，退出码 0；7 个修改的 Python 文件通过 Ruff 和格式检查；`git diff --check` 通过。
- [x] 功能提交 `f27d987af734300393f1cff4457d5472bd24211b` 已推送到 `origin/master`；`git ls-remote origin refs/heads/master` 返回同一 SHA，退出码 0。

## 决策记录

2026-10-20 America/Denver：只将完整 TTF/OTF 字体走直接下载，避免把大量小型 WOFF/WOFF2 和 MathJax/TikZ 资源切回曾较慢的驱动请求。下载结果经过现有内存/磁盘缓存，不扩大 Chromium 调试缓存。

## 验收与恢复

必须看到两个字体的磁盘条目、实际使用 W04/W05 字形、新渲染器禁网成功，并且所有新增缓存用例通过。保留示例图和用户已认可的配置。临时日志、验证脚本和测试缓存放在仓库外 `.misaka-review-20261020`；失败时只重试本任务测试，不删除已有缓存和用户文件。

## 结果与复盘

代码、运行验收及功能提交推送均已完成。实际验证脚本 `D:/desktop/Github/AstrBot/.misaka-review-20261020/verify_font_cache.py` 退出码 0，结果保存在同目录 `font-cache-validation.json`，日志为 `font-cache-validation.log`。冷启动 20.014s，重建浏览器和渲染器后断网出图 4.015s（包含启动及截图，仅为本机单次字体页面验证，不代表所有文章性能）。两张图 SHA-256 均为 `f2ad596397d13a6dc1f2feb0506f26506284c609a5d10330de60f0236bec7feb`。实际 W04/W05 字形均命中。完整字体使用路由下载及显式响应释放，避免依赖 Chromium 调试缓存；磁盘容量不足或缓存关闭时不保证跨重启复用。

## 交付记录

2026-10-20 America/Denver：功能与书籍主题已推送，本次计划完成状态以随后文档提交同步；未创建 Release。无剩余实现事项。
