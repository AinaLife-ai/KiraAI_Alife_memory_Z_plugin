"""v2.8.0：模型侧入参瘦身（短码 / 可读时间 / 名字随行 / 省略默认值）。"""

import importlib
import json
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_diet280")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_diet280", package)
r = importlib.import_module("alife_diet280.retrieval")
e = importlib.import_module("alife_diet280.engine")


def test_named_pair_keeps_id_first():
    """`ID(名字)` 里 ID 在前：模型照抄 subject 时拿到的是稳定 ID。"""
    assert r.named_pair("qq:769690776", "周武") == "qq:769690776(周武)"
    assert r.named_pair("qq:769690776", "") == "qq:769690776"
    assert r.named_pair("qq:769690776", "qq:769690776") == "qq:769690776"
    assert r.bare_id("qq:769690776(周武)") == "qq:769690776"
    assert r.bare_id("qq:769690776") == "qq:769690776"


def test_full_time_is_readable_and_keeps_year():
    stamp = time.mktime(time.strptime("2026-03-08 02:36", "%Y-%m-%d %H:%M"))
    assert r.full_time(stamp) == "2026-03-08 02:36"
    assert r.full_time(0) == ""


def test_category_codes_cover_every_category():
    from alife_diet280.contracts import Settings

    schema_categories = {
        "event", "fact", "preference", "commitment",
        "relationship", "profile", "resource", "self",
    }
    assert set(r.CATEGORY_CODES) == schema_categories
    assert all(len(code) == 2 for code in r.CATEGORY_CODES.values())
    assert Settings  # 契约可导入（类别集合来自它）


def test_compress_records_are_compact_and_named():
    rows = [
        {"id": "a" * 32, "role": "user", "level": 0, "users": ["qq:769690776"],
         "summary": "<msg><text>翅 膀被人打了</text></msg>", "start": 1772908601.0,
         "end": 1772908601.0},
        {"id": "b" * 32, "role": "assistant", "level": 1, "users": [],
         "summary": "摘要", "start": 1772908601.0, "end": 1772908700.0},
    ]
    records = e.compress_records(rows, None, {"qq:769690776": "周武"}, ())
    first, second = records
    assert first["s"] == "翅膀被人打了"          # 剥包裹 + 折行空格合并
    assert "role" not in first and "bot" not in first
    assert second["bot"] == 1                    # assistant 用一位标记
    assert first["u"] == ["qq:769690776"]  # 只写 ID（名字在顶层 names 表，省 token）
    assert first["t"] == "2026-03-08 02:36"      # 可读时间（本地）
    assert second["t2"] and second["t2"] != ""   # 多层存档保留起止两点
    assert "summary" not in first and "start" not in first


def test_restore_audit_ids_accepts_alias_and_real_id():
    aliases = {"f1": "real-1", "f2": "real-2"}
    out = e.restore_audit_ids(
        {"actions": [{"target_id": "f1", "source_ids": ["f2", "real-2"]}]}, aliases
    )
    assert out["actions"][0]["target_id"] == "real-1"
    assert out["actions"][0]["source_ids"] == ["real-2", "real-2"]
    try:
        e.restore_audit_ids({"actions": [{"target_id": "zz", "source_ids": []}]}, aliases)
    except ValueError as exc:
        assert "unknown audit target" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知 id 必须抛错，交给重试路径")


def test_restore_group_ids_for_merges():
    aliases = {"g1-1": "real-1", "g1-2": "real-2"}
    out = e.restore_group_ids(
        {"groups": [{"target_id": "g1-1", "source_ids": ["g1-1", "g1-2"]}]}, aliases
    )
    assert out["groups"][0]["target_id"] == "real-1"
    assert out["groups"][0]["source_ids"] == ["real-1", "real-2"]
    single = e.restore_group_ids({"source_ids": ["d1", "d2"]}, {"d1": "r1", "d2": "r2"})
    assert single["source_ids"] == ["r1", "r2"]


def test_compress_payload_carries_names_map():
    """名字只在顶层给一次（ID→名字），记录里只用 ID —— 少重复、不丢信息。"""
    src = (Path(__file__).resolve().parents[1] / "engine.py").read_text(encoding="utf-8")
    assert '"names": {' in src, "压缩 payload 顶层要带 names 表"
    assert "names 表（ID→名字）" in src or "names 表" in src, "COMMON 要说明 u 是 ID、名字看 names"


