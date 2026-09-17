"""召回侧短化 `recall_text` ✓ —— 用**用户日志里的真实形态**逐条钉死

用户要求（2026-09-16）：
  · 图片/贴纸 → 占位（只保留框架的 img 与括号 ✓）
  · 只有消息 id、**看不到原消息**的纯引用壳 → 也是噪声 ✗ ⇒ 转占位 ✓
  · 但**能显示原消息**的引用要**保留**引用原文 ✓
⚠️ 与压缩侧严格分开 ✗ —— `model_text` 必须保留媒体描述 ✓（那是压缩的原料 ✓）
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_recalltext")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_recalltext", pkg)
retrieval = importlib.import_module("alife_recalltext.retrieval")


class RecallTextCase(unittest.TestCase):
    CASES = [
        # 用户日志里那条（**嵌套一层** ✗ 我先后漏了两次 ✓ 2026-09-16 修好）
        ("[Reply ID: -71，[Sticker 一张动漫风格的插画，白发少女]]", "[Reply]"),
        ("[Reply -208950819] 我记得她喜欢猫 [图片 一只橘猫躺在键盘上]",
         "[Reply] 我记得她喜欢猫 [Image]"),
        # 能显示原消息的引用 → **保留**引用的原文 ✓
        ("[Reply ID: 123 content: 你上次说的那个功能] 我做了",
         "[Reply] 你上次说的那个功能 我做了"),
        ("[Image 这张图片展示了]", "[Image]"),
        ("[Sticker 动漫风格的少女]", "[Image]"),
        ("[Reply ID: -12]", "[Reply]"),
        # 正常文本一个字都不许动 ✗
        ("晚上吃什么", "晚上吃什么"),
        ("她喜欢在晚上写代码", "她喜欢在晚上写代码"),
    ]

    def test_real_shapes(self):
        for raw, want in self.CASES:
            self.assertEqual(retrieval.recall_text(raw, 60).strip(), want, raw)

    def test_compression_side_keeps_description(self):
        """压缩侧**不能**短化 ✗ —— 描述是压缩的原料 ✓ 删了就永远提取不出图片相关事实 ✓"""
        raw = "[Sticker 一张动漫风格的插画，白发少女坐在窗边看雨]"
        self.assertIn("白发少女", retrieval.model_text(raw, ()))
        self.assertNotIn("白发少女", retrieval.recall_text(raw, 60))
