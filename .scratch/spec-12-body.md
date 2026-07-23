## Problem Statement

作者用 InkMind 写长篇小说时，只有 CLI 一个入口。AI 流水线能力已经齐备（Planner/Writer/Editor/MemoryKeeper 协作、L0–L3 四级记忆、事务式持久化、多 Provider 策略），但长篇创作的关键环节——定大纲、审章节、逐段批注、批示修订、管理素材、看护记忆与成本——在命令行里既看不见也摸不着：正文流式生成不可见、批注无法锚定到段落、大纲没有结构视图、素材没有归处、LLM 调用花了多少钱无从知晓。作者需要一个可视化混合工作台：AI 流水线自动生成与评审，人在关键节点介入（定大纲、审章节、行内批注、批示修订），每章人工把关。

## Solution

一个本地 Web 应用：FastAPI 后端 + React/Vite/TipTap 前端，单用户、无账号体系，与现有 CLI 共存、共享同一 SQLite。`uv run inkmind serve` 一键启动。

首版范围（八大块）：

1. **整体信息架构**——窄图标轨 4 目的地：工作区 / 大纲 / 素材 / 系统；落地页 = 工作区
2. **章节写作台**——TipTap 编辑器 + SSE 流式生成 + 版本历史 + 手动编辑
3. **评审与修订交互**——结论卡 + 行内批注（选段留言、五态 thread）+ 修订编排器（勾选批注 → 五区序列化实时预览）
4. **大纲规划视图**——三级体系（总纲/卷纲/章纲）书脊树 + 卷内批量规划 5–50 章 + AI 起草总纲/卷纲
5. **记忆只读面板**——右侧抽屉纯 L1 伴随态 + 系统页全量档案（L2 时间线 / L3 五类分组 / L0 一行统计）
6. **素材管理**——导入 + LLM 拆解 + 标签分类 + FTS 搜索 + 写作台旁路面板一键插入
7. **观测统计面板**——run 级 stats 聚合 + 压缩任务 stats，今日/7日/全部三档窗
8. **模型设置页**——Provider/路由/并发速率可编辑，SQLite app_settings 单行 JSON 为真相源，CLI/UI 同一读取链

驱动节奏：每章人工把关——一圈流水线自动跑完（≤3 次修订循环）后停在 AWAITING_HUMAN 人工门，人裁决：提交定稿 / 批示再修 / 手动改。

本规格综合自 wayfinder 地图 #12 的 20 张已关闭工单（#13–#32，决策全文见各 issue）。

## User Stories

### 启动与信息架构

1. 作为作者，我想用一条命令（`uv run inkmind serve`）启动工作台并在浏览器打开，以便零配置开始创作。
2. 作为作者，我想在缺少前端构建产物时看到显式报错与构建指引，而不是静默失败，以便知道如何修复。
3. 作为作者，我想在窄图标轨的 4 个目的地（工作区/大纲/素材/系统）之间切换，以便界面始终聚焦当前任务。
4. 作为作者，我想落地页直接进入工作区的当前章节，以便无缝继续上次创作。
5. 作为作者，我想创建/打开小说并看到章节列表与每章状态，以便管理多部作品与进度。

### 大纲规划（三级体系）

