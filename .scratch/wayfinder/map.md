---
title: InkMind — AI 小说协作写作系统 · 路线地图
status: ready-for-agent
created: 2026-07-16
labels: [wayfinder, map]
---

## Destination

一个 AI 写小说 Web 应用（FastAPI + Vue 3），个人写作用，AI 包揽全流程（Planner→Writer→Editor），人负责检查/批准/修改。具备结构化四级压缩记忆架构以支持**超长篇（100万+字）**、事务式持久化（快照/回滚）、多 LLM 供应商策略（DeepSeek 默认，BYOK）、类 Scrivener 写作工作台，支持多格式导出（EPUB、Markdown、TXT）。

## Status

> **当前阶段: Charting 完成** — 地图已绘制，7 个工单已就位，4 个研究工单已由 AFK 子代理完成。下一步: 从 #01 领域模型基础 开始，逐个进行人机对话 (HITL) 决议。

## Notes

- **领域**: AI 辅助小说写作，超长文本生成，结构化记忆管理
- **后端**: Python 3.10+，FastAPI，SQLAlchemy / SQLModel
- **前端**: TypeScript，Vue 3，Pinia，Vite
- **包管理**: uv (Python)，pnpm (Vue)
- **每次会话前必须阅读**: AGENTS.md，docs/agents/*
- **核心参考架构**: ainovel-cli（三分法 Agent），MuMuAINovel（技能模块化），Openwrite_skill（单一真源 + 事务写章）
- **#1 优先级**: 写出一本好小说 > 架构纯粹性
- **默认 LLM**: DeepSeek，用户自备 API Key，可配置 OpenAI/Anthropic 降级

## Decisions so far

<!-- 已关闭工单索引 — 每个工单一行：标题、链接、一行决议摘要 -->

### 已完成的研究工单 (AFK)

- **#03 记忆架构** `.scratch/wayfinder/tickets/03-memory-architecture-detailed.md` → 决议: sqlite-vec + BGE-M3 做向量索引，SQLite FTS5 做全文检索，L1默认滑窗5章，L2每10章混合策略摘要+事件三元组，L3角色/世界观/风格分离存储
- **#04 LLM供应商** `.scratch/wayfinder/tickets/04-llm-provider-contract.md` → 决议: LiteLLM 做统一客户端，适配器模式包裹 OpenAI/Anthropic/Ollama，SSE流式，指数退避+断路器+降级链，按任务路由模型
- **#05 持久化策略** `.scratch/wayfinder/tickets/05-persistence-strategy.md` → 决议: SQLite WAL + SQLAlchemy 2.0 Async + SHA256去重 + gzip全量快照 + 50版本上限，Phase3可迁移PostgreSQL
- **#07 导出格式** `.scratch/wayfinder/tickets/07-export-format-support.md` → 决议: IR(Markdown) + 分步渲染架构，ebooklib(EPUB) + WeasyPrint(PDF) + python-docx(DOCX)

### 待现场决定的工单 (HITL)

- **#01 领域模型基础** — 阻塞 #02-#07，首个需人机对话解决的工单
- **#02 Agent管线协议** — 阻塞 #03
- **#06 前端工作台架构** — 独立，需人机对话

## Not yet specified

尚无法精确票证化的待定领域（随着前线推进而逐步清晰）：

- **小说状态机**: 草案→编辑→已完成 的状态流转，版本管理
- **部署架构**: Docker？单二进制？云原生？
- **中文文本处理**: 分词差异，CJK 段落切分，中英文混排
- **导入系统**: 导入已有小说（TXT/MD/EPUB）作为起点
- **搜索功能**: 百万字级全文检索
- **写作风格定制**: 用户自定义写作风格指南的存储与应用

## Out of scope

明确排除在本路线图之外的事项：

- 多用户协作 / 实时协同编辑
- 移动原生 App（PWA 待定但不计划）
- AI 模型微调 / 训练自定义模型
- 内容发布 / 分发平台
- 社交功能（评论、分享）
