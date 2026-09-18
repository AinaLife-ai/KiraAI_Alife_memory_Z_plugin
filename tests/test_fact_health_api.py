"""fact_health 接口：**真调一次**，字段与排序都必须对（★ 2026-09-18）。

为什么要有这个文件：
    v2.18.33 的后端改动把"列名表"取在了 `with self.connect()` **块外**
    ⇒ 连接已关 ⇒ `/fact_health` 直接 **500** ✗（用户在页面上看到"加载失败"）
    而当时只有**前端**烟测（喂假数据）⇒ 后端怎么错都测不出来 ✗
    ⇒ 结论：**接口必须被真的调用一次** ✓
"""

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_pkg = types.ModuleType("z_health_api")   # storage.py 内部用**相对导入** ✓
_pkg.__path__ = [str(ROOT)]
sys.modules["z_health_api"] = _pkg
storage = sys.modules["z_health_api.storage"] if False else __import__(
    "importlib"
).import_module("z_health_api.storage")


def _store(tmp_path):
    st = storage.Store(tmp_path / "m.db")
    st.initialize()
    return st


def _insert(st, **kw):
    with st.connect() as db:
        info = db.execute("PRAGMA table_info(facts)").fetchall()
        cols = [r[1] for r in info]
        row = {r[1]: "" for r in info if r[3] and r[4] is None}  # NOT NULL 无默认值
        row.update(
            {
                "id": "f1",
                "sid": "s1",
                "subject": "u1",
                "content": "猫在窗台",
                "category": "fact",
                "importance": 3,
                "created": time.time(),
            }
        )
        row.update(kw)
        row = {c: v for c, v in row.items() if c in cols}
        db.execute(
            "INSERT INTO facts (%s) VALUES (%s)"
            % (", ".join(row), ", ".join(["?"] * len(row))),
            list(row.values()),
        )


def test_fact_health_really_works(tmp_path):
    """★ 只要这个接口 500（比如连接已关 / 列名错），这里立刻红 ✓"""
    st = _store(tmp_path)
    _insert(st)
    health = st.fact_health()
    assert isinstance(health, dict), health
    rows = health.get("rows")
    assert rows, "接口没返回行：%r" % (health,)
    row = rows[0]
    # 前端模板用到的键**一个都不能少**（这就是"与事实卡片逐样对齐"的数据侧）
    for key in (
        "id", "sid", "subject", "content", "category", "importance",
        "rotate_used", "age_days", "score", "sunk", "never_sink",
    ):
        assert key in row, "缺字段 %s（前端模板会显示 undefined）" % key
    # 这两样前端要 .length / join() ⇒ 必须是**列表**而不是 JSON 字符串
    for key in ("tags", "sources"):
        assert isinstance(row.get(key, []), list), "%s 必须是列表：%r" % (key, row.get(key))
    assert isinstance(row.get("rewrite_pending", 0), int)


def test_fact_health_sorted_by_score(tmp_path):
    st = _store(tmp_path)
    _insert(st, id="low", importance=2, created=time.time())
    _insert(st, id="high", importance=9, created=time.time())
    rows = st.fact_health()["rows"]
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores), "必须分数低的在前（最该处理的先看到）：%r" % (scores,)
    assert rows[0]["id"] == "low"
    assert any(r["never_sink"] for r in rows), "重要度 9 必须标成永不沉"


def test_fact_health_server_side_paging(tmp_path):
    """★ 用户实测"加载太久"的修复：服务端分页（一次只回 50 行，count 给总数）✓"""
    st = _store(tmp_path)
    for i in range(120):
        _insert(st, id="f%03d" % i, importance=1 + i % 10, created=time.time() - i)
    first = st.fact_health()
    assert first["count"] == 120, "count 必须是**总数**（前端翻页器要用）：%r" % first["count"]
    assert len(first["rows"]) == 50, "一页只回 50 行（原来 300 行 = 189.5 KB ✗）"
    assert first["offset"] == 0
    second = st.fact_health(offset=50)
    assert second["count"] == 120 and second["offset"] == 50
    assert len(second["rows"]) == 50
    assert {r["id"] for r in first["rows"]} & {r["id"] for r in second["rows"]} == set(), "两页不许重叠"
