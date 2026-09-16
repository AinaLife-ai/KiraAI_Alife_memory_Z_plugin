"""v2.18.12：媒体判定的正确口径 —— 用户日志里的真实形态逐条钉死。"""
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_media_test")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_media_test", pkg)
retrieval = importlib.import_module("alife_media_test.retrieval")
storage = importlib.import_module("alife_media_test.storage")

# 已知成员名（v2.18.14：@ 的定性靠它 ✓ 不再猜长度 ✓）
NAMES = {"小明", "某位朋友", "阿澄", "一个非常非常长的昵称用来测试超长名字的情况"}

# 用户日志里出现的真实形态（正是"被召回了"的那几条 ✗）
NOISY = [
    "[图片 这是一张动漫风格的插画，描绘了一位白发少女…]",
    "[Sticker 这张图片是一个动漫风格的卡通人物…]",
    "[Reply 1478539]",
    "[Reply ID: -71，[Sticker 这张图片是]",
    "↩7 [图片 一只橘猫]",
    "[表情6]",
    # 只有 at 壳、没有别的文字 ✓（用户明确要求：也不召回 ✓）
    "@123456",
    "@12345678901",
    "@某位朋友",          # ← 已知成员名 ✓
    "@小明",
    "@一个非常非常长的昵称用来测试超长名字的情况",   # ← 超长昵称 ✓ 是成员 → 仍是 at ✓
    "[CQ:at,qq=123]",
    '<at id="1"/>',
]
# 有真实文字的（必须照常召回 ✓）
REAL = [
    "[Reply -208950819] He11o? 金牛你断网啦",
    "晚上吃什么",
    # v2.18.14 的关键修复：**at 粘连的短句绝不能被整条吃掉** ✗
    "@他就好了",
    "@小明你好",
    "@某位路人甲",        # 不是已知成员 → 当正文 ✓
    "@123456 你好",
    "@小明 你好",
    "你好 @小明",
    "@小明你好，今天怎么样",
    "[Reply 123] @小明 你好",
    "at他就好了",         # 纯文字里的 at（没有 @）✓ 不关我的事 ✓
    "at他就好了，你也是",
]


class DetectCase(unittest.TestCase):
    def test_log_shapes_are_media(self):
        """日志里那几种（含套了 [Reply …] 壳的、只有 at 壳的）都必须判为媒体 ✗"""
        for text in NOISY:
            self.assertTrue(retrieval.media_only(text, NAMES), text)

    def test_real_text_is_kept(self):
        """有真实文字的一条都不许误伤 ✓（含 at 粘连的短句 ✗ 实测误伤过 ✓）"""
        for text in REAL:
            self.assertFalse(retrieval.media_only(text, NAMES), text)


class LegacyFilterCase(unittest.TestCase):
    """存量记录：升级前存进去的贴纸/图片描述也必须被挡住 ✗

    `category="media"` 只管新写入 ✗ → 这里验证 `search` 的**运行时**过滤兜住旧数据 ✓
    """

    def _store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = storage.Store(Path(tmp.name) / "m.db")
        st.initialize()
        return st

    def _legacy(self, st, text):
        """模拟"升级前就存进去"的记录：写入后把媒体标记清掉 ✗"""
        st.capture("qq:dm:1", "k", [{"role": "user", "content": text,
                                     "users": ["qq:1"], "speaker": "qq:1", "time": 1.0}])
        with st.connect() as db:
            db.execute("UPDATE records SET category='' WHERE content=?", (text,))
        return st

    def test_legacy_media_is_filtered_by_default(self):
        """存量媒体记录默认**不进结果** ✗（total 是 SQL 原始计数 ✓ 供分页用 ✓ 不参与断言）"""
        st = self._legacy(self._store(), NOISY[1])          # [Sticker …] ✗
        self.assertEqual(st.search("qq:dm:1", limit=10)["items"], [],
                         "存量媒体记录默认必须被挡掉 ✗")
        self.assertEqual(len(st.search("qq:dm:1", limit=10, skip_media=False)["items"]), 1,
                         "关掉开关要能看到（数据仍在 ✓）")

    def test_real_text_still_recalled(self):
        st = self._legacy(self._store(), REAL[1])           # 「晚上吃什么」✓
        self.assertEqual(len(st.search("qq:dm:1", limit=10)["items"]), 1,
                         "有真实文字的一条都不许误伤 ✓")