6. 作为作者，我想在大纲页看到书脊树（总纲 → 卷 → 章三级折叠，带章状态点、▲/★ 节奏标记、待回收伏笔标记），以便一眼把握全书结构。
7. 作为作者，我想就地编辑总纲六字段（主线/核心矛盾/结局/卖点/世界背景/金手指），以便锁定全书方向。
8. 作为作者，我想让 AI 起草总纲——可带自由文本提示，也可零输入由 AI 自主命题，以便快速冷启动。
9. 作为作者，我想在 AI 起草会覆盖非空总纲/卷纲时被强制要求显式确认，以便不丢失已有内容。
10. 作为作者，我想创建卷并就地编辑卷纲四字段（阶段目标/主线/支线/卷末悬念），以便组织中篇结构。
11. 作为作者，我想让 AI 为单卷填补卷纲、或把全书一次性拆成 2–20 卷，以便快速搭骨架。
12. 作为作者，我想看到每卷的预计章数（planned_size）与派生章节区间，以便规划篇幅分配。
13. 作为作者，我想调整卷规模（缩卷下限 = 已排章数、扩卷只改数字），以便结构随创作演进。
14. 作为作者，我想只能尾部追加卷、只能硬删空卷，以便已排章节永不悬空。
15. 作为作者，我想在卷节点上用 ghost 行 → 卷内表单一键批量规划 5–50 章章纲（必落单卷区间内），以便保持宏观情节连贯。
16. 作为作者，我想规划进行中看到 run 面板进度并能取消，以便控制 LLM 调用。
17. 作为作者，我想对未开工章点 ↻ 重新规划（预填现有内容、覆盖生成），以便修正情节走向。
18. 作为作者，我想已定稿/已开工的章自动锁定不可被重规划覆盖，以便保护已完成内容。
19. 作为作者，我想人工就地编辑章纲字段（标题/摘要/关键事件/节奏标记/视角角色/出场角色），以便微调每一章。

### 写作台：生成、评审、版本

20. 作为作者，我想在工作区一键为当前章启动生成，正文随 SSE 逐字流出，以便即时阅读与判断。
21. 作为作者，我想生成中看到细状态条（当前 phase、Writer 状态）与 ■ 停止按钮，以便随时中断。
22. 作为作者，我想中断或崩溃后部分稿保留在 run 记录中、由我裁决保留或丢弃，以便不浪费已生成内容。
23. 作为作者，我想一章自动跑完一圈流水线（Writer → Editor → ≤3 次修订循环）后停在人工门，以便我只在关键节点介入。
24. 作为作者，我想评审结论卡置顶显示（approve / needs_revision + 问题列表）、正文只读但可加批注、底部有裁决条，以便快速完成评审。
25. 作为作者，我想同一章重复启动 run 被 409 拒绝，以便杜绝并发写冲突。
26. 作为作者，我想刷新或断线后 SSE 自动重连并从无事件日志快照恢复进度，以便不丢失进行中的状态。
27. 作为作者，我想只有终稿才落库、版本数等于人见稿数，以便版本历史干净可信。
28. 作为作者，我想在标题栏 v{n}▾ 查看只读历史版本、并看段落对齐的段内字级 diff，以便对比内容演化。

### 行内批注与批示修订

29. 作为作者，我想选中正文片段弹出浮动层、按四类 intent 留下批注，以便精确指示修改点。
30. 作为作者，我想点击锚点在正文流内展开批注卡（总评卡固定在文首、失效批注沉章末未定位区、已解决可开关），以便批注不打断阅读。
31. 作为作者，我想批注 thread 支持多轮 comments，以便围绕一个点迭代沟通。
32. 作为作者，我想在修订编排器左侧勾选批注、右侧实时看到发给 Writer 的五区序列化预览（指纹实时计算），以便确认 AI 将收到的指令。
33. 作为作者，我想人工批示的修订直达 Writer 且只跑一次（不过 Editor），以便快速执行我的明确意图。
34. 作为作者，我想回稿后批注自动重定位——相似度 ≥0.9 自动落位、0.5–0.9 待我确认、<0.5 进未定位区，以便批注跟随正文演化。
35. 作为作者，我想回稿绝不自动 resolve 我的批注，以便保留最终判断权。
36. 作为作者，我想手动编辑正文时批注 mark 跟随、后台扫描对账、保存时指纹刷新，以便锚点长期可靠。

### 手动编辑与提交定稿

37. 作为作者，我想在人工门内随时手动改正文、每次显式保存产生一个人见版本（source_trace=manual），以便保留完整演化史。
38. 作为作者，我想取消编辑零成本（不产生版本），以便放心试错。
39. 作为作者，我想基于过期内容的保存被 409 拒绝（base_digest 乐观锁），以便不会 unknowingly 覆盖更新。
40. 作为作者，我想在人工门内任意交错手动改与批示再修、最后一键提交（finalize）定稿，以便按我的节奏收尾。
41. 作为作者，我想定稿后章节进入 FINALIZED 并触发记忆更新（L0 索引/L1 滑窗/L2 压缩链路），以便后续章节有一致上下文。
42. 作为作者，我想 FINALIZED 章节拒绝内容修改，以便记忆摘要不与正文脱节。

