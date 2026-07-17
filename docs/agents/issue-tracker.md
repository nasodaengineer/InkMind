# Issue Tracker — GitHub 模式

本仓库的 issue 和 PRD 以 GitHub Issues 形式管理（`nasodaengineer/InkMind`），所有操作使用 `gh` CLI。

## 操作约定

- **创建 issue**：`gh issue create --title "..." --body "..."`，多行正文用 heredoc
- **读取 issue**：`gh issue view <number> --comments`，用 `jq` 过滤评论并获取标签
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，按需加 `--label` / `--state` 过滤
- **评论**：`gh issue comment <number> --body "..."`
- **加/删标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

仓库信息从 `git remote -v` 推断——在 clone 目录内 `gh` 会自动识别。

## Pull Request 作为 triage 来源

**PR 作为请求来源：否。**（若本仓库将外部 PR 视为功能请求，改为 `yes`；`/triage` 会读取此标志。）

设为 `yes` 时，PR 与 issue 走相同的标签和状态流转，使用 `gh pr` 系列等价命令：

- **读 PR**：`gh pr view <number> --comments`，diff 用 `gh pr diff <number>`
- **列出待 triage 的外部 PR**：`gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`，只保留 `authorAssociation` 为 `CONTRIBUTOR`、`FIRST_TIME_CONTRIBUTOR` 或 `NONE` 的（过滤掉 `OWNER`/`MEMBER`/`COLLABORATOR`）
- **评论/标签/关闭**：`gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`

GitHub 的 issue 和 PR 共用一个编号空间，裸 `#42` 可能是任一种——先 `gh pr view 42`，失败再 `gh issue view 42`。

## 当技能要求"发布到 issue tracker"时

创建一个 GitHub issue。

## 当技能要求"获取相关工单"时

运行 `gh issue view <number> --comments`。

## Wayfinding 操作

供 `/wayfinder` 使用。**地图（map）** 是单个 issue，**工单（ticket）** 是其子 issue。

- **Map**：一个带 `wayfinder:map` 标签的 issue，正文承载 Notes / Decisions-so-far / Fog。`gh issue create --label wayfinder:map`
- **子工单**：通过 GitHub sub-issue 关联到 map（`gh api` 调 sub-issues 端点）。sub-issues 不可用时，把子工单加入 map 正文的任务列表，并在子工单正文顶部写 `Part of #<map>`。标签：`wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。认领后指派给驱动的开发者
- **阻塞关系**：GitHub **原生 issue 依赖**——规范且 UI 可见的表示。添加边：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 是阻塞方的数字 **database id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，不是 `#编号` 也不是 `node_id`）。GitHub 通过 `issue_dependencies_summary.blocked_by` 报告（仅未关闭的阻塞——实时门槛）。依赖功能不可用时，降级为在子工单正文顶部写 `Blocked by: #<n>, #<n>` 一行。所有阻塞方关闭后工单即解除阻塞
- **前沿查询**：列出 map 的未关闭子工单（`gh issue list --state open`，限定在 map 的 sub-issues / 任务列表内），剔除有未关闭阻塞（`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 行中有未关闭 issue）或已有 assignee 的；按 map 顺序取第一个
- **认领**：`gh issue edit <n> --add-assignee @me`——会话的第一次写操作
- **解决**：`gh issue comment <n> --body "<答案>"`，然后 `gh issue close <n>`，再把上下文指针（gist + 链接）追加到 map 的 Decisions-so-far

## 历史归档

2026-07-17 之前的本地 Markdown 工单（`.scratch/code-review-fixes-2026-07/`）已迁移到 GitHub Issues，本地文件仅作归档保留，不再更新。
