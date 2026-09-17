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
C = importlib.import_module("alife_tidyforce.contracts")


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


class SchemaNameAlignmentCase(unittest.TestCase):
    """生成器的名字列表必须与 Settings 字段**一一对齐** ✗✓

    `generate_schema.py` 用 `dict(zip(model_fields, [中文名单]))` ✗ —— **按位置配对** ✓
    往中间插一个配置却忘了在名单同位置补名 ✗ ⇒ 后面**整片错位 4 格** ✓
    ⇒ 界面会把「事实合并」的名字挂到「压缩推进」的配置上 ✗✓（本会话真实踩过 ✓）

    这里守住两件事：
      ① **数量一致**（少一条 = 后面全错 ✗）
      ② **抽样语义**（几个新配置的名字必须配得上它自己 ✓）
    """

    def test_generator_list_aligns_with_fields(self):
        import ast
        import io
        from pathlib import Path as _P

        root = _P(__file__).resolve().parents[1]
        src = io.open(root / "tools" / "generate_schema.py", encoding="utf-8").read()
        lst = None
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict":
                for a in node.args:
                    if isinstance(a, ast.Call) and getattr(a.func, "id", "") == "zip":
                        for arg in a.args:
                            if isinstance(arg, ast.List):
                                lst = [e.value for e in arg.elts]
        self.assertIsNotNone(lst, "没解析到生成器的名字列表 ✗")
        fields = list(C.Settings.model_fields.keys())
        self.assertEqual(
            len(lst), len(fields),
            "名字列表 %d 条 ≠ 字段 %d 个 ✗ zip 会截断 → 后面整片错位 ✓" % (len(lst), len(fields)),
        )
        # 抽样：名字里必须带该配置的语义关键词 ✓
        probes = {
            "compress_input_max_chars": "上限",
            "compress_stale_after_days": "陈旧",
            "compress_idle_after_hours": "闲置",
            "compress_idle_cooldown_min": "冷却",
            "compress_batches_per_job": "几批",
            "fact_merge_enabled": "合并",
            "record_merge_prompt": "提示词",
            "profile_summary_count": "摘要",
        }
        for key, frag in probes.items():
            i = fields.index(key)
            self.assertIn(frag, lst[i], "%s 的名字配错位了 ✗（拿到 %r）" % (key, lst[i]))
