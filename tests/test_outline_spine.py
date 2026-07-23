"""测试：大纲数据层与三级书脊树（Issue #35）。

HTTP 缝集成测试 — 7 个端点：
1. POST /volumes — 尾部追加
2. PATCH /volumes/{idx} — 卷纲编辑 + planned_size 下限
3. DELETE /volumes/{idx} — 空卷删除 / 非空 409
4. GET /volumes/{idx}/spines — 卷书脊树
5. GET/PATCH /spine — 总纲懒创建 + 六字段编辑
6. PATCH /chapters/{idx}/outline — 章纲字段单行写
7. GET /volumes — 派生区间
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import inkmind.storage.database as db_module
from inkmind.api.app import create_app
from inkmind.models.agent import ChapterStatus
from inkmind.models.chapter import Chapter
from inkmind.models.novel import Novel, NovelMetadata
from inkmind.storage.database import DatabaseManager
from inkmind.storage.unit_of_work import UnitOfWork


@pytest_asyncio.fixture
async def http_env(tmp_path):
    """HTTP 测试环境：文件 DB + 种子小说 + httpx 客户端。"""
    db_module._engine = None
    db_module._session_factory = None

    db_path = str(tmp_path / "test_outline.db")
    manager = DatabaseManager(db_path)
    await manager.create_tables()

    async with manager.session_factory() as s:
        uow = UnitOfWork(s)
        novel = Novel(
            id=uuid4(),
            title="大纲测试小说",
            metadata=NovelMetadata(description="书脊树测试"),
        )
        await uow.novels.save(novel)
        await s.commit()

    await manager.close()
    db_module._engine = None
    db_module._session_factory = None

    app = create_app(db_path=db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, novel, db_path

    db_module._engine = None
    db_module._session_factory = None


@pytest_asyncio.fixture
async def http_env_with_chapters(tmp_path):
    """HTTP 测试环境：文件 DB + 种子小说 + 3 章 + httpx 客户端。"""
    db_module._engine = None
    db_module._session_factory = None

    db_path = str(tmp_path / "test_outline_ch.db")
    manager = DatabaseManager(db_path)
    await manager.create_tables()

    async with manager.session_factory() as s:
        uow = UnitOfWork(s)
        novel = Novel(
            id=uuid4(),
            title="大纲测试小说",
            metadata=NovelMetadata(description="书脊树测试"),
        )
        await uow.novels.save(novel)

        chapters = []
        for i in range(1, 4):
            ch = Chapter(
                novel_id=novel.id,
                index=i,
                title=f"第{i}章",
                content=f"第{i}章正文",
                status=ChapterStatus.PLANNED,
                summary=f"第{i}章摘要",
            )
            await uow.chapters.save(ch)
            chapters.append(ch)
        await s.commit()

    await manager.close()
    db_module._engine = None
    db_module._session_factory = None

    app = create_app(db_path=db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, novel, chapters, db_path

    db_module._engine = None
    db_module._session_factory = None


@pytest_asyncio.fixture
async def http_env_with_volume(tmp_path):
    """HTTP 测试环境：文件 DB + 种子小说 + 1 卷 + 3 章归属该卷。"""
    db_module._engine = None
    db_module._session_factory = None

    db_path = str(tmp_path / "test_outline_vol.db")
    manager = DatabaseManager(db_path)
    await manager.create_tables()

    from inkmind.models.novel import Volume

    async with manager.session_factory() as s:
        uow = UnitOfWork(s)
        novel = Novel(
            id=uuid4(),
            title="大纲测试小说",
            metadata=NovelMetadata(description="书脊树测试"),
        )
        await uow.novels.save(novel)

        volume = Volume(
            novel_id=novel.id,
            volume_index=1,
            title="第一卷：起源",
            planned_size=10,
            stage_goal="引入主角",
        )
        await uow.volumes.save(volume)

        chapters = []
        for i in range(1, 4):
            ch = Chapter(
                novel_id=novel.id,
                index=i,
                title=f"第{i}章",
                content=f"第{i}章正文",
                status=ChapterStatus.PLANNED,
                summary=f"第{i}章摘要",
                volume_id=volume.id,
            )
            await uow.chapters.save(ch)
            chapters.append(ch)
        await s.commit()

    await manager.close()
    db_module._engine = None
    db_module._session_factory = None

    app = create_app(db_path=db_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, novel, volume, chapters, db_path

    db_module._engine = None
    db_module._session_factory = None


# ═══════════════════════════════════════════════════════
#  Seam 1: POST /volumes — 尾部追加
# ═══════════════════════════════════════════════════════


class TestVolumeCreate:
    """POST /api/novels/{novel_id}/volumes"""

    @pytest.mark.asyncio
    async def test_create_first_volume(self, http_env):
        """创建第一卷，volume_index 应为 1。"""
        client, novel, _ = http_env

        resp = await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "第一卷：起源", "planned_size": 20},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "第一卷：起源"
        assert data["volume_index"] == 1
        assert data["planned_size"] == 20
        assert data["novel_id"] == str(novel.id)

    @pytest.mark.asyncio
    async def test_create_second_volume_appends(self, http_env):
        """连续创建两卷，第二卷 volume_index 为 2。"""
        client, novel, _ = http_env

        await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "第一卷"},
        )
        resp = await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "第二卷"},
        )

        assert resp.status_code == 201
        assert resp.json()["volume_index"] == 2


# ═══════════════════════════════════════════════════════
#  Seam 2: PATCH /volumes/{idx} — 卷纲编辑 + planned_size 下限
# ═══════════════════════════════════════════════════════


class TestVolumeUpdate:
    """PATCH /api/novels/{novel_id}/volumes/{volume_index}"""

    @pytest.mark.asyncio
    async def test_update_volume_fields(self, http_env_with_volume):
        """编辑卷纲四字段。"""
        client, novel, volume, _, _ = http_env_with_volume

        resp = await client.patch(
            f"/api/novels/{novel.id}/volumes/1",
            json={
                "stage_goal": "主角觉醒",
                "main_line": "修炼主线",
                "side_line": "情感支线",
                "volume_cliffhanger": "大反派现身",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["stage_goal"] == "主角觉醒"
        assert data["main_line"] == "修炼主线"
        assert data["side_line"] == "情感支线"
        assert data["volume_cliffhanger"] == "大反派现身"

    @pytest.mark.asyncio
    async def test_shrink_planned_size_below_chapter_count_rejected(self, http_env_with_volume):
        """planned_size 调小到已排章数以下 → 400。"""
        client, novel, volume, _, _ = http_env_with_volume

        resp = await client.patch(
            f"/api/novels/{novel.id}/volumes/1",
            json={"planned_size": 2},
        )

        assert resp.status_code == 400
        assert "不得小于" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_shrink_planned_size_equal_chapter_count_ok(self, http_env_with_volume):
        """planned_size 等于已排章数 → 允许。"""
        client, novel, volume, _, _ = http_env_with_volume

        resp = await client.patch(
            f"/api/novels/{novel.id}/volumes/1",
            json={"planned_size": 3},
        )

        assert resp.status_code == 200
        assert resp.json()["planned_size"] == 3


# ═══════════════════════════════════════════════════════
#  Seam 3: DELETE /volumes/{idx} — 空卷 204 / 非空 409
# ═══════════════════════════════════════════════════════


class TestVolumeDelete:
    """DELETE /api/novels/{novel_id}/volumes/{volume_index}"""

    @pytest.mark.asyncio
    async def test_delete_empty_volume(self, http_env):
        """空卷删除 → 204。"""
        client, novel, _ = http_env

        await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "空卷"},
        )
        resp = await client.delete(f"/api/novels/{novel.id}/volumes/1")

        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_nonempty_volume_rejected(self, http_env_with_volume):
        """非空卷删除 → 409。"""
        client, novel, volume, _, _ = http_env_with_volume

        resp = await client.delete(f"/api/novels/{novel.id}/volumes/1")

        assert resp.status_code == 409
        assert "无法删除" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════
#  Seam 4: GET /volumes/{idx}/spines — 卷书脊树
# ═══════════════════════════════════════════════════════


class TestVolumeSpines:
    """GET /api/novels/{novel_id}/volumes/{volume_index}/spines"""

    @pytest.mark.asyncio
    async def test_spines_returns_chapters_with_markers(self, http_env_with_volume):
        """卷书脊树含章状态点 + 节奏标记。"""
        client, novel, volume, chapters, db_path = http_env_with_volume

        # 先给第一章设置节奏标记
        from inkmind.storage.database import DatabaseManager as DM

        db_module._engine = None
        db_module._session_factory = None
        mgr = DM(db_path)
        async with mgr.session_factory() as s:
            uow = UnitOfWork(s)
            ch = await uow.chapters.get_by_novel_and_index(novel.id, 1)
            ch.rhythm_marker = "climax"
            ch.pov = "林逸"
            ch.involved = ["林逸", "苏瑶"]
            await uow.chapters.save(ch)
            await s.commit()
        await mgr.close()
        db_module._engine = None
        db_module._session_factory = None

        resp = await client.get(f"/api/novels/{novel.id}/volumes/1/spines")

        assert resp.status_code == 200
        data = resp.json()
        assert data["volume"]["title"] == "第一卷：起源"
        assert len(data["chapters"]) == 3

        ch1 = data["chapters"][0]
        assert ch1["rhythm_marker"] == "climax"
        assert ch1["pov"] == "林逸"
        assert ch1["involved"] == ["林逸", "苏瑶"]
        assert ch1["status"] == "planned"

    @pytest.mark.asyncio
    async def test_spines_foreshadowing_badge(self, http_env_with_volume):
        """卷书脊树含 L1 派生伏笔徽标。"""
        client, novel, volume, chapters, db_path = http_env_with_volume

        # 写入 L1 记忆归档，含 pending_foreshadowing
        from inkmind.storage.database import DatabaseManager as DM
        from inkmind.storage.models import MemoryArchiveModel

        db_module._engine = None
        db_module._session_factory = None
        mgr = DM(db_path)
        async with mgr.session_factory() as s:
            l1_data = {
                "sliding_window": {
                    "current_chapter_index": 3,
                    "pending_foreshadowing": [
                        {
                            "marker_id": str(uuid4()),
                            "description": "神秘玉佩的秘密",
                            "planted_chapter": 1,
                            "expected_payoff_chapter": 10,
                            "is_resolved": False,
                        },
                        {
                            "marker_id": str(uuid4()),
                            "description": "师父的遗言",
                            "planted_chapter": 1,
                            "expected_payoff_chapter": None,
                            "is_resolved": False,
                        },
                        {
                            "marker_id": str(uuid4()),
                            "description": "路边老者的身份",
                            "planted_chapter": 2,
                            "expected_payoff_chapter": 5,
                            "is_resolved": False,
                        },
                    ],
                },
            }
            s.add(
                MemoryArchiveModel(
                    novel_id=str(novel.id),
                    tier="l1_active",
                    data=l1_data,
                )
            )
            await s.commit()
        await mgr.close()
        db_module._engine = None
        db_module._session_factory = None

        resp = await client.get(f"/api/novels/{novel.id}/volumes/1/spines")

        assert resp.status_code == 200
        data = resp.json()
        assert data["chapters"][0]["foreshadowing_count"] == 2
        assert data["chapters"][1]["foreshadowing_count"] == 1
        assert data["chapters"][2]["foreshadowing_count"] == 0


# ═══════════════════════════════════════════════════════
#  Seam 5: GET/PATCH /spine — 总纲懒创建 + 六字段编辑
# ═══════════════════════════════════════════════════════


class TestSpine:
    """GET/PATCH /api/novels/{novel_id}/spine"""

    @pytest.mark.asyncio
    async def test_get_spine_lazy_creates(self, http_env):
        """GET 总纲时自动创建空总纲。"""
        client, novel, _ = http_env

        resp = await client.get(f"/api/novels/{novel.id}/spine")

        assert resp.status_code == 200
        data = resp.json()
        assert data["novel_id"] == str(novel.id)
        assert data["main_line"] == ""
        assert data["core_conflict"] == ""

    @pytest.mark.asyncio
    async def test_patch_spine_six_fields(self, http_env):
        """PATCH 总纲六字段。"""
        client, novel, _ = http_env

        resp = await client.patch(
            f"/api/novels/{novel.id}/spine",
            json={
                "main_line": "少年逆袭成神",
                "core_conflict": "天道不公",
                "ending": "破碎虚空",
                "selling_points": "爽文节奏",
                "world_background": "九天十地",
                "golden_finger": "混沌珠",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["main_line"] == "少年逆袭成神"
        assert data["core_conflict"] == "天道不公"
        assert data["ending"] == "破碎虚空"
        assert data["selling_points"] == "爽文节奏"
        assert data["world_background"] == "九天十地"
        assert data["golden_finger"] == "混沌珠"

    @pytest.mark.asyncio
    async def test_patch_spine_partial(self, http_env):
        """PATCH 部分字段不影响其他字段。"""
        client, novel, _ = http_env

        await client.patch(
            f"/api/novels/{novel.id}/spine",
            json={"main_line": "主线A", "ending": "结局A"},
        )
        resp = await client.patch(
            f"/api/novels/{novel.id}/spine",
            json={"core_conflict": "矛盾B"},
        )

        data = resp.json()
        assert data["main_line"] == "主线A"
        assert data["ending"] == "结局A"
        assert data["core_conflict"] == "矛盾B"


# ═══════════════════════════════════════════════════════
#  Seam 6: PATCH /chapters/{idx}/outline — 章纲字段单行写
# ═══════════════════════════════════════════════════════


class TestChapterOutlinePatch:
    """PATCH /api/novels/{novel_id}/chapters/{chapter_index}/outline"""

    @pytest.mark.asyncio
    async def test_patch_outline_fields(self, http_env_with_chapters):
        """PATCH 章纲字段（summary/key_events/rhythm_marker/pov/involved）。"""
        client, novel, chapters, _ = http_env_with_chapters

        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1/outline",
            json={
                "summary": "主角初入江湖",
                "key_events": ["拜师", "获得秘籍"],
                "rhythm_marker": "climax",
                "pov": "林逸",
                "involved": ["林逸", "师父"],
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "主角初入江湖"
        assert data["key_events"] == ["拜师", "获得秘籍"]
        assert data["rhythm_marker"] == "climax"
        assert data["pov"] == "林逸"
        assert data["involved"] == ["林逸", "师父"]

    @pytest.mark.asyncio
    async def test_patch_outline_partial(self, http_env_with_chapters):
        """PATCH 部分章纲字段不影响其他字段。"""
        client, novel, chapters, _ = http_env_with_chapters

        await client.patch(
            f"/api/novels/{novel.id}/chapters/1/outline",
            json={"summary": "摘要A", "pov": "角色A"},
        )
        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/1/outline",
            json={"rhythm_marker": "big_climax"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "摘要A"
        assert data["pov"] == "角色A"
        assert data["rhythm_marker"] == "big_climax"

    @pytest.mark.asyncio
    async def test_patch_outline_nonexistent_chapter_404(self, http_env_with_chapters):
        """PATCH 不存在的章节 → 404。"""
        client, novel, _, _ = http_env_with_chapters

        resp = await client.patch(
            f"/api/novels/{novel.id}/chapters/999/outline",
            json={"summary": "不存在"},
        )

        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════
#  Seam 7: GET /volumes — 派生区间
# ═══════════════════════════════════════════════════════


class TestVolumeDerivedRange:
    """GET /api/novels/{novel_id}/volumes 含派生章节区间。"""

    @pytest.mark.asyncio
    async def test_derived_range_single_volume(self, http_env):
        """单卷：start_index=1, end_index=planned_size。"""
        client, novel, _ = http_env

        await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "第一卷", "planned_size": 15},
        )
        resp = await client.get(f"/api/novels/{novel.id}/volumes")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["start_index"] == 1
        assert data[0]["end_index"] == 15

    @pytest.mark.asyncio
    async def test_derived_range_multiple_volumes(self, http_env):
        """多卷：start_index = 1 + Σ前序卷 planned_size。"""
        client, novel, _ = http_env

        await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "第一卷", "planned_size": 10},
        )
        await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "第二卷", "planned_size": 20},
        )
        await client.post(
            f"/api/novels/{novel.id}/volumes",
            json={"title": "第三卷", "planned_size": 5},
        )
        resp = await client.get(f"/api/novels/{novel.id}/volumes")

        data = resp.json()
        assert data[0]["start_index"] == 1
        assert data[0]["end_index"] == 10
        assert data[1]["start_index"] == 11
        assert data[1]["end_index"] == 30
        assert data[2]["start_index"] == 31
        assert data[2]["end_index"] == 35
