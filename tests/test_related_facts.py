"""related_facts：显式关系反查（相关人物）的纯存储测试。"""
import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_related_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_related_test", package)
s = importlib.import_module("alife_related_test.storage")

SID = "qq:gm:1"
FF = "qq:1002"
AY = "qq:1003"
XX = "qq:1004"


def _store(tmp_path):
    store = s.Store(tmp_path / "m.db")
    store.initialize()
    store.capture(SID, "t", [
        {"role": "user", "content": "开场", "users": ["qq:1001"], "time": 1.0}
    ])
    return store


def _fact(store, subject, content, relations, category="relationship"):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return store._add_fact(db, SID, {
            "category": category,
            "subject": subject,
            "content": content,
            "reason": "",
            "scenario": "",
            "tags": [],
            "relations": relations,
            "source_ids": [store.active(SID)[0]["id"]],
            "importance": 7,
        })


def test_related_facts_finds_incoming_explicit_edges(tmp_path):
    store = _store(tmp_path)
    a = _fact(store, AY, "阿远负责夜班巡逻",
              [{"subject": AY, "predicate": "同事", "object": FF}])
    b = _fact(store, XX, "小夏和弗弗是朋友",
              [{"subject": XX, "predicate": "朋友", "object": FF}])
    rows = store.related_facts(
        FF, tokens=[FF, "弗弗"], exclude_subjects=[FF, AY], limit=20
    )
    assert [row["id"] for row in rows] == [b]
    assert rows[0]["_other"] == XX
    assert rows[0]["_predicate"] == "朋友"
    assert a not in [row["id"] for row in rows]


def test_related_facts_skips_generic_predicates_and_bad_json(tmp_path):
    store = _store(tmp_path)
    _fact(store, AY, "阿远觉得弗弗很好",
          [{"subject": AY, "predicate": "觉得", "object": FF}])
    # 脏 relations 不能让反查抛异常
    _fact(store, XX, "坏关系", "", category="relationship")
    rows = store.related_facts(
        FF, tokens=[FF, "弗弗"], exclude_subjects=[FF], limit=20
    )
    assert rows == []


def test_related_facts_outgoing_direction(tmp_path):
    store = _store(tmp_path)
    fact_id = _fact(store, FF, "弗弗的表哥是阿远",
                    [{"subject": FF, "predicate": "表哥", "object": AY}])
    rows = store.related_facts(
        FF, tokens=[FF, "弗弗"], exclude_subjects=[], limit=20
    )
    assert [row["id"] for row in rows] == [fact_id]
    assert rows[0]["_other"] == AY
    assert rows[0]["_predicate"] == "表哥"


def test_related_facts_respects_recall_scope(tmp_path):
    store = _store(tmp_path)
    other = "qq:gm:2"
    store.capture(other, "t", [
        {"role": "user", "content": "别的会话", "users": [FF], "time": 2.0}
    ])
    other_rec = store.active(other)[-1]["id"]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        store._add_fact(db, other, {
            "category": "relationship",
            "subject": AY,
            "content": "阿远在别的会话负责夜班巡逻",
            "reason": "",
            "scenario": "",
            "tags": [],
            "relations": [{"subject": AY, "predicate": "同事", "object": FF}],
            "source_ids": [other_rec],
            "importance": 8,
        })
    tokens = [FF, "弗弗"]
    excluded = [FF]
    assert len(store.related_facts(FF, tokens, excluded, 20, True)) == 1
    assert store.related_facts(FF, tokens, excluded, 20, False, SID, [FF]) == []
