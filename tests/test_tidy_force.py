"""永久记忆整理：`force`（无视冷却）与 `ids`（只整理指定条目）✓

用户需求（2026-09-16）：
  · 前端能手动触发整理并**无视冷却** ✓
  · **单条**记忆能**重新提取事实** ✓
  · bot 也能传参选择无视冷却 ✓
  · 但 force 也要**防抖**（用户定的 10 秒 ✓）防手抖连点烧 token ✓
⚠️ 这里只测存储层的真行为 ✓（引擎的 10 秒防抖由 `_tidy_forced_at` 管 ✓）
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
pkg = types.ModuleType("alife_tidyforce")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_tidyforce", pkg)
storage = importlib.import_module("alife_tidyforce.storage")


async def _seed(n=3):
    """建一个带 n 条"刚整理过"的常驻记忆的库 ✓"""
    tmp = tempfile.TemporaryDirectory()
    store = storage.Store(Path(tmp.name) / "m.db")
    store.initialize()
    sid = "qq:dm:1"
    now = time.time()
    await store.call("capture", sid, "k1", [
        {"role": "user", "content": "叫我爱奈丽", "users": ["qq:1"], "speaker": "qq:1", "time": now}
    ])
    ids = []
    for i in range(n):
        rid = await store.call(
            "memorize", sid, "常驻条目 %d" % i, ["qq:1"], now, now,
            category="preference", importance=5 + i,
        )
        ids.append(rid)
    # 关键：标成"刚刚整理过" ✓ → 按冷却就该挑不到 ✓
    # （派发名是 touch_tidy_at ✓ 直接属性名是 touch_tidy ✗ 别混 ✓）
    store.touch_tidy(ids)   # 同步方法 ✓ 直接调 ✓
    return store, sid, ids, tmp


class TidyForceCase(unittest.TestCase):
    def test_cooldown_blocks_by_default(self):
        """按冷却（默认 14 天）→ 刚整理过的**挑不到** ✓"""
        async def run():
            store, sid, _ids, tmp = await _seed()
            got = await store.call("tidy_candidates", sid, 14, 10)
            tmp.cleanup()
            return got
        self.assertEqual(asyncio.run(run()), [], "刚整理过的还被挑出来了 ✗（冷却没生效）")

    def test_force_ignores_cooldown(self):
        """`days=0`（force ✓）→ **无视冷却** ✓ 刚整理过的也能挑到 ✓"""
        async def run():
            store, sid, _ids, tmp = await _seed()
            got = await store.call("tidy_candidates", sid, 0, 10)
            tmp.cleanup()
            return len(got)
        self.assertEqual(asyncio.run(run()), 3, "无视冷却没生效 ✗")

    def test_ids_filter_picks_only_one(self):
        """`ids=[...]` → **只整理这一条** ✓（单条「重新提取事实」靠它 ✓）"""
        async def run():
            store, sid, ids, tmp = await _seed()
            got = await store.call("tidy_candidates", sid, 0, 10, [ids[1]])
            tmp.cleanup()
            return ids[1], [r["id"] for r in got]
        want, got = asyncio.run(run())
        self.assertEqual(got, [want], "ids 过滤没生效 ✗ 应只返回指定的那一条")
