"""被动注入的两条新规则（2026-09-26，用户拍板）：

① **开 JEV 不许比关着注入更多** ✓（recall_keep_max / recall_budget_mode=strict）
   实测过的问题：候选池 ×3 + 保留线 0.10 太松 ⇒ 52→20、61→27 ✗ 比关着还多
② 跨会话召回**跳过已被摘要代表的折叠原文** ✓（recall_skip_folded，读法A）
   —— 未压缩原文、父行已消失的孤儿折叠行**照旧召回** ✓（唯一副本不能丢）
"""

import importlib
import itertools
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_rbudget")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_rbudget", pkg)
storage = importlib.import_module("alife_rbudget.storage")


def _store():
    s = storage.Store(Path(tempfile.mkdtemp()) / "m.db")
    s.initialize()
    return s


def _msg(text, t=0.0):
    return {"role": "user", "content": text, "time": t, "users": [],
            "speaker": "", "perception": "", "tools": []}


# ── ② 折叠行判定 ─────────────────────────────────────────────────────────────

def test_folded_row_covered_by_live_parent_is_skipped():
    s = _store()
    sid = "qq:gm:OLD"
    s.capture(sid, "t1", [_msg("花生过敏 原始一", 1.0)])
    with s.connect() as db:
        rid = db.execute("SELECT id FROM records WHERE sid=?", (sid,)).fetchone()["id"]
        # 父摘要：活跃、更高层级、跨度覆盖那条子行 ✓
        db.execute(
            "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
            "position,created,search_body,active,deleted,permanent)"
            " VALUES (?,'%s','assistant',1,0,5,'花生过敏 摘要','花生过敏 摘要','[]',9,0,'',1,0,0)"
            % sid, ("p1",))
        db.execute("UPDATE records SET active=0 WHERE id=?", (rid,))
        db.commit()
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM records WHERE sid=?", (sid,)).fetchall()]
    folded = set(s.folded_covered_ids(rows))
    assert rid in folded, "被活跃父摘要覆盖的折叠行应被跳过 ✓"
    assert "p1" not in folded, "活跃行本身不该被跳过 ✓"


def test_orphan_folded_row_is_kept():
    """★ 父行已消失的孤儿折叠行**不能跳** ✗ —— 它是唯一副本 ✓"""
    s = _store()
    sid = "qq:gm:ORPHAN"
    s.capture(sid, "t1", [_msg("花生过敏 孤儿原文", 1.0)])
    with s.connect() as db:
        rid = db.execute("SELECT id FROM records WHERE sid=?", (sid,)).fetchone()["id"]
        db.execute("UPDATE records SET active=0 WHERE id=?", (rid,))
        db.commit()
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM records WHERE sid=?", (sid,)).fetchall()]
    assert rid not in set(s.folded_covered_ids(rows))


def test_uncompressed_raw_is_kept():
    s = _store()
    sid = "qq:gm:RAW"
    s.capture(sid, "t1", [_msg("花生过敏 未压缩原文", 1.0)])
    with s.connect() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM records WHERE sid=?", (sid,)).fetchall()]
    assert s.folded_covered_ids(rows) == []


def test_no_active_parent_means_no_skip():
    """父行存在但**不活跃**（它自己也被压缩了）⇒ 该行不算"被代表" ✓"""
    s = _store()
    sid = "qq:gm:STALE"
    s.capture(sid, "t1", [_msg("花生过敏 原文", 1.0)])
    with s.connect() as db:
        rid = db.execute("SELECT id FROM records WHERE sid=?", (sid,)).fetchone()["id"]
        db.execute(
            "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
            "position,created,search_body,active,deleted,permanent)"
            " VALUES (?,'%s','assistant',1,0,5,'摘要','摘要','[]',9,0,'',0,0,0)" % sid,
            ("p_dead",))
        db.execute("UPDATE records SET active=0 WHERE id=?", (rid,))
        db.commit()
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM records WHERE sid=?", (sid,)).fetchall()]
    assert rid not in set(s.folded_covered_ids(rows)), "父行不活跃 ⇒ 不跳过 ✓"


# ── ① 不变量：开 JEV 注入的事实**不多于**关 JEV ───────────────────────────────
# 桩决策层刻意"全部给高分"（= 完全不清无关 ✗）⇒ 这是最容易放大的最坏情况 ✓
# 旧行为下：候选 ×3 全留 ⇒ 注入 ≈ 关着时的 3 倍 ✗（用户实测 52→20 / 61→27 ✓）

_CORE = os.environ.get("KIRA_CORE")
if _CORE and _CORE not in sys.path:
    sys.path.insert(0, _CORE)


