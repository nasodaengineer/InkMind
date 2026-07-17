# ADR-0007: CLI 入口设计

## 状态

已采纳（2026-07-16）

## 背景

InkMind 需要用户命令行接口。前 6 个工单已产出完整的领域模型、Agent 协议、记忆压缩、Provider 策略和事务式持久化层，CLI 是将它们串联起来的最后一层。

## 决策

### 7-A 指令架构

采用标准 CLI 指令树，共 10 个子命令：

```
inkmind init              → 初始化新小说
inkmind write <title>     → 1 × Writer 写作
inkmind plan [N]          → 1 × Planner 规划前 N 章
inkmind review            → 1 × Editor 评审最新章节
inkmind next              → 1 轮完整协作（Plan→Write→Review→Finalize）
inkmind status            → 显示小说/章节当前状态
inkmind shell             → 交互式写作 REPL
inkmind commit            → 导出 JSON 快照
inkmind restore <file>    → 从快照恢复
inkmind version           → 版本信息
```

### 7-B 交互模式

- **模式 α（一次性）**：每个命令独立运行，完成后退出
- **模式 β（REPL）**：通过 `inkmind shell` 进入交互式写作 Shell
- 两个模式共享同一套命令实现

### 7-C 配置策略

三级配置 fallback（优先级递减）：
1. 命令行参数（最高优先级）
2. 环境变量（`INKMIND_DB_PATH`, `INKMIND_NOVEL_ID`）
3. `inkmind.toml` 配置文件（`project.novel_id`, `storage.path`）

### 7-D 输出格式

- **默认**：人类可读文本输出（带 emoji 图标）
- `--json` 标志：切换为 JSON 输出（stdout 可解析）

## 技术细节

### 依赖

- `argparse` — 标准库，零外部依赖
- `tomllib` — Python 3.11+ 标准库（fallback 用 tomli）

### 文件结构

```
inkmind/__main__.py              — python -m inkmind
inkmind/cli/
├── __init__.py                  — 模块文档
├── main.py                      — argparse 入口 + 命令分发
├── config.py                    — 三级配置加载器
├── formatter.py                 — 文本 / JSON 输出格式化
├── db.py                        — DB session 辅助
└── commands/
    ├── __init__.py              — 命令注册
    ├── init.py                  — inkmind init
    ├── write.py                 — inkmind write
    ├── plan.py                  — inkmind plan
    ├── review.py                — inkmind review
    ├── next.py                  — inkmind next
    ├── status.py                — inkmind status
    ├── shell.py                 — inkmind shell
    ├── commit.py                — inkmind commit
    ├── restore.py               — inkmind restore
    └── version.py               — inkmind version
```

### 每个命令的模式

每个命令模块暴露三个接口：
- `COMMAND` — 命令名（str）
- `HELP` — 帮助字符串
- `USAGE` — 用法示例
- `setup(subparsers)` — 注册 argparser
- `run(args)` — 执行逻辑

## 后果

### 正面

- **零额外依赖**：CLI 只用了 Python 标准库
- **可测试性**：每个命令可通过 `subprocess.run` 黑盒测试
- **可组合性**：`next` 命令演示了完整的流水线编排

### 负面

- **无实时流式输出**：当前命令在事务提交后一次性输出结果
- **无 async CLI**：使用 `asyncio.run()` 包装异步操作

### 后续优化

- 支持 `--watch` 模式实时监听流水线进展
- 支持 TAB 补全（argparse 原生支持）
