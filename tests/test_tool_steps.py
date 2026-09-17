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

    def test_tool_step_feeds_summary_not_json(self):
        """喂给后台模型的是 **summary**（`[调用工具：名(参数)]` ✓ 无 JSON ✓）

        v2.18.19：一度改成裸占位 `[工具调用]` ✗ 但那会**丢掉工具名与参数** ✓
        summary 本就是人话 ✗ 不含 JSON ✓ ⇒ 用它信息量更大 ✓
        """
        from alife_toolcase import retrieval as _r
        self.assertIn("调用工具", _r.tool_call_summary([{"function": {"name": "get_weather", "arguments": "{}"}}]))
        self.assertNotIn("tool_calls", _r.tool_call_summary([{"function": {"name": "x", "arguments": "{}"}}]))


class BackfillCase(unittest.TestCase):
    """**存量用户**升级即可享受：老工具步（升级前入库、没标记）要被补标 ✓

    工具步标记是 v2.18.19 才加的 ✗ ⇒ 升级前的记录没标记 ✓
    不补的话"bot 看不到工具步"这个收益对存量用户**不生效** ✗（用户明确要求 ✓）
    识别方式：工具步的 content 里必定带 `{"tool_calls": …}`（capture 写入 ✓）
    """

    def test_backfill_marks_legacy_tool_steps(self):
        async def run():
            tmp = tempfile.TemporaryDirectory()
            store = storage.Store(Path(tmp.name) / "m.db")
            store.initialize()
            now = time.time()
            await store.call("capture", "s", "k1", [
                {"role": "user", "content": "帮我查天气", "users": ["qq:1"], "speaker": "qq:1", "time": now},
                # ★ 模拟"升级前"的工具步：内容带 tool_calls 但 category 为空 ✗
                {"role": "assistant", "content": '我看看{"tool_calls": [{"name": "get_weather"}]}',
                 "users": ["qq:1"], "speaker": "qq:1", "time": now + 1},
                {"role": "assistant", "content": "明天晴", "users": ["qq:1"], "speaker": "qq:1", "time": now + 2},
            ])
            before = [r["content"][:6] for r in (await store.call("search", "s", limit=10))["items"]]
            marked = await store.call("backfill_tool_steps")
            after = [r["content"][:6] for r in (await store.call("search", "s", limit=10))["items"]]
            ui = [r["content"][:6] for r in (await store.call("search", "s", limit=10, include_tools=True))["items"]]
            again = await store.call("backfill_tool_steps")   # 幂等 ✓
            tmp.cleanup()
            return before, marked, after, ui, again

        before, marked, after, ui, again = asyncio.run(run())
        self.assertIn("我看看", str(before), "回填前 bot 本该看得到（否则用例前提不成立）")
        self.assertGreaterEqual(marked, 1, "没回填到老工具步 ✗")
        self.assertNotIn("我看看", str(after), "回填后 bot 仍看到工具步 ✗")
        self.assertIn("我看看", str(ui), "前端开关应仍能看到 ✓")
        self.assertEqual(again, 0, "回填不幂等 ✗（第二次不该再改）")
