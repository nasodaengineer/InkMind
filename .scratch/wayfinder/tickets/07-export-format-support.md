---
title: 多格式导出支持
type: wayfinder:research
status: research-complete
resolved_at: 2026-07-16
research_findings: '.scratch/research/export-formats.md'
created: 2026-07-16
blocked_by: [01]
labels: [feature, wayfinder]
---

## Question

支持哪些导出格式？各格式的技术实现方案是什么？

需要研究的要点：

1. **目标格式**（按优先级排序）：
   - **EPUB**（电子书标准格式）—— 必须支持
   - **Markdown**（开发者/写作者友好）—— 必须支持
   - **纯文本 TXT**（通用兼容）—— 应该支持
   - **PDF**（打印/排版）—— 最好支持
   - **DOCX**（Word 兼容）—— 可以考虑

2. **EPUB 生成方案**：
   - Pandoc（瑞士军刀，但需要单独安装）
   - python-docx + 第三方 EPUB 库（如 ebooklib）
   - Calibre 的 ebook-convert 工具
   - 纯 Python 生成（pandoc 作为默认？）

3. **排版质量**：
   - 中文 EPUB 的特殊需求（字体嵌入、竖排支持？）
   - 目录生成（TOC.ncx / nav.xhtml）
   - 封面图片、元数据（作者、ISBN 等）
   - CSS 样式定制

4. **导出流程**：
   - 导出配置界面（选择范围、格式、样式主题）
   - 后台异步生成（长篇小说导出可能需要时间）
   - 下载管理

5. **工程注意**：
   - 中文文本的换行/分段
   - 章节标题层级映射
   - 插图支持（如果未来有）

请调研 Python 生态中的最佳导出库组合，输出一个技术选型对比表 + 推荐方案。
