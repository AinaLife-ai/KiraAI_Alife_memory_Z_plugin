"""设置项：**帮助文案 ↔ 实际语义**的守卫 ✓（2026-09-17 用户要求 ✓）

起因：把冷会话语义从「或」改成「与」之后 ✓ 三处描述**全都没跟着改** ✗
（contracts 注释 / setting_help / 函数文档 ✓）—— 当时的守卫都只管
「名字对齐」「版本日志」「契约四方（有没有缺）」✗ ⇒ **语义与数字对不对**没人管 ✓

本文件补三类检查：
  (1) **默认值 ↔ 文案**：帮助写「默认 N」⇒ 实际默认必须就是 N ✓
  (2) **冷会话是「同时满足」**：两条帮助都要写明 + 互相引用 + 代码确实是 and ✓
  (3) **关键约定在文案里**：`probability` 的「0 为仅手动」等 ✓
"""
import importlib
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_pkg = types.ModuleType("alife_sem_test")
_pkg.__path__ = [str(ROOT)]
sys.modules["alife_sem_test"] = _pkg
c = importlib.import_module("alife_sem_test.contracts")
h = importlib.import_module("alife_sem_test.setting_help")


class DefaultMatchesHelpCase(unittest.TestCase):
    """(1) 帮助里写「默认 N」⇒ 实际默认必须等于 N ✓（通用防漂移 ✓）"""

    def test_every_stated_default_matches(self):
        bad = []
        for key, field in c.Settings.model_fields.items():
            text = h.HELP.get(key) or ""
            # ⚠️ 只认**第一处**「默认」✗✓ —— 文案里后面还会出现别的数字
            #   （例：batch_size 的"剩「阈值−每批」条，默认 10 条"说的是 50−40=10 ✗ 不是字段默认 ✓）
            #   第一版正则该条误报 ⇒ **检查器本身也要被验证** ✓（这条教训已第 N 次应验 ✓）
            m = re.search(r"默认\s*([0-9]+(?:\.[0-9]+)?)", text)
            if m:
                stated, actual = float(m.group(1)), float(field.default)
                if stated != actual:
                    bad.append((key, stated, actual))
        self.assertEqual(bad, [], "帮助写的默认值与实际不符 ✗：%s" % bad)

    def test_all_fields_have_help(self):
        missing = [k for k in c.Settings.model_fields if not h.HELP.get(k)]
        self.assertEqual(missing, [], "这些字段没有帮助文案 ✗：%s" % missing)