def test_instructions_carry_the_new_limits():
    text = e.COMMON_INSTRUCTION + e.build_instruction("compress", _settings())
    assert "summary 不超过 300 字" in text
    assert "scenario 不超过 20 字" in text
    assert "content 不超过 60 字" in text
    assert "records[].s 是这段对话的原文" in text
    assert "records[].u 是**可见范围**" in text, "必须说明 u 是可见范围（旧说明写成「实体 ID 列表」曾导致主体错记 ✗）"
    assert "sp 是**说话人的实体 ID**" in text, "必须说明 sp 是说话人 ID（压缩要据此定主体）"
    audit = e.AUDIT_INSTRUCTION
    assert "facts[].sources" not in audit, "sources 已不入参，指令不该再提它"


def test_fact_view_uses_codes_and_drops_defaults():
    fact = {
        "id": "x", "category": "relationship", "subject": "qq:1", "content": "是师傅",
        "importance": 5, "src": "rec-1", "created": 1772908601.0, "relations": [],
    }
    view = r.bot_facts([fact], "sess", short=lambda value: "ab12cd")[0]
    assert view == {"c": "re", "u": "qq:1", "x": "是师傅", "src": "ab12cd", "t": "03-08"}
    # 短码映射里没有的值退回原值，绝不输出 null
    other = r.bot_facts([fact], "sess", short=lambda value: None)[0]
    assert other["src"] == "rec-1"
    # 非默认重要度才输出 imp
    heavy = dict(fact, importance=9)
    assert r.bot_facts([heavy], "sess", short=lambda v: "x")[0]["imp"] == 9


def _settings():
    c = importlib.import_module("alife_diet280.contracts")
    return c.Settings()


@pytest.mark.asyncio
async def test_model_payloads_carry_no_raw_ids_or_float_times(tmp_path):
    """完整审计：发给模型的每一处入参，都不该出现原始长 id 与浮点时间戳。"""
    s = importlib.import_module("alife_diet280.storage")
    c = importlib.import_module("alife_diet280.contracts")
    store = s.Store(tmp_path / "db")
    store.initialize()
    for i in range(4):
        store.capture(
            "qq:gm:1", f"turn{i}",
            [{"role": "user" if i % 2 == 0 else "assistant",
              "content": f"<msg><text>第{i}句 话里有折行</text></msg>",
              "time": 1772908600.0 + i, "users": ["qq:769690776"]}],
        )
    store.observe_name("qq:769690776", "周武", observed=1.0)
    seen = []

    async def model(_, purpose, instruction, schema, payload):
        seen.append((purpose, payload))
        return c.dump({"summary": "摘要", "facts": []})

    cfg = c.Settings(probability=1.0, threshold=4, batch_size=2, model_retries=0)
    await e.Engine(store, lambda: cfg, model, None, None).compress("qq:gm:1")
    assert seen, "压缩必须调用模型"
    for purpose, payload in seen:
        blob = json.dumps(payload, ensure_ascii=False)
        assert "<msg" not in blob, f"{purpose} 入参仍有 XML 包裹"
        # 真实记录 id 是 32 位以上；入参里只应有 r1..rN 这类短别名
        ids = [r["id"] for r in payload.get("records", [])]
        assert all(len(value) <= 8 for value in ids), f"{purpose} 入参用了真实 id"
        # 时间必须是可读字符串，不能再是 epoch 浮点
        for record in payload.get("records", []):
            for key in ("t", "t2"):
                if key in record:
                    assert isinstance(record[key], str) and "-" in record[key]


def test_compress_payload_speaker_candidates():
    """第 1 项：说话人**知道是谁但定位不到唯一账号**时给候选 sp_c（重名/多人场景）

    四条保证（正确性）：
      ① 能唯一确定时仍然给 sp，**行为不变** ✓
      ② 候选只取**原文里真实出现过**的名字（不得凭空造人）✓
      ③ 原文里一个名字都没有 → **不给** sp_c（宁可漏，不许猜）✓
      ④ 候选可逐字符在该记录文本里定位 ✓
    """
    base = {"id": "r1", "summary": "小明说他周末去了杭州", "content": "", "u": ["qq:1", "qq:2"],
            "users": ["qq:1", "qq:2"], "speaker": "小明", "role": "user", "level": 0,
            "type": "chat", "start": 1, "end": 2}
    dup = {"qq:1": "小明", "qq:2": "小明"}

    rec = e.compress_records([base], None, dup)[0]
    assert "sp" not in rec, "重名时不许猜出单一说话人"
    assert rec["sp_c"] == ["小明"], "重名时应给候选（原文里出现过的小明）"
    assert all(name in (base["summary"] + base["content"]) for name in rec["sp_c"]), "候选必须能在原文里定位"

    # ① 唯一可确定 → 行为与以前完全一致（给 sp，不给 sp_c）
    single = e.compress_records([dict(base, speaker="阿澄")], None, {"qq:1": "小明", "qq:2": "阿澄"})[0]
    assert single["sp"] == "qq:2" and "sp_c" not in single, "能唯一确定时行为必须不变"

    # ③ 原文里没有任何已知名字 → 不给候选
    stranger = e.compress_records([dict(base, speaker="小明", summary="他说周末去了杭州")],
                                  None, {"qq:1": "小明", "qq:2": "小明"})[0]
    assert "sp_c" not in stranger, "名字没在原文出现就不许给候选（不许猜）"

    """压缩载荷必须带 sp = 说话人的**实体 ID**：A 说的话不能被记到 B 名下（线上事故 ✗）"""
