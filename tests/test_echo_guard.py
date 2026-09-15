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
