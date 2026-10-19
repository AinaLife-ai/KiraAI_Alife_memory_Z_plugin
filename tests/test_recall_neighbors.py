"""真实宿主：相关人物 / names 补全 / 主动合并 / 当前说话人画像优先。"""
import json
import os
import sys
from pathlib import Path

import pytest

CORE = os.environ.get("KIRA_CORE")
if not CORE:
    pytest.skip("set KIRA_CORE for actual host integration", allow_module_level=True)
sys.path.insert(0, str(Path(CORE).resolve()))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from core.chat import MessageChain
from core.chat.message_elements import Text
from core.chat.message_utils import KiraIMMessage, KiraMessageBatchEvent
from core.chat.session import User, Session
from core.provider import LLMRequest
from test_helpers_plugin import build_plugin

ME = "test:1001"
FF = "test:1002"
AY = "test:1003"
XX = "test:1004"


def _event(text, sender="1001", sid="gm", group=True):
    session = Session(
        adapter_name="test",
        session_type="group" if group else "dm",
        session_id=sid,
    )
    msg = KiraIMMessage(
        message_id="one",
        self_id="bot",
        chain=MessageChain([Text(text)]),
        timestamp=100,
        sender=User(user_id=sender, nickname="我"),
    )
    msg.message_str = "[我] " + text
    return KiraMessageBatchEvent(
        messages=[msg], session=session, timestamp=100, message_types=[]
    )


def _add(store, sid, subject, category, content, importance, relations=None):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return store._add_fact(db, sid, {
            "category": category,
            "subject": subject,
            "content": content,
            "reason": "",
            "scenario": "",
            "tags": [],
            "relations": relations or [],
            "source_ids": [store.active(sid)[-1]["id"]],
            "importance": importance,
        })


