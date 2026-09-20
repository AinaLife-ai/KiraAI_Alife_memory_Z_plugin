"""冷归档（P7 · v7）：**骨架行 + content 外置** 的真跑验证

关键验收（v6 的整行方案就是死在这里 ✗）：
  ★ 跨边界关系必须活下来：edges(cold→hot) 在搬迁后**依然合法** ✓（行都还在 ✓）
  ★ content 一字不丢：冷库取回与原文**逐字符一致** ✓
  ★ restore 后 records **逐字段**回到迁移前 ✓
  ★ 热行 / 非冷行**一条不动** ✓；迁移后 `PRAGMA foreign_key_check` 必须为空 ✓
"""
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DDL = """
CREATE TABLE records (id TEXT PRIMARY KEY, sid TEXT, content TEXT, summary TEXT,
  cold INTEGER NOT NULL DEFAULT 0, archived_at REAL NOT NULL DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE edges (rowid INTEGER PRIMARY KEY, from_id TEXT REFERENCES records(id),
  to_id TEXT REFERENCES records(id), kind TEXT);
CREATE TABLE vectors (rowid INTEGER PRIMARY KEY, record_id TEXT REFERENCES records(id), vec BLOB);
"""


def _cold():
    spec = importlib.util.spec_from_file_location("cold_v7", ROOT / "cold.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cold_v7"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_hot(path, n_cold=3, n_hot=2):
    db = sqlite3.connect(str(path))
    db.executescript(DDL)
    db.execute("PRAGMA foreign_keys=ON")
    old = time.time() - 400 * 86400
    for i in range(n_hot):
        db.execute("INSERT INTO records(id,sid,content,summary,cold,archived_at,active) "
                   "VALUES(?,?,?,?,0,0,1)", ("hot%d" % i, "S1", "热正文" + str(i), "热摘要%d" % i))
    for i in range(n_cold):
        db.execute("INSERT INTO records(id,sid,content,summary,cold,archived_at,active) "
                   "VALUES(?,?,?,?,1,?,0)", ("cold%d" % i, "S1", "冷正文" * 40 + str(i), "摘要%d" % i, old))
        # ★ 故意造**跨边界**关系：冷行 → 热行（v6 就是被它推翻的 ✓）
        db.execute("INSERT INTO edges(from_id,to_id,kind) VALUES(?,?,?)", ("cold%d" % i, "hot0", "cross"))
        db.execute("INSERT INTO vectors(record_id,vec) VALUES(?,?)", ("cold%d" % i, b"xx"))
    db.commit()
    return db


def test_preview_readonly_and_counts(tmp_path):
    m = _cold()
    db = _make_hot(tmp_path / "hot.db")
    before = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    info = m.preview(db, days=180)
    assert info["records"] == 3, info
    assert info["bytes"] > 0
    assert db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == before, "preview 不许写 ✗"


def test_spill_keeps_rows_and_clears_content(tmp_path):
    m = _cold()
    hot, cpath = tmp_path / "hot.db", tmp_path / "cold.db"
    db = _make_hot(hot)
    res = m.spill(db, cpath, days=180)
    assert res["ok"] and res["moved"] == 3, res
    assert db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 5, "★行不许删（外键的根基）✗"
    rows = db.execute("SELECT id, content, summary FROM records WHERE cold=1 ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["cold0", "cold1", "cold2"]
    assert all(r[1] == "" for r in rows), "冷行 content 应已置空 ✓"
    assert [r[2] for r in rows] == ["摘要0", "摘要1", "摘要2"], "summary 必须原样（打分要用）✓"
    assert db.execute("SELECT id, content FROM records WHERE cold=0 ORDER BY id").fetchall() == \
        [("hot0", "热正文0"), ("hot1", "热正文1")], "热行一条不动 ✓"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == [], "★跨边界外键必须完好 ✓"
    assert db.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 3, "关系行不该被搬 ✓"


def test_content_roundtrip_is_lossless(tmp_path):
    m = _cold()
    hot, cpath = tmp_path / "hot.db", tmp_path / "cold.db"
    db = _make_hot(hot)
    before = dict(db.execute("SELECT id, content FROM records WHERE cold=1").fetchall())
    assert m.spill(db, cpath, days=180)["ok"]
    assert m.content_of(cpath, list(before)) == before, "冷库取回必须逐字符一致 ✓"
    r = m.restore(db, cpath, ids=list(before))
    assert r["ok"] and r["restored"] == 3, r
    after = dict(db.execute("SELECT id, content FROM records WHERE cold=1").fetchall())
    assert after == before, "★restore 后必须与迁移前逐字段一致 ✓"
    assert m.content_of(cpath, list(before)) == {}, "restore 后冷库副本应已删 ✓"


def test_idempotent_and_day_gate(tmp_path):
    m = _cold()
    db = _make_hot(tmp_path / "hot.db")
    cpath = tmp_path / "cold.db"
    assert m.spill(db, cpath, days=180)["moved"] == 3
    assert m.spill(db, cpath, days=180)["moved"] == 0, "重跑不应重复搬 ✓"
    db.execute("INSERT INTO records(id,sid,content,summary,cold,archived_at,active) "
               "VALUES(?,?,?,?,1,?,0)", ("fresh", "S1", "刚归档", "s", time.time() - 5 * 86400))
    db.commit()
    assert m.preview(db, days=180)["records"] == 0, "未满 180 天不许搬 ✓"


def test_stats_reports_cold_side(tmp_path):
    m = _cold()
    db = _make_hot(tmp_path / "hot.db")
    cpath = tmp_path / "cold.db"
    st0 = m.stats(cpath)
    assert st0["exists"] is False and st0["records"] == 0
    m.spill(db, cpath, days=180)
    st = m.stats(cpath)
    assert st["exists"] and st["records"] == 3 and st["bytes"] > 0, st


def test_fill_contents_backfills_only_empty(tmp_path):
    """回填：只填空的 ✓ 不覆盖已有正文 ✓ 找不到的保持原样 ✓"""
    m = _cold()
    db = _make_hot(tmp_path / "hot.db")
    cpath = tmp_path / "cold.db"
    m.spill(db, cpath, days=180)
    rows = [{"id": "cold0", "content": ""}, {"id": "hot0", "content": "热正文0"},
            {"id": "nope", "content": ""}]
    out = m.fill_contents(rows, cpath)
    assert out[0]["content"].startswith("冷正文"), "空正文必须被回填 ✓"
    assert out[1]["content"] == "热正文0", "已有正文绝不能被覆盖 ✓"
    assert out[2]["content"] == "", "找不到的原样保留 ✓"
    assert m.default_path(tmp_path / "hot.db").name == "memory_cold.db"


def test_wiring_sites_are_present():
    """接线守卫：三处入口必须在 ⇒ 以后谁删掉一处，立刻红 ✓"""
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    stor_src = (ROOT / "storage.py").read_text(encoding="utf-8")
    assert "def _cold_path_of(self" in main_src and "def _cold_fill(self" in main_src
    assert main_src.count("self._cold_fill(") >= 2, "浏览/详情两处都要回填 ✓"
    assert 'result["items"] = self._cold_fill' in main_src, "面板浏览必须回填 ✓"
    assert "def undelete(self, kind, target, cold_path=None)" in stor_src, "还原要能取回 ✓"
    assert "_cold.restore(db, _p" in stor_src, "还原时必须真的取回正文 ✓"
    assert "cold_archive_enabled" in main_src, "必须受设置开关控制 ✓"
