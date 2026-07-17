# 四级压缩记忆架构 — 技术调研报告

> **日期**: 2026-07-16  
> **范围**: 向量数据库、嵌入模型、活跃上下文、压缩策略、长期知识、检索策略、业界参考

## 1. L0 全文索引

### 1.1 向量数据库选型

| 方案 | 类型 | 查询延迟(10K向量) | 内存占用 | 部署复杂度 | 中文生态 |
|------|------|-------------------|----------|------------|----------|
| **sqlite-vec** | SQLite扩展 | ~2.1ms (p50) | ~8MB+ | 零依赖 | 与SQLite一致 |
| ChromaDB | Python原生 | ~4.8ms | ~64MB+ | pip安装 | 好 |
| FAISS | C++库 | ~1-3ms | 视索引大小 | 需自管理持久化 | 中等 |
| Qdrant (Docker) | 独立服务 | ~7.3ms | ~420MB+ | 高 | 好 |

**推荐: sqlite-vec (首选)**

理由:
- 纯 C 实现 SQLite 扩展，~200KB 编译体积，零外部依赖
- 10K 768维向量查询 p50=2.1ms，空闲仅 8MB
- 支持 float/int8/binary，支持余弦/Euclidean/点积
- 与 InkMind 的 SQLite 主存储天然集成
- 不足: 暂不支持 ANN 索引，1M 向量时延迟约 50ms（对 100 万字小说仍可接受）

### 1.2 中文嵌入模型选型

| 模型 | 维度 | 最大长度 | C-MTEB Avg | 多语言 |
|------|------|----------|------------|--------|
| **BGE-M3** | 1024 | 8192 | 最高(多语言最优) | 是(100+语言) |
| BGE-large-zh-v1.5 | 1024 | 512 | 64.53 | 仅中文 |
| text2vec-base-multilingual | 768 | 512 | 50.26 | 是 |
| Qwen3-Embedding | 1024 | 8192 | 70.58 | 是 |

**推荐: BGE-M3** — 支持 Dense+Sparse+ColBERT 三重检索，最大 8192 tokens 可完整编码章节

## 2. L1 活跃上下文

**推荐: 动态 N 值，默认前 5 章**

Prompt 前缀结构:
```
[当前章节大纲]           -- 来自 architect 的 chapter_plan
[前N章摘要]              -- 滑窗加载
[活跃角色状态卡 x M]     -- 当前章节出场角色的状态快照
[活跃地点状态卡 x P]     -- 当前场景地点的状态
[活跃伏笔 x V]           -- 尚未回收但即将到期的伏笔
[风格指南]               -- 当前写作风格锚点
```

## 3. L2 压缩记忆

**推荐: 分层摘要 + 结构化事件三元组的混合策略**

- 每 10 章触发一次 LLM 摘要 + 事件提取
- 事件表示: 结构化三元组 + 自然语言双轨存储
- ainovel-cli 的四级压缩管线参考: ToolResultMicrocompact → LightTrim → StoreSummaryCompact → FullSummary

## 4. L3 长期知识

角色档案采用"稳定身份 vs 变化状态"分离存储，世界观按主题分章节

## 5. 检索策略

**推荐: 多级混合检索** — BM25 (FTS5) + 向量语义 (sqlite-vec) + 叙事图谱

## 6. 参考项目

### ainovel-cli 四级压缩管线
- Level 1: 清理旧工具结果（零开销）
- Level 2: 截断超长文本
- Level 3: 用 store 中已有的章节摘要/角色快照/伏笔台账替换旧消息（零 LLM 开销）
- Level 4: LLM 生成叙事连续性摘要（兜底方案）
- 中文 Token 估算: `runes × 1.5`

### Narrative World Model (NWM)
- 叙事学接地的事件表示 (focalization/epistemic state、event-vs-reveal、dramatic function、promise/payoff)
- 查询条件混合检索 (BM25 + 稠密向量 + 一跳图谱扩展) → 0.898 多跳故事状态 QA 准确度

## 原始引用

1. [sqlite-vec GitHub](https://www.github.com/asg017/sqlite-vec)
2. [BGE-M3 Paper](https://arxiv.org/html/2402.03216v3)
3. [ainovel-cli GitHub](https://github.com/voocel/ainovel-cli)
4. [NexusSum (ACL 2025)](https://doi.org/10.18653/v1/2025.acl-long.500)
5. [FABLES (arXiv 2404.01261)](https://doi.org/10.48550/arxiv.2404.01261)
6. [NWM (arXiv 2607.05577)](https://arxiv.org/html/2607.05577)
7. [Narrative Knowledge Weaver (arXiv 2606.05724)](https://arxiv.org/html/2606.05724v1)
8. [ComoRAG (arXiv 2508.10419)](https://arxiv.org/abs/2508.10419)
