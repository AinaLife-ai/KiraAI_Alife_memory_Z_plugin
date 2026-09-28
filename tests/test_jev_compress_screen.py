"""JEV 压缩前置筛选（jev_compress_screen）的引擎侧行为守卫。

背景：用户问"开启 JEV 对压缩模型的预筛，确实生效了吗？有无 bug"。
决策层（mdecide）已有 50+ 条测试，但**引擎侧的调用与轮次语义此前没有测试** ✗ ⇒ 这里补上。

被钉死的语义（全部来自 engine.py:1030-1117 的文档与实现）：
· JEV 未启用 / 未就绪 / 未开 jev_compress ⇒ **原样放行**（= 关闭 JEV 的行为 ✓）
· 按**轮次**判：轮内任意一条 ≥ COMPRESS_KEEP_MIN ⇒ 整轮保留 ✓；否则整轮归档 ✓
· **一条都没达标** ⇒ 全部归档 + skip_all=True ⇒ **跳过这次压缩模型调用** ✓
· 全是工具步（判不了）的轮 ⇒ **不动**（保留 ✓）
· 打分不全 / 抛异常 ⇒ 原样放行（绝不误归档 ✓）
· 归档 = active=0（**不是删除**）⇒ 原文仍可按 ID 检索 ✓，且不会被再次挑中 ✓
"""

import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_pkg = types.ModuleType("alife_jev_cs_test")
_pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_jev_cs_test", _pkg)

c = importlib.import_module("alife_jev_cs_test.contracts")
s = importlib.import_module("alife_jev_cs_test.storage")
e = importlib.import_module("alife_jev_cs_test.engine")
mdecide = importlib.import_module("alife_jev_cs_test.mdecide")


def run(coro):
    return asyncio.run(coro)