### 素材管理

43. 作为作者，我想用全页导入向导粘贴原文并启动 LLM 拆解（粘贴 → 进度 → 预览提交三步），以便把参考资料转化为可复用片段。
44. 作为作者，我想拆解按 8000 字块推进（段落边界吸附、无重叠）、单任务上限 10 万字、单块失败不中断整体，以便大批量导入也可控。
45. 作为作者，我想在预览页逐条编辑五字段、覆盖来源、删除/合并/新建片段，默认全选、只提交选中项，以便控制入库质量。
46. 作为作者，我想重复导入同一原文被幂等识别并返回已有记录，以便不制造重复数据。
47. 作为作者，我想在来源台账看到每个来源的状态徽标与块级状态小丸（✓✗!）、并能对失败块/低质块点选重跑，以便管理拆解质量。
48. 作为作者，我想我编辑过的片段（user_edited）在重跑时被整条跳过保护、且可手动清标，以便不丢我的修改。
49. 作为作者，我想收件箱待办区浮顶显示拆解中/失败块/低质块三类卡（空时收起），以便一进素材页就看到待办。
50. 作为作者，我想用关键词（空格分词 AND）+ 类型多选 chip + 标签单选 chip 搜索片段，以便快速找到素材。
51. 作为作者，我想在写作台右侧抽屉旁路搜索素材、卡片展开看四字段、一键插入正文，以便写作中即时取用。
52. 作为作者，我想录入标签时看到已有标签自动补全（Top 50 复用 + 3–6 个建议），以便标签体系自然收敛。
53. 作为作者，我想每个片段带来源血缘（source_quote 定位原文）与 reusability_note（复用注意），以便安全复用、避免抄袭红线。
54. 作为作者，我想手工新建的片段归入"手工片段" pseudo-source 桶，以便台账语义统一。

### 记忆只读面板

55. 作为作者，我想在右侧抽屉看到 L1 伴随态（角色状态卡五字段、待回收伏笔表、滑窗状态行），以便写作中掌握活跃上下文。
56. 作为作者，我想逾期伏笔高亮，以便及时安排回收。
57. 作为作者，我想在系统页看到 L2 时间线卡（总摘要常显 + 结构化事件清单展开、角色显示名字而非 UUID），以便回顾跨章情节。
58. 作为作者，我想在系统页看到 L3 长期知识按五类分组（截断可展开、纯只读），以便核对稳定设定。
59. 作为作者，我想看到 L0 全文索引的一行统计而不是字段细节，以便了解索引规模。
60. 作为作者，我想面板在打开时与 run 完成时自动刷新（无轮询），以便看到最新状态。

### 观测统计

61. 作为作者，我想看到今日/7日/全部三档窗口的调用总览与成本估算（内置单价表、带"估算"标注），以便掌握开销。
62. 作为作者，我想按 provider / model / agent 三个维度加 error 分布拆分统计，以便定位问题调用。
63. 作为作者，我想看到 run 历史与压缩任务小节（含最新失败的压缩任务），以便追踪后台活动。

### 模型设置

64. 作为作者，我想在设置页增删 Provider 连接参数、编辑 Agent→模型路由绑定（模型名白名单校验）、调整并发与速率，以便切换供应商与配额。
65. 作为作者，我想 Retry 策略只读展示，以便了解重试行为但不误改。
66. 作为作者，我想 API Key 只显示"已设置"状态、永不入库（仍走环境变量），以便不泄露密钥。
67. 作为作者，我想新配置在下一个 run 启动时生效、进行中的 run 用旧配置跑完，以便行为可预期。
68. 作为作者，我想 CLI 与 UI 读同一份配置（SQLite 单行 JSON），以便两处行为一致。

### 恢复、快照与并发

69. 作为作者，我想服务重启后进行中的 run 自动标记为 interrupted、部分稿留待裁决，以便干净恢复。
70. 作为作者，我想快照导出/恢复包含全部新表（卷/总纲/素材三表），恢复后 FTS 索引自动重建，以便备份完整可用。
71. 作为作者，我想所有写事务在跨进程文件锁保护下序列化提交，以便 CLI 与 Web 共用同一数据库不打架。

