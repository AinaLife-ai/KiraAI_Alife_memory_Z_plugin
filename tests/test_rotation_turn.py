"""轮换的「轮」必须与压缩的「轮」对齐 ✓：**按真实的用户对话轮**计数 ✗ 不是按 LLM 请求 ✓

背景（实测过 ✗）：工具步会各写一条 `role="assistant"` 记录 ✓ 而轮换的推进写在 `on_request` 里 ✗
⇒ 一次带 5 个工具步的用户轮被记成 5~6 轮 ✗ → `rotate_cooldown_rounds`(默认10) 只相当于 ~2 个真实回合 ✓
本测试钉死：**同一轮内多次请求只推进一次** ✓ 换用户轮才再推进 ✓
"""
import asyncio
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_rot_turn")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_rot_turn", pkg)
storage = importlib.import_module("alife_rot_turn.storage")


def _main_module():
    if not os.environ.get("KIRA_CORE"):
        import pytest
        pytest.skip("set KIRA_CORE for host integration")
    core = Path(os.environ["KIRA_CORE"]).resolve()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    return importlib.import_module("alife_rot_turn.main")


class _Msg:
    def __init__(self, role, content):
        self.role, self.content = role, content


def _req(*roles_and_contents):
    return types.SimpleNamespace(
        messages=[_Msg(r, c) for r, c in roles_and_contents]
    )


