"""③ 覆盖判定稳定化 + ⑤ 自我事实召回门槛 + ④ JEV 附加指令（2026-10-10 审查后改动）

背景
- ③ `covering_fact` 原本只有 `LIMIT 400` **没有 ORDER BY** ✗ ⇒ 判据不确定、高价值旧事实可能永远
  不参与"覆盖"判定 ⇒ 重复照样入库 ✗（注意：**不会**造成"删了旧的、新的又记不进 ⇒ 两条都没" ✓
  因为 `covering_fact` 过滤 `deleted=0` ✓，且 `merge_facts` 在 `BEGIN IMMEDIATE` 里发现源被删会
  整组 `raise Conflict` ✓ —— 这两条本测试一并钉死 ✓）
- ⑤ 自我认知事实（`src_user == self_id`）低重要度会挤占常驻版面 ⇒ 加可配门槛 ✓（0=关 ⇒ 旧行为 ✓）
- ④ JEV 提示词原本写死在代码里 ⇒ 支持用户**追加**指令 ✓（只改 instructions 正文 ✓ 不动结构 ✓）
"""

import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 纯模块：用"假包"避免执行 __init__.py（与仓库既有测试一致 ✓）
pkg = types.ModuleType("alife_memz_gate")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_memz_gate", pkg)
retrieval = importlib.import_module("alife_memz_gate.retrieval")
mdecide = importlib.import_module("alife_memz_gate.mdecide")
try:
    storage = importlib.import_module("alife_memz_gate.storage")
except Exception:                     # storage 依赖宿主时跳过它那几条 ✓
    storage = None


# ─────────────────────────── ⑤ 自我事实门槛 ───────────────────────────
FACTS = [
    {"content": "我是个小助手", "src_user": "bot1", "importance": 4, "category": "profile"},
    {"content": "我的名字叫小Z", "src_user": "bot1", "importance": 8, "category": "profile"},
    {"content": "用户喜欢喝咖啡", "src_user": "u1", "importance": 3, "category": "preference"},
]


def test_self_facts_below_threshold_are_dropped():
    out = retrieval.self_only_last(FACTS, "bot1", 6)
    texts = [f["content"] for f in out]
    assert "我是个小助手" not in texts, "自我事实 + 重要度 4 ≤ 6 ⇒ 不该参与召回 ✗"
    assert "我的名字叫小Z" in texts, "自我事实 + 重要度 8 > 6 ⇒ 必须保留 ✓"


def test_others_facts_are_never_touched():
    """反向验证：**别人**的事，无论重要度多低都不受门槛影响 ✓（防误伤 ✓）"""
    out = retrieval.self_only_last(FACTS, "bot1", 6)
    assert "用户喜欢喝咖啡" in out and out[-1]["content"] != "用户喜欢喝咖啡" or True
    assert any(f["content"] == "用户喜欢喝咖啡" for f in out)


def test_self_facts_still_sorted_last():
    """旧语义（自述降序）必须保留 ✓"""
    out = retrieval.self_only_last(FACTS, "bot1", 0)
    assert out[-1]["content"] == "我的名字叫小Z"


def test_zero_threshold_is_identical_to_old_behaviour():
    """门槛 0 = 关闭 ⇒ 与旧实现输出**完全一致** ✓（老用户零变化 ✓）"""
    old = []
    others, selves = [], []
    for f in FACTS:
        (selves if f.get("src_user") == "bot1" else others).append(f)
    old = others + selves
    assert retrieval.self_only_last(FACTS, "bot1", 0) == old


def test_pack_facts_threads_the_threshold():
    """接线：pack_facts 必须把门槛透传给 self_only_last ✓（否则设置是死的 ✗）"""
    out = retrieval.pack_facts(list(FACTS), "s1", view=retrieval.FACT_VIEW_FLAT,
                               self_id="bot1", self_drop_max=6)
    assert "我是个小助手" not in json.dumps(out, ensure_ascii=False)
    assert "我的名字叫小Z" in json.dumps(out, ensure_ascii=False)


# ─────────────────────────── ③ 覆盖判定 ───────────────────────────
def _mk_fact(store, fid, sid, content, importance, deleted=0):
    with store.connect() as db:
        db.execute(
            "INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,relations,"
            "sources,fingerprint,deleted,revision,audited,importance,merge_pending,created,"
            "rewrite_pending) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, sid, "profile", "u1", content, "t", "", "[]", "[]", "[]", "fp", deleted, 1,
             0.0, importance, 0, 1000.0, 0),
        )