class FakeDecisions:
    """假 JEV 决策层：只实现 compress_screen ✓（其余方法本用例不需要）。"""

    def __init__(self, fn=None, ready=True, boom=False):
        self.ready = ready
        self.boom = boom
        self.fn = fn
        self.calls = 0

    async def compress_screen(self, items, timeout=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("jev boom")
        return None if self.fn is None else self.fn(items)


class JevCompressScreenCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.tmp.name) / "m.db")
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, sid="qq:gm:1", messages=None):
        self.store.capture(sid, "turn", messages or [
            {"role": "user", "content": "早", "users": ["qq:9"], "time": 0.0},
            {"role": "assistant", "content": "我记下了：花生过敏 / 每周日提醒",
             "users": ["qq:9"], "time": 1.0},
            {"role": "user", "content": "好", "users": ["qq:9"], "time": 2.0},
            {"role": "assistant", "content": "嗯", "users": ["qq:9"], "time": 3.0},
        ])

    def engine(self, cfg, decisions, model=None):
        async def _model(*args, **kw):
            return "{}"
        eng = e.Engine(self.store, lambda: cfg, model or _model, None, None)
        eng.decisions = decisions
        return eng

    def active_ids(self, sid="qq:gm:1"):
        return sorted(r["id"] for r in run(self.store.call("active", sid)))

    def archived_ids(self, sid="qq:gm:1"):
        with self.store.connect() as db:
            return sorted(r[0] for r in db.execute(
                "SELECT id FROM records WHERE sid=? AND active=0", (sid,)).fetchall())

    # ── 生效性 ────────────────────────────────────────────────
    def test_off_is_pass_through(self):
        """JEV 关闭 ⇒ 原样放行、零归档 ✓"""
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings()                       # 默认 jev_enabled=False ✓
        fake = FakeDecisions(fn=lambda items: {k: 0.0 for k, _r, _t in items})
        eng = self.engine(cfg, fake)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        self.assertEqual([r["id"] for r in kept], [r["id"] for r in cands])
        self.assertFalse(skip)
        self.assertEqual(fake.calls, 0, "关闭时不该调用 JEV ✓")
        self.assertEqual(self.archived_ids(), [])

    def test_not_ready_is_pass_through(self):
        """开了但决策层未就绪 ⇒ 原样放行 ✓（不许误归档 ✗）"""
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        fake = FakeDecisions(fn=lambda items: {k: 0.0 for k, _r, _t in items}, ready=False)
        eng = self.engine(cfg, fake)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        self.assertEqual(len(kept), len(cands))
        self.assertFalse(skip)
        self.assertEqual(fake.calls, 0)
        self.assertEqual(self.archived_ids(), [])

    def test_valuable_round_kept_and_valueless_round_archived(self):
        """轮内任一条达标 ⇒ 整轮保留；否则整轮归档 ✓

        轮的定义（engine.py:1071-1078）：**遇到用户消息即开新轮**，其后的助手/工具步
        归属该轮 ✓ —— 所以这里的"早 + 花生回复"是**同一轮**、整轮保留 ✓；
        "好 + 嗯"是另一轮、整轮归档 ✓（实测探针确认 ✓）。
        """
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        fake = FakeDecisions(fn=lambda items: {
            k: (0.90 if "花生" in t else 0.05) for k, _r, t in items})
        eng = self.engine(cfg, fake)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        self.assertGreaterEqual(fake.calls, 1, "开启后必须真的调用 JEV ✓")
        self.assertFalse(skip)
        kept_text = [str(r.get("content") or "") for r in kept]
        self.assertIn("我记下了：花生过敏 / 每周日提醒", kept_text,
                      "达标的整轮必须整轮保留 ✓")
        self.assertIn("早", kept_text,
                      "与达标回复同轮的用户消息也要保留（轮内任一条达标 ⇒ 整轮 ✓）")
        archived = self.archived_ids()
        self.assertEqual(len(archived), 2, "低价值轮的两条都要归档 ✓")
        # 归档 ≠ 删除：原文仍在库里、可按 id 取回 ✓
        with self.store.connect() as db:
            n = db.execute("SELECT COUNT(*) FROM records WHERE id IN (%s)"
                           % ",".join("?" * len(archived)), archived).fetchone()[0]
            rows = db.execute("SELECT content FROM records WHERE id=?",
                              (archived[0],)).fetchone()
        self.assertEqual(n, len(archived), "归档后原文必须还在（不丢内容 ✓）")
        self.assertTrue(str(rows[0]).strip(), "归档行的内容不许被清空 ✓")

    def test_all_valueless_skips_the_compress_call(self):
        """整批都不值得记 ⇒ 全部归档 + skip_all=True（省掉那次压缩调用 ✓）"""
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        fake = FakeDecisions(fn=lambda items: {k: 0.01 for k, _r, _t in items})
        eng = self.engine(cfg, fake)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        # 语义（engine.py:1103-1108）：全批不够格 ⇒ 返回**原候选** + skip=True，
        # 调用方只看 skip（跳过那次压缩模型调用 ✓），返回值本身不参与压缩 ✓
        self.assertTrue(skip, "整批无价值 ⇒ 必须告诉调用方跳过压缩模型 ✓")
        self.assertEqual(len(self.archived_ids()), len(cands),
                         "整批都要归档（active=0，不丢内容 ✓）")
        self.assertEqual(len(kept), len(cands),
                         "该分支返回的是原候选（调用方据 skip 直接 continue ✓）")

    def test_threshold_boundary_uses_keep_min(self):
        """阈值就是 mdecide 的 COMPRESS_KEEP_MIN ✓（低一点就留、低一截就走 ✓）"""
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        hi = mdecide.COMPRESS_KEEP_MIN
        fake = FakeDecisions(fn=lambda items: {k: hi for k, _r, _t in items})
        eng = self.engine(cfg, fake)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        self.assertEqual([r["id"] for r in kept], [r["id"] for r in cands],
                         "正好等于阈值时必须保留（>= ✓）")
        self.assertFalse(skip)
        self.assertEqual(self.archived_ids(), [])

    # ── 安全性（判不了就不许动 ✗）────────────────────────────
    def test_partial_scores_pass_through(self):
        """打分不全 ⇒ 整批原样放行 ✓（绝不按"缺分=0"误归档 ✗）"""
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        fake = FakeDecisions(fn=lambda items: {items[0][0]: 1.0})   # 只给第一条
        eng = self.engine(cfg, fake)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        self.assertEqual(len(kept), len(cands))
        self.assertFalse(skip)
        self.assertEqual(self.archived_ids(), [], "解析不全时一条都不许归档 ✗")

    def test_failure_passes_through(self):
        """JEV 抛异常 ⇒ 原样放行 ✓（照常压缩）"""
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        eng = self.engine(cfg, FakeDecisions(boom=True))
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        self.assertEqual(len(kept), len(cands))
        self.assertFalse(skip)
        self.assertEqual(self.archived_ids(), [])

    def test_tool_only_round_is_never_archived(self):
        """全是工具步的轮（判不了）⇒ 一律保留 ✓（不许连坐归档 ✗）"""
        self.seed(sid="qq:gm:2", messages=[
            {"role": "assistant", "content": "[工具] 查询结果",
             "users": ["qq:9"], "time": 0.0, "category": "tool"},
            {"role": "user", "content": "看看", "users": ["qq:9"], "time": 1.0},
        ])
        cands = run(self.store.call("active", "qq:gm:2"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        # 只给"看看"打分（工具步不参与打分 ✓）；低分 ⇒ 该轮走归档
        fake = FakeDecisions(fn=lambda items: {k: 0.01 for k, _r, _t in items})
        eng = self.engine(cfg, fake)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        kept_text = [str(r.get("content") or "") for r in kept]
        self.assertIn("查询结果", " ".join(kept_text),
                      "工具步必须随轮/保留，不许被单独判分归档 ✗")
        self.assertEqual(skip, False)

    def test_engine_side_helper_never_raises_without_decisions(self):
        """没有决策层（未初始化）⇒ 原样放行 ✓（绝不影响主流程 ✓）"""
        self.seed()
        cands = run(self.store.call("active", "qq:gm:1"))
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        eng = e.Engine(self.store, lambda: cfg, None, None, None)
        kept, skip = run(eng.jev_compress_screen(cands, cfg))
        self.assertEqual(len(kept), len(cands))
        self.assertFalse(skip)


    def test_tool_step_never_scored_but_rides_with_its_round(self):
        """工具步：不单独判分 ✓ 但**随其所在轮**一起保留/归档 ✓（用户追问的点 ✓）。

        依据：常规捕获时工具步落的是 role="assistant" + category="tool"（main.py:3046/3053）⇒
              预筛里 `is_user = role != "assistant"` ⇒ 它**不开新轮** ✓；
              同时 `_tool(row)` 把它排除在打分 items 之外 ✓（原始 tool_calls JSON 不送判定层 ✓）。
        """
        seen = {}

        class _Fake:
            ready = True

            async def compress_screen(self, items, timeout=None):
                seen["items"] = [str(t)[:16] for _k, _r, t in items]
                return {k: (0.90 if "花生" in t else 0.05) for k, _r, t in items}

        self.store.capture("qq:gm:9", "turn", [
            {"role": "user", "content": "帮我记：花生过敏", "users": ["qq:9"], "time": 0.0},
            {"role": "assistant", "content": '{"tool_calls": [{"id": "1"}]}',
             "users": ["qq:9"], "time": 1.0, "category": "tool"},
            {"role": "assistant", "content": "已记住：花生过敏 / 每周日提醒",
             "users": ["qq:9"], "time": 2.0},
            {"role": "user", "content": "好", "users": ["qq:9"], "time": 3.0},
            {"role": "assistant", "content": "嗯", "users": ["qq:9"], "time": 4.0},
        ])
        cfg = c.Settings(jev_enabled=True, jev_compress=True)
        engine = self.engine(cfg, None)
        engine.decisions = _Fake()
        cands = run(self.store.call("active", "qq:gm:9"))
        kept, skip = run(engine.jev_compress_screen(cands, cfg))
        # ① 工具步不送去打分 ✓
        self.assertEqual(len(seen["items"]), 4, "5 条里工具步应被排除 ⇒ 只送 4 条 ✓")
        self.assertFalse(any("tool_calls" in s for s in seen["items"]),
                         "原始工具 JSON 不许送判定层 ✓")
        # ② 工具步随它所在的轮一起保留 ✓（有 0.90 那条在同一轮）
        kept_ids = {r["id"] for r in kept}
        tool_row = next(r for r in cands if r.get("category") == "tool")
        self.assertIn(tool_row["id"], kept_ids, "工具步必须跟着它那一轮保留 ✓")
        # ③ 另一轮（0.05）整轮归档 ✓
        archived = self.archived_ids("qq:gm:9")
        self.assertEqual(sorted(archived), sorted(r["id"] for r in cands
                                                  if r["id"] not in kept_ids))
        self.assertFalse(skip)


if __name__ == "__main__":
    unittest.main()
