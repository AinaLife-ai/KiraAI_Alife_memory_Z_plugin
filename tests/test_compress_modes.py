"""v2.18.11：压缩分批的两种模式。

用户要求：做成模式选择 ✓ 默认**纯按轮**（10 轮 ✓）；另一种"按条"也保留 ✓
但**最后一轮必须收尾完整** ✗ —— 无视条数，也要停在一轮的结尾 ✓

轮的定义（与 KiraOS 的 chunk 一致）：用户（们）发言 → 助手回复
⇒ 边界 =「助手说完之后、下一条用户发言之前」✓
"""
import asyncio
import importlib
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_mode_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_mode_test", package)
engine = importlib.import_module("alife_mode_test.engine")
contracts = importlib.import_module("alife_mode_test.contracts")
storage = importlib.import_module("alife_mode_test.storage")


def row(i, role, vis="session", level=0):
    return {
        "id": "r%03d" % i, "role": role, "level": level, "position": i,
        "permanent": 0, "visibility": vis,
    }


def conversation(rounds, per_round=(2, 2)):
    """造 rounds 轮对话 ✓ 每轮 = 若干条 user + 若干条 assistant ✓"""
    rows, i = [], 0
    for _ in range(rounds):
        for _ in range(per_round[0]):
            rows.append(row(i, "user")); i += 1
        for _ in range(per_round[1]):
            rows.append(row(i, "assistant")); i += 1
    return rows


class RoundsModeCase(unittest.TestCase):
    """默认模式：**纯按轮** ✓ 攒够 N 个完整轮才动手，绝不切半轮 ✓"""

    def setUp(self):
        self.cfg = contracts.Settings(
            compress_batch_mode="rounds", compress_rounds=10,
            threshold=50, batch_size=40, max_level=5,
        )

    def test_default_is_rounds_with_twelve(self):
        d = contracts.Settings()
        self.assertEqual(d.compress_batch_mode, "rounds", "默认必须按轮 ✓")
        self.assertEqual(d.compress_rounds, 12, "默认 12 轮 ✓（约等于原来的 50 条）")

    def test_takes_all_complete_rounds_within_char_budget(self):
        """门槛按 need 轮判 ✓ 批量**只受字符预算**约束（不看条数 ✓ 用户 2026-09-17 确认 ✓）

        历史：曾短暂引入过"按 batch_size 条装满"的条数约束 ✗
        ⇒ 用户指出按轮模式本就"与条数无关、只跟字符数有关" ✓ 已改回 ✓
        """
        rows = conversation(12)                       # 12 轮 × 4 条
        subset, level = engine.compression_plan(rows, self.cfg)
        self.assertEqual(level, 1)
        # 12 轮里只有 11 个「已收尾」轮（最后那轮要等下一个用户发言才成界 ✓ 与原行为一致 ✓）
        # 字符远没用完 ⇒ **11 轮全部取走** ✓（不受 batch_size 条数约束 ✓ 48 vs 44 的差别就在这）
        self.assertEqual(len(subset), 44, "字符没用完 ⇒ 已收尾的轮全取 ✓（不看条数 ✓）")
        self.assertEqual(subset[-1]["role"], "assistant", "必须停在轮尾 ✗ 不许切半轮 ✓")

    def test_waits_for_the_tenth_round_to_finish(self):
        rows = conversation(9) + [row(900, "user")]   # 第 10 轮刚开始 ✗ 还没回复
        self.assertIsNone(engine.compression_plan(rows, self.cfg), "本轮没完不能动手 ✓")

    def test_dangling_turn_is_not_counted_or_included(self):
        rows = conversation(10) + [row(900, "user")]  # 10 整轮 + 半句 ✗
        subset, _ = engine.compression_plan(rows, self.cfg)
        self.assertTrue(all(r["id"] != "r900" for r in subset), "半轮不许进批次 ✓")
        self.assertEqual(len(subset), 40, "只算完整的 10 轮 = 40 条 ✓（末尾半轮排除 ✓）")


class RecordsModeCase(unittest.TestCase):
    """按条模式 ✓ 仍受条数上限保护 ✓ 但**最后一轮必须收尾完整** ✗"""

    def setUp(self):
        self.cfg = contracts.Settings(
            compress_batch_mode="records", threshold=8, batch_size=6, max_level=5,
        )

    def test_last_round_is_completed_even_beyond_the_limit(self):
        rows = conversation(5)                        # 5 轮 × 4 条 = 20 条
        subset, _ = engine.compression_plan(rows, self.cfg)
        self.assertGreater(len(subset), 6, "第 6 条落在轮中 → 必须把这一轮走完 ✗")
        self.assertEqual(subset[-1]["role"], "assistant", "必须停在轮尾 ✓")

    def test_whole_rounds_only(self):
        rows = conversation(5)
        subset, _ = engine.compression_plan(rows, self.cfg)
        self.assertEqual(len(subset) % 4, 0, "批次长度应当是整轮的倍数 ✓")