## Implementation Decisions

### A. 工程基座与形态（#26）

- Python 依赖正规化是前提：pyproject 补 `[project]` 与全量依赖声明、Python >= 3.11、`uv sync` 后一律 `uv run`。
- 布局：前端 `web/`（仓库根目录），后端 `inkmind/api/`（与 `inkmind/cli/` 对称）；新建 `inkmind/materials/` 模块承载素材拆解编排。
- 前端工具链：pnpm（packageManager 字段锁定）+ Node 22 LTS；UI 基线 = Tailwind v4 + shadcn/ui（Radix copy-in）+ lucide-react；路由 = React Router v7 库模式；REST 数据 = TanStack Query；SSE = 薄 EventSource 自封装（不过 Query）；不引入全局状态库与表单库。
- 开发双进程：`serve --reload` 只出 /api + `pnpm dev`（vite proxy 透传 SSE）；生产单进程：FastAPI 托管 `web/dist`（SPA fallback，dist 不入库）。
- 一键启动：`uv run inkmind serve`（CLI 新增 serve 子命令内嵌 uvicorn.run，127.0.0.1:8000，--reload/--open；dist 缺失显式报错、不自动构建）。不用 uv script / concurrently。
- 修改模块：`inkmind/cli/`（serve 子命令）；新建：`inkmind/api/`、`inkmind/materials/`、`web/`。

### B. 数据模型与存储变更

**新表：**

- `runs`（#21/#22/#32）——执行记录层：kind（generate/revise/finalize/plan）、plan 时带 level（chapter/spine/volume）与 chapter_id 可空；单轴状态 running → 五终态（awaiting_human/completed/failed/cancelled/interrupted，awaiting_human 是终态、球在人手）+ phase 遥测；partial_content（部分稿）+ stats（聚合快照）+ AI 起草被覆盖的旧值随 run 存留；(chapter_id, running) 部分唯一索引实现章节级互斥（409）。
- `volumes`（#31）——卷一等实体：volume_index 显式排序（尾部追加）、卷纲四字段平铺、planned_size 软规模；start_index 派生 = 1 + Σ 前序卷 planned_size，区间 `[start, start+planned_size)`；增删 = 尾部追加 / 仅空卷硬删。
- `outline_spines`（#31）——总纲独立表：novel 1:1、六字段平铺、懒创建 upsert（worlds 同构）。
- `material_sources` / `material_chunks` / `material_fragments`（#17）——按小说隔离：sources 原文+digest+状态；chunks 块级幂等状态机 pending→done/failed/low_quality；fragments 全字段 + source_chunk_id 血缘，tags 走 JSON 列不设 Tag 表。
- `app_settings`（#25）——app 级 singleton 单行 JSON（LLMConfig 真相源，不按 novel 隔离；懒 seed，无记录/表不存在 → 代码默认兜底）。
- FTS5 trigram 虚拟表（#17）——只索引 fragments 的 title+content；<3 字符查询降级 LIKE；json_each 标签过滤；应用层同步 + rebuild 兜底。

**既有表变更：**

- `chapters`：+ volume_id（NOT NULL 外键）；rhythm_marker 枚举 + pov + involved 补落库（#31）；ChapterStatus 增加 `AWAITING_HUMAN` 人工门（#21）——run 期间章节停 WRITING/REVISING，中间翻转收敛进 phase。
- `compression_tasks`：+ 同构 stats 挂点（#24），补压缩成本观测漏洞。
- `ProviderStats`：+ agent_name（#24），统计全维度 = provider/model/agent/error。
- `PipelineState.total_chapters` 保持「已规划高水位」语义，与全书预计规模（Σ planned_size）解耦（#31）。
- 枚举集中：素材片段 8 枚举（excerpt/scene_idea/character_seed/setting_seed/dialogue_sample/style_sample/technique/misc）集中在 `inkmind/models`，前后端同契约、用户不可扩展（#15）。
- 批注模型入核心包（#18）：AnchorFingerprint（W3C quote 指纹，跨版本真相）+ 单轴五态 thread + 多轮 comments + intent 四类（note 类不序列化进 prompt）。

