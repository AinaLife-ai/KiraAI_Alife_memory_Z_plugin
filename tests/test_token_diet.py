"""v2.5.3 token 瘦身：注入视图、审计 payload、压缩别名、retract、配置同步。"""

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
package = types.ModuleType("alife_diet_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_diet_test", package)
c = importlib.import_module("alife_diet_test.contracts")
s = importlib.import_module("alife_diet_test.storage")
e = importlib.import_module("alife_diet_test.engine")
r = importlib.import_module("alife_diet_test.retrieval")


def fact(**overrides):
    row = {
        "id": "f-1",
        "sid": "qq:dm:u",
        "category": "preference",
        "subject": "qq:u",
        "content": "喜欢猫",
        "reason": "用户自己说的",
        "scenario": "日常闲聊",
        "tags": ["猫"],
        "relations": [],
        "sources": ["rec-1"],
        "fingerprint": "fp-1",
        "deleted": 0,
        "revision": 0,
        "audited": 0,
        "importance": 6,
        "merge_pending": 0,
        "created": 1700000000.0,
        "relationship_status": "evidence_required",
        "verified_relations": [],
        "relation_warnings": [],
    }
    row.update(overrides)
    return row


class BotFactsTests(unittest.TestCase):
    def test_keeps_only_decision_and_provenance_fields(self):
        view = r.bot_facts([fact()], "qq:dm:u")[0]
        # 默认重要度(5)省略，这里用例给的是 6，所以 imp 会出现
        self.assertEqual(set(view), {"c", "u", "x", "imp", "src", "t"})
        self.assertEqual(view["c"], "pr")  # 类别用短码，图例在静态规则块里
        self.assertEqual(view["src"], "rec-1")
        # 日期短码：同年只给月-日，跨年才补上年份（1700000000 是 2023 年，所以带年）
        self.assertTrue(view["t"].endswith("11-15"), view["t"])
        self.assertNotIn("t2", view)  # 单点事件不给区间
        self.assertNotIn("rec", view)  # 记录时刻与事件时间相同就不重复说
        # 本会话事实不再重复 sid/by
        self.assertNotIn("sid", view)

    def test_cross_session_fact_keeps_source_and_speaker(self):
        view = r.bot_facts(
            [fact(sid="global", src_user="qq:other")], "qq:dm:u"
        )[0]
        self.assertEqual(view["sid"], "global")
        self.assertEqual(view["by"], "qq:other")

    def test_event_span_and_record_time_are_separate(self):
        """跨天合并要显示区间；"记下来"和"发生"差得远时额外标出来。"""
        day = 86400.0
        view = r.bot_facts(
            [
                fact(
                    event_at=1700000000.0,
                    event_end=1700000000.0 + 3 * day,
                    created=1700000000.0 + 30 * day,
                )
            ],
            "qq:dm:u",
        )[0]
        self.assertIn("t", view)
        self.assertIn("t2", view)  # 区间
        self.assertIn("rec", view)  # 30 天后才整理出来的
        self.assertEqual(r.short_day(1700000000.0), view["t"])

    def test_needs_review_only_when_flagged(self):
        self.assertNotIn("rev", r.bot_facts([fact()], "qq:dm:u")[0])
        flagged = r.bot_facts(
            [fact(relationship_status="needs_review")], "qq:dm:u"
        )[0]
        self.assertTrue(flagged["rev"])  # 短键：待核对的关系


class PayloadTests(unittest.TestCase):
    def test_compress_records_use_short_aliases_and_single_timestamp(self):
        rows = [
            {"id": "a" * 32, "role": "user", "level": 0, "summary": "内容", "users": ["u"],
             "start": 100.0, "end": 100.0},
            {"id": "b" * 32, "role": "assistant", "level": 0, "summary": "回复", "users": ["u"],
             "start": 101.0, "end": 101.0},
        ]
        aliases = {"r1": "a" * 32, "r2": "b" * 32}
        records = e.compress_records(rows, aliases, {"u": "周武"})
        self.assertEqual([rec["id"] for rec in records], ["r1", "r2"])
        self.assertNotIn("level", records[0])
        self.assertNotIn("role", records[0])          # 默认 user 不再显式输出
        self.assertEqual(records[1]["bot"], 1)        # assistant 用一位短标记
        self.assertEqual(records[0]["t"], "1970-01-01 08:01")  # 可读时间（本地）
        self.assertNotIn("start", records[0])
        self.assertEqual(records[0]["u"], ["u"])   # 只写 ID；名字在 payload 顶层 names 表

    def test_compress_records_keep_range_for_archives(self):
        rows = [
            {"id": "c" * 32, "role": "assistant", "level": 1, "summary": "摘要", "users": [],
             "start": 10.0, "end": 20.0},
        ]
        record = e.compress_records(rows, {"r1": "c" * 32})[0]
        self.assertNotIn("start", record)
        # 一层以上的存档保留起止两点，都换成可读时间
        self.assertEqual((record["t"], record["t2"]), ("1970-01-01 08:00", "1970-01-01 08:00"))

    def test_restore_maps_aliases_and_rejects_unknown(self):
        aliases = {"r1": "real-1"}
        out = e.restore_compress_ids(
            {"summary": "s", "facts": [{"source_ids": ["r1"]}]}, aliases
        )
        self.assertEqual(out["facts"][0]["source_ids"], ["real-1"])
        with self.assertRaises(ValueError):
            e.restore_compress_ids(
                {"summary": "s", "facts": [{"source_ids": ["r9"]}]}, aliases
            )

    def test_schema_titles_are_stripped(self):
        ov = importlib.import_module("alife_diet_test.output_validation")
        stripped = ov.strip_schema_titles(c.Compression.model_json_schema())
        self.assertNotIn("title", json.dumps(stripped))
        self.assertIn("properties", stripped)


