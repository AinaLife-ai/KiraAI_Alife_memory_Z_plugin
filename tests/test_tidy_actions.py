"""常驻记忆整理（tidy）的三种动作**真的落库** ✓（2026-09-27）

这是"区分什么留在永久记忆、什么提炼成事实"的环节 ✓（不是 classify ✗）
  keep    → 继续常驻 ✓（可顺手修正 category/importance ✓）
  extract → 事实入库 + 该记录 active=False（归档不删 ✓ 不再占每轮席位 ✓）
  split   → 事实入库 + **记录保留但只留 keep_content 那段**（继续常驻 ✓）
"""

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.environ.get("KIRA_CORE"):
    sys.path.insert(0, os.environ["KIRA_CORE"])
pkg = types.ModuleType("alife_tidyA")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_tidyA", pkg)
engine_mod = importlib.import_module("alife_tidyA.engine")
contracts = importlib.import_module("alife_tidyA.contracts")
storage_mod = importlib.import_module("alife_tidyA.storage")


def _fact(text, cat="fact", sid_ok="p1"):
    return {"category": cat, "subject": "用户", "content": text, "reason": "整理",
            "scenario": "", "tags": [], "relations": [], "source_ids": [sid_ok],
            "importance": 7}


async def _run(tmp, action, keep_content=None, fact=_fact("约定：每周日打电话")):
    store = storage_mod.Store(tmp / ("m-%s" % action))
    store.initialize()
    cfg = contracts.Settings(permanent_tidy_enabled=True, permanent_tidy_days=0)
    eng = engine_mod.Engine(store, lambda: cfg, None, None, None)
    rid = await store.call("memorize", "qq:gm:t", "用户要求每周日给妈妈打电话",
                           [], 1000.0, 1000.0, 9, "commitment")
    aliases = {"p1": rid}   # ★ tidy 的别名前缀是 p ✓（classify 才是 r ✓）

    async def _fake(schema, purpose, payload, cfg_, **kw):
        item = {"id": "p1", "action": action, "reason": "测试"}
        if fact is not None:
            item["facts"] = [fact]
        if keep_content:
            item["keep_content"] = keep_content
        return schema(**{"items": [item]}).model_dump()
    eng.structured = _fake

    await eng.start()
    await eng.tidy_permanents("qq:gm:t", force=True)
    await eng.stop()
    row = await store.call("get", rid)
    facts = await store.call("facts", sid="qq:gm:t", limit=10)
    return row, facts


@pytest.mark.asyncio
async def test_keep_stays_active(tmp_path):
    row, _f = await _run(tmp_path, "keep")
    assert row["active"], "keep ⇒ 继续常驻 ✓"


@pytest.mark.asyncio
async def test_extract_archives_and_writes_facts(tmp_path):
    row, facts = await _run(tmp_path, "extract")
    assert not row["active"], "extract ⇒ 该记录不再占常驻席位（归档不删 ✓）"
    assert facts, "extract ⇒ 事实必须入库 ✓"


@pytest.mark.asyncio
async def test_split_keeps_only_constraint(tmp_path):
    row, facts = await _run(tmp_path, "split", keep_content="必须每周日给妈妈打电话")
    assert row["active"], "split ⇒ 约束那段继续常驻 ✓"
    assert "每周日" in (row["summary"] or ""), "只剩 keep_content 那段 ✓"
    assert facts, "split ⇒ 同时提炼出事实 ✓"


@pytest.mark.asyncio
async def test_single_id_rebuild_does_not_merge_whole_session(tmp_path):
    """★ 面板「完全重新提取这一条」（ids 指定）**只能动这一条** ✓

    2026-09-27：写入链给会话级整理加了"先合并相似永久记忆" ✓
    但**单条重提取**是"就改这一条"的语义 ✗ ⇒ 不能顺手合并同会话别的记忆 ✗
    （否则按钮语义被悄悄放大 ✓）
    """
    store = storage_mod.Store(tmp_path / "single.db")
    store.initialize()
    cfg = contracts.Settings(permanent_tidy_enabled=True, permanent_tidy_days=0)
    eng = engine_mod.Engine(store, lambda: cfg, None, None, None)
    rid = await store.call("memorize", "qq:gm:one", "用户对花生过敏，吃了会休克",
                           [], 1000.0, 1000.0, 9, "健康")

    called = []

    async def _spy(sid, *a, **k):
        called.append(sid)
        return {"merged": 0, "permanent": 1}
    eng.consolidate = _spy

    async def _keep(schema, purpose, payload, cfg_, **kw):
        return schema(**{"items": [{"id": "p1", "action": "extract", "reason": "t",
                                    "facts": [{"category": "fact", "subject": "用户",
                                               "content": "对花生过敏会休克", "reason": "重提取",
                                               "scenario": "", "tags": [], "relations": [],
                                               "source_ids": ["p1"], "importance": 9}]}]}).model_dump()
    eng.structured = _keep

    await eng.start()
    await eng.tidy_permanents("qq:gm:one", force=True, ids=[rid], rebuild=True)
    assert not called, "单条重提取不该触发整会话相似合并 ✗（按钮语义被放大 ✗）"
    await eng.tidy_permanents("qq:gm:one", force=True)      # 会话级 ⇒ 应该合并 ✓
    assert called, "会话级整理应该先合并相似永久记忆 ✓"
    await eng.stop()