**事务边界扩展（ADR-0005 族谱）：**

| 事务 | 内容 | 来源 |
|---|---|---|
| T2（扩展） | 落卷校验 + 仅 PLANNED 可覆盖硬删 + 带卷插入；拆卷时规划产物批量落库 | #31/#32 |
| T6 | 素材导入（source + chunks 落库） | #17 |
| T7 | 拆解提交（片段批量落库；重跑低质块时替换非 user_edited 旧片段） | #17 |
| T8 | Run 启动（含从 DB 重读配置重建 LLMClient） | #22/#25 |
| T9 | 落稿收口（内嵌 T1/T2） | #22 |
| T10 | Run 终态收口 | #22 |
| T11 | 设置保存 | #25 |
| T12 | 手动编辑落稿（归档旧版 + 写章 + fingerprint_updates 三方原子；base_digest 校验在事务内；PATCH 按是否含 content 分流） | #27 |

checkpoint（2s/500 字节流式落 partial_content）与单行写不编号（#22/#31）。

**幂等与恢复：**

- digest 幂等路径不变（#21）；素材导入幂等 = (novel_id, content_digest) UNIQUE，重复导入返回已有；块级 digest 重跑只补 failed；片段不自动去重（#17）。
- 手动编辑不走 T1 content-digest 全局去重（改回旧文会被静默吞掉），幂等 = base_digest 乐观锁、重试即 409（#27）。
- 恢复第 9 步：running 状态 run → interrupted（#21）；拆解中断不进 RecoveryManager，走 UI 继续拆解（#17）。
- 快照：含 volumes/outline_spines（restore 卷先章后）与素材三表（restore 后重建 FTS）（#17/#31）；app_settings 配置不进小说快照（#25）。
- 软删 source 不级联；级联删除跳过 user_edited 片段（#17）。
- 并发安全沿用 ADR-0011：所有写事务 commit 在 `{db_path}.lock` 文件锁下序列化。

### C. 执行层 Run 与流水线编排（#21/#22/#32）

- Run 四种 kind：generate / revise / finalize / plan。plan 复用执行层、chapter_id 可空、body 加 volume_id 必填（落单卷）+ level 判别（chapter 默认 / spine / volume）。
- 生命周期：running → awaiting_human / completed / failed / cancelled / interrupted；取消与崩溃一套语义（部分稿留 run.partial_content，人裁决保留/丢弃）；仅终稿落库。
- 每章控制环：一圈流水线自动跑（Writer → Editor → ≤3 次修订循环）→ 停 AWAITING_HUMAN；人工批示 = 单次 revise 直达 Writer、不过 Editor、回稿仍停人工门；提交 = finalize run。
- 章节级互斥：(chapter_id, running) 部分唯一索引，冲突 409。
- 修订保护不变：每章最多 3 次修订迭代，超限自动降级 APPROVED。

### D. Planner 能力扩展（#32）

- `PlanRequestPayload` 加 level / prompt / volume_count；`PlannerAgent` 新增 draft_spine / draft_volume / split_volumes。
- 输入可选自由文本；零输入 AI 自主命题。
- 卷纲双粒度：单卷填补 + 全书拆卷（2–20 卷）；零卷冷启动拆卷 409（volumes_exist 语义反转详见 #32）；spine_required 前置校验。
- 直接落库 + 非空 confirm_overwrite 服务端强制 + 旧值随 run 存留；无 user_edited 概念。
- 注入面：Planner 收全量三级（总纲六字段 + 卷四字段 + 前卷悬念）；Writer 收精简锚块三行。
- 输出结构级硬校验（形状/数组长度/planned_size≥1），重试 ≤3，字段级容忍空串。
- 事务：填补/spine 单行不编号；拆卷走 T2 扩展。

### E. REST API 面（#22/#23/#24/#25/#31）