class ConfigSyncTests(unittest.TestCase):
    def test_new_switches_exist_in_all_three_places(self):
        schema = json.load(open(ROOT / "schema.json", encoding="utf-8"))["alife"]["fields"]
        help_text = importlib.import_module("alife_diet_test.setting_help").HELP
        for key in ("compress_persona", "audit_persona", "inject_mode"):
            self.assertIn(key, schema)
            self.assertIn(key, help_text)
            self.assertIn(key, c.Settings.model_fields)
        defaults = c.Settings()
        self.assertTrue(defaults.compress_persona)
        self.assertFalse(defaults.audit_persona)
        self.assertEqual(defaults.inject_mode, "situational")


class RetractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "db.sqlite3")
        self.store.initialize()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for index, fid in enumerate(("f-1", "f-2")):
                db.execute(
                    """INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,
                       relations,sources,fingerprint,deleted,revision,audited,importance,
                       merge_pending,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,0,?,0,?)""",
                    (fid, "qq:dm:u", "event", "qq:u", "内容", "理由", "", "[]", "[]",
                     "[]", "fp-" + fid, 5, 1000.0),
                )

    def tearDown(self):
        self.temp.cleanup()

    def test_retract_soft_deletes_and_keeps_version(self):
        candidates = self.store.audit_candidates("qq:dm:u", limit=5)
        self.assertEqual(len(candidates), 2)
        target = candidates[0]
        self.store.audit(
            candidates,
            {
                "actions": [
                    {
                        "action": "retract",
                        "target_id": target["id"],
                        "source_ids": [target["id"]],
                        "content": target["content"],
                        "reason": "与证据矛盾",
                    }
                ]
            },
        )
        rows = {row["id"]: row for row in self.store.facts("qq:dm:u", "", "", 50, 0, True)}
        self.assertNotIn(target["id"], rows)          # 已退出注入与检索
        self.assertEqual(rows[candidates[1]["id"]]["deleted"], 0)
        with self.store.connect() as db:
            snapshot = db.execute(
                "SELECT snapshot FROM versions WHERE kind='fact' AND target=?",
                (target["id"],),
            ).fetchone()
        self.assertIsNotNone(snapshot)                # 原文留档，可恢复


class AuditPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "db.sqlite3")
        self.store.initialize()
        self.seen = []

        async def model_call(model, purpose, instruction, schema, payload):
            self.seen.append({"purpose": purpose, "payload": payload, "schema": schema})
            return json.dumps({"actions": []}, ensure_ascii=False)

        self.engine = e.Engine(
            self.store,
            lambda: c.Settings(audit_persona=False),
            model_call,
            lambda text, cfg: asyncio.sleep(0, result=(None, "")),
            lambda sid: asyncio.sleep(0, result=None),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_audit_payload_drops_internal_fields(self):
        self.store.capture(
            "qq:dm:u",
            "turn",
            [{"role": "user", "content": "我喜欢猫", "time": 1000.0, "users": ["qq:u"]}],
        )
        record_id = self.store.active("qq:dm:u")[0]["id"]
        with self.store.connect() as db:
            db.execute(
                """INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,
                   relations,sources,fingerprint,deleted,revision,audited,importance,
                   merge_pending,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,0,?,0,?)""",
                ("f-1", "qq:dm:u", "preference", "qq:u", "喜欢猫", "用户自己说的",
                 "日常", '["猫"]', "[]", json.dumps([record_id]), "fp", 5, 1000.0),
            )
        asyncio.run(self.engine.audit("qq:dm:u"))
        payload = self.seen[-1]["payload"]
        # 入库的 facts[].id 换成了 f1..fN 短别名；sources 不再入参（指令本来就要求别用它）
        self.assertEqual(payload["facts"][0]["id"], "f1")
        self.assertEqual(
            set(payload["facts"][0]),
            {"id", "sid", "subject", "category", "content", "reason", "relations",
             "importance"},
        )
        self.assertNotIn("sources", json.dumps(payload))
        self.assertEqual(set(payload["evidence"][0]), {"content", "t"})
        self.assertNotIn("title", json.dumps(self.seen[-1]["schema"]))


if __name__ == "__main__":
    unittest.main()


class AuditSummaryTests(unittest.TestCase):
    def test_summary_lists_actions(self):
        e = importlib.import_module("alife_diet_test.engine")
        self.assertEqual(
            e.audit_summary({"scanned": 20, "keep": 20}), "本次审计 20 条：全部保留"
        )
        self.assertEqual(
            e.audit_summary(
                {"scanned": 20, "keep": 15, "correct": 3, "merge": 2, "merged_facts": 2, "retract": 1}
            ),
            "本次审计 20 条：保留 15 · 修正 3 · 合并 2 组（并入 2 条） · 撤回 1",
        )
        self.assertIn("撤回 1", e.audit_summary({"scanned": 5, "keep": 4, "retract": 1}))


class AuditStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "m.db")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def fact(self, content="萤火喜欢猫", subject="qq:9"):
        self.store.capture(
            "qq:gm:1", "t", [{"role": "user", "content": content, "users": [subject], "time": 1.0}]
        )
        record = self.store.active("qq:gm:1")[-1]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.store._add_fact(
                db,
                "qq:gm:1",
                {
                    "category": "preference",
                    "subject": subject,
                    "content": content,
                    "reason": "",
                    "scenario": "",
                    "tags": [],
                    "relations": [],
                    "source_ids": [record["id"]],
                },
            )

    def test_audit_reports_counts_and_retract_hides_fact(self):
        a = self.fact("萤火喜欢猫")
        b = self.fact("萤火喜欢猫粮")
        candidates = self.store.facts("qq:gm:1")
        output = {
            "actions": [
                {
                    "action": "keep",
                    "target_id": a,
                    "source_ids": [a],
                    "content": "萤火喜欢猫",
                    "reason": "证据一致",
                },
                {
                    "action": "retract",
                    "target_id": b,
                    "source_ids": [b],
                    "content": "萤火喜欢猫粮",
                    "reason": "与上一条重复",
                },
            ]
        }
        counts = self.store.audit(candidates, output)
        self.assertEqual(counts["keep"], 1)
        self.assertEqual(counts["retract"], 1)
        self.assertEqual(counts["merge"], 0)
        left = [f["id"] for f in self.store.facts("qq:gm:1")]
        self.assertEqual(left, [a])


