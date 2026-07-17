# ADR-0003: 四级压缩记忆架构

## 状态

已采纳

## 上下文

InkMind 需要支持 100 万字以上的长篇连续写作。大型语言模型（LLM）的上下文窗口有限（通常 4K-128K tokens），无法一次性容纳整部小说的正文。同时，AI 在写作过程中需要保持情节连贯性、角色一致性和世界观完整性。

需要设计一套记忆系统，实现：
1. **有限窗口** — LLM 上下文窗口内的即时可用信息
2. **层级压缩** — 正文 → 摘要 → 归档，逐层浓缩
3. **按需检索** — 跨章事件的精确定位
4. **异步持久化** — 写作不阻塞，压缩在后台完成

## 决策

### 四级记忆层级

采用四级层级架构（L0→L3），数据量逐级递减、抽象度逐级递增：

```
L3  长期知识        (角色档案 / 世界观手册 / 风格指南)
  ↑ 引用、不随章节变化
L2  压缩记忆        (每 N 章压缩摘要 + 事件清单)
  ↑ 检索、仅注入最近 N 条
L1  活跃上下文      (前 M 章全文 + 角色状态卡 + 伏笔表)
  ↑ 直接注入、默认 5 章滑窗
L0  全文索引        (逐段落倒排索引)
  ↑ 精确定位、仅按需查询
```

### 各层级详细定义

#### L0 — FullTextIndex
- **内容**: 章节号 → 段落列表 → 段落内容 + 来源标记组成倒排索引
- **更新时机**: 每章定稿后同步更新
- **查询方式**: 关键词定位 → 返回所属章节和段落
- **设计决策**: 使用纯 Python dict + list 结构，避免引入外部搜索引擎

#### L1 — ActiveContext
- **内容**: SlidingWindowState（窗口元信息）+ CharacterStateCard[] + ForeshadowingMarker[]
- **默认窗口**: 5 章（写第 N 章时，注入第 N-5 到 N-1 章）
- **角色状态卡**: 每个角色在窗口内的最新位置、最近行动、状态快照
- **伏笔表**: 未回收的伏笔列表，驱动动态扩展
- **动态扩展**: 当待回收伏笔数超过阈值时，窗口向上扩展以包含伏笔埋设章节
- **更新时机**: 每章定稿后同步更新（滚动 + 状态卡刷新）

#### L2 — CompressedMemory
- **内容**: 一段总摘要 + 结构化事件列表（PerChapterEvent）
- **默认粒度**: 每 10 章一次压缩
- **动态调整**: 事件密度高或伏笔密集时可缩小粒度（如 5 章）；情节平缓时可扩大（如 15 章）
- **异步执行**: 写作流水线不阻塞，MemoryKeeper 后台执行
- **LLM 驱动**: 使用纯 LLM 做压缩，不采用 NLP 混合方案
- **保留数量**: MemorySnapshot 中保留最近 3 条压缩记忆

#### L3 — LongTermArchive
- **内容**: 枚举条目类型（CharacterArchive / WorldBible / StyleGuide），每个条目为自由文本
- **更新时机**: 用户或 Designer 手动维护，不随章节自动变更
- **注入方式**: MemorySnapshot 携带引用，不被自动覆盖

### 记忆快照（MemorySnapshot）

Writer 每写一章前，向 MemoryKeeper 请求一次 MemorySnapshot，包含：
1. L1 活跃上下文（滑窗内全文 + 角色状态卡 + 伏笔提示）
2. 最近 3 条 L2 压缩记忆
3. L3 长期知识引用（章节目标签，全量由查询接口提供）
4. 待回收伏笔的显式提醒

### 异步压缩通知

L2 压缩为异步任务，完成时通过 `COMPRESSION_NOTIFICATION` 类型 Packet 广播：
- `compression_started` — 任务已创建
- `compression_completed` — 压缩完成，摘要已归档
- `compression_failed` — 压缩失败（LLM 调用异常）

### Pipeline 集成

```
每章定稿后:
  Editor → MemorizeRequest(含正文+事件+角色变化) → MemoryKeeper
  MemoryKeeper:
    1. 同步: 更新 L0 索引 + 滚动 L1 滑窗
    2. 判断: 达到压缩阈值? → 是 → 创建 CompressionTask(PENDING)
    3. 返回: Memorized(digest) → Editor → 流水线继续下一章
  ─── 异步后台 ───
    4. 执行: CompressionTask(RUNNING) → 调用 LLMCompressor
    5. 完成: 更新 L2Archive → CompressionNotification(completed)
```

### 被否决的方案

| 方案 | 原因 |
|------|------|
| 双级记忆（活跃+长期） | 中间缺少摘要层，长篇信息丢失严重；无法在滑窗全文和高度抽象之间连贯过渡 |
| 单级全文检索（仅向量库） | 每次 Writer 调用都需要检索，延迟不可控；未解决"当前章需要什么"的意图理解问题 |
| 同步立即压缩 | 每 10 章阻塞写作等待 LLM 压缩完成（约 10-30 秒），违背"写作不打断"的设计目标 |
| NLP 关键词压缩混合方案 | 在 AI 小说场景下，需要理解情节和角色关系，纯 NLP 无法保证摘要质量 |

## 后果

### 正面
- Writer 的窗口始终可控（默认 ~5K-15K tokens），不因章节累积膨胀
- 异步压缩不阻塞流水线主流程
- 四级结构覆盖"精确查找→即时上下文→近期总结→长期知识"全链路
- 伏笔驱动的动态扩展确保关键情节不会滑出窗口

### 负面
- 异步压缩意味着如果 Writer 连续快速写，L2 摘要可能延迟更新
- 伏笔动态扩展增加了 L1 窗口的不确定性，需要额外测试覆盖
- LLM 压缩依赖外部 Provider，有失败风险（已通过异步重试 + 通知降级处理）
- 四级架构相对复杂，增加了 MemoryKeeper Agent 的实现成本

### 补偿措施
- CompressionNotification 允许外部系统（如 UI）提示用户"压缩中"
- 最多保留 3 条压缩记忆在 Snapshot 中，不会无限增长 L2 引用
- L1 窗口上限为动态扩展后的最大边界（默认不超过 20 章），防止窗口无限膨胀

## 相关文档

- `inkmind/models/memory.py` — 四级记忆全套模型
- `inkmind/memory/compressor.py` — MemoryKeeper 压缩管线核心实现
- `tests/test_memory_models.py` — 记忆模型单元测试（29 个用例）
- `tests/test_compression_pipeline.py` — 压缩管线逻辑测试（56 个用例）
- `CONTEXT.md` — 术语表（MemoryTier 及相关定义）
- `AGENTS.md` — @memory-keeper 能力描述和 Pipeline 流程