- 约定：GitHub 风格资源骨架（集合挂父 / 单体扁平）、chapter_index 与 volume_index 寻址、`/api` 前缀无版本；裸数组为默认返回形态、仅 fragments 分页；错误统一 `{error:{code,message,details}}` 信封；一切状态冲突返回 409；代码优先生成 OpenAPI。
- 章节面：PATCH 一端两用（含 content → T12 手动编辑；否则大纲字段单行写）；base_digest 乐观锁；无独立 review 资源；PATCH content 白名单仅 AWAITING_HUMAN，其余 409（chapter_not_editable），FINALIZED 首版拒改。
- Run 面：启动（按 kind）、取消、查询；revise 只收 thread_ids，服务端 prompts 模块权威序列化（#18 五区渲染）。
- 批注面：无单体 GET；re-anchor 端点合并 fuzzy 确认与人工重锚；apply-relocation 批量回写。
- 素材面：拆解不进 Run（独立进度通道）；merge 专用原子端点；tags 自动补全端点；搜索端点（FTS / LIKE 降级）。
- 大纲面：/volumes 集合挂父 + volume_index 寻址；/spine 单体；plan run body volume_id 必填；不造聚合端点。
- 记忆面（#23）：四端点 window / archives / long-term / status——裸数组、不分页、无过滤、无单体 GET；status 含最新 failed 压缩任务；角色 UUID 服务端转名；L3 = l3_permanent blob 唯一真相、五类分组、纯只读；L0 永不进面板、仅一行统计。
- 观测面（#24）：stats/overview?window（today/7d/all，默认 today，现算不物化）+ compression-tasks 裸数组 + 复用 runs 端点；内置单价表实算成本（现 0.0 占位）带估算标注；无重置，与执行史共生灭。
- 设置面（#25）：LLMConfig 读写（Provider 连接参数全字段可增删 + 路由绑定模型名白名单校验 + 并发/速率可改；Retry 只读）；API Key 仅显"已设置"状态、永不入库、仍走 env；生效 = T8 重读重建、进行中 Run 用旧配置跑完、CLI 每次启动读 DB 天然热；`inkmind.toml` 永不引入 `[llm]`；`INKMIND_LLM_FAKE` 压制一切。

### F. SSE 协议（#21）

- 5 种事件：phase / token / verdict / done / error；正文 SSE 直流显示。
- 无确认打断：停止留部分稿可丢弃/保留。
- 重连：无事件日志快照重连（客户端从快照恢复进度）。

### G. 行内批注子系统（#13/#18）

- 编辑器：自研薄 comment Mark（TipTap/ProseMirror）+ 浏览器端 CommentStore；不引 Yjs、不用 TipTap Pro Comments。
- 跨版本真相 = AnchorFingerprint（W3C quote 指纹）；整章重写后加权模糊重定位，阈值 0.9/0.5 分流（≥0.9 自动 / 0.5–0.9 待确认 / <0.5 未定位区）；浏览器端执行。
- thread 单轴五态 + 多轮 comments + intent 四类；note 类不序列化。
- `RevisionRequestPayload` 加结构化 AnnotationRef（thread_id 随协议流转）；prompt 五区渲染；orphaned 批注照发；Writer 输出维持纯正文。
- 回稿绝不自动 resolve；手动编辑 mark 跟随 + 扫描对账；保存时指纹刷新。

### H. 素材子系统（#14/#15/#16/#17/#28）

- 拆解四件套：低温 0.3 + 严格 schema/枚举白名单 + ≤3 重试 + 降级兜底；sha256 增量指纹同构 digest 幂等。
- 片段字段：title（≤20 字）/content（≤2000 字，excerpt 豁免）/type（白名单，非法降级 misc + 埋点）/tags（3–6 软引导、Top 50 复用、无硬上限）/source/source_quote（8–50 字子串校验、excerpt 豁免）/reusability_note（来源绑定必产，防抄袭红线）/user_note 分家；user_edited 整条保护 + 重跑跳过 + 可清标。
- 类型判定规则 Y：先判内容专类、逐字对话归 dialogue_sample；逐字性由 source_quote 存在性表达。
- LLM 输出契约：单 JSON 信封（schema_version + fragments，无 summary）；1–10 条指引 + 20 硬上限；失败链 = 清洗层 + 回喂重试×3；单块失败不中断；低质只标记不自动重试；「原文存 excerpt」逃生门。
- 输入切块：单段连续文本、8000 字/块、段落边界吸附、无重叠、单任务 10 万字上限。
- 拆解路由：material_decomposer → flash 非流式。
- 预览页：五字段编辑 + source 覆盖 + 删除/合并/新建；默认全选、只入选中；编辑触发 user_edited。

