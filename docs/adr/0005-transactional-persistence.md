# ADR-0005: 事务式持久化架构

## 状态
✅ 已采纳

## 背景
InkMind 需要持久化层支持：
- 原子写入（Agent 提交的 Packet 必须全部或全部不写入）
- Digest 幂等（同一内容不重复存储）
- 快照回滚（导出/导入项目状态）
- 跨进程锁（防止同一项目的并发写入）
- 故障恢复（重启后自动恢复进行中的任务）

## 决策

### 5-1 存储方案：混合架构

**选择：SQLite + SQLAlchemy 作为核心存储，辅以 JSON 序列化层**

理由：
- SQLite 零部署（单用户桌面应用），天然 ACID 事务
- SQLAlchemy 提供声明式表定义和异步支持（`sqlalchemy[asyncio]`）
- JSON 序列化层处理 Pydantic ↔ ORM 转换，避免对 ORM 的深度耦合
- 被否决：纯 JSON 文件系统（无原子写入）、纯 ORM（耦合过深）

### 5-2 持久化策略：混合模型映射

**选择：核心实体用 ORM 表 + 归档数据用 JSON Column**

| 表类型 | 包含的实体 | 理由 |
|---|---|---|
| ORM 表 | NovelModel, ChapterModel, PipelineStateModel, CompressionTaskModel, SnapshotModel | 需要按字段查询、排序、过滤 |
| JSON Column | MemoryArchiveModel.data (含 L0/L2/L3 archive), NovelModel.metadata | 仅按 novel_id 读取，内部结构复杂 |

### 5-3 Digest 幂等策略

**选择：per-packet digest 去重**

- 每个 `AgentPacket` 在 `PayloadBase` 层携带 `digest` 字段（SHA-256 of serialized payload）
- `PacketTrackingModel` 的 `digest` 设为 UNIQUE 约束
- 写入前校验：若 `packet_type + packet_id + digest` 已存在，跳过写入
- Writer 重发同一内容不会产生重复记录

### 5-4 章节版本历史

**选择：保留全量 + 标记基线**

- `ChapterVersionModel` 存储每个版本的完整正文和变更 diff
- `ChapterModel.current_version_id` 指向最新版本
- 用户可以标记任意版本为 `is_baseline=True` 用于回退
- 无自动版本清理（写作数据不可丢失）

### 5-5 事务边界：5 个原子事务

| 事务 | 包含操作 | 保护目的 |
|---|---|---|
| T1: Writer 完成章节 | 写入 Chapter content + 更新 ChapterStatus → DRAFT_READY | Writer 的输出与状态一致 |
| T2: Planner 完成规划 | 批量插入 ChapterOutline + 更新 PipelineState.total_chapters | 规划不丢失 |
| T3: Editor 完成评审 | 写入 Verdict + 更新 ChapterStatus → APPROVED/REVISING | 评审结论不丢失 |
| T4: MemoryKeeper 完成压缩 | 写入 CompressedMemory + 更新 L2Archive + 标记 Task COMPLETED | 压缩不重复 |
| T5: 滑窗更新 | 更新 SlidingWindowState + L1 snapshot | Writer 拿到一致上下文 |

每个事务通过 `UnitOfWork` 实现：
```python
async with uow.begin():
    repo.write_chapter(packet)
    repo.update_chapter_status(chapter_id, "draft_ready")
    await uow.commit()
```

### 5-6 故障恢复流程

**RecoveryManager 启动时按序恢复：**

1. 连接存储 → 按 novel_id 加载
2. 加载 L0Index（从 MemoryArchiveModel.tier="l0_index"）
3. 加载 L2Archive（从 MemoryArchiveModel.tier="l2_compressed"）
4. 加载 L3Archive（从 MemoryArchiveModel.tier="l3_permanent"）
5. 加载 SlidingWindowState（从 MemoryArchiveModel.tier="l1_active"）
6. 加载所有 PENDING/RUNNING 的 CompressionTask → 重置 RUNNING 为 PENDING
7. 加载 PipelineState（所有章节状态映射）
8. 恢复完成 → 返回 RecoveredMemoryState

## 文件结构

```
inkmind/storage/
├── __init__.py           # 公开 API
├── models.py             # ORM 表定义（SQLAlchemy declarative）
├── serializers.py        # Pydantic ↔ ORM 转换
├── repositories.py       # Repository 模式：NovelRepo, ChapterRepo, PipelineRepo, TaskRepo, ArchiveRepo
├── uow.py               # UnitOfWork 事务边界管理
├── snapshot.py           # 快照导出/回滚
├── recovery.py           # RecoveryManager 故障恢复
└── idempotency.py        # Digest 幂等校验器
```

## 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 纯 JSON 文件系统 | 无原子写入、大文件性能差、并发不安全 |
| 纯 ORM（全表映射） | L0/L2/L3 内部结构复杂，非关系型，ORM 映射成本高 |
| MongoDB | 单用户应用引入 MongoDB 太重、部署成本高 |
| Redis | 数据持久化不保证、不适合长期存储 |
