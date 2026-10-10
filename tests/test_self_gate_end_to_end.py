"""⑤ 自我认知门槛 **端到端**：从"来源记录"到"真被挡下"的完整链路 ✓

为什么必须有这个测试（2026-10-10 实测发现的坑 ✗→✓）
- `src_user` **不是数据库列** ✗ —— 它是 `storage.attach_evidence()` 在**读取时**
  从事实的 `sources`（来源记录）里算出来的（`records.users[0]` = 说话人 ✓）
- 所以任何"拿裸查询结果直接过滤自我事实"的代码都是**静默空转** ✗
  （看着接了、其实 `src_user` 是 None ⇒ 永不触发 ✗）
- 本轮就抓到过一处：**轮换池**（独立 SQL ✗ 不过 pack_facts ✗）⇒ 已在过滤前补
  `attach_evidence` ✓（main.py 轮换槽那段）
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get("KIRA_CORE"):
    pytest.skip("需要 KIRA_CORE 的宿主集成用例", allow_module_level=True)
if os.environ["KIRA_CORE"] not in sys.path:
    sys.path.insert(0, os.environ["KIRA_CORE"])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import importlib
import json
import time
import types

pkg = types.ModuleType("alife_selfgate")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_selfgate", pkg)
m = importlib.import_module("alife_selfgate.main")
storage = importlib.import_module("alife_selfgate.storage")
retrieval = importlib.import_module("alife_selfgate.retrieval")

SID = "qq:gm:e2e"
BOT = "bot-self-id"
run = asyncio.run


def _plugin(tmp_path):
    m.get_data_path = lambda: tmp_path
    ctx = types.SimpleNamespace(
        get_plugin_data_dir=lambda: tmp_path,
        plugin_mgr=types.SimpleNamespace(plugin_configs={}),
    )
    p = m.AlifeMemoryPlugin(ctx, {"alife": {"probability": 0.0, "audit_enabled": False}})
    run(p.initialize())
    return p, p.store


def _seed(store, content, importance, speakers):
    """造一条**真记录** + 一条指向它的事实（和真实捕获路径同形 ✓）。"""
    rid = "rec-" + str(abs(hash(content)) % 10**8)
    now = time.time()
    with store.connect() as db:
        db.execute(
            "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
            "position,created,search_body) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            # ★ role 要与说话人一致：bot 发言 = assistant ✓（否则 assistant_user_id 推断不出来 ✗）
            (rid, SID, "assistant" if speakers and speakers[0] == BOT else "user",
             "L0", now, now, content, content,
             json.dumps(speakers), 0, now, content),
        )
        db.execute(
            "INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,"
            "relations,sources,fingerprint,importance,created,revision,deleted,"
            "merge_pending,audited) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f-" + rid, SID, "state", "x", content, "", "", "[]", "[]",
             json.dumps([rid]), "fp-" + rid, importance, now, 1, 0, 0, 0),
        )
    return rid


def txt_of(x):
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)


def test_src_user_comes_from_source_record(tmp_path):
    """① attach_evidence 能把「来源记录的说话人」算成 src_user ✓"""
    p, store = _plugin(tmp_path)
    _seed(store, "我说话有点啰嗦", 3, [BOT])
    rows = [dict(r) for r in run(store.call("facts", sid=SID, limit=50))]
    assert rows, "事实没写进去 ✗"
    assert rows[0].get("src_user") is None, "裸查询不该有 src_user（它是读时算的 ✓）"
    rows = run(store.call("attach_evidence", rows))
    assert rows[0]["src_user"] == BOT, "attach_evidence 没算出发言人 ✗"


def test_gate_fires_only_after_evidence(tmp_path):
    """② 补了 src_user ⇒ 门槛真触发；不补 ⇒ 静默不触发（本轮修的就是它 ✗）"""
    p, store = _plugin(tmp_path)
    _seed(store, "我说话有点啰嗦", 3, [BOT])
    _seed(store, "萤火喜欢甜口蛋糕", 8, ["u1"])
    raw = [dict(r) for r in run(store.call("facts", sid=SID, limit=50))]
    assert all(retrieval.low_value_self(r, BOT, 6) is False for r in raw), "裸行不该被判自我（src_user 还没算 ✓）"
    ev = run(store.call("attach_evidence", raw))
    dropped = [r for r in ev if retrieval.low_value_self(r, BOT, 6)]
    assert len(dropped) == 1 and dropped[0]["content"].startswith("我说话"), "门槛算错了 ✗"
    packed = retrieval.pack_facts(ev, current_sid=SID, self_id=BOT, self_drop_max=6)
    txt = txt_of(packed)
    assert "啰嗦" not in txt and "甜口蛋糕" in txt, "打包时门槛没生效 ✗"
    assert "啰嗦" in txt_of(retrieval.pack_facts(ev, current_sid=SID, self_id=BOT, self_drop_max=0)), \
        "门槛=0 时应与旧行为一致（不挡）✗"


def test_rotation_pool_attaches_before_filtering():
    """③ 结构判据：轮换池必须在过滤**之前** attach ✗→✓（否则静默空转 ✗）"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    i_attach = src.index('fact_pool = await self.store.call("attach_evidence", fact_pool)')
    i_filter = src.index("and not low_value_self(")
    assert i_attach < i_filter, "轮换池没在过滤前 attach ⇒ 门槛会静默空转 ✗"