class SafetyCapCase(unittest.TestCase):
    """一轮异常长（助手一直不回 / 用户刷屏）时别无界增长 ✓"""

    def test_cap_stops_a_never_ending_round(self):
        cfg = contracts.Settings(
            compress_batch_mode="records", threshold=4, batch_size=2, max_level=5,
        )
        rows = [row(i, "user") for i in range(20)]    # 20 条全用户 ✗ 一轮永不结束
        subset, _ = engine.compression_plan(rows, cfg)
        self.assertLessEqual(len(subset), 2 * 3, "整批总长必须 ≤ 3×count（安全上限）✓")

class TrimToRoundCase(unittest.TestCase):
    """重试收缩也必须**停在轮尾** ✗（此前是生硬折半 → 切半轮 ✓）"""

    def test_shrink_lands_on_round_end(self):
        rows = conversation(12)                    # 每轮 4 条 ✓
        out = engine.trim_to_round(rows, 15)       # 目标 15 条（**不是**整轮 ✓ 必须补到轮尾）
        self.assertGreaterEqual(len(out), 15)
        self.assertEqual(len(out) % 4, 0, "必须停在轮尾（每轮 4 条）✗")
        self.assertLessEqual(len(out), 15 + 8, "受安全上限约束 ✓")

    def test_no_reply_falls_back_to_count(self):
        rows = [row(i, "user") for i in range(20)]   # 分不出轮 ✗
        out = engine.trim_to_round(rows, 5)
        self.assertEqual(len(out), 5, "没有助手回复时退回纯按条 ✓")


class OversizedRoundCase(unittest.TestCase):
    """一个轮本身就超过字符预算时怎么办 ✓（2026-09-17 用户提问 ✓）

    优先级（冲突时的让步顺序 ✓）：
      ① 不永久滞留（防死锁）> ② 不超字符预算 > ③ 绝不切半轮
    ⇒ 实测三种情形的处置见各用例 ✓
    """

    def _mk(self, specs):
        now = time.time()
        rows = []
        i = 0
        for r, (ln, cnt) in enumerate(specs):
            t = now - 400 * 86400 - r * 3600
            rows.append(dict(id="u%d" % r, sid="s:big", role="user", level=0, start=t, end=t,
                             created=t, summary="x" * ln, permanent=0, tier="active",
                             importance=5, position=i + 1, visibility="session"))
            i += 1
            for k in range(cnt - 1):
                rows.append(dict(id="a%d_%d" % (r, k), sid="s:big", role="assistant", level=0,
                                 start=t + k + 1, end=t + k + 1, created=t + k + 1,
                                 summary="x" * ln, permanent=0, tier="active", importance=5,
                                 position=i + 1, visibility="session"))
                i += 1
        return rows

    def setUp(self):
        self.cfg = contracts.Settings(compress_batch_mode="rounds", compress_rounds=2,
                              batch_size=40, threshold=50)
        self.cap = self.cfg.compress_input_max_chars

    def test_oversized_round_in_middle_backs_off_to_round_boundary(self):
        """超长轮在中间 ⇒ 退到前一个整轮边界（只压前面的完整轮 ✓ 无 … 记号 ✓）"""
        rows = self._mk([(100, 2), (100, 2), (7000, 3), (100, 2)])
        subset, _ = engine.compression_plan(rows, self.cfg)
        self.assertLessEqual(sum(len(r["summary"]) for r in subset), self.cap, "超预算 ✗")
        self.assertFalse(any(r.get("_partial") for r in subset), "退让后不该有 … 记号 ✓")
        self.assertEqual(subset[-1]["role"], "assistant", "必须停在轮尾 ✓")

    def test_oversized_round_first_takes_what_fits_with_mark(self):
        """超长轮在最前 ⇒ 装到装不下为止 + 打 … 记号（否则永远压不动 ✗）"""
        rows = self._mk([(7000, 3), (100, 2), (100, 2)])
        subset, _ = engine.compression_plan(rows, self.cfg)
        self.assertTrue(subset, "完全不压 ⇒ 永久滞留 ✗")
        self.assertLessEqual(sum(len(r["summary"]) for r in subset), self.cap)
        self.assertTrue(subset[-1].get("_partial"), "拆轮时必须打 … 记号 ✓")

    def test_single_record_over_budget_is_still_taken(self):
        """**单条消息**就超预算 ⇒ 预算让位于"至少给 1 条"（防死锁 ✓）"""
        rows = self._mk([(30000, 1), (500, 2), (500, 2), (500, 2), (500, 2)])
        subset, _ = engine.compression_plan(rows, self.cfg)
        self.assertTrue(subset, "单条超预算就永远压不动 ✗")
        self.assertGreater(len(subset[0]["summary"]), self.cap, "应当保留这一条（不能丢 ✓）")

    def test_oversized_round_eventually_drains(self):
        """超大轮**不会卡死**：反复压直到无内容，最终只剩"还没收尾的新轮" ✓"""
        rows = self._mk([(7000, 3), (100, 2), (100, 2), (100, 2)])
        left = list(rows)
        for _ in range(10):
            plan = engine.compression_plan(left, self.cfg)
            if not plan:
                break
            ids = {r["id"] for r in plan[0]}
            left = [r for r in left if r["id"] not in ids]
        self.assertLessEqual(len(left), 2, "残留 %d 条 ⇒ 有滞留 ✗" % len(left))