class ColdSessionSemanticsCase(unittest.TestCase):
    """(2) 冷会话是「**同时满足**」✓ —— 帮助写清 + 代码确实是 and ✓"""

    STALE = "compress_stale_after_days"
    IDLE = "compress_idle_after_hours"

    def test_both_helps_state_and_semantics(self):
        for key in (self.STALE, self.IDLE):
            text = h.HELP.get(key) or ""
            self.assertIn("同时满足", text,
                          "%s 的帮助没写明两条件需同时满足 ✗（代码是「与」✓）" % key)

    def test_both_helps_cross_reference(self):
        stale = h.HELP.get(self.STALE) or ""
        idle = h.HELP.get(self.IDLE) or ""
        self.assertIn("闲置", stale, "%s 没提到另一个条件 ✗" % self.STALE)
        self.assertIn("陈旧", idle, "%s 没提到另一个条件 ✗" % self.IDLE)

    def test_zero_means_disabled_documented(self):
        for key in (self.STALE, self.IDLE):
            self.assertIn("0", h.HELP.get(key) or "",
                          "%s 没说明设为 0 的含义 ✗（0 会退回「或」✓）" % key)

    def test_code_implements_and_not_or(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertIn("boost = _stale and _idle", code,
                      "冷会话没有按「同时满足」实现 ✗（文案与代码不符 ✓）")


class KeyContractsInHelpCase(unittest.TestCase):
    """(3) 关键约定必须出现在帮助里 ✓（因为代码确实按它实现 ✓）"""

    def test_probability_zero_means_manual_only(self):
        self.assertIn("仅手动", h.HELP.get("probability") or "",
                      "probability 没写 0=仅手动 ✗（代码确实如此 ✓）")

    def test_batch_cap_is_documented(self):
        self.assertIn("批", h.HELP.get("compress_batches_per_job") or "",
                      "单任务批次上限没解释「批」✗")

    def test_mode_choices_documented(self):
        text = h.HELP.get("compress_batch_mode") or ""
        for word in ("rounds", "records"):
            self.assertIn(word, text, "模式 %s 没在帮助里说明 ✗" % word)


class HelpStyleCase(unittest.TestCase):
    """帮助文案要**像产品说明** ✓ 不能像代码注释 ✗（2026-09-17 用户指出 ✓）

    实测：我写的两条带 `**粗体**` 与 ⚠️ ✓ 长度 141/118 字（全库中位只有 45 字 ✗）
    ⇒ 界面会把 `**` **原样显示**出来 ✗ 而且又长又像"内部推理" ✓
    ⇒ 这条守卫把三件事钉住：无格式符号 ✓ 无 emoji ✓ 不过长 ✓
    """

    MAX = 120

    def test_no_markdown_symbols(self):
        bad = [k for k, v in h.HELP.items() if re.search(r"\*\*|__|`", v or "")]
        self.assertEqual(bad, [], "帮助文案里有 Markdown 符号 ✗（界面会显示成 `**这样**` ✓）：%s" % bad)

    def test_no_emoji(self):
        bad = [k for k, v in h.HELP.items()
               if re.search(r"[\u2600-\u27bf\U0001F300-\U0001FAFF\u26A0\uFE0F]", v or "")]
        self.assertEqual(bad, [], "帮助文案里有 emoji ✗：%s" % bad)

    def test_not_comment_like(self):
        bad = [(k, len(v)) for k, v in h.HELP.items() if len(v or "") > self.MAX]
        self.assertEqual(bad, [], "帮助文案超过 %d 字 ✗（像注释 ✓ 应像产品说明 ✓）：%s" % (self.MAX, bad))

    def test_style_matches_house_tone(self):
        """抽查：至少多数帮助是**一句话**级别 ✓（中位数应远小于上限 ✓）"""
        lens = sorted(len(v or "") for v in h.HELP.values())
        median = lens[len(lens) // 2]
        self.assertLess(median, self.MAX / 2,
                        "帮助文案整体变长了 ✗（中位 %d 字 ✓ 应在一句话级别 ✓）" % median)


class HtmlStructureCase(unittest.TestCase):
    """HTML 结构底线：**不允许重复 id** ✓（2026-09-17 用户实测事故 ✓）

    v2.18.20 加「重新提取事实」按钮时，把 `删除 / 保存修改` **又抄了一份** ✗
    ⇒ 界面上出现**两组**按钮 ✓ 第二个点了**没反应**（同 id 只绑定第一个 ✓）
    ⇒ 这类错误不报错、只是长得不对 ✗ ⇒ 必须静态查 ✓
    """

    def test_no_duplicate_ids(self):
        import collections
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        ids = re.findall(r'\bid="([^"]+)"', html)
        dup = sorted(k for k, v in collections.Counter(ids).items() if v > 1)
        self.assertEqual(dup, [], "HTML 里 id 重复 ✗（第二个按钮点了没反应 ✓）：%s" % dup)

    def test_every_js_referenced_id_exists(self):
        """JS 里 `$("#x")` 引用的 id 必须在 HTML 里存在 ✓（防"按钮消失" ✓）"""
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        # ⚠️ 三个来源都要扫 ✗✓ —— 只扫"HTML 文件里"会漏掉**JS 内联 HTML 里创建的 id**
        #   （例：graphClear / newSid 都是 app.js 的模板字符串里 `<button id="…">` ✓）
        #   第一版正则漏了这两处 ⇒ **误报** ✓（"检查器本身要被验证"今天第三次应验 ✓）
        have = (set(re.findall(r'\bid="([^"]+)"', html))
                | set(re.findall(r'\bid="([^"]+)"', js))
                | set(re.findall(r'\.id\s*=\s*"([^"]+)"', js)))
        want = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
        missing = sorted(want - have)
        self.assertEqual(missing, [], "JS 引用了 HTML 里不存在的 id ✗：%s" % missing)


class SchemaDescriptionSyncCase(unittest.TestCase):
    """`schema.json` 的 description 必须与 `setting_help` 一致 ✓

    （2026-09-17 发现：改了帮助文案却**忘了重新生成** schema ✗ ⇒ 界面起作用的
      其实是 schema 里的描述 ✓ 不同步就会出现"代码改了、界面还是旧话"✓）
    """

    def test_schema_description_matches_help(self):
        import json
        sch = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))["alife"]["fields"]
        bad = []
        for key in c.Settings.model_fields:
            entry = sch.get(key) or {}
            desc = entry.get("description")
            if desc and desc != h.HELP.get(key):
                bad.append(key)
        self.assertEqual(bad, [], "schema 描述与 setting_help 不一致 ✗（忘记重新生成？✓）：%s" % bad)