def test_panel_marks_self_and_filtered_like_recall(tmp_path):
    """★ 面板上的「自我 / 不参与召回」标记必须与**召回判据完全一致** ✓

    用户问题（2026-10-11）："这几条香香的是不是都被门槛挡掉了？"
    ⇒ 光靠解释不够 ✓ 面板上要**一眼可见** ✓ 且判据不能是另写一份 ✗
    """
    p, store = _plugin(tmp_path)
    _seed(store, "我说话有点啰嗦，要收敛", 3, [BOT])       # 助手自述 + 低重要度 ⇒ 应被挡
    _seed(store, "哥要求助手用音乐卡片推送", 5, ["u1"])   # 用户说的 ⇒ 不是自我事实 ⇒ 不挡
    _seed(store, "助手有自己的名字", 8, [BOT])            # 助手自述但 ≥7 ⇒ 不挡
    rows = run(p.api_facts(sid=SID))
    assert len(rows) == 3, "应能取到刚造的 3 条事实 ⇒ 实际 %d 条" % len(rows)

    def verdict(fragment):
        hit = next((r for r in rows if fragment in (r.get("content") or "")), None)
        assert hit is not None, "没找到含 %r 的事实（实际：%r）" % (fragment, [r.get("content") for r in rows])
        return (hit.get("self_fact"), hit.get("recall_filtered"))

    assert verdict("啰嗦") == (True, True), "助手自述 + 低重要度 ⇒ 自我 且 不参与召回 ✗"
    assert verdict("音乐卡片") == (False, False), "用户说的 ⇒ 不是自我事实，不该被挡 ✗"
    assert verdict("自己的名字") == (True, False), "助手自述但重要度 ≥7 ⇒ 保留 ✗"
    # 面板标记与召回判据**同源**（同一个 low_value_self）✓
    for r in rows:
        assert bool(r["recall_filtered"]) == bool(
            retrieval.low_value_self(r, BOT, 6)), "面板与召回判据不一致 ✗"


def test_assistant_user_id_infers_bot_when_no_event(tmp_path):
    """面板请求没有事件 ⇒ 要从"助手发言记录"里推断 bot 自己的 id ✓（推断不出就空 ✓）"""
    p, store = _plugin(tmp_path)
    assert run(store.call("assistant_user_id")) == "", "没有助手记录时应返回空 ✗"
    _seed(store, "助手说过一句话", 3, [BOT])
    assert run(store.call("assistant_user_id")) == BOT, "应从助手发言里推断出 bot 的 id ✗"
