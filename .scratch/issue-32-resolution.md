## Resolution

**决策记录（2026-07-19 /grilling 会话，9 题全部锁定）**

**1. 做不做**：**做**。Planner 扩展支持总纲+卷纲起草；是否使用完全由终端用户决定（人触发、人采纳，系统绝不自动起草）。核心原则：**人介入时可修改、辅助人决策；无人时辅助 AI 自己——总纲是流水线「不迷路」的锚**。

**2. 入口形态**：**kind=plan 加 `level` 判别字段**（`chapter` 默认 / `volume` / `spine`），拒绝独立 Run kind。Run 层价值（执行记录/SSE phase/取消崩溃语义/stats 聚合）与层级无关，三级起草同构；符合 #22「判别字段+条件校验、不造新资源」克制先例；前端复用同一 run 面板（pulse + ■取消）零新组件。

**3. 输入**：**可选自由文本**——一句话梗概/结构化笔记皆可（AI 都能理解），可从 novel 简介预填；**零输入时 AI 自主命题**。不做结构化分项表单（切碎灵感、违背起草低摩擦定位）。

**4. 卷纲粒度**：**双入口共用 level=volume**——
- **单卷填补**：人已建空卷（name+planned_size），AI 基于总纲+前文摘要填四字段（对应写到中途的续卷场景）
- **全书拆卷**：AI 读总纲产 N 卷提案（name+四字段+planned_size），**直接落成真卷行**（尾部追加，#31 路径），人用既有 CRUD 策展（改字段/删空卷），不造「拟议卷」第二套数据（对应八步流程第六步的冷启动场景）

**5. 人工所有关系**：**直接落库（章纲模式）**，拒绝提案待采纳。空目标直落；非空目标须 `confirm_overwrite:true`（**进 API 契约、服务端强制**，不止前端弹窗）；旧值随 run 记录存留（恢复=打开旧 run 复制回，不建版本表）；**无 user_edited 保护**——起草永远人触发（题 1 锁定），无静默覆盖路径，素材 #15 的批量重跑场景不同构；采纳点击在占多数的空目标场景是纯摩擦。

**6. prompt 注入面**（「不迷路的锚」落实）：
- **Planner 全量注入**：总纲六字段全文 + 目标卷四字段+卷名 + **前卷卷末悬念**（相邻卷衔接防断章）+ 既有前文摘要
- **Writer 精简锚块**：三行级——总纲 `main_plot` + `core_conflict` + 当前卷 `stage_goal`（上下文预算已被 L1 滑窗+章纲占大头，三行是最低成本防跑偏保险）

**7. Run body 三形态与启动校验**（违反即 409）：

| level | body | 校验 |
|---|---|---|
| `chapter`（默认） | `volume_id`+`start_index`+`chapter_count` | #31 原样（落卷校验/覆盖章全 PLANNED） |
| `spine` | `prompt?` | 总纲任一字段非空 → 须 `confirm_overwrite:true`，否则 409 `outline_not_empty` |
| `volume` 填补 | `volume_id`+`prompt?` | 卷存在；四字段任一非空 → 同上 confirm_overwrite |
| `volume` 拆卷 | `volume_count`+`prompt?` | ① `volume_count` 硬校验 **2–20**（prompt 指引 5–10，调研 §九）② 总纲 `main_plot` 空 → 409 `spine_required`（拆卷必须从总纲长出来，维护三级体系前提）③ 已有任何卷行 → 409 `volumes_exist`（拆卷=纯冷启动；中途续卷走填补，语义不混） |

**互斥粒度**：spine/拆卷 = novel 级，单卷填补 = 卷级——同一目标同时只允许一个 running，（target, running）部分唯一索引族扩展（#21 同族），409 冲突。

**8. 事务编号**（#31「编号不变、定义扩展」先例）：
- 总纲 upsert、单卷填补 = **单行写不编号**（#31 人工编辑先例延伸），落库在 run 收口（T9 族）内嵌
- 拆卷落 N 卷行 = **T2 定义再扩展**：「章纲批量落库」→「规划产物批量落库（章纲批量 OR 卷批量）」，实现期入 ADR-0005 族谱（同 #27 T12/#31 T2 先例）
- 快照零改动（outline_spines/volumes 两表 #31 已进快照）

**9. 输出契约与失败链**（#16 先例同构）：
- spine = JSON 六字段对象；fill = 四字段对象；split = N 元素数组（`name`+四字段+`planned_size≥1`）
- **结构级硬校验**：JSON 形状、split 数组长度 == `volume_count`、`planned_size≥1`——违反即清洗层+回喂重试 ≤3，最终失败 → run `failed` 终态（#21）
- **字段级容忍**：单字段空串不判失败（总纲本有懒创建空骨架语义，人就地补）
- 模型路由不变（planner 绑定 pro→flash）；stats 按 agent=planner 聚合不变，runs 表 `level` 列供 breakdown（#24 面板可见维度）

**10. 接口扩展**：
- `PlanRequestPayload` 加 `level`（默认 chapter 向后兼容）+ `prompt?` + `volume_count?`（`volume_id` 章级 #31 已带）
- `PlannerAgent` 新增 `draft_spine` / `draft_volume` / `split_volumes` 三方法，共用 JSON 解析+重试基建；prompts.py 对应三个 builder + `build_planner_prompt` 三级注入改写 + Writer prompt 锚块（题 6）
- 覆盖场景旧值快照进 run 记录载荷（题 5「旧值随 run 存留」落点）

**11. 计划伏笔同族评估**（#31 雾区挂钩项）：**首版不做，维持 #31 裁定**（章纲零伏笔字段、树「伏」徽标纯 L1 派生）。完整做需动 ChapterOutline 模型/T2/Planner 输出契约/树徽标双态/MemoryKeeper 对账逻辑 + 埋/回收 chip UI 新设计（出自 #30 已弃变体 B），是独立特性体量；L1 派生徽标已覆盖主要价值（「哪章埋的未回收」可见），缺的只是计划意图对账增强。移入地图 Out of scope 作 v2 候选。