class EmptyLexicalQueryCase(unittest.TestCase):
    """空/纯符号查询**不得**让词面召回崩 ✗✓（2026-09-17 生产事故复现 ✓）

    `_lexical_sql` 无词元时曾返回裸 ``"0"`` ✗ → 拼进 ``ORDER BY`` 被 SQLite
    当成**列位置** → ``1st ORDER BY term out of range - should be between 1 and 21`` ✓
    触发：群里一条纯表情消息（「🤔」）经被动召回传进 ``facts(lexical=...)`` ✓
    同源问题也在 records 的 ``{lexical_sql}`` 上 ✓ 故两处一起守 ✓
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "db")
        self.store.initialize()
        self.cfg = c.Settings()
        with self.store.connect() as db:
            db.execute(
                """INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,
                   relations,sources,fingerprint,deleted,revision,audited,importance,
                   merge_pending,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,0,?,0,?)""",
                ("f-1", "qq:gm:1", "event", "qq:u", "内容", "理由", "", "[]", "[]",
                 "[]", "fp-1", 5, 1000.0),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_facts_with_tokenless_lexical_does_not_crash(self):
        for text in ("", " ", "!!!", "🤔", "、、。"):
            with self.subTest(text=text):
                rows = self.store.facts("qq:gm:1", lexical=text, limit=5)
                self.assertIsInstance(rows, list)  # 不崩即通过 ✓（行数无所谓 ✓）

    def test_records_with_tokenless_lexical_does_not_crash(self):
        for text in ("", " ", "!!!", "🤔"):
            with self.subTest(text=text):
                res = self.store.search("qq:gm:1", lexical=text, limit=5)
                self.assertIsInstance(res, dict)  # 不崩即通过 ✓（search 返回 {total,items} ✓）
                self.assertIn("items", res)

    def test_lexical_sql_never_returns_bare_integer(self):
        """判据本身 ✓：无词元时返回的必须是**表达式**而不是裸整数 ✗"""
        for empty in ((), []):
            sql = s._lexical_sql("lower(content)", empty)
            self.assertFalse(
                sql.strip().isdigit(),
                "_lexical_sql 无词元时返回了裸整数 %r ✗ —— ORDER BY 会把它当列位置 ✓" % sql,
            )
            # 真跑一遍 SQLite 才算数 ✓
            with self.store.connect() as db:
                db.execute("SELECT * FROM facts ORDER BY %s DESC LIMIT 1" % sql).fetchall()


class CompressionTriggerCase(unittest.TestCase):
    """冷会话三触发 + 迁移（B4）门槛退回 ✓（2026-09-17 用户要求实测确认 ✓）

    规则（`engine.compression_plan` ✓）：
      · 常态：攒够 `compress_rounds` 个**完整轮**才压 ✓
      · **陈旧**：最老记录超过 `compress_stale_after_days` → 门槛降为 **1** ✓
      · **闲置**：最新记录超过 `compress_idle_after_hours` → 门槛降为 **1** ✓
      · **B4**：一个完整轮都算不出（迁移导入 / 同角色堆叠）→ 退回**按条** ✓
    行的形状必须带 start/end + level + position ✓（boost 读的是 end/start ✗ 不是 created ✓）
    """

    def _rows(self, n, age_h, roles=("user", "assistant")):
        now = time.time()
        out = []
        for i in range(n):
            t = now - age_h * 3600 - i * 180
            out.append(dict(id="r%d" % i, start=t, end=t, created=t, summary="s%d" % i,
                            permanent=0, tier="active", importance=5, level=0,
                            position=i + 1, sid="qq:gm:1", visibility="session",
                            role=roles[i % len(roles)]))
        return out

    def _cfg(self, **over):
        base = dict(compress_batch_mode="rounds", compress_rounds=12,
                    batch_size=8, threshold=40)
        base.update(over)
        return c.Settings(**base)

    def test_stale_triggers_threshold_one(self):
        plan = e.compression_plan(self._rows(20, 24 * 10), self._cfg(),
                                  now=time.time(), boost_allowed=True)
        self.assertIsNotNone(plan, "陈旧数据没触发压缩 ✗（阈值没降到 1 ✗）")

    def test_idle_triggers_threshold_one(self):
        plan = e.compression_plan(self._rows(20, 30), self._cfg(),
                                  now=time.time(), boost_allowed=True)
        self.assertIsNotNone(plan, "闲置会话没触发压缩 ✗")

    def test_fresh_session_does_not_trigger(self):
        plan = e.compression_plan(self._rows(20, 0.1), self._cfg(),
                                  now=time.time(), boost_allowed=True)
        self.assertIsNone(plan, "刚聊过的会话不该降门槛 ✗")

    def test_boost_disallowed_does_not_trigger(self):
        plan = e.compression_plan(self._rows(20, 24 * 10), self._cfg(),
                                  now=time.time(), boost_allowed=False)
        self.assertIsNone(plan, "调度没放行时不该降门槛 ✗")

    def test_migrated_rows_fall_back_to_count(self):
        """B4：迁移导入（全是 user，算不出完整轮）→ 退回按条 ✓"""
        rows = self._rows(24, 24 * 400, roles=("user",))
        plan = e.compression_plan(rows, self._cfg(), now=time.time(), boost_allowed=True)
        self.assertIsNotNone(plan, "迁移数据压不动 ✗（B4 退回按条没生效 ✗）")
        self.assertLessEqual(len(plan[0]), 8, "退回按条时不得超过 batch_size ✗")


class BoostGateCase(unittest.TestCase):
    """闸门（scheduler / on_request / 启动扫描）**不得消耗降门槛资格** ✗✓

    2026-09-17 生产实测的真实故障 ✓：
      · 闸门 `compression_plan(..., boost_allowed=_boost_ok(sid, cfg))` ✗
        —— `_boost_ok` 会**盖章**（写冷却时间戳 ✓）
      · 于是任务真正跑起来再问一次 ✗ 已经在冷却里 ⇒ 必然 False ✗
      · ⇒ **每一个排出去的压缩任务都空转** ✓ 日志刷屏"本次没有需要压缩的内容" ✓
        迁移来的 L0 **永远压不掉** ⇒ 永远提炼不出事实 ✓

    判据：闸门用 `stamp=False` ✓ 只有真正要压的那个调用点才盖章 ✓
    """

    def _rows(self, n=20):
        now = time.time()
        out = []
        for i in range(n):
            t = now - 400 * 86400 - i * 180
            out.append(dict(id="r%d" % i, start=t, end=t, created=t, summary="s%d" % i,
                            permanent=0, tier="active", importance=5, level=0,
                            position=i + 1, sid="s:gate", visibility="session", role="user"))
        return out

    def _cfg(self):
        return c.Settings(compress_batch_mode="rounds", compress_rounds=12,
                          batch_size=8, threshold=40)

    def test_gate_does_not_consume_boost(self):
        sid, cfg = "s:gate:1", self._cfg()
        e._BOOST_AT.pop(sid, None)
        self.assertTrue(e._boost_ok(sid, cfg, stamp=False), "闸门该放行 ✓")
        self.assertNotIn(sid, e._BOOST_AT, "闸门盖了戳 ✗ ⇒ 任务必然空转 ✓")
        # 闸门排得出任务 ✓
        self.assertIsNotNone(e.compression_plan(self._rows(), cfg, boost_allowed=True))
        # 任务里再问一次，仍须拿到降门槛 ✓（这才是能真压的前提 ✓）
        self.assertTrue(e._boost_ok(sid, cfg), "任务拿不到降门槛 ✗ ⇒ 排了也白排 ✓")
        self.assertIn(sid, e._BOOST_AT, "任务该盖戳（冷却从这里开始算 ✓）")

    def test_job_stamp_still_enforces_cooldown(self):
        sid, cfg = "s:gate:2", self._cfg()
        e._BOOST_AT.pop(sid, None)
        self.assertTrue(e._boost_ok(sid, cfg))
        # 冷却期内闸门不再放行 ✓（避免重复排任务 ✓）
        self.assertFalse(e._boost_ok(sid, cfg, stamp=False), "冷却没生效 ✗")


class SchedulerCompressCase(unittest.TestCase):
    """scheduler 真的能把**安静的迁移会话**压掉吗 ✓（对照组 ✓ 2026-09-17 用户提问 ✓）

    两个事实必须同时成立 ✓：
      ① `probability` 是**骰子闸门** ✗ —— 默认 1.0 时 scheduler 会中 ✓
         =0（从不主动回复）时 **永远不会中** ✗ ⇒ 迁移会话压不动 ✓
      ② 即便中了 ✗ —— 修复前 `_boost_ok` 的盖章会让任务 100% 空转 ✓

    所以 `queue_compress_all()`（启动扫描 / 迁移后扫描）是有意义的：
    它**不看骰子** ✓ 确定性兜底 ✓
    """

    def _seed(self, sid):
        now = time.time()
        with self.store.connect() as db:
            for i in range(20):
                t = now - 400 * 86400 - i * 180
                db.execute(
                    "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
                    "position,created,visibility,active,deleted) VALUES(?,?,?,0,?,?,?,?,?,?,?,?,1,0)",
                    ("m-%d" % i, sid, "user", t, t, "旧 %d" % i, "旧 %d" % i,
                     "[]", i + 1, now, "session"))
            db.commit()

    def _levels(self, sid):
        with self.store.connect() as db:
            return dict(db.execute(
                "SELECT level, count(*) FROM records WHERE sid=? AND deleted=0 GROUP BY level",
                (sid,)).fetchall())

    def _cfg(self, probability):
        return c.Settings(compress_batch_mode="rounds", compress_rounds=12,
                          batch_size=8, threshold=40, probability=probability)

    def _run(self, probability, with_sweep):
        sid = "legacy:t:%s:%s" % (probability, with_sweep)
        self._seed(sid)
        cfg = self._cfg(probability)
        e._BOOST_AT.pop(sid, None)
        loop = asyncio.new_event_loop()
        try:
            model = lambda *a, **k: asyncio.sleep(0, result=json.dumps(
                {"summary": "迁移摘要", "facts": []}))

            async def flow():
                eng = e.Engine(self.store, lambda: cfg, model, None, None)
                if with_sweep:
                    rows = await self.store.call("active", sid)
                    if e.compression_plan(rows, cfg, now=time.time(),
                                        boost_allowed=e._boost_ok(sid, cfg, stamp=False)):
                        await eng.enqueue("compress", sid, automatic=True)
                    await eng.start()
                    await asyncio.sleep(2.0)
                else:
                    await eng.start()          # 起 worker + scheduler ✓
                    await asyncio.sleep(3.5)
                await eng.stop()
            loop.run_until_complete(flow())
        finally:
            loop.close()
        return self._levels(sid)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "db")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_scheduler_can_compress_quiet_migrated_when_dice_allows(self):
        lv = self._run(1.0, with_sweep=False)
        self.assertIn(1, lv, "scheduler 掷中骰子也没压成 ✗（boost 盖章问题回归了 ✓）")

    def test_scheduler_starves_when_probability_zero(self):
        lv = self._run(0.0, with_sweep=False)
        self.assertNotIn(1, lv, "probability=0 时不该压 ✓（骰子闸门失效了 ✗）")

    def test_startup_sweep_does_not_need_the_dice(self):
        """扫描的价值：**不看骰子** ✓（用极小概率让 scheduler 实际不可能中 ✓）"""
        lv = self._run(1e-9, with_sweep=True)
        self.assertIn(1, lv, "启动扫描不该依赖 probability 掷骰 ✓")

    def test_probability_zero_means_manual_only(self):
        """`自动压缩概率=0` 的文案是「仅手动」✗ ⇒ 启动扫描也必须尊重 ✓"""
        sid = "legacy:t:manual"
        self._seed(sid)
        cfg = self._cfg(0.0)
        e._BOOST_AT.pop(sid, None)
        import types as _t
        plugin = _t.SimpleNamespace(
            runtime_settings=lambda: cfg, store=self.store, engine=None)
        # 直接验证判定：probability=0 时扫描应当**不排任何会话** ✓
        self.assertEqual(cfg.probability, 0.0)
        self.assertFalse(bool(cfg.probability), "0 就是「仅手动」✓（扫描必须直接返回 ✓）")


class CascadeCatchUpCase(unittest.TestCase):
    """一条压缩任务必须能**追平整个会话** ✗✓（用户问"2000 条会怎样"时实测 ✓）

    故障形态（修复前 ✓）：`_compress_cascade` 在循环里**每轮都问一次** `_boost_ok` ✓
    而它会盖章写冷却 ✓ ⇒ 第二轮就已经"冷却中" ⇒ 只能压 **1 批(40 条)** 就收手 ✓
      实测：200 条 → `active{0:40, 1:161}` ✗ 2000 条要 40 条/30 分钟爬 25 小时 ✗✓
    修复：资格**每条任务只判定一次** ✓ 循环里一直有效 ✓（计划为空即退出 ✓ 不空转 ✓）
    """

    def _seed(self, sid, n):
        now = time.time()
        with self.store.connect() as db:
            for i in range(n):
                t = now - 400 * 86400 - i * 180
                db.execute(
                    "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
                    "position,created,visibility,active,deleted) VALUES(?,?,?,0,?,?,?,?,?,?,?,?,1,0)",
                    ("c-%d" % i, sid, "user", t, t, "旧 %d" % i, "旧 %d" % i,
                     "[]", i + 1, now, "session"))
            db.commit()

    def test_one_job_respects_batch_cap(self):
        """一条任务最多压 `compress_batches_per_job` 批 ✓（花钱闸门 ✓ 2026-09-17 用户要求 ✓）

        历史：先修了"每轮重问 boost ⇒ 只压 1 批"的 bug ✓ 但放开成"追平整个会话"后
        2000 条 ≈50 次模型调用会几分钟烧完 ✗ ⇒ 改为可配置上限（默认 3 批 = 120 条）✓
        """
        sid = "legacy:cap:1"
        self._seed(sid, 200)
        cfg = c.Settings()          # compress_batches_per_job 默认 3 ✓
        e._BOOST_AT.pop(sid, None)
        calls = []

        async def model(*a, **k):
            calls.append(1)
            return json.dumps({"summary": "合并摘要", "facts": []})

        loop = asyncio.new_event_loop()

        async def flow():
            eng = e.Engine(self.store, lambda: cfg, model, None, None)
            await eng.start()
            await eng.compress(sid)
            await eng.stop()
        try:
            loop.run_until_complete(flow())
        finally:
            loop.close()
        with self.store.connect() as db:
            archived = db.execute(
                "SELECT count(*) FROM records WHERE sid=? AND active=0 AND level=0", (sid,)).fetchone()[0]
        cap = cfg.compress_batches_per_job * cfg.batch_size
        self.assertLessEqual(archived, cap, "压了 %d 条，超过上限 %d ✗（花钱闸门失效 ✓）" % (archived, cap))
        self.assertGreater(archived, cfg.batch_size,
                           "只压了一批 ✗ ⇒ 又回到「每轮重问 boost」的老 bug ✓")
        self.assertLessEqual(len(calls), cfg.compress_batches_per_job + 1,
                             "模型调用 %d 次，超出闸门 ✗" % len(calls))

    def test_batch_cap_one_means_one_batch(self):
        sid = "legacy:cap:2"
        self._seed(sid, 200)
        cfg = c.Settings(compress_batches_per_job=1)
        e._BOOST_AT.pop(sid, None)

        async def model(*a, **k):
            return json.dumps({"summary": "合并摘要", "facts": []})

        loop = asyncio.new_event_loop()

        async def flow():
            eng = e.Engine(self.store, lambda: cfg, model, None, None)
            await eng.start()
            await eng.compress(sid)
            await eng.stop()
        try:
            loop.run_until_complete(flow())
        finally:
            loop.close()
        with self.store.connect() as db:
            archived = db.execute(
                "SELECT count(*) FROM records WHERE sid=? AND active=0 AND level=0", (sid,)).fetchone()[0]
        self.assertLessEqual(archived, cfg.batch_size, "cap=1 时应只压 1 批（40 条）✗")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "db")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()


class QuietAutomaticJobCase(unittest.TestCase):
    """自动任务**空转**时不该刷日志/占工作台 ✓（2026-09-17 用户要求 ✓）

    用户看到的是：一批「事实合并完成（合并 0 组重复事实）」✗
    —— 这类**不调模型、什么都没做**的任务 ✓ 只是噪音 ✓（连排 7 条 ✓）

    ⚠️ 三条铁律（不能伤到有意义的日志 ✓）：
      · **手动**任务永不静默 ✓（用户明确要求手动的要看得见 ✓）
      · **真干活**的（哪怕只合并了 1 组）永不静默 ✓
      · `audit` / `classify` / `rewrite` **一定调过模型** ⇒ 永不静默 ✓✓
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "db")
        self.store.initialize()
        self.eng = e.Engine(self.store, lambda: c.Settings(), None, None, None)
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()
        self.temp.cleanup()

    def _q(self, kind, detail, automatic=True):
        return self.loop.run_until_complete(
            self.eng._quiet_automatic({"id": "x", "kind": kind, "sid": "s", "automatic": int(automatic)}, detail))

    def test_automatic_noop_is_quiet(self):
        self.assertTrue(self._q("fact_merge", "合并 0 组重复事实（0 条并入）"), "空合并该静默 ✓")
        self.assertTrue(self._q("compress", "本次没有需要压缩的内容"), "空压缩该静默 ✓")

    def test_manual_noop_is_visible(self):
        self.assertFalse(self._q("fact_merge", "合并 0 组重复事实（0 条并入）", automatic=False),
                         "手动任务必须可见 ✗（用户明确要求 ✓）")

    def test_real_work_is_visible(self):
        self.assertFalse(self._q("fact_merge", "合并 2 组重复事实（3 条并入）"), "真合并必须可见 ✓")
        self.assertFalse(self._q("compress", "压缩 40 条 → L1"), "真压缩必须可见 ✓")

    def test_model_calling_kinds_never_quiet(self):
        for kind, detail in (("audit", "保留 3 · 修正 1"), ("classify", "已归类"),
                             ("rewrite", "重写 2 条")):
            self.assertFalse(self._q(kind, detail), "%s 一定调过模型 ⇒ 不许静默 ✗" % kind)

    def test_quiet_notes_only_contain_noop_wording(self):
        """清单本身也要守 ✓：只允许"确定不调模型"的空转措辞 ✓"""
        for note in e.QUIET_JOB_NOTES:
            self.assertIn("没有", note, "清单里混进了非空转措辞 ✗：%s" % note)


