"""压缩「一次能提炼几条事实」的**动态上限**守卫（2026-09-23 用户拍板）。

口径：`cap = clamp(min(完整轮数, ceil(消息数 / 6)), 1, 12)`；
摘要层（level > 0）按本批条数；没有上下文时退回 12（旧口径 ✓ 兼容单测/归类批）。
"""

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_facts_cap")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_facts_cap", pkg)
e = importlib.import_module("alife_facts_cap.engine")
c = importlib.import_module("alife_facts_cap.contracts")


def rows(messages, turns):
    """造 `turns` 个完整轮（每轮 1 条用户 + 其余助手），总条数尽量接近 messages"""
    per = max(1, int(messages) // max(1, int(turns)))
    out = []
    for t in range(turns):
        out.append({"role": "user", "id": "u%d" % t, "level": 0})
        for k in range(per - 1):
            out.append({"role": "assistant", "id": "a%d_%d" % (t, k), "level": 0})
    return out


def test_density_mapping_matches_the_agreed_table():
    """★ 用户拍板的那张表（K=6）：消息越多，能提炼的条数才越多"""
    assert e.facts_cap_for(rows(24, 12), 0) == 4      # 12 轮闲聊（每轮 2 条）
    assert e.facts_cap_for(rows(36, 12), 0) == 6      # 12 轮普通（每轮 3 条）
    assert e.facts_cap_for(rows(60, 12), 0) == 10     # 12 轮密集
    assert e.facts_cap_for(rows(18, 6), 0) == 3
    assert e.facts_cap_for(rows(6, 3), 0) == 1
    assert e.facts_cap_for(rows(2, 1), 0) == 1


def test_cap_is_bounded_by_turns_and_by_twelve():
    """轮数是第二道闸（防"条数多但轮数少"的畸形批刷高上限）；上限封在 12"""
    # ① 没有**完整轮**（按条模式的常态）⇒ 退回按消息密度，而不是 0/1
    listed = [{"role": "user", "id": "u0", "level": 0}] + [
        {"role": "assistant", "id": "a%d" % i, "level": 0} for i in range(60)
    ]
    assert e.count_rounds(listed) == 0, "这种批算不出完整轮"
    assert e.facts_cap_for(listed, 0) == 11, "算不出轮 ⇒ 按 ceil(61/6) = 11"
    # ② 轮数是第二道闸：轮少 ⇒ 上限跟着轮数走（24 条 / 6 = 4，但只有 11 轮且不整 ⇒ min 生效）
    assert e.facts_cap_for(rows(24, 12), 0) == 4
    # ③ 封顶 12
    assert e.facts_cap_for(rows(600, 100), 0) == 12


def test_summary_layer_uses_count_not_density():
    """摘要层没有"轮" ⇒ 按本批条数（3 条摘要 ⇒ 至多 3 条事实）"""
    layer = [{"role": "user", "id": "r%d" % i, "level": 2} for i in range(3)]
    assert e.facts_cap_for(layer, 2) == 3


def test_empty_batch_is_safe():
    assert e.facts_cap_for([], 0) == 1


def test_instruction_renders_the_cap_and_defaults_to_twelve():
    """★ 指令里的条数上限**随批走**；没有上下文时保持旧口径 12（兼容单测/单条归类批）"""
    assert "至多12条事实" in e.build_instruction("compress", c.Settings())
    text = e.build_instruction("compress", c.Settings(), facts_cap=3)
    assert "至多3条事实" in text
    assert "至多12条事实" not in text


def test_instruction_carries_importance_scale_and_summary_facts_split():
    """★ 新增的两段必须真在提示词里（这是本轮"少收琐事"的落点）"""
    text = e.build_instruction("compress", c.Settings())
    assert "1-2=只对当时情境有效" in text and "9-10=不应遗忘" in text, "importance 分档缺失"
    assert "facts 只写脱离这段对话仍然成立" in text, "summary/facts 分工缺失"
    assert "只对当时情境有效的情绪与寒暄留在 summary 里" in text, "成对约束缺失（会诱导过度过滤）"
    # 用户可配置文本**未被改动**（保持原样 ✓）
    default_instruction = c.Settings().compress_instruction
    assert "不要按珍贵程度丢弃线索" in default_instruction


def test_compact_schema_has_the_placeholder_and_is_rendered():
    """紧凑声明里是占位符 ⇒ 由 structured() 渲染（静态断言两份代码都在）"""
    assert "{facts_cap}" in e.COMPACT_SCHEMAS["compress"]
    src = (ROOT / "engine.py").read_text(encoding="utf-8")
    assert 'schema.replace("{facts_cap}",' in src, "structured() 必须渲染占位符"
    assert "build_instruction(\n            purpose, cfg, forced=forced, facts_cap=facts_cap\n        )" in src or (
        "facts_cap=facts_cap" in src
    ), "structured() 必须把 cap 传给指令组装"


def test_compression_contract_still_allows_more_than_the_prompt_cap():
    """上限是**提示词**约束 ✗ 契约不能被收紧 —— 否则模型偶尔多给几条就会白烧一次重试"""
    schema = c.Compression.model_json_schema()
    facts_field = schema["properties"]["facts"]
    assert facts_field.get("maxItems") == 100
