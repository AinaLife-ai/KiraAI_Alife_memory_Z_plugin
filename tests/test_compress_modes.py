"""v2.18.11：压缩分批的两种模式。

用户要求：做成模式选择 ✓ 默认**纯按轮**（10 轮 ✓）；另一种"按条"也保留 ✓
但**最后一轮必须收尾完整** ✗ —— 无视条数，也要停在一轮的结尾 ✓

轮的定义（与 KiraOS 的 chunk 一致）：用户（们）发言 → 助手回复
⇒ 边界 =「助手说完之后、下一条用户发言之前」✓
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_mode_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_mode_test", package)
engine = importlib.import_module("alife_mode_test.engine")
contracts = importlib.import_module("alife_mode_test.contracts")


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

    def test_default_is_rounds_with_ten(self):
        d = contracts.Settings()
        self.assertEqual(d.compress_batch_mode, "rounds", "默认必须按轮 ✓")
        self.assertEqual(d.compress_rounds, 10, "默认 10 轮 ✓")

    def test_takes_exactly_n_complete_rounds(self):
        rows = conversation(12)                       # 12 轮 × 4 条
        subset, level = engine.compression_plan(rows, self.cfg)
        self.assertEqual(level, 1)
        self.assertEqual(len(subset), 40, "10 轮 × 4 条 = 40")
        self.assertEqual(subset[-1]["role"], "assistant", "必须停在轮尾 ✗ 不许切半轮 ✓")

    def test_waits_for_the_tenth_round_to_finish(self):
        rows = conversation(9) + [row(900, "user")]   # 第 10 轮刚开始 ✗ 还没回复
        self.assertIsNone(engine.compression_plan(rows, self.cfg), "本轮没完不能动手 ✓")

    def test_dangling_turn_is_not_counted_or_included(self):
        rows = conversation(10) + [row(900, "user")]  # 10 整轮 + 半句 ✗
        subset, _ = engine.compression_plan(rows, self.cfg)
        self.assertTrue(all(r["id"] != "r900" for r in subset), "半轮不许进批次 ✓")
        self.assertEqual(len(subset), 40, "只算完整的 10 轮 = 40 条 ✓")


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