class MigratedDistilledCase(unittest.TestCase):
    """方案 B：迁移导入且**已提炼过知识**的内容 ⇒ 只归档、不调模型 ✓

    为什么能省（2026-09-17 用户提出 ✓）：迁移时每个条目**就已经写过一条 fact** ✓
    知识已经在事实层 ✓ 再让模型压一遍纯属重复花钱 ✗（2000 条 ≈50 次调用 → **0 次** ✓）

    判据对**存量用户**同样有效 ✓（不依赖新列 ✓ 直接查 migration_items + facts.sources ✓）
    且**只对迁移来的记录生效** ✓（普通会话不在 migration_items 里 ⇒ 行为完全不变 ✓✓）

    ⚠️ 合并与审计**不受影响** ✗：
      · 合并：迁移时 queue_migration_merges ✓ 压缩产生新事实时 queue_fact_merges ✓
        召回时 queue_recall_merges ✓ —— 都在本路径之外 ✓
      · 审计：scheduler 按会话跑 ✓ 与压缩无关 ✓
    """

    def _seed(self, cats, sid, n=40):
        now = time.time()
        with self.store.connect() as db:
            self.store._ensure_entities(db, sid, ["u-1"])
            for i in range(n):
                t = now - 400 * 86400 - i * 60
                rid = "m%02d" % i
                db.execute(
                    "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
                    "position,created,visibility,active,deleted) VALUES(?,?,?,0,?,?,?,?,?,?,?,?,1,0)",
                    (rid, sid, "user", t, t, "旧内容 %d" % i, "旧内容 %d" % i,
                     json.dumps(["u-1"]), i + 1, now, "session"))
                db.execute("INSERT INTO migration_items VALUES(?,?,?,?,?,?,?,?)",
                           ("old", "k%d" % i, "d%d" % i, rid, "imported", "fh", "{}", now))
                db.execute(
                    "INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,"
                    "relations,sources,fingerprint,deleted,revision,audited,importance,"
                    "merge_pending,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,1,0,5,0,?)",
                    ("f%d" % i, sid, cats[i % len(cats)], "u-1", "知识 %d" % i, "", "", "[]",
                     "[]", json.dumps([rid]), "fp%d" % i, now))
            db.commit()

    def _run(self, sid):
        calls = []

        async def model(*a, **k):
            calls.append(1)
            return json.dumps({"summary": "摘要", "facts": []})

        cfg = contracts.Settings()
        engine._BOOST_AT.pop(sid, None)
        loop = asyncio.new_event_loop()

        async def flow():
            eng = engine.Engine(self.store, lambda: cfg, model, None, None)
            await eng.start()
            await eng.compress(sid)
            await eng.stop()
        try:
            loop.run_until_complete(flow())
        finally:
            loop.close()
        with self.store.connect() as db:
            ac = dict(db.execute("SELECT active,count(*) FROM records WHERE sid=? GROUP BY active",
                                 (sid,)).fetchall())
            nf = db.execute("SELECT count(*) FROM facts WHERE sid=? AND deleted=0", (sid,)).fetchone()[0]
        return calls, ac, nf

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = storage.Store(Path(self.temp.name) / "db")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_knowledge_categories_skip_model_and_archive(self):
        sid = "legacy:know:1"
        self._seed(["fact", "preference", "profile"], sid)
        calls, ac, nf = self._run(sid)
        self.assertEqual(calls, [], "还在调模型 ✗ ⇒ 没省到钱（方案 B 失效 ✓）")
        self.assertEqual(ac.get(1, 0), 0, "记录没退出活跃上下文 ✗")
        self.assertEqual(nf, 40, "事实被动了 ✗（必须一条不丢 ✓）")

    def test_event_category_still_compresses(self):
        """经历类（event）**不跳** ✓ —— 它还需要模型做叙事摘要 ✓"""
        sid = "legacy:event:1"
        self._seed(["event"], sid)
        calls, ac, nf = self._run(sid)
        self.assertTrue(calls, "event 类也被跳过了 ✗ ⇒ 经历类会失去摘要 ✓")

    def test_non_migrated_session_unaffected(self):
        """普通会话（不在 migration_items 里）行为**完全不变** ✓"""
        sid = "qq:gm:5"
        now = time.time()
        with self.store.connect() as db:
            self.store._ensure_entities(db, sid, ["u-1"])
            for i in range(20):
                t = now - 10 * 86400 - i * 60
                db.execute(
                    "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
                    "position,created,visibility,active,deleted) VALUES(?,?,?,0,?,?,?,?,?,?,?,?,1,0)",
                    ("n%02d" % i, sid, ("user", "assistant")[i % 2], t, t, "对话 %d" % i,
                     "对话 %d" % i, json.dumps(["u-1"]), i + 1, now, "session"))
            db.commit()
        calls, ac, nf = self._run(sid)
        self.assertTrue(calls, "普通会话也应正常压缩 ✗（不能被我改坏 ✓）")


