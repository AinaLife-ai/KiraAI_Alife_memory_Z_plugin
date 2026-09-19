"""强制重新提取（rebuild）★ 2026-09-19（用户定的规矩）

用户的原话：
    「我们要强制重新提取，那就不能 keep 了」✓
    「不要强制的时候，它这套运作和提示词不能被破坏」✓

⇒ 本文件同时守**两件事**：
   ① 强制 ⇒ **不允许 keep**（否则"点了等于没点"）
   ② 不强制 ⇒ 正常 tidy 的规则与提示词**一个字节都不变**
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENG = (ROOT / "engine.py").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))


def test_rebuild_ban_exists_and_forbids_keep():
    assert "REBUILD_BAN = (" in ENG, "必须有强制用的附加指令"
    assert "不允许输出 keep" in ENG, "强制时必须**明确禁止 keep**"


def test_normal_tidy_gets_no_extra_instruction():
    """★ ② 不强制 ⇒ 一个字节都不变（用户最在意的一条）"""
    assert 'extra=(REBUILD_BAN if rebuild else "")' in ENG, "只有 rebuild 才追加禁令"
    # 正常路径绝不带禁令
    assert 'extra=REBUILD_BAN' not in ENG, "不许无条件追加 ✗"
    # 正常 tidy 的提示词原文仍在（未被改动）
    assert "keep=继续常驻" in ENG and "只有必须每轮在场的约束才 keep" in ENG, \
        "原有 tidy 规则必须原样保留 ✗"


def test_bot_rebuild_only_accepts_single_id():
    assert "rebuild_requires_single_id" in MAIN, "bot 侧必须拦'无 ids 的 rebuild'"
    assert "tidy_rebuild_bot_enabled" in MAIN, "开关设置必须真被引用"
    assert "tidy_rebuild_bot_cooldown_minutes" in MAIN, "冷却设置必须真被引用"
    assert "rebuild_disabled_by_setting" in MAIN and "rebuild_cooldown" in MAIN


def test_jobs_rejects_global_rebuild():
    assert "「完全重新提取」只能针对单条永久记忆" in MAIN, "/jobs 必须拦住全局 rebuild"


def test_settings_present_in_all_four_places():
    """契约 / help / schema / 前端标签 —— 四处都要有（否则判据会红 ✓）"""
    assert "tidy_rebuild_bot_enabled: bool" in (ROOT / "contracts.py").read_text(encoding="utf-8")
    assert "tidy_rebuild_bot_enabled" in (ROOT / "setting_help.py").read_text(encoding="utf-8")
    assert any(
        k == "tidy_rebuild_bot_enabled" for g in SCHEMA.values() if isinstance(g, dict)
        for k in g
    ) or "tidy_rebuild_bot_enabled" in json.dumps(SCHEMA, ensure_ascii=False), "schema 里必须有"
    assert "tidy_rebuild_bot_enabled" in APP, "前端标签里必须有"


def test_frontend_offers_both_modes_with_rebuild_default():
    assert "askReextractMode" in APP, "单条必须有同风格弹窗"
    assert 'value="rebuild" checked' in APP, "默认必须是「完全重新提取」"
    assert 'value="rule"' in APP, "也要能选「按规则」"
    assert 'rebuild: mode === "rebuild"' in APP, "选择要真的传到后端"
    assert "可在永久记忆页面对单条强制重新提取事实" in APP, "全局那行小字（用户原话）"