def test_compress_payload_carries_speaker_id():
    """压缩载荷必须带 sp = 说话人的**实体 ID**：A 说的话不能被记到 B 名下（线上事故 ✗）"""
    row = {"id": "r1", "summary": "小明说他周末去了杭州", "content": "", "users": ["qq:1", "qq:2"],
           "speaker": "小明", "role": "user", "level": 0, "type": "chat", "start": 1, "end": 2}
    rec = e.compress_records([row], None, {"qq:1": "小明", "qq:2": "阿澄"})[0]
    assert rec["sp"] == "qq:1", "sp 必须是说话人的 ID（不是名字、也不是从可见范围里随便挑 ✗）"
    assert rec["u"] == ["qq:1", "qq:2"], "u 仍然只是可见范围"
    # 两人重名 / 判定不出 → 宁缺勿错 ✗（不许瞎猜）
    dup = e.compress_records([dict(row, speaker="阿澄")], None, {"qq:1": "小明", "qq:2": "小明"})[0]
    assert "sp" not in dup, "说话人无法唯一确定时必须不给 sp（瞎猜就是这次的 bug ✗）"


def test_audit_can_fix_attribution():
    """审计必须**有工具**纠正归属（把 A 的话记到 B 名下是线上事故 ✗）:

    ① 契约允许 correct 带 subject ✓  ② 不带 subject 仍然照旧（向后兼容 ✓）
    ③ 应用处只对 correct 生效、只接受已知实体、拒绝时计数不写垃圾 ✓  ④ 提示词说明了这件事 ✓
    """
    c = importlib.import_module("alife_diet280.contracts")
    a = c.AuditAction(action="correct", target_id="f1", source_ids=["f1"],
                                content="x", reason="y")
    assert a.subject is None, "不带 subject 必须仍然可用（老输出兼容 ✓）"
    b = c.AuditAction(action="correct", target_id="f1", source_ids=["f1"],
                                content="x", reason="y", subject="qq:1")
    assert b.subject == "qq:1"
    root = ROOT
    src = (root / "storage.py").read_text(encoding="utf-8")
    assert 'a["action"] == "correct" else ""' in src, "只对 correct 生效 ✗"
    assert "UPDATE facts SET subject=?" in src, "必须真的能改主体"
    assert "subject_rejected" in src, "拿不准时必须拒绝并计数（不许写垃圾 ✗）"
    assert "重名撞车时**必须拒绝**" in src, "重名时必须拒绝（任取一个就是制造新错记 ✗）"
    assert "subject_fixed" in src
    eng = (root / "engine.py").read_text(encoding="utf-8")
    assert "subject 填成正确的人名" in eng, "提示词必须告诉审计它能改归属"


def test_audit_evidence_carries_speaker():
    """审计要能核对归属，证据里必须有「原文是谁说的」✗
    否则它看得出"这条归给谁"，却看不出"原文是谁说的" → 只能猜（比不改更糟 ✓）"""
    src = (ROOT / "engine.py").read_text(encoding="utf-8")
    assert 'additions[source]["sp"] = speaker' in src, "审计证据必须带说话人显示名"
    assert "evidence[].sp 是原文**说话人显示名**" in src, "提示词必须说明核对依据"
    assert "没有 sp 或看不出是谁说的就别改主体" in src, "必须禁止瞎猜 ✗"


def _prompt_texts():
    import re
    src = ROOT
    out = {}
    for name in ("engine.py", "main.py"):
        text = (src / name).read_text(encoding="utf-8")
        for m in re.finditer(r'([A-Z_]{4,})\s*=\s*\((.*?)\n\)', text, re.S):
            body = "".join(re.findall(r'"([^"]*)"', m.group(2)))
            if len(body) >= 150:
                out[m.group(1)] = body
    return out


def test_instruction_sentences_are_not_glued():
    """防「两句话粘一起」✗ 真实事故：追加规则时新内容被粘进 retract 那句的中间，
    模型读成「retract 是用来改主体的」。机器怎么发现？一条经验规则：
    **同一句里不该出现两个「：」**（那通常就是两条规则粘一起了）"""
    for name, text in _prompt_texts().items():
        for seg in text.split("。"):
            assert seg.count("：") <= 1, "%s 疑似两句话粘一起：%s。" % (name, seg[:60])


