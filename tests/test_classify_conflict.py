"""记忆归类的守卫回归：源记录被并发「整理」时不许再判死。

背景（用户实测）：UI 里「记忆归类」几乎总是失败，错误是
`ValueError: classification source changed`。
根因：归类要调模型（几秒），期间「分层压缩 / 提炼归档 / 清理」会 bump 同一条记录的
revision；旧守卫死抠 `revision` 相等 ⇒ 模型调用窗口内几乎必然冲突 ⇒ 任务直接判死 ✗。
"""

import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_classify_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_classify_test", package)
s = importlib.import_module("alife_classify_test.storage")


def run(coro):
    return asyncio.run(coro)


class GuardUnitCase(unittest.TestCase):
    """守卫函数本身：什么样的变化还算数、什么不算。"""

    def test_content_unchanged_is_accepted(self):
        row = {"content": "原正文"}
        current = {"revision": 9, "deleted": 0, "active": 1,
                   "content": "原正文", "summary": ""}
        self.assertTrue(s._classify_source_still_valid(row, current))

    def test_archived_source_is_accepted(self):
        row = {"content": "原正文"}
        current = {"revision": 9, "deleted": 0, "active": 0,
                   "content": "摘要：……", "summary": "摘要：……"}
        self.assertTrue(s._classify_source_still_valid(row, current))

    def test_rewritten_content_is_rejected(self):
        row = {"content": "原正文"}
        current = {"revision": 9, "deleted": 0, "active": 1,
                   "content": "完全不同的另一段内容", "summary": ""}
        self.assertFalse(s._classify_source_still_valid(row, current))


class ClassifyConflictCase(unittest.TestCase):
    """走真库：并发「整理」后归类仍然要成功；正文被换掉才失败。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "m.db")
        self.store.initialize()
        self.rid = run(self.store.call(
            "memorize", "s1", "用户在杭州工作，喜欢喝龙井茶。", ["u1"],
            1.0, 1.0, 6, "general"))

    def _row(self):
        return run(self.store.call("get", self.rid))

    def _bump_revision_like_compression(self):
        """模拟压缩/归档那条链路对同一条记录的写法（只 bump revision）。"""
        with self.store.connect() as db:
            db.execute("UPDATE records SET revision=revision+1 WHERE id=?", (self.rid,))

    def test_classify_survives_concurrent_tidy(self):
        row = self._row()                      # 模型看到的那一份（旧 revision）
        self._bump_revision_like_compression()  # 判定期间被别的任务整理
        out = {"facts": [{"content": "用户在杭州工作", "source_ids": [self.rid],
                          "category": "general", "importance": 6, "subject": "用户",
                          "reason": "用户自述", "scenario": "", "relations": [], "tags": []}]}
        run(self.store.call("classify", row, out))   # ★ 不许再抛 Conflict
        facts = run(self.store.call("facts", "s1")) or []
        self.assertTrue(facts, "归类应当真的写入了事实")

    def test_classify_still_rejects_rewritten_source(self):
        row = self._row()
        with self.store.connect() as db:
            # 明确模拟"正文被换成别的内容且未被归档、无摘要" ⇒ 守卫必须仍然拒绝
            db.execute("UPDATE records SET content=?, summary='', active=1,"
                       " revision=revision+1 WHERE id=?",
                       ("这是一条完全不同的记录", self.rid))
        out = {"facts": [{"content": "x", "source_ids": [self.rid],
                          "category": "general", "importance": 6, "subject": "用户",
                          "reason": "用户自述", "scenario": "", "relations": [], "tags": []}]}
        with self.assertRaises(s.Conflict):
            run(self.store.call("classify", row, out))


if __name__ == "__main__":
    unittest.main()