@pytest.mark.asyncio
async def test_passive_names_neighbors_and_no_duplicates(tmp_path):
    plugin, store = await build_plugin(tmp_path)
    await plugin.wait_migration()
    try:
        s = plugin.settings
        s.top_k = 5
        s.jev_enabled = False
        s.rotate_enabled = True
        s.rotate_count = 3
        s.fact_recall_min_score = 2
        s.fact_merge_enabled = False
        s.audit_enabled = False
        s.permanent_tidy_enabled = False
        s.profile_neighbors = "smart"
        s.profile_neighbors_max = 2

        event = _event("弗弗是谁？")
        sid = event.sid
        for entity, name in ((sid, "测试群"), (ME, "我"), (FF, "弗弗"),
                             (AY, "阿远"), (XX, "小夏")):
            store.observe_name(entity, name, context=sid, observed=1.0)
        store.capture(sid, "t", [
            {"role": "user", "content": "开场", "users": [ME], "time": 1.0}
        ])

        _add(store, sid, FF, "profile", "弗弗是档案局的记录员", 9)
        _add(store, sid, FF, "preference", "弗弗喜欢猫", 5)
        _add(store, sid, AY, "relationship", "阿远是弗弗的表哥", 8,
             [{"subject": AY, "predicate": "表哥", "object": FF}])
        _add(store, sid, AY, "relationship", "阿远负责夜班巡逻", 7,
             [{"subject": AY, "predicate": "同事", "object": FF}])
        _add(store, sid, XX, "relationship", "小夏是弗弗的同事", 6,
             [{"subject": XX, "predicate": "同事", "object": FF}])
        for i in range(5):
            _add(store, sid, "test:20%02d" % i, "profile",
                 "路人%d号是群里的活跃成员" % i, 10)

        merge_calls = []

        async def merge_spy(target_sid, facts):
            merge_calls.append([fact.get("content") for fact in facts])
            return set()

        plugin.queue_recall_merges = merge_spy
        req = LLMRequest()
        await plugin.on_request(event, req)
        m = json.loads(next(
            p.content for p in req.user_prompt if p.name == "alife_memory"
        ))["m"]

        assert any(
            "阿远负责夜班巡逻" in (content or "")
            for call in merge_calls for content in call
        ), "相关人物事实必须进入合并检查 ✓"
        assert "=弗弗" in m and "=阿远" in m and "=小夏" in m
        # 只补点名实体/关系端点，不把 5 个路人的主体也塞进码表
        assert "=路人0号" not in m
        # B 只补尚未出现在 facts 里的“夜班巡逻”，已召回的小夏不重复
        assert "相关人物：阿远（弗弗的同事）｜阿远负责夜班巡逻 ★7" in m
        assert "小夏（弗弗的同事）" not in m
        # 主召回 + 轮换不再重复同一 id
        assert m.count("弗弗是档案局的记录员") == 1
        assert m.count("弗弗喜欢猫") == 1
        assert m.count("阿远负责夜班巡逻") == 1
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_neighbor_facts_respect_recall_scope(tmp_path):
    plugin, store = await build_plugin(tmp_path)
    await plugin.wait_migration()
    try:
        s = plugin.settings
        s.jev_enabled = False
        s.recall_scope = "session"
        s.rotate_enabled = False
        s.fact_merge_enabled = False
        s.profile_neighbors = "smart"
        event = _event("弗弗是谁？")
        sid = event.sid
        other = "test:group:other"
        store.observe_name(sid, "测试群", context=sid, observed=1.0)
        store.observe_name(FF, "弗弗", context=sid, observed=1.0)
        store.observe_name(AY, "阿远", context=sid, observed=1.0)
        store.capture(sid, "t", [
            {"role": "user", "content": "开场", "users": [ME], "time": 1.0}
        ])
        store.capture(other, "t", [
            {"role": "user", "content": "别的会话", "users": [FF], "time": 2.0}
        ])
        _add(store, other, AY, "relationship", "阿远在别的会话负责夜班巡逻", 9,
             [{"subject": AY, "predicate": "同事", "object": FF}])

        req = LLMRequest()
        await plugin.on_request(event, req)
        m = json.loads(next(
            p.content for p in req.user_prompt if p.name == "alife_memory"
        ))["m"]
        assert "别的会话" not in m
        assert "相关人物：" not in m
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_neighbor_digest_and_off_modes(tmp_path):
    plugin, store = await build_plugin(tmp_path)
    await plugin.wait_migration()
    try:
        s = plugin.settings
        s.top_k = 5
        s.jev_enabled = False
        s.rotate_enabled = False
        s.fact_merge_enabled = False
        s.profile_neighbors = "digest"
        event = _event("弗弗是谁？")
        sid = event.sid
        store.observe_name(sid, "测试群", context=sid, observed=1.0)
        store.observe_name(FF, "弗弗", context=sid, observed=1.0)
        store.observe_name(AY, "阿远", context=sid, observed=1.0)
        store.capture(sid, "t", [
            {"role": "user", "content": "开场", "users": [ME], "time": 1.0}
        ])
        _add(store, sid, AY, "relationship", "阿远负责夜班巡逻", 7,
             [{"subject": AY, "predicate": "同事", "object": FF}])

        req = LLMRequest()
        await plugin.on_request(event, req)
        m = json.loads(next(
            p.content for p in req.user_prompt if p.name == "alife_memory"
        ))["m"]
        assert "相关人物：阿远（弗弗的同事）" in m
        assert "阿远负责夜班巡逻" not in m

        s.profile_neighbors = "off"
        req2 = LLMRequest()
        await plugin.on_request(event, req2)
        m2 = json.loads(next(
            p.content for p in req2.user_prompt if p.name == "alife_memory"
        ))["m"]
        assert "相关人物：" not in m2
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_flat_view_still_works_with_neighbors(tmp_path):
    plugin, store = await build_plugin(tmp_path)
    await plugin.wait_migration()
    try:
        s = plugin.settings
        s.jev_enabled = False
        s.fact_view = "flat"
        s.rotate_enabled = False
        s.fact_merge_enabled = False
        s.profile_neighbors = "smart"
        event = _event("弗弗是谁？")
        sid = event.sid
        store.observe_name(sid, "测试群", context=sid, observed=1.0)
        store.observe_name(FF, "弗弗", context=sid, observed=1.0)
        store.observe_name(AY, "阿远", context=sid, observed=1.0)
        store.capture(sid, "t", [
            {"role": "user", "content": "开场", "users": [ME], "time": 1.0}
        ])
        _add(store, sid, FF, "profile", "弗弗是档案局的记录员", 9)
        _add(store, sid, AY, "relationship", "阿远负责夜班巡逻", 7,
             [{"subject": AY, "predicate": "同事", "object": FF}])

        req = LLMRequest()
        await plugin.on_request(event, req)
        m = json.loads(next(
            p.content for p in req.user_prompt if p.name == "alife_memory"
        ))["m"]
        assert "弗弗是档案局的记录员" in m
        assert "相关人物：阿远（弗弗的同事）" in m
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_active_overview_and_profile_queue_fact_merges(tmp_path):
    plugin, store = await build_plugin(tmp_path)
    await plugin.wait_migration()
    try:
        s = plugin.settings
        s.fact_merge_enabled = True
        s.jev_enabled = False
        s.audit_enabled = False
        s.permanent_tidy_enabled = False
        event = _event("弗弗是谁？")
        sid = event.sid
        store.observe_name(sid, "测试群", context=sid, observed=1.0)
        store.observe_name(FF, "弗弗", context=sid, observed=1.0)
        store.capture(sid, "t", [
            {"role": "user", "content": "开场", "users": [ME], "time": 1.0}
        ])
        _add(store, sid, FF, "preference", "弗弗喜欢猫", 6)
        _add(store, sid, FF, "preference", "弗弗喜欢猫薄荷", 6)

        calls = []

        async def spy(target_sid, facts):
            calls.append([fact["id"] for fact in facts])
            return set()

        plugin.queue_recall_merges = spy
        await plugin.overview(event)
        assert calls and len(calls[0]) >= 2, "overview 必须用 raw rows 排合并"
        calls.clear()
        await plugin.get_profile(event, subject=FF)
        assert calls and len(calls[0]) >= 2, "GetProfile 必须用 raw categories 排合并"
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_current_speaker_profile_is_not_crowded_out(tmp_path):
    plugin, store = await build_plugin(tmp_path)
    await plugin.wait_migration()
    try:
        s = plugin.settings
        s.top_k = 5
        s.jev_enabled = False
        s.rotate_enabled = False
        s.fact_merge_enabled = False
        s.session_affinity = True
        s.profile_neighbors = "smart"
        event = _event("你今天怎么样", sender="1002", sid="1002", group=False)
        # DM 里人名不一定出现在正文；这里刻意让正文只有“你今天怎么样”，
        # 验证 subjects 为空时 prefer_users 仍把当前说话人的画像排前面。
        event.messages[0].message_str = "你今天怎么样"
        event.messages[0].sender.nickname = "弗弗"
        sid = event.sid
        store.observe_name(FF, "弗弗", context=sid, observed=1.0)
        store.capture(sid, "t", [
            {"role": "user", "content": "开场", "users": [FF], "time": 1.0}
        ])
        _add(store, sid, FF, "profile", "弗弗喜欢甜食", 4)
        for i in range(10):
            _add(store, sid, "test:20%02d" % i, "profile",
                 "路人%d号是群里的活跃成员" % i, 10)

        req = LLMRequest()
        await plugin.on_request(event, req)
        m = json.loads(next(
            p.content for p in req.user_prompt if p.name == "alife_memory"
        ))["m"]
        assert "弗弗喜欢甜食" in m
    finally:
        await plugin.terminate()