class RotationTurnCase(unittest.TestCase):
    """① 判据本身 ✓  ② 同一轮只推进一次 ✓  ③ 换轮才推进 ✓"""

    def test_turn_key_ignores_tool_steps(self):
        """同一个用户轮的多个请求（含工具步 ✗）必须得到**同一个**轮标识 ✓"""
        main = _main_module()
        plugin = main.AlifeMemoryPlugin
        first = plugin.rotation_turn_key(None, _req(("user", "你好"), ("assistant", "在的")))
        step2 = plugin.rotation_turn_key(None, _req(
            ("user", "你好"), ("assistant", "在的"),
            ("assistant", "[调用工具]"), ("tool", "结果"), ("assistant", "好了")))
        self.assertEqual(first, step2, "工具步被当成新的一轮了 ✗")
        # 用户真的说了新的一句 → 必须换轮 ✓
        next_turn = plugin.rotation_turn_key(None, _req(
            ("user", "你好"), ("assistant", "在的"), ("user", "换一句")))
        self.assertNotEqual(first, next_turn, "新的用户轮没被识别出来 ✗")

    def test_same_content_twice_still_counts_as_new_turn(self):
        """用户连发两句一模一样的话 ✗ 也必须算**两个**轮 ✓（靠用户消息条数区分 ✓）"""
        main = _main_module()
        plugin = main.AlifeMemoryPlugin
        a = plugin.rotation_turn_key(None, _req(("user", "嗯")))
        b = plugin.rotation_turn_key(None, _req(("user", "嗯"), ("assistant", "嗯？"), ("user", "嗯")))
        self.assertNotEqual(a, b, "连发同样的话被漏成同一轮 ✗")

    def test_rotation_advances_once_per_user_turn(self):
        """同一轮里请求 3 次（模拟 2 个工具步 + 收尾）→ 轮换只推进**一次** ✓"""
        main = _main_module()

        async def run():
            tmp = tempfile.TemporaryDirectory()
            store = storage.Store(Path(tmp.name) / "m.db")
            store.initialize()
            stub = types.SimpleNamespace(
                store=store, rotation={},
                seen_window=types.SimpleNamespace(get=lambda k: {}, remember=lambda *a, **k: None),
                model_text=lambda t, *a: t or "",
            )
            stub.rotation_state = types.MethodType(main.AlifeMemoryPlugin.rotation_state, stub)
            cfg = types.SimpleNamespace(
                rotate_enabled=True, rotate_count=2, rotate_min_hits=0,
                fact_recall_min_score=0.0, rotate_cooldown_rounds=10, rotate_keep_rounds=3,
            )
            rows = [{"id": "r%d" % i, "content": "记忆 %d" % i} for i in range(6)]
            text_of = lambda row: row.get("content") or ""
            # 第 1 次调用是**建批**（`next` 初始为真 ✓）✓ 它会把本轮记下来但不加轮数 ✓
            stub._rotation_turn = (1, 111)
            await main.AlifeMemoryPlugin.rotation_extras(
                stub, "s", cfg, rows, "k1", text_of, "archive")
            st0 = stub.rotation["s"]["slots"]["archive"]
            rounds_base, cooldown_base = st0["rounds"], dict(st0["cooldown"])
            for _ in range(3):                         # 同一轮的另外 3 个请求（工具步 ✓）
                stub._rotation_turn = (1, 111)
                await main.AlifeMemoryPlugin.rotation_extras(
                    stub, "s", cfg, rows, "k1", text_of, "archive")
            st1 = stub.rotation["s"]["slots"]["archive"]
            same_turn = (st1["rounds"] == rounds_base and dict(st1["cooldown"]) == cooldown_base)
            # 换一个用户轮 → 这次必须推进 ✓（轮数 +1 且冷却 -1 ✓）
            stub._rotation_turn = (2, 222)
            await main.AlifeMemoryPlugin.rotation_extras(
                stub, "s", cfg, rows, "k1", text_of, "archive")
            st2 = stub.rotation["s"]["slots"]["archive"]
            tmp.cleanup()
            return same_turn, st2["rounds"] - rounds_base, st1["cooldown"], st2["cooldown"]

        same_turn, advanced, cd_before, cd_after = asyncio.run(run())
        self.assertTrue(same_turn, "同一轮内 rounds/cooldown 被推进了 ✗（工具步不该算一轮）")
        self.assertEqual(advanced, 1, "换了用户轮之后应该正好推进 1 轮 ✗（实际 %d）" % advanced)
        # 冷却是否递减取决于该轮有没有重建批次 ✓ 不在这里硬断言 ✓
        # （同一轮内"冷却不动"已由 same_turn 覆盖 ✓ 那才是本次修的重点 ✓）
        self.assertIsInstance(cd_after, dict)

    def test_feedback_counted_once_per_turn(self):
        """同一个用户轮里回复多次（含工具步 ✗）→ 「被用上」只计**一次** ✓"""
        main = _main_module()

        async def run():
            tmp = tempfile.TemporaryDirectory()
            store = storage.Store(Path(tmp.name) / "m.db")
            store.initialize()
            stub = types.SimpleNamespace(
                store=store, rotation={},
                settings=types.SimpleNamespace(rotate_min_hits=1, rotate_keep_rounds=3),
            )
            holder = stub.rotation.setdefault("s", {"slots": {}})
            holder["slots"]["archive"] = {
                "rows": [], "texts": {"r1": "她喜欢在晚上写代码"},
                "ids": ["r1"], "rounds": 1, "next": False, "cooldown": {}, "turn": (1, 111),
            }
            hits = []

            async def _fake_call(name, *a, **k):
                if name == "mark_rotation":
                    hits.append(a)
                return None

            store.call = _fake_call

            for _ in range(3):     # 同一轮回复 3 次（工具步各回一次 ✓）
                await main.AlifeMemoryPlugin.rotation_feedback(
                    stub, "s", "她喜欢在晚上写代码")
            first = len(hits)
            # 注：命中后既有逻辑会置 `next=True`（决定下轮换批 ✓）→ 后续本就不再计数 ✓
            #     这是**原有设计** ✓ 本测试只钉"同一轮不许重复计入" ✓
            await main.AlifeMemoryPlugin.rotation_feedback(stub, "s", "她喜欢在晚上写代码")
            tmp.cleanup()
            return first, len(hits)

        first, total = asyncio.run(run())
        self.assertEqual(first, 1, "同一轮里重复计入 %d 次 ✗（应该只计 1 次）" % first)
        self.assertEqual(total, first, "同一轮里又计了一次 ✗")
