"""海马体记忆（kira_plugin_hippocampus_memory）迁移对齐测试。

结论（实测）：它的 TOML 树与 KIRAOS **同构** ✓ 复用同一解析分支即可 ✓
它写在自己的插件数据目录下（main.py:110）→ 数据根与 KIRAOS 不同 ✓
"""
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_pkg = types.ModuleType("alife_hippo_test")
_pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_hippo_test", _pkg)
m = importlib.import_module("alife_hippo_test.migration")

FACT = 'id = "hates_css"\ntype = "fact"\ntext = "用户讨厌写 CSS"\nimportance = 6\ntags = ["frontend"]\n\n[source]\nsession = "qq:dm:769690776"\ntime = 2026-03-01T14:30:00+08:00\n'
SELF = 'id = "self_voice"\ntype = "reflection"\ntext = "我发现自己太啰嗦"\nimportance = 8\n\n[source]\nsession = "qq:dm:769690776"\ntime = 2026-03-02T09:00:00+08:00\n'
LONG = 'id = "big"\ntype = "fact"\ntext = "%s"\nimportance = 5\n' % ("很长的一条记忆" * 20)
PROFILE = '{"entity_id":"qq:769690776","entity_type":"user","name":"周武","nickname":"小武","traits":["耐心"],"preferences":{"theme":"dark"},"relationships":{"qq:1":"好友"},"facts":["做后端"],"aliases":["武哥"]}'


class HippocampusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "global" / "self").mkdir(parents=True)
        (root / "global" / "skills").mkdir(parents=True)
        ent = root / "entities" / "user_qq%3A769690776"
        (ent / "facts").mkdir(parents=True)
        (ent / "skills").mkdir(parents=True)
        (ent / "facts" / "hates_css.toml").write_text(FACT, encoding="utf-8")
        (ent / "profile.json").write_text(PROFILE, encoding="utf-8")
        (ent / "skills" / "skipme.toml").write_text(FACT, encoding="utf-8")
        (root / "global" / "self" / "self_voice.toml").write_text(SELF, encoding="utf-8")
        (root / "global" / "skills" / "skip2.toml").write_text(FACT, encoding="utf-8")
        (root / "global" / "facts" / "big.toml").parent.mkdir(parents=True, exist_ok=True)
        (root / "global" / "facts" / "big.toml").write_text(LONG, encoding="utf-8")
        self.root = root
        self.snap = m.snapshot(root, m.HIPPOCAMPUS, 120)

    def tearDown(self):
        self.tmp.cleanup()

    def by_subject(self):
        out = {}
        for it in self.snap["items"]:
            out.setdefault(it.get("subject"), []).append(it)
        return out

    def test_source_is_registered(self):
        self.assertIn(m.HIPPOCAMPUS, m.SOURCES)
        self.assertEqual(m.HIPPOCAMPUS, "kira_plugin_hippocampus_memory")
    def test_user_fact_maps_to_user_subject(self):
        """entities/user_qq%3A769690776/facts/*.toml → subject=qq:769690776 ✓"""
        items = self.by_subject().get("qq:769690776", [])
        texts = [i.get("content") for i in items]
        self.assertIn("用户讨厌写 CSS", texts)
        fact = [i for i in items if i.get("content") == "用户讨厌写 CSS"][0]["fact"]
        self.assertEqual(fact["category"], "fact")
        # importance 会被"按年龄折算"（样本是 2026-03-01，比 now 老）→ 关掉折算看原值 ✓
        raw = m.snapshot(self.root, m.HIPPOCAMPUS, 120, decay_days=0)
        raw_fact = [
            i for i in raw["items"] if i.get("content") == "用户讨厌写 CSS"
        ][0]["fact"]
        self.assertEqual(raw_fact["importance"], 6)
        self.assertLessEqual(fact["importance"], 6, "老记忆的 importance 应被折算下调 ✓")
        self.assertGreaterEqual(fact["importance"], 1)

    def test_self_reflection_lands_in_self_category(self):
        """global/self 的反思必须落到 self ✓（人格/自我是对齐的一部分）"""
        items = self.by_subject().get("legacy:self", [])
        self.assertTrue(items, "global/self 的反思没有落到 legacy:self ✗")
        fact = items[0]["fact"]
        self.assertEqual(fact["category"], "self")
        self.assertIn("legacy_import", fact["tags"])

    def test_profile_json_expands(self):
        """profile.json → traits/aliases/facts/preferences/relationships 全展开 ✓"""
        cats = {}
        for it in self.snap["items"]:
            f = it.get("fact") or {}
            cats.setdefault(f.get("category"), 0)
            cats[f.get("category")] += 1
        for want in ("profile", "fact", "preference", "relationship"):
            self.assertIn(want, cats, "profile.json 的 %s 没展开 ✗" % want)

    def test_skills_are_skipped(self):
        joined = " ".join(
            str(it.get("file", "")) for it in self.snap["items"]
        )
        self.assertNotIn("skills", joined, "skills 不该作为记忆导入 ✗")
    def test_over_limit_item_is_skipped_not_fatal(self):
        """超过字符上限 → 那条跳过 ✓ 但整个迁移不能失败 ✗（配置承诺的语义）"""
        self.assertEqual(self.snap["errors"], [], "超限不该被当成文件级错误 ✗")
        long_items = [i for i in self.snap["items"] if i.get("reason")]
        self.assertTrue(long_items, "超限条目应带 reason 以便计数为 skipped")
        self.assertNotIn(
            "很长的一条记忆很长的一条记忆",
            " ".join(str(i.get("content") or "") for i in self.snap["items"]),
        )

    def test_corrupt_source_is_reported_not_silent(self):
        """坏 TOML 必须进 errors（宁可中止也不半读）✓"""
        bad = self.root / "global" / "facts" / "broken.toml"
        bad.write_text('id = "x"\ntype = [unclosed\n', encoding="utf-8")
        snap = m.snapshot(self.root, m.HIPPOCAMPUS, 120)
        self.assertTrue(snap["errors"], "坏源文件必须上报 ✗")
        bad.unlink()


