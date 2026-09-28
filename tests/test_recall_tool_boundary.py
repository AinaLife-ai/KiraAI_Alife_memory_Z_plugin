"""召回边界守卫：主/被动召回永远看不到工具步。

前端「显示工具步」勾选只影响**面板浏览**（store.call("search", ..., include_tools=True)），
不影响召回调用点（main.py 的召回调用**不传** include_tools ⇒ 默认 False ⇒ 永远过滤）。
"""
import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_recall_boundary_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_recall_boundary_test", package)
s = importlib.import_module("alife_recall_boundary_test.storage")


def run(coro):
    return asyncio.run(coro)


class RecallToolBoundaryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.tmp.name) / "m.db")
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, sid="qq:gm:1"):
        self.store.capture(sid, "turn", [
            {"role": "user", "content": "帮我记：花生过敏",
             "users": ["qq:9"], "time": 0.0},
            {"role": "assistant", "content": '{"tool_calls": [1]}',
             "category": "tool", "summary": "[调用工具：查天气]",
             "users": ["qq:9"], "time": 1.0},
            {"role": "assistant", "content": "已记住：花生过敏",
             "users": ["qq:9"], "time": 2.0},
        ])

    # ↓↓↓ 测试方法 ↓↓↓

    def test_recall_never_returns_tool_steps(self):
        """召回调用形态（不传 include_tools）⇒ 结果里**绝不能**有工具步 ✓。"""
        sid = "qq:gm:1"
        self.seed(sid)
        out = run(self.store.call("search", sid, limit=10))
        texts = [r["content"] for r in out["items"]]
        self.assertTrue(texts, "普通消息应该能被召回（不是空结果 ✓）")
        self.assertFalse(
            [t for t in texts if '"tool_calls"' in t or "查天气" in t],
            "主/被动召回永远看不到工具步 ✗",
        )

    def test_panel_flag_only_widens_the_panel_view(self):
        """面板勾选「显示工具步」⇒ 只是**能翻出来看** ✓（include_tools=True ✓）。"""
        sid = "qq:gm:1"
        self.seed(sid)
        ui = run(self.store.call("search", sid, limit=10, include_tools=True))
        texts = [r["content"] for r in ui["items"]]
        self.assertTrue(
            any('"tool_calls"' in t for t in texts),
            "面板勾选后要能翻出工具步（仅展示 ✓）",
        )

    def test_recall_call_site_passes_no_include_tools(self):
        """静态钉死：**召回调用点**不许带 include_tools ✗（只有面板那条路能传 ✓）。

        否则以后有人顺手加一个参数，就会把工具步放回上下文 —— 正是用户担心的那种回归 ✗。
        """
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        i = src.find('self.store.call("search", sid')
        self.assertGreater(i, 0, "没找到召回调用点（形态变了？请同步调整本守卫）")
        seg = src[i:i + 420]
        self.assertNotIn("include_tools", seg, "召回调用点不许带 include_tools ✗")


if __name__ == "__main__":
    unittest.main()