def _boot(tmp, **settings):
    core_ok = True
    try:
        import core  # noqa: F401
    except Exception:                                  # noqa: BLE001
        core_ok = False
    if not core_ok:
        pytest.skip("需要宿主框架（设 KIRA_CORE 指向真框架）")

    sys.path.insert(0, str(ROOT))
    import importlib as _il
    main = _il.import_module("alife_rbudget.main")
    from core.provider import LLMRequest
    from core.agent.message import OpenAIMessage
    from core.prompt_manager import Prompt
    from core.chat import MessageChain
    from core.chat.message_elements import Text
    from core.chat.message_utils import KiraIMMessage, KiraMessageBatchEvent
    from core.chat.session import User, Session

    data_dir = tmp / "data" / "alife_memory"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(settings)
    ctx = types.SimpleNamespace(
        get_plugin_data_dir=lambda: data_dir,
        plugin_mgr=types.SimpleNamespace(plugin_configs={}))
    plugin = main.AlifeMemoryPlugin(ctx, {"alife": {"probability": 0.0, **payload}})
    globals().update({"LLMRequest": LLMRequest, "OpenAIMessage": OpenAIMessage,
                      "Prompt": Prompt, "MessageChain": MessageChain, "Text": Text,
                      "KiraIMMessage": KiraIMMessage,
                      "KiraMessageBatchEvent": KiraMessageBatchEvent,
                      "User": User, "Session": Session})
    return plugin


def _seed(store, sid, texts):
    """种**事实**（facts 表）✓ —— 不是记录（records）✗
    踩过：种成 records ⇒ 事实通道全空 ⇒ budget=0、两端都只数到档案行 ⇒ 假绿 ✗"""
    store.add_facts(sid, [
        {"subject": "用户", "category": "健康", "content": t,
         "importance": 7, "reason": "seed", "scenario": "",
         "relations": [], "tags": [], "source_ids": [],
         "time": "2026-09-%02dT10:00:00" % (i % 28 + 1)}
        for i, t in enumerate(texts)])


async def _inject(plugin, text, sess):
    session = Session(adapter_name="test", session_type="dm", session_id=sess)
    msg = KiraIMMessage(message_id="m", self_id="bot",
                        chain=MessageChain([Text(text)]), timestamp=100,
                        sender=User(user_id="u", nickname="小明"))
    msg.message_str = "[小明] %s" % text
    ev = KiraMessageBatchEvent(messages=[msg], session=session, timestamp=100,
                               message_types=[])
    req = LLMRequest(messages=[OpenAIMessage(role="user", content="原历史")],
                     system_prompt=[Prompt("稳定人格", name="stable")],
                     user_prompt=[Prompt(text, name="message")])
    await plugin.on_request(ev, req)
    req.assemble_prompt()
    return req.messages[-1].content


def _fact_lines(text):
    """粗口径数"事实行"：非空、非表头、不含 `|`（档案行才有竖线 ✓）"""
    out = []
    for line in (text or "").split("\\n"):
        s = line.strip()
        if not s or "=[" in s:
            continue
        if "|" in s or "｜" in s or s.startswith("- "):
            continue          # 档案行（`- U｜…` / `序号|角色|…`）不算事实 ✓
        out.append(s)
    return out


@pytest.mark.asyncio
async def test_jev_on_never_injects_more_facts_than_off(tmp_path):
    main = importlib.import_module("alife_rbudget.main")
    common = dict(enabled=True, recall_scope="global", audit_enabled=False,
                  fact_merge_enabled=False, proactive_enabled=False,
                  permanent_tidy_enabled=False, tidy_rebuild_bot_enabled=False)
    # ★ 必须**多于未放大池的上限**（≈25）才看得出放大 ✗ 否则两边都取满 ⇒ 假绿
    seeds = ["用户花生过敏的永久记录 %03d" % i for i in range(1, 201)]

    # ① JEV 关
    p_off = _boot(tmp_path / "off", **common)
    await p_off.initialize()
    _seed(p_off.store, "qq:gm:seed", seeds)
    off_view = await _inject(p_off, "你还记得我花生过敏的事吗", "s-off")
    n_off = len(_fact_lines(off_view))

    # ② JEV 开 + 桩（全给高分 ⇒ 不清无关 ✗ 最坏情况）
    p_on = _boot(tmp_path / "on", **dict(common, jev_enabled=True, jev_recall=True,
                                    jev_sample=1.0, jev_base_url="http://127.0.0.1:9",
                                    jev_api_key="stub", jev_model_name="jev-latest",
                                    recall_budget_mode="strict"))
    await p_on.initialize()
    _seed(p_on.store, "qq:gm:seed", seeds)
    # ★ 先把决策层建好，再装桩 ✗ 否则 on_request 里的 _ensure_aux() 会重建，
    #   把桩冲掉 ⇒ 测试变成"假绿"（实测踩到：切 loose 也不红 ✓ 说明 JEV 根本没生效）
    p_on._ensure_aux()
    d = p_on.decisions
    assert d is not None
    used = {"n": 0}

    async def _all_high(items, *a, **k):
        used["n"] += 1
        return [(key, 0.9) for key, _text in items]

    d.api_key = "stub"
    d.fails = 0
    d.recall_filter = _all_high
    d.last_trigger = 0.0
    on_view = await _inject(p_on, "你还记得我花生过敏的事吗", "s-on")
    n_on = len(_fact_lines(on_view))

    assert used["n"] >= 1, (
        "桩决策层一次都没被调用 ⇒ JEV 没真正参与，这条断言没意义 ✗（假绿）")
    assert n_on <= n_off, (
        "★ 不变量破了：开 JEV 注入 %d 条 > 关着 %d 条 ✗（池 ×3 + 全留 ⇒ 放大）"
        % (n_on, n_off))
