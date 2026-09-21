"""Versioned config migration: upgrade untouched defaults, never user choices."""

import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_config_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_config_test", package)
c = importlib.import_module("alife_config_test.contracts")
m = importlib.import_module("alife_config_test.config_migrate")


class MigrationCase(unittest.TestCase):
    def test_old_default_is_upgraded(self):
        changed, updated = m.migrate({"alife": {"audit_interval": 1800}})
        self.assertEqual(changed, ["audit_interval"])
        self.assertEqual(updated["alife"]["audit_interval"], 7200)
        self.assertEqual(updated["alife_meta"]["config_version"], m.CURRENT_VERSION)

    def test_customised_value_is_kept(self):
        changed, updated = m.migrate({"alife": {"audit_interval": 3600}})
        self.assertEqual(changed, [])
        self.assertEqual(updated["alife"]["audit_interval"], 3600)
        self.assertEqual(updated["alife_meta"]["config_version"], m.CURRENT_VERSION)

    def test_missing_key_is_left_to_host_defaults(self):
        changed, updated = m.migrate({"alife": {}})
        self.assertEqual(changed, [])
        self.assertNotIn("audit_interval", updated["alife"])

    def test_runs_once(self):
        _, first = m.migrate({"alife": {"audit_interval": 1800}})
        changed, second = m.migrate(first)
        self.assertEqual(changed, [])
        self.assertEqual(second["alife"]["audit_interval"], 7200)

    def test_empty_or_broken_config_is_safe(self):
        for value in ({}, None, {"alife": None}, {"alife_meta": {"config_version": "x"}}):
            changed, updated = m.migrate(value)
            self.assertEqual(changed, [])
            self.assertEqual(
                updated["alife_meta"]["config_version"], m.CURRENT_VERSION
            )

    def test_prompt_defaults_match_contracts(self):
        defaults = m.prompt_defaults()
        self.assertEqual(defaults["fact_merge_prompt"], c.FACT_MERGE_PROMPT)
        self.assertEqual(defaults["record_merge_prompt"], c.RECORD_MERGE_PROMPT)
        self.assertEqual(c.Settings().fact_merge_prompt, c.FACT_MERGE_PROMPT)
        self.assertEqual(c.Settings().record_merge_prompt, c.RECORD_MERGE_PROMPT)

    def test_soft_limit_cannot_exceed_hard_limit(self):
        with self.assertRaises(Exception):
            c.Settings(fact_merge_soft_chars=200, fact_merge_max_chars=150)
        with self.assertRaises(Exception):
            c.Settings(record_merge_soft_reason_chars=90, record_merge_reason_chars=60)


if __name__ == "__main__":
    unittest.main()


def test_search_active_only_flips_only_when_untouched():
    """v2.6.0 翻转的默认值：老配置要被带过去，用户自己设过的不动。"""
    current = {
        "alife": {"search_active_only": True, "threshold": 120},
        "alife_meta": {"config_version": 2},
    }
    changed, updated = m.migrate(current)
    assert changed == ["search_active_only"]
    assert updated["alife"]["search_active_only"] is False
    assert updated["alife"]["threshold"] == 120  # 其它自定义保持不变

    # 用户若是刻意设回 true（而不是停留旧默认），不该被改写：
    # 迁移只在「配置版本落后 + 值等于旧默认」时生效，版本已是最新就等于不再动它。
    settled = {
        "alife": {"search_active_only": True},
        "alife_meta": {"config_version": m.CURRENT_VERSION},
    }
    assert m.migrate(settled) == ([], settled)


def test_prompt_wording_is_upgraded_but_custom_kept():
    """v2.18.9：存量用户**默认文案**要能升上来 ✗ 用户自己改过的绝对不碰 ✓

    `config_migrate` 的设计就是"只改写仍等于旧默认的值" ✓
    但光有机制不够 ✗ 我这次改了 `fact_merge_prompt` 却**忘了加迁移条目** ✓
    → 存着旧文案的存量用户永远拿不到"来源强度优先于时间"这条规则 ✗
    """
    import importlib, sys, types
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pkg = types.ModuleType("alife_cfgt_test")
    pkg.__path__ = [str(root)]
    sys.modules.setdefault("alife_cfgt_test", pkg)
    mod = importlib.import_module("alife_cfgt_test.config_migrate")
    contracts = importlib.import_module("alife_cfgt_test.contracts")

    # ① 存着旧默认文案（且版本也是那个年代 ✓）→ 一路上升到当前默认 ✓
    changed, out = mod.migrate(
        {
            "alife": {"fact_merge_prompt": mod._OLD_FACT_MERGE_PROMPT},
            "alife_meta": {"config_version": 3},
        }
    )
    assert "fact_merge_prompt" in changed
    assert out["alife"]["fact_merge_prompt"] == contracts.FACT_MERGE_PROMPT

    # ①b 升过 v4 的用户（存着 v4 那版文案 ✓）→ 只跑 v5 也要升到最新 ✓
    changed_b, out_b = mod.migrate(
        {
            "alife": {"fact_merge_prompt": mod._V4_FACT_MERGE_PROMPT},
            "alife_meta": {"config_version": 4},
        }
    )
    assert "fact_merge_prompt" in changed_b
    assert out_b["alife"]["fact_merge_prompt"] == contracts.FACT_MERGE_PROMPT

    # ② 用户自己改过的措辞 → 一个字都不动 ✓
    mine = "我自己写的合并提示词"
    changed2, out2 = mod.migrate(
        {
            "alife": {"fact_merge_prompt": mine},
            "alife_meta": {"config_version": mod.CURRENT_VERSION - 1},
        }
    )
    assert changed2 == []
    assert out2["alife"]["fact_merge_prompt"] == mine

    # ③ 已经是最新版 → 幂等 ✓
    changed3, _ = mod.migrate({"alife": {}, "alife_meta": {"config_version": mod.CURRENT_VERSION}})
    assert changed3 == []