### I. 前端页面与关键交互（#19/#20/#28/#30）

- 信息架构（#19）：窄图标轨 4 目的地（工作区/大纲/素材/系统）；落地页 = 工作区；评审/修订 = 工作区模式 tab（写作|评审|修订）；记忆/素材 = 右侧抽屉（素材另保留全页）；观测/设置/记忆浏览 = 系统页 tab；每章控制环不出一屏。
- 写作台（#20）：行内嵌卡批注（点锚点正文流内展开；总评卡文首；失效沉章末未定位区；已解决可开关；抽屉批注退为索引）；正文保最宽列；状态区 = 模式 tab 下细状态条（生成中整条换 Writer 状态 + ■停止）；选段浮动层 intent 四类；修订编排器左勾选右五区序列化实时预览（指纹实时算）；版本入口 = 标题栏 v{n}▾ 只读查看 + 段落对齐段内字级 diff（并排双栏不做）；素材旁路 = 空格分词 AND + 类型多选/标签单选 chip + 卡片展开四字段；评审 mode = 结论卡置顶 + 只读正文可加批注 + 裁决条。
- 素材全页（#28）：来源台账为主体（accordion 来源卡 = 标题/字数/状态徽标/块状态小丸 ✓✗! 可点重跑/计数/操作；展开见块行 + 片段子表 dense table；手工片段 pseudo-source 桶）；搜索/chips 非空时切跨来源平铺结果表；收件箱待办区浮顶（拆解中/失败块/低质块三类卡，空时收起，= 台账内联操作快捷面）；导入 = 全页步骤流（粘贴 → 进度 → 预览提交，提交/取消回台账）；不做三栏/modal。
- 大纲视图（#30）：书脊树 · 层级导航 + 详情面板——左树三级折叠（总纲/卷/章 + 状态点 + ▲★ 节奏 + 待回收伏笔标记，伏笔徽标纯 L1 派生零新存储）+ 右详情按节点切换；总纲六字段/卷纲四字段人工所有、就地可改；卷节点驱动规划（ghost 行 → 卷内表单 5–50 落卷内 → run 面板取消）；重规划章级 ↻ 预填覆盖未开工；演示态语义：已定稿锁定 / 重规划仅覆盖未开工 / 5–50 约束。

### J. ADR 修订义务

- ADR-0010：「stats 不进 SQLite」否决项实现期修订——run.stats 聚合快照为唯一持久源（不建 per-call 表、CLI 不纳入）（#24）。
- ADR-0005：T6–T12 入族谱 + 恢复第 9 步（running → interrupted）。
- CONTEXT.md 词汇随实现补齐（Run、AnnotationThread、MaterialFragment 等；Volume/OutlineSpine/RhythmMarker/UnstartedChapter 已入）。

## Testing Decisions

**好测试的标准**：只测外部行为，不测实现细节——对后端即 HTTP 请求/响应、SSE 事件序列、SQLite 中可见状态；对前端纯逻辑即输入/输出。不 mock 内部模块、不断言内部调用链。

**缝一（主缝，后端）：HTTP API 边界。** FastAPI 测试客户端（httpx ASGI transport）+ 临时目录真实 SQLite + `INKMIND_LLM_FAKE=1` 走 ScriptedLLMClient。工作台的一切后端行为都经此缝可达：

- Run 生命周期：启动/phase 推进/SSE 五事件序列/取消/部分稿裁决/章节级 409 互斥/重启后 interrupted（第二个 app 实例挂同一 DB 文件模拟重启）。
- 章节：PATCH 分流（content → T12 / 大纲字段单行写）、base_digest 409、非 AWAITING_HUMAN 409、FINALIZED 拒改、版本归档与人见版本计数。
- 批注：thread CRUD、re-anchor（确认与人工重锚合并）、apply-relocation 批量回写、revise 仅收 thread_ids。
- 素材：导入幂等（重复返回已有）、拆解提交 T7、重跑只补 failed/替换非 user_edited、merge 原子端点、搜索（FTS、<3 字符 LIKE 降级、json_each 标签过滤）、tags 补全。
- 大纲：卷 CRUD（尾部追加/空卷硬删/缩卷下限）、spine 懒创建 upsert、plan run（volume_id 必填、confirm_overwrite 强制、spine_required 前置、仅 PLANNED 可覆盖）、AI 起草三级校验与 ≤3 重试。
- 记忆四端点、stats 三档窗与压缩任务小节、设置读写（T8 重读生效、Retry 只读、API Key 永不入库）。
- 快照：含新表导出/恢复往返、restore 后 FTS 重建、卷先章后。

