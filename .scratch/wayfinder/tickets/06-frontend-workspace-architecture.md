---
title: Vue 写作工作台前端架构
type: wayfinder:prototype
status: needs-triage
created: 2026-07-16
blocked_by: [01]
labels: [feature, wayfinder]
---

## Question

Vue 3 写作工作台的前端架构应该是什么样的？

需要原型化的决策点：

1. **布局方案**：
   - Scrivener 式三栏布局（左侧大纲树｜中间编辑器｜右侧侧栏）
   - 每栏是否可折叠/拖拽调整大小？
   - 响应式设计——桌面优先还是移动优先？

2. **编辑器选择**：
   - Tiptap/ProseMirror（富文本，功能最全）
   - CodeMirror（Markdown 编辑，轻量）
   - 自定义 Block Editor（类似 Notion/思源笔记）
   - 纯 Markdown + 实时预览（类似 Typora）
   - 推荐 Tiptap——支持富文本+Markdown 双模，扩展性最强

3. **状态管理**：
   - Pinia store 拆分——novelStore, chapterStore, characterStore, worldStore
   - 后端 API 的缓存策略
   - 本地草稿暂存（未保存的状态）

4. **组件树（关键结构）**：
   ```
   App.vue
   ├── WorkspaceLayout.vue (三栏主布局)
   │   ├── OutlinePanel.vue (左侧大纲/目录树)
   │   │   ├── TreeNode.vue (可拖拽的层级节点)
   │   │   └── NovelStructure.vue (卷/部/章)
   │   ├── EditorPanel.vue (中间编辑器)
   │   │   ├── EditorToolbar.vue
   │   │   ├── NovelEditor.vue (Tiptap)
   │   │   └── AIAssistant.vue (AI 建议浮动按钮)
   │   └── SidePanel.vue (右侧上下文面板)
   │       ├── CharacterCard.vue
   │       ├── WorldGlossary.vue
   │       └── WritingStats.vue
   ├── AIDialog.vue (AI 对话/指令弹窗)
   └── ExportDialog.vue (导出设置)
   ```

5. **AI 交互方式**：
   - 选中文本后弹出 AI 操作菜单（续写/扩写/改写/翻译）
   - 侧边聊天窗口（与 AI 讨论情节）
   - /slash 命令（Notion 风格的 AI 触发）

6. **路由设计**：
   - `/workspace/:novelId` — 主工作区
   - `/novels` — 小说列表/创建页
   - `/settings` — 设置页（API Key 配置等）

请输出 Vue 组件树 + Pinia store 结构 + 路由表 + 关键交互流程的 Mermaid 图。无需实现代码，只需架构设计。
