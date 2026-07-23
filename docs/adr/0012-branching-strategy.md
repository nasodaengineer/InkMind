# ADR-0012: 分支策略 — GitHub Flow + 波次集成

## 状态

已采纳（2026-07-23）

## 背景

项目从初始化至今未正式定义分支策略。实际开发中自然形成了以下模式：

1. **仅 `main` 一条长期分支**，无 `develop`、`release/*`、`hotfix/*`。
2. **功能分支**按 `feat/<issue>-<desc>` 命名，独立开发。
3. **波次集成**：多个功能分支汇入一条 `merge/wave<N>` 分支，验证后一次性合回 `main`。
4. 另有 `prototype/*`（交互原型）和 `research/*`（调研报告）两类辅助分支，不合入 main。

此模式接近 GitHub Flow，但多了"波次"这一批量集成层。Git Flow 的 develop/release/hotfix 体系对当前单人 + AI Agent 协作的开发节奏过重，不予采用。

## 决策

### 12-A 长期分支

- **`main`**：唯一长期分支，始终保持可运行状态（`uv run pytest` 全绿）。
- 不引入 `develop`、`release/*`、`hotfix/*`。

### 12-B 功能分支命名

| 前缀 | 用途 | 合入目标 |
|------|------|----------|
| `feat/<issue>-<desc>` | 功能开发 | 波次分支或 main |
| `fix/<issue>-<desc>` | 缺陷修复 | main（可直接合入） |
| `prototype/<desc>` | 交互原型，不合入 | — |
| `research/<desc>` | 调研报告，不合入 | — |

- `<issue>` 为 GitHub Issues 编号。
- `<desc>` 使用 kebab-case 英文短语。

### 12-C 波次集成

当一轮规划产出多个并行功能分支时，使用波次分支做批量集成：

1. 从 `main` 创建 `merge/wave<N>`。
2. 各 `feat/*` 分支完成后合入（merge）`merge/wave<N>`。
3. 在波次分支上运行完整测试 + 手动验证。
4. 验证通过后，`merge/wave<N>` 合入 `main`（merge commit，保留历史）。
5. 波次分支合入后删除。

若单个功能独立且影响面小，可跳过波次直接 PR 合入 main。

### 12-D 合并规则

- 合入 main 必须通过 PR（至少 self-review + CI 绿灯）。
- 合并方式：`--no-ff` merge commit，保留分支拓扑。
- 禁止对 main 执行 force-push。
- 功能分支开发期间定期 rebase main，保持线性提交。

### 12-E 提交信息

沿用现有格式：

```
<type>: <中文摘要>（#<issue>）
```

type 取值：`feat` / `fix` / `docs` / `chore` / `refactor` / `test` / `prototype` / `merge`

### 12-F 标签与发布

- 里程碑完成时在 main 打 tag：`v<major>.<minor>.<patch>`。
- 当前阶段（0.x）不维护 release 分支，tag 即发布点。

## 执行机制

### 本地 Git Hooks（`.githooks/`）

通过 `git config core.hooksPath .githooks` 启用：

| Hook | 职责 |
|------|------|
| `commit-msg` | 校验提交信息格式 `<type>: <摘要>（#issue）`，Merge/Revert 自动放行 |
| `pre-commit` | 运行 `ruff check` + `ruff format --check` + `mypy`，任一失败则阻止提交 |

### CI（`.github/workflows/ci.yml`）

- 触发：PR 到 `main` 或 `merge/**`，以及 push 到 `main`。
- 两个 job：`lint`（ruff + mypy）、`test`（pytest）。

### GitHub 分支保护（main）

- 禁止 force-push 和分支删除。
- 合入必须通过 PR，且 `lint` + `test` 状态检查通过（strict 模式，要求分支已 rebase 到最新 main）。
- 管理员同样受约束（enforce_admins）。

## 后果

- 分支模型简单，与 GitHub Issues + PR 原生对齐。
- 波次机制适配"批量规划 → 并行开发 → 一次集成"的 Agent 协作节奏。
- 不引入 Git Flow 的多分支管理开销。
- 风险：波次分支存活时间过长时可能与 main 产生冲突——通过 12-D 的定期 rebase 缓解。