**缝二（浏览器端纯逻辑）：Vitest 单元测试。** API 缝够不到的浏览器端逻辑：

- CommentStore：W3C quote 指纹生成、加权模糊重定位三档阈值（≥0.9 / 0.5–0.9 / <0.5）、扫描对账、手动编辑 mark 跟随。
- SSE 客户端：断线重连 + 快照恢复。

React 组件不加测试缝（薄渲染层）；不做浏览器 E2E。

**现有缝扩展（非新缝）**：T6–T12 事务原子性（含 T9 内嵌 T1/T2）、恢复第 9 步、新表序列化往返——沿用 `tests/test_integration.py` 的既有风格（真实 SQLite + UnitOfWork）。

**支撑改动**：ScriptedLLMClient 扩展确定性 `chat_stream`（token 分片、可注入队列），使 SSE/run 编排离线可测（其现有 chat 契约与 stats 埋点不变）。

**Prior art**：`tests/test_integration.py`（跨模块全链路、真实 SQLite）、`tests/test_cli.py`（subprocess + INKMIND_LLM_FAKE 端到端）、`tests/test_llm_observability.py`（stats 断言）、`tests/test_concurrency.py`（FileLock/UoW 互斥）。

## Out of Scope

- 多用户/账号体系（单用户本地）；桌面应用打包（已锁定本地 Web 形态）；实时协同编辑。
- 素材自动检索注入 prompt（已选旁路面板消费方式）。
- 连跑自动驾驶（已选每章人工把关）。
- 计划伏笔（章纲埋/收字段 + 埋/回收 chip）——独立特性体量，v2 候选；L1 派生徽标已覆盖主要价值。
- 记忆组装快照预览（Writer 实际所见的 MemorySnapshot 组装态展示）——#23 裁定首版不做，实现期再评估。
- 定稿后修订（FINALIZED 章节改内容的重摘要语义）——#27 裁定首版拒改，实现期评估。
- 整卷推倒（清空卷内未开工章 + 删卷）——#31 裁定首版无路径（非空卷禁删、章节无 DELETE）。
- 卷重排（拖拽改 volume_index）与中间插卷——#31 裁定首版仅尾部追加。
- React 组件测试与浏览器 E2E（见 Testing Decisions）。
- CLI 新功能；CLI 不废除，但兼容不再是设计约束（web 功能导致 CLI 坏掉可忽略；snapshot dump/restore 照常工作）。

## Further Notes

- 本规格是 wayfinder 地图 #12 的目的地交付物；全部决策细节见子工单 #13–#32（研究/原型分支：research/editor-anchor-annotations、research/ref-project-material-practices、prototype/ia-navigation、prototype/writing-desk、prototype/materials-page、prototype/outline-view-v2；#29 三变体已弃，prototype/outline-view 仅供对照）。
- 建议实施切片顺序（按依赖拓扑，每片独立可交付可测）：① 工程基座（pyproject 正规化 + web/ + inkmind/api/ + serve）→ ② Schema 与事务（新表 + T6–T12 + 恢复第 9 步 + 快照扩展）→ ③ 执行层 + SSE + REST 骨架 → ④ 写作台（生成/评审/批注/修订/手动编辑）→ ⑤ 大纲视图 + Planner 扩展 → ⑥ 素材子系统 → ⑦ 记忆/观测/设置面板 → ⑧ 启动与打磨。⑤⑥ 可在 ④ 之后互换。
- 前端 dist 不入库；发布/CI 的构建职责在实施期另定。
- 交互语言：中文（界面与文档）。
