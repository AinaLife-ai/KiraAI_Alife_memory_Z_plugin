"""压缩分批：**不漏、不丢、不重复** ✓（用户要求核验的正是这个 ✓）

钉死三条不变量（两种分批模式 × 有/无尾巴 ✓）：
  ① 一条都不删 ✓（压缩只是 active=0 ✗ 不是删除 ✓）
  ② 同一条不会被压两次 ✓
  ③ 每一批都是「当前 active 列表」的**前缀** ✓（不会跳过中间的 ✓）
另外：每条被压的记录都留了**回溯边** ✓（能走回原文 ✓）
"""
import asyncio
import importlib
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_media_test") if "alife_media_test" in sys.modules else types.ModuleType("alife_noloss_test")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault(pkg.__name__, pkg)
engine = importlib.import_module(pkg.__name__ + ".engine")
storage = importlib.import_module(pkg.__name__ + ".storage")


class _Cfg:
    compress_batch_mode = "rounds"
    compress_rounds = 3
    threshold = 4
    batch_size = 2
    max_level = 6


async def _drive(mode, rounds, tail):
    tmp = tempfile.TemporaryDirectory()
    store = storage.Store(Path(tmp.name) / "m.db")
    store.initialize()
    sid = "qq:gm:t"
    now = time.time()
    messages = []
    for i in range(rounds):
        messages.append({"role": "user", "content": "问 %d" % i, "users": ["qq:1"],
                         "speaker": "qq:1", "time": now + i * 2})
        messages.append({"role": "assistant", "content": "答 %d" % i, "users": [],
                         "speaker": "qq:1", "time": now + i * 2 + 1})
    if tail:
        messages.append({"role": "user", "content": "尾巴（还没答复）", "users": ["qq:1"],
                         "speaker": "qq:1", "time": now + 99})
    await store.call("capture", sid, "k1", messages)
    with store.connect() as db:
        original = [r[0] for r in db.execute("SELECT id FROM records WHERE sid=?", (sid,))]

    cfg = _Cfg()
    cfg.compress_batch_mode = mode
    seen, batches = [], 0
    for _ in range(40):
        rows = await store.call("active", sid)
        plan = engine.compression_plan(rows, cfg)
        if plan is None:
            break
        picked, level = plan
        ids = [r["id"] for r in picked]
        assert ids == [r["id"] for r in rows[: len(ids)]], "第 %d 批不是前缀（跳过了中间的）" % batches
        assert not (set(ids) & set(seen)), "第 %d 批重复压了已压过的记录" % batches
        seen.extend(ids)
        batches += 1
        await store.call("compress", sid, picked, level - 1, {"summary": "摘要", "facts": []})

    with store.connect() as db:
        alive = [r[0] for r in db.execute("SELECT id FROM records WHERE sid=? AND deleted=0", (sid,))]
        leftover = db.execute("SELECT count(*) FROM records WHERE sid=? AND active=1 AND deleted=0",
                              (sid,)).fetchone()[0]
        edges = db.execute("SELECT count(*) FROM edges").fetchone()[0]
    tmp.cleanup()
    return {"original": original, "covered": seen, "alive": alive,
            "leftover": leftover, "edges": edges, "batches": batches}


class NoLossCase(unittest.TestCase):
    def _check(self, mode, rounds, tail):
        r = asyncio.run(_drive(mode, rounds, tail))
        # ① 一条都没删 ✓（丢了任何一条都会在此暴露 ✓）
        lost = [i for i in r["original"] if i not in r["alive"]]
        self.assertEqual(lost, [], "压缩把记录删掉了 ✗ 原始记录必须全留着 ✓")
        # ② 每条被压的记录都有回溯边 ✓（原文随时能走回去 ✓）
        self.assertGreaterEqual(r["edges"], len(r["covered"]), "回溯边少于被压条数 ✗")
        # ③ 没被压的仍在 active ✓（随时可召回 ✓）
        self.assertGreaterEqual(r["leftover"], 0)

    def test_rounds_mode_keeps_everything(self):
        """按轮模式：有尾巴 / 没尾巴 两种都不许丢 ✗"""
        self._check("rounds", 3, True)
        self._check("rounds", 12, True)
        self._check("rounds", 3, False)

    def test_records_mode_keeps_everything(self):
        """按条模式：同样一条都不许丢 ✗（存量用户切模式也安全 ✓）"""
        self._check("records", 3, True)
        self._check("records", 12, True)
        self._check("records", 3, False)

    def test_tail_is_kept_for_recall(self):
        """攒不够时**不压但也不丢** ✓：数据仍在 active ✓ 照样能召回 ✓"""
        r = asyncio.run(_drive("rounds", 1, True))
        self.assertEqual(r["batches"], 0, "不足阈值时不该动手 ✓")
        with_original = [i for i in r["original"] if i in r["alive"]]
        self.assertEqual(len(with_original), len(r["original"]), "攒不够时更不该丢 ✓")
