## Destination

小说创作可视化工作台的**可交付实现规格**——一份可直接进入开发的规格文档（数据模型、API 面、页面结构、交互流）。本地 Web 应用：FastAPI + React/Vite/TipTap，与现有 CLI 共存、共享同一 SQLite。实现本身不在本图内。

## Notes

- 域：AI 小说协作写作（多 Agent 流水线 + L0–L3 四级记忆），本图为它加可视化混合工作台
- 会话应咨询的技能：决策类工单用 /grilling + /domain-modeling，原型类用 /prototype，调研类用 /research
- 交互语言：中文
- 既有数据源（建图时已查证）：`CharacterStateCard`/`SlidingWindowState`（inkmind/models/memory.py）、`ChapterVersion`（inkmind/models/chapter.py）、`PipelineState`（inkmind/models/agent.py）、`chat_stream` 全链路（inkmind/llm/）、`ProviderStats` 埋点（ADR-0010）

### 建图时已锁定的基线决策（2026-07-18 grilling 会话）

- **核心用途**：混合工作台——AI 流水线生成/评审，人在关键节点介入（定大纲、审章节、行内批注、批示修订）
- **形态**：本地 Web 应用，单用户，无账号体系
- **首版范围**：章节写作台、评审与修订交互、大纲规划视图、记忆只读面板、素材管理（导入+LLM 拆解、标签分类、搜索、旁路面板一键插入）、观测统计面板、模型设置页
- **驱动节奏**：每章人工把关（一圈流水线自动跑完 ≤3 次修订循环后停，人裁决：提交/批示再修/手动改）
- **修订批示**：行内批注（正文选段留言，Writer 收带定位批注集）
- **技术栈**：FastAPI 后端 + React/Vite/TipTap 前端；SSE 流式显示
- **与 CLI 关系**：UI 后端 import inkmind 包，共享同一 DB，CLI 保留（#31 澄清：CLI 不废除，但兼容不再是设计约束——CLI 因 web 功能坏掉可忽略；snapshot dump/restore 不感知卷、照常工作）

## Decisions so far

