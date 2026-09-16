"""工具步：bot 侧搜不到 ✓ 但**压缩侧照常看得到**（转短占位 ✓）

用户要求（2026-09-16）：
  「工具步永远不主动和被动召回，这个肯定算纯噪声。就是查档案也查不到最好」
  「反正如果重要的，是会被压成事实的」
⇒ 所以过滤只发生在**召回**这条路 ✓ 压缩侧必须保留 ✗
  （否则工具步里的内容永远不会变成事实 ✗ 用户那个前提就不成立了 ✓）
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
pkg = types.ModuleType("alife_toolcase")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_toolcase", pkg)
storage = importlib.import_module("alife_toolcase.storage")
retrieval = importlib.import_module("alife_toolcase.retrieval")


async def _drive():
    tmp = tempfile.TemporaryDirectory()
    store = storage.Store(Path(tmp.name) / "m.db")
    store.initialize()
    sid = "qq:dm:1"
    now = time.time()
    await store.call("capture", sid, "k1", [
        {"role": "user", "content": "帮我查下天气", "users": ["qq:1"], "speaker": "qq:1", "time": now},
        {"role": "assistant", "content": "我看看", "summary": "我看看", "category": "tool",
         "users": ["qq:1"], "speaker": "qq:1", "time": now + 1},
        {"role": "assistant", "content": "明天晴，25 度", "users": ["qq:1"], "speaker": "qq:1", "time": now + 2},
    ])
    bot = [r["content"] for r in (await store.call("search", sid, limit=10))["items"]]
    ui = [r["content"] for r in (await store.call("search", sid, limit=10, include_tools=True))["items"]]
    rows = await store.call("active", sid)
    tmp.cleanup()
    return bot, ui, rows


class ToolStepCase(unittest.TestCase):
    def test_bot_cannot_see_tool_steps(self):
        """bot 侧（主被动召回 / 查档案）看不到工具步 ✓"""
        bot, _, _ = asyncio.run(_drive())
        self.assertNotIn("我看看", bot, "工具步被 bot 看到了 ✗")
        self.assertIn("明天晴，25 度", bot, "正常消息被误伤 ✗")

    def test_frontend_toggle_can_see_tool_steps(self):
        """前端"显示工具步"开关打开时看得到 ✓"""
        _, ui, _ = asyncio.run(_drive())
        self.assertIn("我看看", ui, "前端开关失效 ✗")

    def test_compression_still_sees_tool_steps(self):
        """压缩侧仍看得到工具步 ✗（否则"重要的会被压成事实"就不成立 ✓）"""
        _, _, rows = asyncio.run(_drive())
        self.assertEqual(len(rows), 3, "压缩侧的活跃行少了 ✗")
        flags = [retrieval.is_tool_step(r) for r in rows]
        self.assertEqual(flags, [False, True, False], "工具步标记不对 ✗")

    def test_placeholder_is_short(self):
        """喂给压缩模型的工具步是极短占位 ✓（不夹带 tool_calls JSON ✗）"""
        self.assertLessEqual(len(retrieval.tool_placeholder()), 8)