def test_prompt_budget_is_enforced():
    """预算守卫 ✓：以后想往指令里加内容，必须先删再写（否则 token 只会一直涨 ✗）"""
    texts = _prompt_texts()
    assert len(texts["COMMON_INSTRUCTION"]) <= 620, "COMMON 超预算 %d" % len(texts["COMMON_INSTRUCTION"])
    assert len(texts["AUDIT_INSTRUCTION"]) <= 560, "AUDIT 超预算 %d" % len(texts["AUDIT_INSTRUCTION"])
    assert len(texts["MEMORY_RULES"]) <= 560, "MEMORY_RULES 超预算 %d" % len(texts["MEMORY_RULES"])


def test_audit_judges_on_the_same_text_as_compression():
    """审计必须看**压缩当时依据的那份文本**（summary）✗

    真实事故：压缩依据 records[].summary 生成事实，审计却拿 content 断案 →
    「证据无 X」→ 把**是对的**事实改坏（肖洋/紫酱 那条 ✓）
    """
    src = (ROOT / "engine.py").read_text(encoding="utf-8")
    assert 'k: row[k] for k in ("id", "content", "summary", "start", "end")' in src, \
        "审计证据必须包含 summary（与压缩同一份文本 ✗）"
    assert 'row["summary"], keep' in src, "压缩应当仍在看 summary（若改了，两边要一起改 ✓）"


def test_audit_must_not_treat_silence_as_contradiction():
    """审计不得把「证据里没提到」当成「事实错误」✗

    真实事故（2026-09-13）：Shana 说"紫酱...还挺 cute"，压缩抽对了，
    审计却以"证据无紫酱"为由把它改成另一句 → **对的事实被改坏** ✓
    只有证据与事实**矛盾**才可以 correct；看不到就 keep ✓
    """
    src = (ROOT / "engine.py").read_text(encoding="utf-8")
    assert "没提到" in src and "事实错误" in src, "缺少「沉默不等于矛盾」规则 ✗"
    assert "只有证据与事实**矛盾**才 correct" in src, "必须只在矛盾时才改"
    assert "看不到就当 keep" in src, "看不到必须保持不动"


def test_widen_source_ids_behavior():
    """B 的**行为测试**（不是文本守卫）：模型只挂一条来源 → 必须补上真正含该内容的那条

    真实事故：内容在 B 记录（"紫酱 cute"），来源只挂了 A → 审计报「证据无 X」→ 把对的改坏
    四条性质：① 该补的补 ② 已覆盖的不动 ③ 无关键词不乱补 ④ 同批次外不许补
    """
    storage = importlib.import_module("alife_diet280.storage")
    a = {"id": "rA", "summary": "So cheesy 听起来像刚满月的小猫", "content": ""}
    b = {"id": "rB", "summary": "不过叫紫酱听着还挺 cute 的", "content": ""}

    fact = {"content": "Shana 觉得「紫酱」还挺 cute", "source_ids": ["rA"]}
    assert storage.widen_source_ids([fact], [a, b]) == 1
    assert set(fact["source_ids"]) == {"rA", "rB"}, "必须补上真正含该内容的记录"

    covered = {"content": "So cheesy 听起来像刚满月的小猫", "source_ids": ["rA"]}
    assert storage.widen_source_ids([covered], [a, b]) == 0
    assert covered["source_ids"] == ["rA"], "已覆盖就不许动"

    vague = {"content": "嗯", "source_ids": ["rA"]}
    assert storage.widen_source_ids([vague], [a, b]) == 0
    assert vague["source_ids"] == ["rA"], "没有关键词宁可漏补，不许乱补"

    outsider = {"content": "紫酱 cute", "source_ids": ["rA"]}
    assert storage.widen_source_ids([outsider], [a]) == 0
    assert outsider["source_ids"] == ["rA"], "同批次里没有就不许补（不许跨批次乱挂）"


def test_audit_sees_the_same_processed_text():
    """审计看到的必须是**压缩当时那份加工后的文本**（model_text(summary)）

    压缩：model_text(row["summary"])  审计证据：也必须过一遍 model_text ✗
    否则审计会看到压缩从没见过的内容（思考块/被截断的长描述）→ 判断失据
    """
    src = (ROOT / "engine.py").read_text(encoding="utf-8")
    assert "model_text(row[\"summary\"], keep)" in src, "压缩必须仍用 model_text(summary)"
    assert "model_text(str(row[\"summary\"] or \"\"))" in src, "审计证据的 summary 必须过同一加工"
