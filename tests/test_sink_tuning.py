"""2026-09-23 下沉重新标定（用户拍板）的守卫。

口径：阈值 **15**；新鲜度 **按天线性衰减（60 天归零）**；
本轮命中 **+4**；被用次数 **60 天半衰期**；重要度 ≥8 永不沉。

判据都写成"**旧实现会失败**"的形式 ⇒ 回退补丁时必然报红 ✓
"""

import importlib
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_sink_tuning")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_sink_tuning", pkg)
r = importlib.import_module("alife_sink_tuning.retrieval")
c = importlib.import_module("alife_sink_tuning.contracts")

NOW = 1_800_000_000.0


def fact(importance=5, used=0, age_days=0, used_at_days=None, has_created=True):
    d = {"id": "f" + str(importance), "importance": importance, "rotate_used": used}
    if has_created:
        d["created"] = NOW - age_days * 86400
    if used_at_days is not None:
        d["rotate_used_at"] = NOW - used_at_days * 86400
    return d


def test_default_threshold_and_constants():
    assert c.Settings().fact_sink_threshold == 15, "默认阈值应为 15（用户拍板）"
    assert r.FRESH_FADE_DAYS == 60 and r.RELEVANCE_BONUS == 4
    assert r.USED_HALF_LIFE_DAYS == 60
    assert r.NEVER_SINK_IMPORTANCE == 8, "重要度 ≥8 永不沉（用户拍板保持不变）"


def test_freshness_fades_linearly_not_in_steps():
    """★ 旧实现是 30/90 档位 ⇒ 分数会在档位处**跳变**、整批事实卡在同一分上。
    现在必须平滑：0 天 20 分、30 天 15 分、60 天及以后只剩重要度本身。"""
    assert r.fact_sink_score(fact(5, 0, 0), now=NOW) == 20
    assert r.fact_sink_score(fact(5, 0, 30), now=NOW) == 15
    assert r.fact_sink_score(fact(5, 0, 60), now=NOW) == 10
    assert r.fact_sink_score(fact(5, 0, 90), now=NOW) == 10, "归零后不再降"


def test_importance_six_and_seven_can_sink_but_eight_never():
    """★ 旧口径 6×2=12 且阈值 12 ⇒ 重要度 ≥6 **实际永不沉**（与面板说明矛盾）。
    新口径必须能让 6、7 在足够老时让位；≥8 仍永不沉。"""
    assert r.should_sink(fact(6, 0, 120), 15, now=NOW) is True
    assert r.should_sink(fact(7, 0, 120), 15, now=NOW) is True
    assert r.should_sink(fact(8, 0, 9999), 15, now=NOW) is False
    assert r.should_sink(fact(10, 0, 9999), 15, now=NOW) is False


def test_default_importance_keeps_about_a_month():
    """重要度 5（默认档）：一个月内不让位，之后让位（≈30 天）"""
    assert r.should_sink(fact(5, 0, 20), 15, now=NOW) is False
    assert r.should_sink(fact(5, 0, 30), 15, now=NOW) is False
    assert r.should_sink(fact(5, 0, 40), 15, now=NOW) is True


def test_legacy_migration_rows_are_not_stuck_at_the_threshold():
    """★ 线上那条：迁移事实 = 重要度1 + 新鲜10 = 恰好 12 ⇒ 旧阈值 12 时**卡住不沉**。
    新口径下它必须能沉（这是用户报的原始现象）。"""
    assert r.fact_sink_score(fact(1, 0, 8), now=NOW) == 11      # 2 + 8.7 → 11
    assert r.should_sink(fact(1, 0, 8), 15, now=NOW) is True
    assert r.should_sink(fact(2, 0, 8), 15, now=NOW) is True    # 4 + 8.7 → 13 ⇒ 也沉
    assert r.should_sink(fact(3, 0, 8), 15, now=NOW) is False   # 6 + 8.7 → 15 ⇒ 刚好留住


def test_relevance_bonus_prioritises_hits_without_exempting_them():
    """★ 相关优先，但**不是豁免**：命中的低重要度事实短期留下、时间一长仍让位"""
    assert r.should_sink(fact(1, 0, 0), 15, now=NOW) is True
    assert r.should_sink(fact(1, 0, 0), 15, now=NOW, bonus=r.RELEVANCE_BONUS) is False
    assert r.should_sink(fact(1, 0, 30), 15, now=NOW, bonus=r.RELEVANCE_BONUS) is True
    # 重要度更高时，命中能把窗口拉得更长
    assert r.should_sink(fact(4, 0, 30), 15, now=NOW, bonus=r.RELEVANCE_BONUS) is False


def test_used_effect_has_a_half_life():
    """★ 旧实现 `rotate_used` 只增不减 ⇒ 用够几次就永久免沉（没有出口）。
    现在 200 天前用过的 4 次已经衰减到救不回 ⇒ 重新进入考察。"""
    fresh_use = fact(5, 4, 400, used_at_days=0)
    stale_use = fact(5, 4, 400, used_at_days=200)
    assert r.fact_sink_score(fresh_use, now=NOW) > r.fact_sink_score(stale_use, now=NOW)
    assert r.should_sink(fresh_use, 15, now=NOW) is False, "刚被用过的应当浮着"
    assert r.should_sink(stale_use, 15, now=NOW) is True, "半年没人提过 ⇒ 该重新考察"


def test_legacy_rows_without_timestamp_do_not_decay():
    """存量数据没有 `rotate_used_at` ⇒ **不衰减** ✓（升级不惊扰历史数据的命运）"""
    assert r.fact_sink_score(fact(5, 4, 400), now=NOW) == 22     # 10 + 12 + 0
    assert r.should_sink(fact(5, 4, 400), 15, now=NOW) is False


def test_sink_filter_bonus_order_and_no_mutation():
    import json

    facts = [
        fact(9, 0, 500),
        fact(2, 0, 8),                       # 13 分 ⇒ 无关时沉；命中 +4 ⇒ 17 ⇒ 留住
        fact(5, 5, 500, used_at_days=0),      # 10+15 = 25 ⇒ 无论如何都留
    ]
    before = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    kept = r.sink_filter(facts, 15, now=NOW)
    assert [f["id"] for f in kept] == ["f9", "f5"], "顺序稳定、该留的留"
    kept2 = r.sink_filter(facts, 15, now=NOW, bonus=r.RELEVANCE_BONUS)
    assert [f["id"] for f in kept2] == ["f9", "f2", "f5"], "命中加成把低重要度救回来"
    assert json.dumps(facts, ensure_ascii=False, sort_keys=True) == before, "不许就地改"


def test_no_created_never_sinks_and_zero_disables():
    assert r.should_sink(fact(2, 0, 9999, has_created=False), 15, now=NOW) is False
    facts = [fact(1, 0, 999), fact(9, 0, 999)]
    assert len(r.sink_filter(facts, 0, now=NOW)) == 2, "0 = 关闭下沉"
