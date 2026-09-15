"""回声防线（v2.18.9）的行为与契约测试。

背景：bot 自己的发言会被压缩成事实、注入回去、再被审计当成"证据" ✗
→ 自我强化闭环（我说 X → 记忆里有 X → 我更坚信 X ✓）。
这里逐条钉住防线：**每条都要能"改坏就红"** ✓
"""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_echo_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_echo_test", package)

contracts = importlib.import_module("alife_echo_test.contracts")
retrieval = importlib.import_module("alife_echo_test.retrieval")
storage = importlib.import_module("alife_echo_test.storage")
migration = importlib.import_module("alife_echo_test.migration")


class OnlySelfCase(unittest.TestCase):
    """① 证据只有助手自己 → 审计不许提升 importance ✗"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = storage.Store(Path(self.tmp.name) / "m.db")
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def _fact(self, importance=3):
        """用**真实快照**落一条事实（手搓条目形状容易漏字段 ✗ 实测会 KeyError）"""
        root = Path(self.tmp.name) / "data"
        (root / "entities/user_qq%3A1/facts").mkdir(parents=True)
        (root / "entities/user_qq%3A1/facts/a.toml").write_text(
            'id = "a"\ntype = "fact"\ntext = "用户住在杭州"\nimportance = %d\n'
            '[source]\nsession = "qq:dm:1"\ntime = 2026-09-01T10:00:00+08:00\n' % importance,
            encoding="utf-8",
        )
        snap = migration.snapshot(root, migration.HIPPOCAMPUS, 500, decay_days=0)
        self.store.import_legacy(snap)
        # 身份解析会把老实体映射成 legacy id ✗ 所以按库里真实的 sid 取 ✓
        with self.store.connect() as db:
            sid = db.execute("SELECT sid FROM facts LIMIT 1").fetchone()[0]
        return self.store.facts(sid)[0]

    def test_only_self_cannot_raise_importance(self):
        fact = self._fact(importance=3)
        out = {
            "actions": [
                {
                    "action": "correct",
                    "target_id": fact["id"],
                    "source_ids": [fact["id"]],
                    "content": fact["content"],
                    "reason": "自证",
                    "importance": 9,
                    "only_self": True,
                }
            ]
        }
        self.store.audit([fact], out)
        with self.store.connect() as db:
            sid = db.execute("SELECT sid FROM facts LIMIT 1").fetchone()[0]
        after = self.store.facts(sid)[0]
        self.assertEqual(after["importance"], 3, "只有助手自己的证据，importance 不许被提升 ✗")

    def test_user_backed_can_raise_importance(self):
        """对照组：有用户佐证（未标 only_self）→ 照旧可以提 ✓"""
        fact = self._fact(importance=3)
        out = {
            "actions": [
                {
                    "action": "correct",
                    "target_id": fact["id"],
                    "source_ids": [fact["id"]],
                    "content": fact["content"],
                    "reason": "有用户证言",
                    "importance": 9,
                }
            ]
        }
        self.store.audit([fact], out)
        with self.store.connect() as db:
            sid = db.execute("SELECT sid FROM facts LIMIT 1").fetchone()[0]
        self.assertEqual(self.store.facts(sid)[0]["importance"], 9)


class ContractCase(unittest.TestCase):
    """② only_self 必须可选（老输出不带它也要能过 ✓ 向后兼容）"""

    def test_defaults_false_and_backward_compatible(self):
        old = contracts.AuditAction(
            action="keep", target_id="f1", source_ids=["f1"], content="x", reason="y"
        )
        self.assertFalse(old.only_self, "缺省必须是 False，否则老输出全被拒 ✗")
        new = contracts.AuditAction(
            action="correct", target_id="f1", source_ids=["f1"], content="x",
            reason="y", only_self=True,
        )
        self.assertTrue(new.only_self)


class SelfFlagCase(unittest.TestCase):
    """③ 注入的事实必须标出"来源是助手自己" ✓"""

    def _facts(self):
        return [
            {"category": "fact", "subject": "n1", "content": "我答应过要提醒他",
             "importance": 6, "src_user": "qq:bot", "relations": []},
            {"category": "fact", "subject": "n1", "content": "他在杭州住",
             "importance": 6, "src_user": "qq:1", "relations": []},
        ]

    def test_flat_view_flags_self_only(self):
        items = retrieval.bot_facts(self._facts(), "qq:dm:1", self_id="qq:bot")
        self.assertEqual(items[0].get("self"), 1, "助手的自己的来源必须打 self ✗")
        self.assertNotIn("self", items[1], "用户的来源不许被打 self ✗")

    def test_grouped_view_flags_self_only(self):
        packed = retrieval.bot_facts_grouped(
            self._facts(), "qq:dm:1", codes={"n1": "n1"}, self_id="qq:bot"
        )
        rows = packed["n1"]
        flags = [r[-1] for r in rows]
        self.assertIn("self", flags, "分组视图也要能看出哪条来自助手 ✗")
        self.assertEqual(flags.count("self"), 1, "只许标助手自己那条 ✗")

    def test_no_self_id_means_no_flags(self):
        """拿不到自身 id（例如别的调用方）→ 不猜，一律不标 ✓"""
        items = retrieval.bot_facts(self._facts(), "qq:dm:1")
        self.assertTrue(all("self" not in i for i in items))


class MediaCase(unittest.TestCase):
    """④ 只有表情/图片的消息：数据留着 ✓ 默认不进召回 ✗"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = storage.Store(Path(self.tmp.name) / "m.db")
        self.store.initialize()
        self.store.capture(
            "qq:dm:1",
            "k",
            [
                {"role": "user", "content": "今天去哪吃饭", "users": ["qq:1"], "time": 1.0},
                {"role": "user", "content": "[图片 一只橘猫躺在键盘上]",
                 "users": ["qq:1"], "time": 2.0, "category": "media"},
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_media_kept_but_skipped_by_default(self):
        got = self.store.search("qq:dm:1", scope="session", limit=20)
        texts = [r["content"] for r in got["items"]]
        self.assertIn("今天去哪吃饭", texts)
        self.assertNotIn("[图片 一只橘猫躺在键盘上]", texts, "media 默认不该进召回 ✗")
        with self.store.connect() as db:
            kept = db.execute(
                "SELECT count(*) FROM records WHERE category='media'"
            ).fetchone()[0]
        self.assertEqual(kept, 1, "media 记录必须**保留在库里** ✗（只是不召回）")

    def test_switch_off_returns_media(self):
        got = self.store.search("qq:dm:1", scope="session", limit=20, skip_media=False)
        texts = [r["content"] for r in got["items"]]
        self.assertIn("[图片 一只橘猫躺在键盘上]", texts, "关掉开关就该能召回 ✓")


class PromptCase(unittest.TestCase):
    """⑤ 提示词与代码必须一致（提示词说了、载荷/代码里就得有 ✓）"""

    def test_prompts_and_payloads_agree(self):
        eng = (ROOT / "engine.py").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        ret = (ROOT / "retrieval.py").read_text(encoding="utf-8")
        self.assertTrue("records[].bot=1" in eng, "压缩/审计提示词必须解释 bot ✗")
        self.assertTrue('additions[source]["bot"] = 1' in eng, "审计证据必须真的带 bot ✗")
        self.assertTrue("evidence[].bot=1" in eng, "审计提示词必须解释 evidence[].bot ✗")
        self.assertTrue('item["bot"] = 1' in main, "相关记录也必须打 bot（三处一致 ✓）")
        self.assertTrue('["self"] = 1' in ret, "事实行必须能标 self ✗")
        self.assertTrue("self 表示这条事实的最新来源是助手自己" in ret, "图例必须解释 self ✗")


if __name__ == "__main__":
    unittest.main()

class ModelMisbehavesCase(unittest.TestCase):
    """**模型不配合**也必须拦住 ✓（端到端实验发现的真问题 ✓）

    实验：一条事实的来源**只有助手那句话** ✓ 模型按旧习惯提权到 9 ✗
    而且**没有**填 only_self ✗ —— 如果只在"模型自觉"时才钳制，这条就会漏 ✓
    → 所以应用侧必须**自己查来源角色** ✓ 不能信模型 ✓
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = storage.Store(Path(self.tmp.name) / "m.db")
        self.store.initialize()
        self.sid = "qq:dm:7"
        self.store.capture(self.sid, "k1", [
            {"role": "user", "content": "我今天搬完家了", "users": ["qq:7"],
             "speaker": "周武", "time": 1789459000.0},
            {"role": "assistant", "content": "记住了，主人住在上海", "users": ["qq:7"],
             "time": 1789459010.0},
        ])
        with self.store.connect() as db:
            self.bot_record = db.execute(
                "SELECT id FROM records WHERE role='assistant'"
            ).fetchone()[0]
        self.store.add_facts(self.sid, [{
            "category": "fact", "subject": "qq:7", "content": "用户住在上海",
            "reason": "助手提到", "scenario": "", "tags": [], "relations": [],
            "source_ids": [self.bot_record], "importance": 5,
        }])

    def tearDown(self):
        self.tmp.cleanup()

    def _candidates(self):
        return self.store.audit_candidates(self.sid, limit=10, recheck_seconds=0)

    def _importance(self):
        with self.store.connect() as db:
            return db.execute("SELECT importance FROM facts").fetchone()[0]

    def test_model_raising_without_flag_is_still_blocked(self):
        cands = self._candidates()
        self.assertTrue(cands, "夹具没造出候选")
        cands[0]["sources"] = [self.bot_record]      # 唯一来源＝助手自己 ✗
        self.store.audit(cands, {"actions": [{
            "action": "correct", "target_id": cands[0]["id"],
            "source_ids": [cands[0]["id"]],
            "content": "用户住在上海，已确认", "reason": "证据显示",
            "importance": 9,                          # ← 模型乱提 ✗ 且没填 only_self ✗
        }]})
        self.assertEqual(self._importance(), 5, "模型不配合时被提权了 ✗")

    def test_user_backed_still_can_raise(self):
        """有**用户**佐证时照旧可提 ✓（别把功能也拦掉了 ✓）"""
        with self.store.connect() as db:
            user_rec = db.execute(
                "SELECT id FROM records WHERE role='user' LIMIT 1"
            ).fetchone()[0]
        cands = self._candidates()
        cands[0]["sources"] = [user_rec]              # 来源是用户 ✓
        self.store.audit(cands, {"actions": [{
            "action": "correct", "target_id": cands[0]["id"],
            "source_ids": [cands[0]["id"]],
            "content": "用户住在上海", "reason": "用户说过", "importance": 9,
        }]})
        self.assertEqual(self._importance(), 9, "用户佐证时应该允许提权 ✓")


class MediaScopeCase(unittest.TestCase):
    """媒体跳过只该影响"喂给模型的召回" ✓ 不该挡人在网页上浏览 ✓"""

    def test_web_api_still_shows_media(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        i = src.index('path="/search"')
        block = src[i : i + 1400]
        self.assertTrue(
            "skip_media=False" in block,
            "网页端搜索必须显式 skip_media=False ✗ 否则表情/图片记录在界面上消失 ✓",
        )
        self.assertTrue("include_cold=True" in block)

    def test_recall_paths_use_the_switch(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            src.count("skip_media="), 3, "被动召回 / 召回工具 / 网页端都应显式传参 ✓"
        )
        self.assertTrue("recall_skip_media" in src)


class ContentRewriteBlockedCase(unittest.TestCase):
    """来源**全是助手自己**时：不许改写正文/关系/主体，也不许软删 ✗ 只许 keep 与降权 ✓"""

    def _make(self, with_user=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = storage.Store(Path(tmp.name) / "m.db")
        store.initialize()
        sid = "qq:dm:9"
        store.capture(sid, "k", [
            {"role": "assistant", "content": "主人住在上海", "speaker": "qq:9",
             "users": ["qq:9"], "time": 1.0},
        ])
        with store.connect() as db:
            bot = db.execute("SELECT id FROM records WHERE role='assistant'").fetchone()[0]
        srcs = [bot]
        if with_user:
            store.capture(sid, "k2", [
                {"role": "user", "content": "我住在杭州", "speaker": "qq:9",
                 "users": ["qq:9"], "time": 2.0},
            ])
            with store.connect() as db:
                user = db.execute(
                    "SELECT id FROM records WHERE role='user'"
                ).fetchone()[0]
            srcs.append(user)
        store.add_facts(sid, [
            {"category": "fact", "subject": "qq:9", "content": "用户住在上海",
             "reason": "", "scenario": "", "tags": [], "relations": [],
             "source_ids": srcs, "importance": 5},
        ])
        return store, sid

    def _candidates(self, store, sid):
        with store.connect() as db:
            fid = db.execute("SELECT id FROM facts").fetchone()[0]
        return fid, store.audit_candidates(sid, limit=10, recheck_seconds=0)

    def _rewrite(self, store, cands, fid, **kw):
        out = {"actions": [dict({"action": "correct", "target_id": fid,
                                 "source_ids": [fid], "content": "用户住在上海，已确认",
                                 "reason": "证据"}, **kw)]}
        store.audit(cands, out)
    def test_content_rewrite_is_blocked_when_only_bot_spoke(self):
        store, sid = self._make()
        fid, cands = self._candidates(store, sid)
        self._rewrite(store, cands, fid)
        with store.connect() as db:
            self.assertEqual(
                db.execute("SELECT content FROM facts").fetchone()[0], "用户住在上海",
                "来源只有助手自己 ✗ 正文不许被改写 ✓",
            )

    def test_user_backed_rewrite_still_works(self):
        store, sid = self._make(with_user=True)
        fid, cands = self._candidates(store, sid)
        self._rewrite(store, cands, fid)
        with store.connect() as db:
            self.assertEqual(
                db.execute("SELECT content FROM facts").fetchone()[0], "用户住在上海，已确认",
                "有用户佐证时应照常可改 ✓（别把功能一起拦掉 ✗）",
            )


class ProvenanceCase(unittest.TestCase):
    """按**来源**决定审计能做什么（v2.18.9 修正后）✓"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = storage.Store(Path(tmp.name) / "m.db")
        store.initialize()
        sid = "qq:dm:8"

        def cap(role, text, t):
            store.capture(sid, "k%d" % int(t * 10), [{
                "role": role, "content": text, "users": ["qq:8"],
                "speaker": "qq:8", "time": float(t)}])
            with store.connect() as db:
                return db.execute(
                    "SELECT id FROM records WHERE role=? ORDER BY created DESC LIMIT 1",
                    (role,)).fetchone()[0]

        self.u = cap("user", "我住在杭州", 1.0)
        self.b = cap("assistant", "主人住在上海", 2.0)
        self.sid = sid
        self.store = store
        return store, sid

    def _fact(self, srcs, content="用户住在杭州"):
        self.store.add_facts(self.sid, [{
            "category": "fact", "subject": "qq:8", "content": content,
            "reason": "", "scenario": "", "tags": [], "relations": [],
            "source_ids": list(srcs), "importance": 5}])
        with self.store.connect() as db:
            return db.execute("SELECT id FROM facts ORDER BY rowid DESC LIMIT 1").fetchone()[0]

    def _run(self, actions):
        cands = self.store.audit_candidates(self.sid, limit=20, recheck_seconds=0)
        self.store.audit(cands, {"actions": actions})
        with self.store.connect() as db:
            return db.execute(
                "SELECT content,importance,deleted FROM facts ORDER BY rowid"
            ).fetchall()

    def test_retract_allowed_for_self_only(self):
        """自我来源的事实**允许软删** ✓（清理她自己的垃圾 ✗ 软删可恢复 ✓）"""
        fid = self._fact([self.b], "主人住在上海")
        rows = self._run([{"action": "retract", "target_id": fid,
                           "source_ids": [fid], "reason": "助手自己的说法"}])
        self.assertEqual(rows[0][2], 1, "自我来源的事实应当能被清理 ✗")

    def test_no_source_fact_is_protected_too(self):
        """**没有来源**的事实也要受保护 ✓（曾经漏掉它 ✗ 助手一句话就能改写 ✓）"""
        fid = self._fact([], "用户养了狗")
        rows = self._run([{"action": "correct", "target_id": fid, "source_ids": [fid],
                           "content": "用户养了猫", "importance": 9, "reason": "助手乱改"}])
        self.assertEqual(rows[0][0], "用户养了狗", "没来源的事实正文不许被改 ✗")
        self.assertEqual(rows[0][1], 5, "没来源的事实不许被提权 ✗")


class MatrixCase(unittest.TestCase):
    """v2.18.9 矩阵实测固化：自我来源能降权 ✓；混合合并必须被拦 ✗"""

    def _mk(self, kind_a, kind_b):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        st = storage.Store(Path(tmp.name) / "m.db"); st.initialize()
        sid = "qq:dm:2"
        base = {"users": ["qq:2"], "speaker": "qq:2", "time": 1.0}
        st.capture(sid, "ku", [dict(base, role="user", content="我住在杭州")])
        st.capture(sid, "kb", [dict(base, role="assistant", content="主人住在上海")])
        with st.connect() as db:
            u = db.execute("SELECT id FROM records WHERE role='user'").fetchone()[0]
            b = db.execute("SELECT id FROM records WHERE role='assistant'").fetchone()[0]
        pick = {"user": [u], "bot": [b]}
        for text, kind in (("用户住在杭州", kind_a), ("主人住在上海", kind_b)):
            st.add_facts(sid, [{"category": "fact", "subject": "qq:2", "content": text,
                                "reason": "", "scenario": "", "tags": [], "relations": [],
                                "source_ids": pick[kind], "importance": 5}])
        return st, sid

    def test_self_only_fact_can_be_downgraded(self):
        """自我来源的事实**降权必须生效** ✗（曾经因为按字段判断改写 → 被误杀 ✓）"""
        st, sid = self._mk("bot", "bot")
        with st.connect() as db:
            fid = db.execute("SELECT id FROM facts ORDER BY rowid").fetchone()[0]
        cands = st.audit_candidates(sid, limit=10, recheck_seconds=0)
        st.audit(cands, {"actions": [{
            "action": "correct", "target_id": fid, "source_ids": [fid],
            "content": "用户住在杭州", "importance": 2, "reason": "过时了"}]})
        with st.connect() as db:
            r = db.execute("SELECT importance FROM facts WHERE id=?", (fid,)).fetchone()
        self.assertEqual(r[0], 2, "降权应该生效 ✗")

    def test_mixed_merge_is_blocked(self):
        """混合组合并必须被拦 ✗（否则助手的说法会被"洗白"成用户背书 ✓）"""
        st, sid = self._mk("user", "bot")
        with st.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM facts ORDER BY rowid")]
        cands = st.audit_candidates(sid, limit=10, recheck_seconds=0)
        st.audit(cands, {"actions": [{
            "action": "merge", "target_id": ids[0], "source_ids": ids,
            "content": "用户住在杭州（合并后）", "reason": "同一条"}]})
        with st.connect() as db:
            row = db.execute("SELECT content FROM facts WHERE id=?", (ids[0],)).fetchone()
        self.assertEqual(row[0], "用户住在杭州", "混合合并不该落地 ✗")

class ReportCountsCase(unittest.TestCase):
    """审计报告必须**如实记账** ✗（否则会误以为"模型很乖" ✓）"""

    def test_blocked_and_clamped_are_counted(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = storage.Store(Path(tmp.name) / "m.db"); store.initialize()
        sid = "qq:dm:3"
        store.capture(sid, "k", [{"role": "assistant", "content": "主人住在上海",
                                  "users": ["qq:3"], "speaker": "qq:3", "time": 1.0}])
        with store.connect() as db:
            bot = db.execute("SELECT id FROM records WHERE role='assistant'").fetchone()[0]
        store.add_facts(sid, [{"category": "fact", "subject": "qq:3",
                               "content": "用户住在上海", "reason": "", "scenario": "",
                               "tags": [], "relations": [], "source_ids": [bot]}])
        cands = store.audit_candidates(sid, limit=5, recheck_seconds=0)
        fid = cands[0]["id"]
        # ① 改写正文 → 拦下 ✓（两个动作不能同时给同一条 ✗ 校验会拒 ✓ 所以分两轮）
        c1 = store.audit(cands, {"actions": [
            {"action": "correct", "target_id": fid, "source_ids": [fid],
             "content": "用户住在北京", "reason": "助手认为"}]})
        self.assertEqual(c1.get("only_self_blocked"), 1, "改写被拦要记账 ✗")
        # ② 纯提权 → 压回原值 ✓ 也要记账 ✓
        c2 = store.audit(store.audit_candidates(sid, limit=5, recheck_seconds=0),
                         {"actions": [
            {"action": "correct", "target_id": fid, "source_ids": [fid],
             "content": "用户住在上海", "importance": 9, "reason": "助手认为"}]})
        self.assertEqual(c2.get("only_self_clamped"), 1, "提权被压回要记账 ✗")
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT importance FROM facts").fetchone()[0], 5)