@pytest.mark.skipif(storage is None, reason="storage 需要宿主框架")
def test_covering_fact_ignores_deleted(tmp_path):
    store = storage.Store(tmp_path / "d.db")
    store.initialize()
    _mk_fact(store, "f1", "s1", "用户喜欢在早上喝一杯拿铁咖啡", 6)
    assert store.covering_fact("s1", "用户喜欢在早上喝一杯拿铁咖啡") is not None, "未删时应拦住 ✓"
    with store.connect() as db:
        db.execute("UPDATE facts SET deleted=1 WHERE id='f1'")
    assert store.covering_fact("s1", "用户喜欢在早上喝一杯拿铁咖啡") is None, (
        "★ 已删的行**不该**再拦住新记忆 ✗（否则就是用户担心的「旧没了、新进不来」✗）")


@pytest.mark.skipif(storage is None, reason="storage 需要宿主框架")
def test_covering_fact_respects_limit_and_order(tmp_path):
    """limit 生效 + **按重要度排序**（高重要度先扫 ✓）—— 这条钉死 ORDER BY 的存在 ✓"""
    store = storage.Store(tmp_path / "d2.db")
    store.initialize()
    _mk_fact(store, "low", "s1", "用户喜欢在早上喝一杯拿铁咖啡", 2)
    _mk_fact(store, "high", "s1", "完全无关的一条高重要度事实内容", 9)
    hit = store.covering_fact("s1", "用户喜欢在早上喝一杯拿铁咖啡", threshold=0.4, limit=1)
    assert hit is None, "limit=1 时只扫到重要度 9 那条 ⇒ 说明确实按重要度排序了 ✓"
    hit2 = store.covering_fact("s1", "用户喜欢在早上喝一杯拿铁咖啡", threshold=0.4, limit=200)
    assert hit2 and hit2["fact"]["id"] == "low", "放开 limit ⇒ 命中低重要度但高相似的 ✓"


# ─────────────────────── ④ JEV 附加指令（提示词可编辑）───────────────────────
PAYLOAD = {"q": {"type": "noul", "instructions": "原有正文",
                 "criteria": {"true": "是", "false": "否"}}}


def test_empty_append_is_a_noop():
    """留空 ⇒ **同一个对象**返回 ✓ ⇒ 与旧行为逐字节一致 ✓"""
    assert mdecide._with_prompt_append(PAYLOAD, "") is PAYLOAD
    assert mdecide._with_prompt_append(PAYLOAD, "   ") is PAYLOAD


def test_append_reaches_instructions_without_touching_structure():
    out = mdecide._with_prompt_append(PAYLOAD, "只认铁律，别自作主张")
    assert "原有正文" in out["q"]["instructions"]
    assert out["q"]["instructions"].rstrip().endswith("只认铁律，别自作主张")
    assert out["q"]["type"] == "noul" and out["q"]["criteria"] == {"true": "是", "false": "否"}, (
        "★ 结构（type/criteria）绝不能被用户文本碰到 ✗")


def test_append_works_on_real_call_path():
    """真实链路：JevClient.call() ⇒ 送出的 questions 里必须带上用户附加指令 ✓"""
    client = mdecide.JevClient("http://127.0.0.1:9", "k", "m", 0.5, prompt_append="只认铁律")
    seen = {}

    def spy(payload, timeout=None):        # 真实签名：_post_sync(payload, timeout) ✓
        seen["q"] = payload
        return {"answers": {"bad_q": 0.1}}

    client._post_sync = spy
    asyncio.run(client.call("recall", {"q": dict(PAYLOAD["q"])}))
    blob = json.dumps(seen.get("q") or {}, ensure_ascii=False)
    assert "只认铁律" in blob, "附加指令没进真实请求 ✗"
    assert '"criteria"' in blob, "结构丢了 ✗"


def test_jev_config_accepts_prompt_append():
    cfg = mdecide.JevConfig(enabled=True, api_key="k", prompt_append="甲乙丙")
    assert cfg.prompt_append == "甲乙丙"
    assert cfg.client().prompt_append == "甲乙丙", "client() 必须把附加指令带过去 ✓"


# ─────────────────────────── 结构与前端接线 ───────────────────────────
def test_schema_and_readme_and_panel_are_in_sync():
    fields = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))["alife"]["fields"]
    for key, default in (("jev_prompt_append", ""), ("self_fact_max_importance", 6),
                         ("cover_check_limit", 800)):
        assert key in fields, "schema 缺 %s ✗" % key
        assert fields[key]["default"] == default, "%s 默认值应为 %r ✗" % (key, default)
        assert fields[key].get("name"), "%s 缺显示名 ✗（框架字段需要 name ✓）" % key
        assert "`%s`" % key in (ROOT / "README.md").read_text(encoding="utf-8"), (
            "README 缺 %s 的配置说明 ✗（仓库有覆盖性测试 ✓）" % key)
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'key.endsWith("_prompt_append")' in js, "前端没把附加指令渲染成多行文本框 ✗"
