# Issue Tracker — Local Markdown 模式

## 目录结构

```
.scratch/
  <feature-slug>/
    spec.md          # 规格文件
    issues/
      01-<slug>.md   # 工单文件，从 01 开始编号
      02-<slug>.md
      ...
```

## 工单格式

每个 `.md` 工单文件包含以下 YAML front matter：

```markdown
---
title: <简短标题>
status: needs-triage | needs-info | ready-for-agent | ready-for-human | wontfix
created: <YYYY-MM-DD>
labels: [bug, feature, writing, memory, agent, perf]
---

## 描述

问题/任务的详细描述。

## 复现步骤（可选）

1. ...
2. ...

## 上下文

相关文件、日志或背景信息。
```

## 状态流转

```
needs-triage ──→ needs-info ──→ ready-for-agent ──→ ready-for-human
                                        │                  │
                                        └──→ wontfix ←────┘
```

## 操作约定

- `Status:` 行记录当前 triage 状态
- 每个工单建议一条目的、可独立追踪的问题
- 工单编号从 `01` 开始，递增分配
