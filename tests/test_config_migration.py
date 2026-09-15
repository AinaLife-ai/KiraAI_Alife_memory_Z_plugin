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
