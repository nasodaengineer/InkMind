## 决策结论（2026-07-18 grilling 会话）

### A. 归属与表设计

- **按小说隔离**：全部素材表 `novel_id NOT NULL`，与全库一致；跨书复用留「复制到小说」演进口（schema 无需预留）。
- **三张新表**（遵全库 int PK + uuid 双键、`is_deleted/deleted_at` 软删、JSON 列惯例）：

| 表 | 关键列 |
|---|---|
| `material_sources` | title、content（原文 ≤10 万字，持久化供重跑）、content_digest、status（`decomposing / partial / done`） |
| `material_chunks` | source_id FK、chunk_index、digest、status（`pending → done / failed / low_quality`，**无持久化 processing 中间态**）、error |
| `material_fragments` | novel_id FK、source_id FK 可空、**source_chunk_id FK 可空**、title String(100)、content Text、type String(20)（8 枚举白名单在代码层）、tags JSON、source String(200) 可空、source_quote String(255) 可空、reusability_note、user_note、user_edited |

- 片段 `source`（来源标注文本，用户可编辑，#16 预览页「source 覆盖」改它）与 `source_id`（系统血缘 FK）是两个字段，不合并。
- 低质标记记 chunk（`status=low_quality`），单一真相，查询 join 带出；片段不设 quality 列。
- 不设独立 Tag 表：tags 走 JSON 列；Top 50 自动补全 Python 侧 Counter；合并/重命名演进=批量 JSON 重写。

### B. 事务边界（UoW 新增两个编号事务）

- **T6 素材导入**：source + chunks 批量创建，原子。
- **T7 拆解提交**：fragments 批量 + chunk 状态 + source 状态重算，原子；重跑复用此方法。
- 单片段 CRUD 不编号，走 MaterialRepository + 常规 commit（对齐 `create_compression_task` 先例）；写事务经 `get_uow(db_path)` 持 `{db_path}.lock` 文件锁（ADR-0011 §11-C 既有机制）。

### C. 幂等与重跑

- **原文层**：`(novel_id, content_digest)` UNIQUE（复用 `compute_content_digest`）；重复导入返回已有 source，不报错不新建。
- **块层**（#16 已锁）：chunk digest 唯一；重跑只处理 `failed` 块，`low_quality` 需人显式触发。
- **片段层**：不做自动去重（预览页人工合并/删除，#16）。
- **不借用 `processed_digests` 表**（packet_id NOT NULL，Agent 包流专用）；素材 digest 自带落点。
- **重跑低质块**：T7 内删除该块非 user_edited 旧片段 + 插入新片段；user_edited 原样保留（#15「重跑跳过」的精确实现）；手工片段无 chunk_id 永不受波及。

### D. 搜索支撑

- **FTS5 trigram** 虚表（环境已验证 SQLite 3.47.1 + trigram 可用），**只索引 title + content**；tags/reusability_note/user_note 不进索引。
- 查询 **<3 字符自动降级 LIKE** 兜底（trigram token 下限 3 字符，中文单双字查询的硬限制）。
- **标签过滤**：SQL 层 `EXISTS (SELECT 1 FROM json_each(tags) WHERE value IN (...))`，与 type 过滤、FTS 命中组合单查询。
- **应用层同步**：写入路径（T7/单片段 CRUD/软删/重跑替换，均收拢在 UoW/Repository 内）显式维护 FTS 行；不用 DB 触发器（无迁移框架、软删是 UPDATE、静默漂移风险）。`rebuild_material_fts()` 维护函数兜底。
- DDL：虚表 `CREATE VIRTUAL TABLE IF NOT EXISTS` 挂在 `create_tables()` 同一生命周期（无 alembic，create_all 不建虚表）。

### E. 快照与中断恢复

- **JSON 快照包含素材三表**（dump/restore 各加三个 section）——快照语义是「指定 novel 的全部数据」，素材库是用户资产；FTS 虚表不导出，restore 后调 `rebuild_material_fts()`。
- **拆解中断不进 RecoveryManager 8 步**：崩溃留下 pending 块 + `partial` source；素材拆解本是人触发的动作，UI 导入页显示「继续拆解」，从 pending/failed 块续跑（即重跑路径）。

### F. 删除语义

- **软删 source 不级联**：fragments 保留、source_id 不动（血缘不丢，指向已软删 source 无害）。
- 「删除来源及其全部片段」为 UI 显式二级动作：级联软删但**跳过 user_edited 片段**并提示保留数量（与 #15 整条保护一致）。

---

依据：#15（类型/标签/字段/重跑保护）、#16（块级幂等/预览编辑/失败链）、调研报告 `docs/research/ref-project-material-practices.md`（#14，FTS5 为首版差异化、不上向量库）。