def test_archive_rotation_default_flips_off_once():
    """v2.18.56：「档案轮换槽」改为**默认关** ✓ 存量配置一次性迁移到关 ✓

    语义（用户拍板）：存量配置里存着的 True —— 无论它是框架自动灌进去的默认值，
    还是用户自己手动打开的 —— 一律改成 False ✗✓（想用的人自己再打开 ✓）。
    迁移只跑一次：之后用户开回来**不会再被动** ✓

    安全性靠四个分支钉住：
      ① 代码默认值本身 = 关 ✓（不然新装的还开着 ✗）
      ② 旧默认 True ⇒ 改关 ✓（且报告 changed ✓）
      ③ 已经关着的 ⇒ 空报告 ✓（不产生无意义写盘 ✓）
      ④ 配置里没这个键 ⇒ **不主动写入** ✓（交给宿主的字段默认值 ✓ 避免和它打架 ✓）
      ⑤ 幂等：迁移过再跑不动 ✓
      ⑥ 迁移后用户自己开回来 ⇒ 不被再次关掉 ✓
    """
    import importlib, sys, types
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pkg = types.ModuleType("alife_cfg2_test")
    pkg.__path__ = [str(root)]
    sys.modules.setdefault("alife_cfg2_test", pkg)
    mod = importlib.import_module("alife_cfg2_test.config_migrate")
    contracts = importlib.import_module("alife_cfg2_test.contracts")

    # ① 代码默认值 = 关 ✓
    assert contracts.Settings().rotate_archive_enabled is False

    # ② 存量（旧默认 True）⇒ 关 ✓
    changed, out = mod.migrate({"alife": {"rotate_archive_enabled": True}})
    assert changed == ["rotate_archive_enabled"]
    assert out["alife"]["rotate_archive_enabled"] is False

    # ③ 已经关着的 ⇒ 空报告 ✓
    changed2, out2 = mod.migrate({"alife": {"rotate_archive_enabled": False}})
    assert changed2 == []
    assert out2["alife"]["rotate_archive_enabled"] is False

    # ④ 没有这个键 ⇒ 不主动写入 ✓
    _, out3 = mod.migrate({"alife": {}})
    assert "rotate_archive_enabled" not in out3["alife"]

    # ⑤ 幂等 ✓
    changed4, _ = mod.migrate(out)
    assert changed4 == []

    # ⑥ 迁移后用户自己开回来 ⇒ 不被再关 ✓
    mine = {"alife": dict(out["alife"], rotate_archive_enabled=True),
            "alife_meta": dict(out["alife_meta"])}
    changed5, out5 = mod.migrate(mine)
    assert changed5 == []
    assert out5["alife"]["rotate_archive_enabled"] is True


def test_archive_rotation_schema_and_help_agree():
    """面（schema.json）与帮助文案都要跟代码默认对上 —— 避免三处各说各话 ✓"""
    import importlib, json, sys, types
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pkg = types.ModuleType("alife_cfg3_test")
    pkg.__path__ = [str(root)]
    sys.modules.setdefault("alife_cfg3_test", pkg)
    contracts = importlib.import_module("alife_cfg3_test.contracts")
    help_mod = importlib.import_module("alife_cfg3_test.setting_help")

    field = json.loads((root / "schema.json").read_text("utf-8"))["alife"]["fields"][
        "rotate_archive_enabled"
    ]
    assert field["default"] is False, "schema.json 默认值没跟着改 ✗"
    assert "默认：关" in field["description"], "schema 描述还写着默认开 ✗"

    text = help_mod.HELP["rotate_archive_enabled"]
    assert "默认：关" in text and "默认：开" not in text, "帮助文案没跟着改 ✗"
    assert contracts.Settings().rotate_archive_enabled is False