class RootsAndDecayTest(unittest.TestCase):
    def test_source_roots(self):
        roots = m.source_roots(Path("/data/memory"), Path("/data/plugin_data"))
        self.assertEqual(roots[m.SIMPLE], Path("/data/memory"))
        self.assertEqual(roots[m.KIRAOS], Path("/data/memory"))
        self.assertEqual(
            roots[m.HIPPOCAMPUS], Path("/data/plugin_data/kira_plugin_hippocampus_memory/memory")
        )

    def test_aged_importance(self):
        """导入时按年龄折算一次 ✓（海马体有衰减，我们没有）"""
        now = 1_800_000_000.0
        fresh = now - 86400
        two_years = now - 86400 * 730
        self.assertEqual(m.aged_importance(8, fresh, now=now), 8)
        self.assertEqual(m.aged_importance(8, two_years, now=now), 2)
        self.assertEqual(m.aged_importance(8, two_years, now=now, half_life_days=0), 8)
        self.assertEqual(m.aged_importance(8, None, now=now), 8)
        self.assertEqual(m.aged_importance(8, two_years, now=now * 1000), 2, "毫秒时间戳要兜底")
        self.assertGreaterEqual(m.aged_importance(1, two_years * 10, now=now), 1)

    def test_frontend_labels_cover_all_sources(self):
        """前端来源下拉必须覆盖 SOURCES ✓（保持界面统一 ✗ 不许漏）"""
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for pid in m.SOURCES:
            self.assertIn('%s: "' % pid, js, "前端缺少来源标签：%s" % pid)

    def test_settings_and_schema_declare_decay(self):
        """新配置项必须同时出现在 Settings / schema / HELP ✓（三处同步守卫）"""
        import re as _re

        c = (ROOT / "contracts.py").read_text(encoding="utf-8")
        self.assertIn("migration_decay_half_life_days", c)
        s = (ROOT / "schema.json").read_text(encoding="utf-8")
        self.assertIn("migration_decay_half_life_days", s)
        h = (ROOT / "setting_help.py").read_text(encoding="utf-8")
        self.assertIn("migration_decay_half_life_days", h)
        self.assertTrue(_re.search(r"migration_decay_half_life_days: int = Field\(default=365", c))


if __name__ == "__main__":
    unittest.main()