class DuplicateImportCase(unittest.TestCase):
    """重复导入的记录（同一 key 第二次导入）也要被判为「已提炼」✓

    迁移时重复项**记录照写、事实不写** ✗（fact 只在首次写 ✓）
    ⇒ 按 record_id 判会漏掉它们 ⇒ 白走一次模型压缩 ✗（成本泄漏 ✓）
    ⇒ 改成按**来源 key** 判：同 key 只要有一条被提炼过 ✓ 整组都算已提炼 ✓
    """

    def test_duplicate_key_record_is_distilled(self):
        now = time.time()
        st = storage.Store(Path(tempfile.mkdtemp()) / "db")
        st.initialize()
        sid = "legacy:dup:1"
        with st.connect() as db:
            st._ensure_entities(db, sid, ["u-1"])
            # 首次导入：记录 + 事实 ✓
            db.execute("INSERT INTO records(id,sid,role,level,start,end,summary,content,users,position,created,visibility,active,deleted)"
                       " VALUES('r1',?,'user',0,?,?,?,?,?,?,?,?,1,0)",
                       (sid, now, now, "内容", "内容", "[]", 1, now, "session"))
            db.execute("INSERT INTO migration_items VALUES('old','same-key','d1','r1','',?, '{}',?)", ("fh", now))
            db.execute("INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,relations,sources,"
                       "fingerprint,deleted,revision,audited,importance,merge_pending,created)"
                       " VALUES('f1',?,'preference','u-1','知识','','','[]','[]',?,'fp1',0,1,0,5,0,?)",
                       (sid, json.dumps(["r1"]), now))
            # 二次导入：同 key，记录照写 ✓ 但**没有事实** ✗
            db.execute("INSERT INTO records(id,sid,role,level,start,end,summary,content,users,position,created,visibility,active,deleted)"
                       " VALUES('r2',?,'user',0,?,?,?,?,?,?,?,?,1,0)",
                       (sid, now, now, "内容", "内容", "[]", 2, now, "session"))
            db.execute("INSERT INTO migration_items VALUES('old','same-key','d2','r2','',?, '{}',?)", ("fh", now))
            db.commit()
        self.assertTrue(st.distilled_only(sid, ["r1"]), "首次导入应判为已提炼 ✓")
        self.assertTrue(st.distilled_only(sid, ["r2"]), "重复导入（无事实 ✗）也必须判为已提炼 ✓")
        self.assertTrue(st.distilled_only(sid, ["r1", "r2"]), "整批都要判为已提炼 ✓")

    def test_event_category_is_not_distilled(self):
        now = time.time()
        st = storage.Store(Path(tempfile.mkdtemp()) / "db")
        st.initialize()
        sid = "legacy:dup:2"
        with st.connect() as db:
            st._ensure_entities(db, sid, ["u-1"])
            db.execute("INSERT INTO records(id,sid,role,level,start,end,summary,content,users,position,created,visibility,active,deleted)"
                       " VALUES('e1',?,'user',0,?,?,?,?,?,?,?,?,1,0)",
                       (sid, now, now, "经历", "经历", "[]", 1, now, "session"))
            db.execute("INSERT INTO migration_items VALUES('old','ev-key','d1','e1','',?, '{}',?)", ("fh", now))
            db.execute("INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,relations,sources,"
                       "fingerprint,deleted,revision,audited,importance,merge_pending,created)"
                       " VALUES('f2',?,'event','u-1','经历','','','[]','[]',?,'fp2',0,1,0,5,0,?)",
                       (sid, json.dumps(["e1"]), now))
            db.commit()
        self.assertFalse(st.distilled_only(sid, ["e1"]), "event 类不许判为已提炼 ✗（仍需要摘要 ✓）")