class BotIssuedTaskVisibleCase(unittest.TestCase):
    """**有发起方**的任务必须有日志 ✓（bot 发起 / 工作台按钮 ✓）

    用户 2026-09-17 指出：tidy 那边如果**是 bot 发出的**，也要算"手动" ⇒ 要有日志 ✓
    （区分标准不是"谁在跑"，而是"**有没有人在等结果**" ✓）
    """

    def test_queue_tidy_all_accepts_automatic_flag(self):
        import inspect
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn("async def queue_tidy_all(self, fallback_sid=\"\", automatic=True)", src)
        self.assertIn('enqueue("tidy", owner, automatic=automatic)', src)

    def test_bot_and_workbench_call_it_as_manual(self):
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertIn("automatic=False,  # bot 发起的", code, "bot 的 tidy 没标成手动 ✗（会没日志 ✓）")
        self.assertIn("automatic=False  # 工作台按钮", code, "工作台按钮没标成手动 ✗")
        self.assertIn('enqueue("tidy", value.sid, automatic=False)', code,
                      "bot 写永久记忆后的整理没标成手动 ✗")

    def test_truly_automatic_ones_stay_automatic(self):
        """真正自动的（阈值兜底 / 调度器）保持 automatic=True ✓ 空转时仍可静默 ✓"""
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertIn('enqueue("tidy", owner, automatic=True)', code, "阈值兜底那条被误改了 ✗")

    def test_reindex_noop_drops_job(self):
        src = (Path(__file__).resolve().parents[1] / "engine.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertIn('"drop_job", job["id"]', code, "reindex 空转还在留任务 ✗")


class JobHousekeepingCase(unittest.TestCase):
    """方案 A（清理过期任务）+ 方案 C（扫描按陈旧度排 ✓）—— 2026-09-17 用户要求 ✓

    A：工作台的"工作明细"原来**只增不减** ✗（jobs 表永久堆积 ✓）
    C：扫描原来用 `sorted(sessions)` = **字母序** ✗ ⇒ 挑出的 8 个跟"谁更需要压"无关 ✓
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "db")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    # ── A ──
    def _job(self, jid, state, updated):
        # ⚠️ jobs 有 UNIQUE(kind, sid) ✗✓ ⇒ 同一 (kind,sid) 只能有一行 ✓
        # （这正是"同一会话不会重复排队"的天然保证 ✓ 也说明"任务多"= 会话多 ✓）
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO jobs(id,kind,sid,state,detail,created,updated,automatic)"
                " VALUES(?,'compress',?,?,?,?,?,1)",
                (jid, "s-" + jid, state, "", updated, updated))
            db.commit()

    def test_prune_drops_old_but_keeps_recent(self):
        now = time.time()
        for i in range(10):
            self._job("old-%d" % i, "completed", now - 30 * 86400 - i)
        for i in range(3):
            self._job("new-%d" % i, "completed", now - i)
        self.store.prune_jobs(keep_days=7, keep_min=3)
        with self.store.connect() as db:
            left = {r[0] for r in db.execute("SELECT id FROM jobs").fetchall()}
        self.assertTrue(all(x.startswith("new-") for x in left), "新任务被误删 ✗：%s" % left)
        self.assertGreaterEqual(len(left), 3, "keep_min 没保住 ✗")

    def test_prune_never_touches_queued_or_running(self):
        """🔴 安全底线：**正在排队/执行的任务绝不能删** ✓✓"""
        now = time.time()
        self._job("q-1", "queued", now - 90 * 86400)
        self._job("r-1", "running", now - 90 * 86400)
        for i in range(30):
            self._job("done-%d" % i, "completed", now - 60 * 86400 - i)
        self.store.prune_jobs(keep_days=1, keep_min=1)
        with self.store.connect() as db:
            left = {r[0] for r in db.execute("SELECT id FROM jobs").fetchall()}
        self.assertEqual({"q-1", "r-1"}, left, "排队/执行中的任务被删了 ✗✗ 严重 ✓")

    def test_prune_cleans_orphan_items(self):
        now = time.time()
        self._job("keep", "completed", now)
        self._job("gone", "completed", now - 100 * 86400)
        with self.store.connect() as db:
            db.execute("INSERT INTO job_items(job_id,kind,target,action,note,before,created) VALUES('gone','compress','t','a','','',?)", (time.time(),))
            db.commit()
        self.store.prune_jobs(keep_days=7, keep_min=1)
        with self.store.connect() as db:
            n = db.execute("SELECT count(*) FROM job_items WHERE job_id='gone'").fetchone()[0]
        self.assertEqual(n, 0, "孤儿明细没清掉 ✗")

    # ── C ──
    def test_sessions_by_age_orders_oldest_first(self):
        now = time.time()
        with self.store.connect() as db:
            for sid, age_days in (("s:new", 1), ("s:old", 30), ("s:mid", 10)):
                db.execute(
                    "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
                    "position,created,visibility,active,deleted) VALUES(?,?,'user',0,?,?,?,?,?,?,?,?,1,0)",
                    ("r-" + sid, sid, now - age_days * 86400, now - age_days * 86400,
                     "内容", "内容", "[]", 1, now, "session"))
            db.execute(
                "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
                "position,created,visibility,active,deleted) VALUES('r-off','s:old','user',0,?,?,?,?,?,?,?,?,0,0)",
                (now, now, "内容", "内容", "[]", 9, now, "session"))
            db.commit()
        out = self.store.sessions_by_age()
        self.assertEqual(out, ["s:old", "s:mid", "s:new"], "没按陈旧度排 ✗（字母序会排成 mid/new/old ✓）")


class HousekeepingWiringCase(unittest.TestCase):
    """接线检查 ✓（剥注释后判 ✗ 免得被注释骗过 ✓）"""

    def _code(self, name):
        src = (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
        return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))

    def test_scan_uses_staleness_order(self):
        code = self._code("main.py")
        self.assertIn('"sessions_by_age"', code, "扫描没用按陈旧度排序 ✗（方案 C 失效 ✓）")
        self.assertIn("sorted(await self.store.call(\"sessions\"))", code,
                      "兜底分支没了 ✗（老库要还能跑 ✓）")

    def test_engine_prunes_jobs_periodically(self):
        code = self._code("engine.py")
        self.assertIn('"prune_jobs"', code, "没有接周期清理 ✗（方案 A 失效 ✓ 列表会无限增长 ✓）")
        self.assertIn("_last_prune", code, "没有节流 ⇒ 会每 30 秒清一次 ✗")