- [调研：富文本编辑器锚点批注机制（TipTap/ProseMirror）](https://github.com/nasodaengineer/InkMind/issues/13) — 自研薄 comment Mark + 浏览器端 CommentStore + W3C quote 指纹为跨版本真相 + 整章重写后加权模糊重定位；不引 Yjs、不用 TipTap Pro Comments。报告见分支 research/editor-anchor-annotations
- [调研：参考项目的素材/片段管理实践](https://github.com/nasodaengineer/InkMind/issues/14) — 三家均无用户素材库概念，最近参照 Openwrite source pack（review→promote 人工晋升）；拆解四件套照搬（低温 0.3 + 严格 schema/枚举白名单 + ≤3 重试 + 降级兜底，sha256 增量指纹同构 digest 幂等）；建议片段 8 枚举 + 自由标签 + source_quote 子串校验 + reusability_note 防抄袭红线；旁路面板 + SQLite FTS 为首版差异化。报告见分支 research/ref-project-material-practices
- [决策：素材片段类型与标签体系](https://github.com/nasodaengineer/InkMind/issues/15) — 内容单轴 8 枚举锁死（excerpt/scene_idea/character_seed/setting_seed/dialogue_sample/style_sample/technique/misc；规则 Y 先判内容专类、逐字对话归 dialogue_sample，逐字性由 source_quote 存在性表达；枚举集中 inkmind/models 前后端同契约，用户不可扩展）；自由多值标签 + 输入自动补全源头减污（合并/重命名留演进，FTS 兜底）；type 双保险（prompt 枚举 + 代码白名单，非法降级 misc + 埋点）、tags 软引导（Top 50 复用 + 3–6 建议无硬上限）；user_edited 整条保护 + 重跑跳过 + 可清标；字段 title/content/type/tags/source/source_quote（excerpt 豁免）/reusability_note/user_note 分家；LLM 输出契约归 #16、存储模型归 #17
- [决策：素材存储模型与事务边界](https://github.com/nasodaengineer/InkMind/issues/17) — 按小说隔离三张新表（material_sources 原文+digest+状态 / material_chunks 块级幂等状态机 pending→done/failed/low_quality / material_fragments 全字段+source_chunk_id 血缘，tags 走 JSON 列不设 Tag 表）；UoW 新增 T6 素材导入+T7 拆解提交，单片段 CRUD 不编号；幂等=(novel_id,content_digest) UNIQUE 重复导入返回已有+块级 digest 重跑只补 failed+片段不自动去重，重跑低质块 T7 内替换非 user_edited 旧片段；搜索=FTS5 trigram 只索引 title+content、<3 字符降级 LIKE、json_each 标签过滤、应用层同步+rebuild 兜底；快照含素材三表 restore 后重建 FTS，拆解中断不进 RecoveryManager 走 UI 继续拆解；软删 source 不级联，级联删除跳过 user_edited
- [决策：行内批注 → revision_request 序列化协议](https://github.com/nasodaengineer/InkMind/issues/18) — 批注模型入核心包（AnchorFingerprint 跨版本真相 + 单轴五态 thread + 多轮 comments + intent 四类，note 不序列化）；RevisionRequestPayload 加结构化 AnnotationRef（thread_id 随协议流转），prompt 五区渲染、orphaned 照发、Writer 输出维持纯正文；人工批示 = 单次 revise 直达人不过 Editor；回稿绝不自动 resolve（0.9/0.5 阈值分流，浏览器端重定位）；手动编辑 mark 跟随 + 扫描对账，保存时指纹刷新；落地事务归 #21/#22
- [决策：素材拆解 LLM 输出契约](https://github.com/nasodaengineer/InkMind/issues/16) — 单 JSON 信封（schema_version+fragments，无 summary）；LLM 产 6 字段（title≤20 字/content≤2000 字且 excerpt 豁免/type 白名单降级 misc/tags 3–6 软引导/source_quote 8–50 字子串校验降级/reusability_note 来源绑定必产），1–10 指引 + 20 硬上限；输入=单段连续文本、8000 字/块段落边界吸附无重叠、单任务 10 万字上限、块级 digest 幂等；失败链=清洗层+回喂重试×3、单块失败不中断、低质只标记不自动重试、「原文存 excerpt」逃生门；预览页=5 字段编辑+source 覆盖+删除/合并/新建、默认全选只入选中、user_edited 编辑触发；material_decomposer 路由 flash 非流式、inkmind/materials/ 新模块；存储模型归 #17
- [原型：整体信息架构（页面与导航）](https://github.com/nasodaengineer/InkMind/issues/19) — 裁决变体 B「章节中心·图标轨」：仅 4 目的地（工作区/大纲/素材/系统，窄图标轨），落地页=工作区；评审/修订=工作区模式 tab（写作|评审|修订），记忆/素材=右侧抽屉（素材库另保留全页），观测/设置/记忆浏览=系统页 tab；每章控制环不出一屏；原型全三变体见分支 prototype/ia-navigation
- [原型：写作台（编辑器 + 批注 + 素材面板）](https://github.com/nasodaengineer/InkMind/issues/20) — 裁决变体 C「行内嵌卡」：点锚点正文流内展开批注卡（总评卡文首、失效沉章末未定位区、已解决可开关、抽屉批注退为索引），正文保最宽列；状态区=模式 tab 下细状态条（生成中整条换 Writer 状态+■停止）；SSE 直流正文+无确认打断留部分稿可丢弃/保留；选段浮动层 intent 四类；修订编排器左勾选右 #18 五区序列化实时预览（指纹实时算）；版本入口=标题栏 v{n}▾ 只读查看+段落对齐段内字级 diff（并排双栏不做）；素材旁路=空格分词 AND+类型多选/标签单选 chip+卡片展开四字段；评审 mode=结论卡置顶+只读正文可加批注+裁决条；原型全三变体见分支 prototype/writing-desk

- [决策：每章控制环的 API 表达（job / SSE）](https://github.com/nasodaengineer/InkMind/issues/21) — 新建 runs 执行记录层（kind=generate/revise/finalize；单轴 running→五终态 awaiting_human/completed/failed/cancelled/interrupted + phase 遥测，awaiting_human 为终态球在人手）；ChapterStatus 加 AWAITING_HUMAN 人工门（run 期间章节停 WRITING/REVISING，中间翻转收敛进 phase）；取消/崩溃一套语义（部分稿留 run.partial_content 人裁决保留/丢弃，节流 checkpoint 2s/500 字，恢复第 9 步 running→interrupted）；SSE 5 事件 phase/token/verdict/done/error + 无事件日志快照重连；章节级 (chapter_id,running) 部分唯一索引 409 互斥 + digest 幂等路径不变；仅终稿落库、版本数人见稿；REST 映射归 #22、观测持久化归 #24
- [决策：REST 资源划分与 UoW 事务边界对齐](https://github.com/nasodaengineer/InkMind/issues/22) — GitHub 风格资源骨架（集合挂父/单体扁平、chapter_index 寻址、/api 无版本）；Run 加第四 kind=plan（novel 级批量规划复用执行层，chapter_id 可空）；revise 只收 thread_ids 服务端 prompts.py 权威序列化；批注面（无单体 GET、re-anchor 合并 fuzzy 确认与人工重锚、apply-relocation 批量回写）+ 素材面（拆解不进 Run、merge 专用原子端点、tags 补全端点）+ 章节面（PATCH 一端两用、base_digest 乐观锁、无独立 review 资源）全量钉定；UoW 新增 T8 Run 启动 / T9 落稿收口（内嵌 T1/T2）/ T10 终态收口，checkpoint 与单行写不编号；裸数组默认仅 fragments 分页、错误统一 {error:{code,message,details}} 信封 409 一切状态冲突；代码优先 OpenAPI；手动编辑事务边界毕业为 #27
- [决策：记忆只读面板的展示粒度与读取 API](https://github.com/nasodaengineer/InkMind/issues/23) — 抽屉=纯 L1 伴随态（状态卡 5 字段+伏笔+滑窗状态行，逾期伏笔高亮前端派生）、系统页=全量档案；L2 时间线卡摘要常显+事件清单展开+角色 UUID 服务端转名（llm_model/granularity 归 #24）；L3=l3_permanent blob 唯一真相五类分组截断展开（characters/worlds 空壳表排除）、纯只读；L0 字段级永不进面板仅一行统计；组装快照预览不做入雾区；API 四端点 window/archives/long-term/status（裸数组不分页无过滤无单体 GET，status 含最新 failed 压缩任务）；刷新=打开拉取+run done 重拉，无轮询无独立 SSE
- [决策：观测统计的持久化与展示粒度](https://github.com/nasodaengineer/InkMind/issues/24) — run.stats 聚合快照为唯一持久源（不建 per-call 表、CLI 不纳入），compression_tasks 加同构 stats 挂点两源聚合补压缩成本漏洞；ProviderStats 加 agent_name 全维度（provider/model/agent/error）；面板=总览+三维 breakdown+error 分布+run 史+压缩任务小节、今日/7日/全部三档窗、不做图表；内置单价表实算成本（现 0.0 占位）带估算标注；API=stats/overview?window（默认 today 现算不物化）+ compression-tasks 裸数组 + 复用 #22 runs 端点；无重置与执行史共生灭；ADR-0010「stats 不进 SQLite」否决项实现期修订
- [决策：模型设置页的配置读写路径](https://github.com/nasodaengineer/InkMind/issues/25) — LLMConfig 真相源=SQLite app_settings 单行 JSON（app 级 singleton 不按 novel 隔离，懒 seed 无记录/表不存在→代码默认兜底，CLI/UI 同一读取链零冲突）；编辑分档=Provider 连接参数全字段可增删+路由绑定（模型名白名单校验）+并发/速率可改、Retry 只读、API Key 仅显已设置状态永不入库仍走 env；生效=Run 启动（T8）从 DB 重读重建 LLMClient、进行中 Run 用旧配置跑完、CLI 每次启动读 DB 天然热；inkmind.toml 永不引入 [llm]、INKMIND_LLM_FAKE 压制一切、配置不进小说快照；设置保存=T11
- [决策：前端工程布局与一键启动方式](https://github.com/nasodaengineer/InkMind/issues/26) — 前提=Python 依赖正规化（pyproject 补 [project]+全量依赖、>=3.11、uv sync 后一律 uv run）；布局=前端 web/ 根目录 + 后端 inkmind/api/（与 cli/ 对称）；pnpm+packageManager 字段+Node 22 LTS；开发双进程（serve --reload 只出 /api + pnpm dev，vite proxy 透传 SSE）/生产单进程 FastAPI 托管 dist（SPA fallback，dist 不入库）；一键启动=uv run inkmind serve（CLI serve 子命令内嵌 uvicorn.run，127.0.0.1:8000、--reload/--open，dist 缺失显式报错不自动构建），不用 uv script/concurrently；UI 基线=Tailwind v4+shadcn/ui（Radix copy-in）+lucide-react；路由=React Router v7 库模式、REST=TanStack Query、SSE 薄 EventSource 封装不过 Query；不引全局状态库/表单库
- [决策：写作台手动编辑与 ChapterVersion/状态机的交互细节](https://github.com/nasodaengineer/InkMind/issues/27) — 每次显式保存=一个人见版本（归档旧版+version+1+source_trace=manual，iteration 不变，取消编辑零成本）；不走 T1 content-digest 全局去重（改回旧文会被 is_duplicate 静默吞掉），幂等=base_digest 乐观锁重试即 409；不新增状态（编辑中=纯前端态、服务端无编辑会话，AWAITING_HUMAN 门内手动改↔批示再修任意交错，提交=finalize run）；T12 手动编辑落稿入 ADR-0005 族谱（归档+写章+fingerprint_updates 三方原子，base_digest 校验在事务内，PATCH 按是否含 content 分流）；PATCH content 白名单仅 AWAITING_HUMAN 其余 409（chapter_not_editable），FINALIZED 首版拒改（防记忆摘要脱节），定稿后修订入雾区
- [原型：素材库全页（导入向导 / 拆解监控 / 片段管理）](https://github.com/nasodaengineer/InkMind/issues/28) — 裁决 B+C 拼装：来源台账为主体（accordion 来源卡=标题/字数/状态徽标/块状态小丸✓✗!可点重跑/计数/操作，展开见块行+片段子表 dense table；手工片段 pseudo-source 桶；搜索/chips 非空切跨来源平铺结果表）+C 收件箱待办区浮顶（拆解中/失败块/低质块三类卡，空时收起，=台账内联操作快捷面不造第二套语义、不承接预览），导入=B 全页步骤流（粘贴→进度→预览提交，提交/取消回台账）；弃 A 三栏/modal；预览页契约/失败块三操作/低质显式重跑/user_edited 保护/搜索 chip 语言/旁路=消费端分工同锁；原型全三变体见分支 prototype/materials-page
- [原型：大纲规划视图（批量规划流 / 编辑粒度 / 重规划）](https://github.com/nasodaengineer/InkMind/issues/29) — **三变体方向全弃**（A 表格台账 / B 文档阅读流 / C 批次控制台均非所愿），跳过；用户将先行调研产出报告后按新参照重新设计（另成新票）；演示态语义（已定稿锁定/重规划覆盖/5–50 约束）随重新设计一并回答；原型全三变体留存分支 prototype/outline-view 供对照——三个不要的方向已排除
- [原型：大纲规划视图重新设计（三级体系：总纲/卷纲/章纲）](https://github.com/nasodaengineer/InkMind/issues/30) — 裁决变体 A「书脊树·层级导航+详情面板」：左树三级折叠（总纲/卷/章+状态点/▲★节奏/待回收伏笔标记）+右详情按节点切换；三级体系为前提（总纲六字段/卷纲四字段人工所有、就地可改）；卷节点驱动规划（ghost行→卷内表单5–50落卷内→run面板取消）；重规划章级↻预填覆盖未开工；#29演示态语义沿用（已定稿锁定/覆盖未开工/5–50）；卷一等实体与总纲存储归 #31、总纲/卷纲AI起草归 #32；弃 B 卷轴泳道/C 三级钻取；原型见分支 prototype/outline-view-v2
- [决策：三级大纲（总纲/卷）的存储模型与事务边界](https://github.com/nasodaengineer/InkMind/issues/31) — 卷一等实体（volumes 表：volume_index 显式排序尾部追加+四字段平铺+planned_size 软规模，start_index 派生=1+Σ前序卷 size，chapters.volume_id NOT NULL 外键）；总纲独立表 outline_spines（novel 1:1 六字段平铺、懒创建 upsert，worlds 同构）；total_chapters 保持高水位与预计规模解耦；缩卷下限=已排章数、扩卷只改 size、plan run 落单卷必填 volume_id；增删=尾部追加/仅空卷硬删；节奏标记 rhythm_marker 枚举三层同步+pov/involved 补落库；伏笔徽标纯 L1 派生零新存储；T2 扩展=落卷校验+仅 PLANNED 可覆盖硬删+带卷插入，人工编辑全单行不编号；REST=/volumes 集合挂父+volume_index 寻址+/spine 单体，plan run body 加 volume_id 必填，不造聚合端点；快照含两新表 restore 卷先章后；CLI 不废除但兼容不再是设计约束
- [决策：总纲/卷纲的 AI 起草（Planner 能力扩展）](https://github.com/nasodaengineer/InkMind/issues/32) — 做且是否使用由用户自决：kind=plan 加 level 判别（chapter 默认/spine/volume），输入可选自由文本（零输入 AI 自主命题）；卷纲双粒度=单卷填补+全书拆卷（2–20 卷、零卷冷启动 409 volumes_exist、spine_required 前置）；直接落库+非空 confirm_overwrite 服务端强制+旧值随 run 存留、无 user_edited；注入面=Planner 全量三级（六字段+卷四字段+前卷悬念）/Writer 精简锚块三行；事务=填补/spine 单行不编号、拆卷 T2 定义再扩展（规划产物批量落库）；输出结构级硬校验（形状/数组长度/planned_size≥1）重试 ≤3 字段级容忍空串；PlanRequestPayload 加 level/prompt/volume_count，PlannerAgent 新增 draft_spine/draft_volume/split_volumes；计划伏笔同族评估=首版不做列 v2 候选

## Not yet specified

- 实现规格文档的章节结构与交付形式——前沿推完后成形
- 记忆组装快照预览（Writer 写第 N 章实际所见的 MemorySnapshot 组装态展示）——#23 裁定首版不做（依赖 MemoryKeeper 运行时组装链路），实现规格阶段再评估
- 定稿后修订（FINALIZED 章节改内容的重摘要语义：L2 重触发、摘要覆盖、状态回退）——#27 裁定首版拒改留此问，实现规格阶段评估
- 整卷推倒（清空卷内未开工章 + 删卷）——#31 裁定首版无路径（非空卷禁删、章节无 DELETE），实现规格阶段评估
- 卷重排（拖拽改 volume_index）与中间插卷——#31 裁定首版仅尾部追加，实现规格阶段评估

## Out of scope

- 多用户/账号体系（单用户本地）
- 桌面应用打包（已锁定本地 Web 形态）
- 素材自动检索注入 prompt（已选旁路面板消费方式）
- 连跑自动驾驶（已选每章人工把关）
- 实时协同编辑（单用户无此需求）
- 计划伏笔（章纲埋/收字段 + 埋/回收 chip）——#32 同族评估裁定首版不做：独立特性体量（模型/T2/Planner 输出/徽标双态/对账/chip UI 新设计），L1 派生徽标已覆盖主要价值；v2 候选，目的地重绘时另起









