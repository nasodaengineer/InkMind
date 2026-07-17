---
title: 四级压缩记忆架构详细设计
type: wayfinder:research
status: research-complete
resolved_at: 2026-07-16
research_findings: '.scratch/research/memory-architecture.md'
created: 2026-07-16
blocked_by: [01, 02]
blocks: [05]
labels: [memory, wayfinder]
---

## Question

四级压缩记忆架构的具体实现方案是什么？

需要研究的核心问题：

1. **L0 全文索引**：用什么向量数据库/嵌入模型做章节级语义索引？ChromaDB？FAISS？轻量级SQLite向量扩展？中文场景的嵌入模型选型（BGE-M3？text2vec？）

2. **L1 活跃上下文**：当前章节 + 前N章 + 角色/地点状态卡。N 取多少合适？如何动态构建 prompt 前缀？

3. **L2 压缩记忆**：每10章的压缩策略。是用 LLM 做摘要（调用模型成本）还是用 NLP 提取关键事件？事件表示用什么格式（结构化三元组 vs 自然语言）？

4. **L3 长期知识**：角色档案、世界观手册、风格指南的存储结构。什么时候触发更新？

5. **检索策略**：当 Writer 需要参考某段前文时，如何定位？基于向量的语义检索？基于关键词的全文检索？混合检索？

6. **压缩时机**：同步（写作时实时压缩）vs 异步（后台任务压缩）？用户是否感知等待？

7. **Token 预算**：每一级各分配多少 Token？如何防止上下文窗口溢出？

请调研业界最佳实践（包括 ainovel-cli 的 `memory compressing` 管线、MemGPT/Letta 的思路），输出一份技术方案文档。保留原始引用来源链接。
