# 多格式导出 — 技术调研报告

> **日期**: 2026-07-16  
> **范围**: EPUB/PDF/DOCX/Markdown/TXT 导出方案、中文排版、管线架构

## 1. 推荐方案总览

| 格式 | 推荐库 | 原因 |
|------|-------|------|
| **EPUB** | **ebooklib** | 纯Python、完整EPUB3、中文社区验证充分 |
| **PDF** | **WeasyPrint** | HTML+CSS驱动、中文字体嵌入、CSS Paged Media |
| **DOCX** | **python-docx** | 唯一实质选择，需注意东亚字体设置 |
| **Markdown** | 原生输出 | 字符串模板 + YAML frontmatter |
| **TXT** | 原生输出 | 字符串拼接 + 编码处理 |

## 2. 导出管线架构

**推荐: IR(Markdown) + 分步渲染 架构**

```
InkMind Project Data (JSON model)
        │
        ▼
┌──────────────────────────┐
│    Export Pipeline Core   │  ← 格式无关预处理
│    - 章节构建与排序        │
│    - 元数据组装            │
│    - 封面资源配置          │
│    - CSS 主题选择          │
│    - 中文排版预处理         │
└──────────┬───────────────┘
           │
    ┌──────┴──────┐
    │ Intermediate │  ← Markdown + Frontmatter (单一真源)
    │  Markdown    │
    └──────┬──────┘
           │
    ┌──────┴──────┐
    │ 格式路由器   │
    └──┬─┬─┬─┬─┬─┘
       │ │ │ │ │
     EPUB PDF DOCX MD TXT
```

## 3. EPUB: ebooklib

核心代码模式:
```python
book = epub.EpubBook()
book.set_identifier('inkmind-novel-001')
book.set_title('标题')
book.set_language('zh-CN')
book.add_author('作者')

chapter = epub.EpubHtml(title='第一章', file_name='chap_1.xhtml', lang='zh')
chapter.content = '<h1>第一章</h1><p>正文...</p>'
book.add_item(chapter)

book.toc = [chapter]
book.add_item(epub.EpubNcx())
book.add_item(epub.EpubNav())
book.spine = ['nav', chapter]

epub.write_epub('output.epub', book, {})
```

## 4. PDF: WeasyPrint

优势: 纯Python、HTML+CSS驱动、支持 `@page` margin boxes (页眉/页脚/页码)、`@font-face` 字体嵌入

中文关键配置:
```python
from weasyprint import HTML, FontConfiguration
font_config = FontConfiguration()
HTML(string=html_content).write_pdf('output.pdf', font_config=font_config)
```

CSS需 `@font-face { font-family: 'Noto Sans SC'; src: url(...); }`

## 5. DOCX: python-docx

关键: 设置中文字体需额外设置 `w:eastAsia` 字体槽

```python
from docx.oxml.ns import qn
rPr = run._r.get_or_add_rPr()
rFonts = rPr.find(qn('w:rFonts'))
if rFonts is None:
    rFonts = rPr.makeelement(qn('w:rFonts'), {})
    rPr.insert(0, rFonts)
rFonts.set(qn('w:eastAsia'), '宋体')
```

## 6. 中文排版特殊需求

### 竖排 (EPUB 3.0)
```css
body { writing-mode: vertical-rl; -epub-writing-mode: vertical-rl; }
<spine page-progression-direction="rtl"/>
```
**MVP 仅支持横排，V1 可加竖排选项**

### 中文换行 (Markdown/TXT)
CJK 字符间换行符不应转为空格。处理策略:
- 中文段落内连续行合并（无空格拼接）
- 段落之间保留空行分隔

### 目录生成
- EPUB: ebooklib 的 `book.toc` + NCX/NAV
- PDF: WeasyPrint CSS `string-set` 自动生成 PDF 书签
- DOCX: python-docx 不支持 TOC 域代码，需手动构建

## 原始引用

1. [EbookLib GitHub](https://github.com/aerkalov/ebooklib)
2. [WeasyPrint vs wkhtmltopdf](https://pdf4.dev/blog/weasyprint-vs-wkhtmltopdf)
3. [python-docx Issue #346: 中文字体](https://github.com/python-openxml/python-docx/issues/346)
4. [CommonMark CJK Line Breaks](https://github.com/commonmark/commonmark-spec/issues/744)
5. [cn-epub-maker: 中文竖排EPUB](https://github.com/muyen/cn-epub-maker)
6. [Practitioner Publishing Stack (Zenodo)](https://doi.org/10.5281/zenodo.19765986)
7. [Scriveno Publishing Guide](https://github.com/aihxp/scriveno/blob/main/docs/publishing.md)
