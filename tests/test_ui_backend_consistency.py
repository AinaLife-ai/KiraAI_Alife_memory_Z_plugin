"""前后端一致性回归：① 模型标签可区分 ② 恢复默认拿得到真默认 ③ 改完立即生效（缓存失效）④ 缓存失效的完整性

由来（2026-10-12 用户实拍报告）：
  1. "同名甚至同提供商名（但不同提供商类型）的模型，会不会无法正常显示和选择，重复了就一个都不能选？"
  2. "点恢复默认不是变回跟随默认模型，而是只显示按钮文案、不正常变回"
  3. "我在前端改动了永久记忆，bot 能即时查到吗？下轮对话注入是不是也就更新了？"

本文件把这些"看不见的约定"钉成断言 ✓ —— 都属于**手点不出来**、但用户一定会踩的那类问题 ✗
"""
import asyncio
import importlib
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get("KIRA_CORE"):
    pytest.skip("需要 KIRA_CORE 的宿主集成用例", allow_module_level=True)
if os.environ["KIRA_CORE"] not in sys.path:
    sys.path.insert(0, os.environ["KIRA_CORE"])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pkg = types.ModuleType("alife_uib")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_uib", pkg)
m = importlib.import_module("alife_uib.main")
m_q = m   # 本文件里统一用 m ✓（brief 也在这个模块 ✓）

SID = "qq:ui:1"
run = asyncio.run


def build(tmp_path, **cfg):
    ctx = types.SimpleNamespace(
        get_plugin_data_dir=lambda: tmp_path,
        plugin_mgr=types.SimpleNamespace(plugin_configs={}),
        get_llm_client=None,
        get_default_llm_client=None,
        get_default_fast_llm_client=None,
        get_embedding_client=None,
        provider_mgr=None,
    )
    m.get_data_path = lambda: tmp_path
    m.get_config_path = lambda: tmp_path / "cfg"
    base = {"probability": 0.0, "audit_enabled": False}
    base.update(cfg)
    p = m.AlifeMemoryPlugin(ctx, {"alife": base})
    run(p.initialize())
    return p


def _seed_fact(p, content, fact_id="f-ui-1"):
    """直接落一条永久事实（走 store.memorize ⇒ 会 bump ✓）"""
    p.store.add_facts(SID, [{
        "subject": "用户", "category": "profile", "content": content,
        "importance": 9, "reason": "ui-test", "scenario": "",
        "relations": [], "tags": [], "source_ids": [],
        "time": "2026-10-12T10:00:00",
    }])
    with p.store.connect() as db:
        row = db.execute("SELECT id FROM facts WHERE content=? ORDER BY created DESC LIMIT 1",
                         (content,)).fetchone()
    return row["id"] if row else fact_id


def _classes():
    """宿主类：**照抄仓库既有测试的导入**（位置随框架世代变过 ⇒ 别自己猜 ✗）"""
    import importlib as _il
    return {
        "LLMRequest": _il.import_module("core.provider").LLMRequest,
        "OpenAIMessage": _il.import_module("core.agent.message").OpenAIMessage,
        "Prompt": _il.import_module("core.prompt_manager").Prompt,
        "MessageChain": _il.import_module("core.chat").MessageChain,
        "Text": _il.import_module("core.chat.message_elements").Text,
        "KiraIMMessage": _il.import_module("core.chat.message_utils").KiraIMMessage,
        "KiraMessageBatchEvent": _il.import_module("core.chat.message_utils").KiraMessageBatchEvent,
        "User": _il.import_module("core.chat.session").User,
        "Session": _il.import_module("core.chat.session").Session,
    }


def _inject(p, text, sess=SID):
    """走**真注入路径**（on_request）取这一轮实际会看到的内容 ✓"""
    C = _classes()
    session = C["Session"](adapter_name="test", session_type="dm", session_id=sess)
    msg = C["KiraIMMessage"](message_id="m", self_id="bot",
                             chain=C["MessageChain"]([C["Text"](text)]), timestamp=100,
                             sender=C["User"](user_id="qq:u", nickname="小明"))
    msg.message_str = "[小明] " + text
    ev = C["KiraMessageBatchEvent"](messages=[msg], session=session, timestamp=100, message_types=[])
    req = C["LLMRequest"](messages=[C["OpenAIMessage"](role="user", content="原历史")],
                          system_prompt=[C["Prompt"]("稳定人格", name="stable")],
                          user_prompt=[C["Prompt"](text, name="message")])

    async def go():
        await p.on_request(ev, req)
        return req

    return run(go())


def _injected_text(p, text="你还记得我的事吗"):
    req = _inject(p, text)
    req.assemble_prompt()
    return req.messages[-1].content or ""


# ---------------------------------------------------------------- ③ 改完立即生效
def test_fact_edit_is_visible_to_injection_immediately(tmp_path):
    """★ 面板改一条永久记忆 ⇒ **同一会话的下一轮注入必须立刻反映**（不能被进程内缓存挡住）✓

    用户问题 3："我在前端改动了永久记忆，bot 能即时查到吗？下轮对话注入是不是也就更新了？"
    """
    p = build(tmp_path)
    fid = _seed_fact(p, "用户花生过敏")
    before = _injected_text(p, "你还记得我花生过敏的事吗")
    assert "花生过敏" in before, "铺数据失败：注入里看不到这条事实 ✗ 实际注入＝%r" % before[:200]

    # 面板的编辑动作（/edit ⇒ store.edit ✓）
    run(p.store.call("edit", "fact", fid, None, {"content": "用户改口了：对花生不过敏了"}, ""))

    after = _injected_text(p, "你还记得我花生过敏的事吗")
    assert "对花生不过敏了" in after, (
        "改完之后注入仍是旧的 ✗ —— 说明写路径没让进程内缓存失效（memo 按 store revision 失效）"
    )


def test_config_change_takes_effect_without_restart(tmp_path):
    """★ 面板改配置（保存并生效）⇒ 不重启也要生效（这里查 aux/engine 是否拿到新设置）"""
    p = build(tmp_path)
    before = id(p.decisions)
    # 面板「保存并生效」= api_save_config 里做的事：换 settings + 重建辅助层 ✓
    p.settings = p.settings.model_copy(update={"rerank_models": ["x:1"], "rerank_model": "x:1"})
    p._build_aux()
    assert id(p.decisions) != before, "改了配置但辅助层（JEV/重排）没重建 ✗ ⇒ 新配置不生效 ✗"


# ---------------------------------------------------------------- ④ 缓存失效的完整性
def test_every_content_write_bumps_store_revision(tmp_path):
    """★ 结构性守卫：**任何会改内容的写方法都必须 bump revision** ✗

    否则：面板改了 / 后台改了 ⇒ 进程内 memo 不失效 ⇒ 注入与面板显示不一致 ✗
    （用户问题 3 的根因就是这一类 ✓ 已实测过的例子：unmerge_fact）
    例外（白名单，且要写清理由）：
      · touch_accessed / touch_tidy / mark_rotation：**纯访问计数**，故意不失效（每轮都改 ⇒
        若 bump 会让缓存彻底失效 ⇒ 性能塌方 ✗）
      · set_state / enqueue / claim / drop_job / prune_jobs：只动任务/状态记账，不参与召回内容 ✓
      · 内部辅助（_add_fact/_orphan_facts/short_id/…）：由调用方（memorize/audit/merge_facts ✓）统一 bump ✓
    """
    import ast
    import re

    src = (ROOT / "storage.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    write_re = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)
    exempt = {
        "touch_accessed", "touch_tidy", "mark_rotation", "set_state",
        "enqueue", "claim", "drop_job", "prune_jobs",
        "_add_fact", "_orphan_facts", "short_id", "archive_distilled",
        "backfill_speaker_ids", "backfill_tool_steps", "backfill_time_provenance",
        "backfill_rewrite_pending", "mark_rewrite_pending", "bump_rewrite_attempts",
        "prepare_search_index", "index_backfill", "rebuild_search_index",
        "setup_search_index", "scrub_capture_text", "finish_capture_scrub",
        "has_active_job", "entity_ids", "entities", "_ensure_entities", "_lexical_sql",
        "initialize", "requeue_running", "set_vector",
    }
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = "\n".join(lines[node.lineno - 1: node.end_lineno])
        if not write_re.search(body):
            continue
        if "meta SET value=value+1" in body:      # 计数器自己
            continue
        if "self.bump(" in body or "bump(db)" in body:
            continue
        if node.name in exempt:
            continue
        offenders.append("%s(L%d)" % (node.name, node.lineno))
    assert not offenders, (
        "这些写方法没有让缓存失效（会表现为：改了却要等别的写入才生效 ✗）：%s" % ", ".join(offenders)
    )


# ---------------------------------------------------------------- ② 恢复默认
def test_config_api_exposes_real_defaults(tmp_path):
    """★ /config 必须交出「真·默认值全表」（恢复默认依赖它 ✓）

    用户问题 2 的根因：`*_models` 这类 default_factory 字段在 JSON Schema 里**没有 default** ✗
    ⇒ 前端旧代码读到 undefined 就 return ⇒ 点「恢复默认」**毫无反应** ✗
    """
    p = build(tmp_path)
    cfg = run(p.api_config())
    assert "defaults" in cfg, "/config 没给 defaults ✗ ⇒ 恢复默认会再次静默失效 ✗"
    defaults = cfg["defaults"]
    props = cfg["schema"]["properties"]
    missing = [k for k in props if k not in defaults]
    assert not missing, "这些字段在 defaults 里缺失 ⇒ 恢复默认拿不到值 ✗：%s" % missing[:8]
    # 关键几个必须真的是"空链 = 跟随默认"
    for k in ("compress_models", "audit_models", "jev_models", "rerank_models"):
        assert defaults.get(k) == [], "%s 的默认应为空链（= 跟随 KiraAI 默认模型）✗ 实际 %r" % (k, defaults.get(k))
    for k in ("compress_model", "audit_model", "jev_model_name", "rerank_model", "embedding_model"):
        assert defaults.get(k) == "", "%s 的默认应为空串 ✗ 实际 %r" % (k, defaults.get(k))


# ---------------------------------------------------------------- ① 同名模型
def _fake_models_api(p, providers):
    """把 provider_mgr 换成假的 ⇒ 走真 api_models 逻辑 ✓"""
    p.ctx.provider_mgr = types.SimpleNamespace(kira_config={"providers": providers})
    return run(p.api_models())["models"]


def test_same_display_name_models_are_marked_selectable(tmp_path):
    """★ 同名模型（含"同名但类型不同的提供商"）必须能被前端区分 ✓

    用户问题 1："同提供商名称但不同提供商类型 ⇒ 会不会显示不了/选不了，重复了一个都不能选？"
    实测：条目不会丢 ✓，但**标签一模一样** ⇒ 用户分不清、容易删错 ✗
    ⇒ 后端按 (显示名+提供商显示名+类型) 标 dup，并给 tag ⇒ 前端加区分后缀 ✓
    """
    p = build(tmp_path)
    prov = {
        "pid-a": {"name": "Workbuddy", "model_config": {
            "llm": {"uuid-1": {"model_name": "gpt-4o"}}}},
        "pid-b": {"name": "Workbuddy", "model_config": {     # ← 同显示名，不同提供商实例 ✓
            "llm": {"uuid-2": {"model_name": "gpt-4o"}}}},
        "pid-c": {"name": "Trae", "model_config": {
            "llm": {"uuid-3": {"model_name": "gpt-4o"}}}},
    }
    got = _fake_models_api(p, prov)
    assert len(got) == 3, "模型条目丢了 ✗（应 3 条）"
    ids = [m["id"] for m in got]
    assert len(set(ids)) == 3, "条目 id 撞车 ⇒ 前端会有一个选不了 ✗：%s" % ids
    dup = [m for m in got if m.get("dup")]
    assert len(dup) == 2, "同名（含同名提供商）的两条应被标记 dup ✗ 实际 %d 条" % len(dup)
    assert all(m.get("tag") for m in dup), "标了 dup 却没给区分用 tag ✗"
    assert {m["tag"] for m in dup} == {"pid-a", "pid-b"}, "tag 没起到区分作用 ✗"
    plain = [m for m in got if not m.get("dup")]
    assert len(plain) == 1 and plain[0]["provider"] == "Trae", "不该被标记的条目被误标 ✗"


# ---------------------------------------------------------------- ④ 其他一致性
def test_chain_limit_has_single_source_of_truth(tmp_path):
    """★ 链长上限只能有一个来源（mchains.CHAIN_MAX）⇒ 前端不许硬编码 ✗

    否则哪天把上限调大/调小，面板还是老的 ⇒ 用户看到"加不进去/能加超过上限" ✗
    """
    from alife_uib import mchains

    p = build(tmp_path)
    cfg = run(p.api_config())
    assert cfg.get("chain_max") == mchains.CHAIN_MAX, "/config 没透出正确的 chain_max ✗"
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "config.chain_max" in js, "前端没有使用后端给的 chain_max ✗"
    # 允许 `|| 10` 兜底，但不能再出现独立的硬编码上限判断 ✓
    # 注意别用 "length < 10" 这种宽串 ✗ —— `shownFacts.length < 100` 会误命中 ✓
    for bad in ("out.length < 10", "slice(0, 10)", "out.length < 10)"):
        assert bad not in js, "前端仍有硬编码的链长上限 ✗：%s" % bad


# ---------------------------------------------------------------- ③-b bot 自己写
def test_bot_capture_refreshes_cached_reads(tmp_path):
    """★ **bot 自己存进来的**（capture）⇒ 进程内缓存必须失效、按新数据重算 ✓

    注意别把断言写成"新消息一定出现在下一轮注入里" ✗ —— **原始消息按设计不会逐条注入** ✓
    （要经压缩/审计沉淀成事实 ✓）所以这里验证的是**缓存机制本身**：
    同一个 memo key 前后必须重算出**不同且包含新内容**的结果 ✓
    （用户要确认的是"bot 自己改的/删的会不会即时生效" ⇒ 这就是那条链路 ✓）
    """
    p = build(tmp_path)
    key = ("ui-consistency", SID)

    async def go():
        v1 = await p.memo(key, lambda: p.store.call(
            "context", SID, ["test:qq:u"], scope="global"))
        await p.store.call("capture", SID, "turn-9", [{
            "role": "user", "content": "我刚说了：我搬到杭州了", "time": time.time(),
            "users": ["test:qq:u"], "speaker": "小明", "speaker_id": "test:qq:u",
        }])
        v2 = await p.memo(key, lambda: p.store.call(
            "context", SID, ["test:qq:u"], scope="global"))
        return v1, v2

    v1, v2 = run(go())
    assert repr(v1) != repr(v2), "bot 存了新消息，但同一 memo key 的结果**没变** ✗ ⇒ 缓存没失效 ✗"
    assert "杭州" in repr(v2), "bot 刚存的内容没有出现在重算结果里 ✗"


def test_bot_memorize_is_visible_to_next_injection(tmp_path):
    """★ **bot 自己记住的**（memorize）⇒ 下一轮注入立刻可见 ✓"""
    p = build(tmp_path)
    before = _injected_text(p, "你还记得我住哪吗")
    run(p.store.call("memorize", SID, "用户住杭州西湖区", ["test:qq:u"],
                     time.time(), time.time() + 1, 9, "profile"))
    after = _injected_text(p, "你还记得我住哪吗")
    assert after != before and "杭州" in after, "bot 自己记住的事实没进下一轮注入 ✗"


def test_background_compress_refreshes_cached_reads(tmp_path):
    """★ **后台模型改的**（压缩 = 后台模型任务）⇒ 同样即时生效 ✓

    用户要确认的第三条链路："后台模型修改的也是即时能查和能生效，包括下一轮真的变化了 ✓"
    （压缩会把原始消息沉淀成摘要 ⇒ 下一轮注入看到的就是**摘要后的内容** ✓）
    """
    p = build(tmp_path, compress_batch_mode="records", threshold=8, batch_size=6)
    now = time.time()
    run(p.store.call("capture", SID, "turn-a", [
        {"role": "user", "content": "第 %d 句：我们聊到搬家的事" % i, "time": now + i,
         "users": ["test:qq:u"], "speaker": "小明", "speaker_id": "test:qq:u"}
        for i in range(8)]))

    calls = []

    async def fake_model(model, purpose, instruction, schema, payload, link_timeout=None):
        calls.append(purpose)
        return '{"summary": "【已压缩】用户搬到杭州西湖区了", "facts": []}'

    p.engine.model_call = fake_model                      # 只换模型调用，其余全真的 ✓
    key = ("ui-consistency", SID)

    async def go():
        v1 = await p.memo(key, lambda: p.store.call(
            "context", SID, ["test:qq:u"], scope="global"))
        await p.engine.compress(SID)
        v2 = await p.memo(key, lambda: p.store.call(
            "context", SID, ["test:qq:u"], scope="global"))
        return v1, v2

    v1, v2 = run(go())
    assert calls, "压缩压根没调模型 ⇒ 这条断言没意义 ✗"
    assert repr(v1) != repr(v2), "后台压缩改完了，但同一 memo key 的结果没变 ✗ ⇒ 缓存没失效 ✗"
    assert "已压缩" in repr(v2), "压缩后的摘要没有进入重算结果 ✗"


# ---------------------------------------------------------------- 注入简报渲染
def test_brief_renders_short_codes_as_names_not_list_repr(tmp_path):
    """★ 注入简报头里的「短码=名字」必须是**名字**，不能是列表 repr ✗

    实测抓到（2026-10-14）：分组视图下 `short_names()` 给的是
    `{短码: [真实 id, "名字|别名"]}`（**给 JSON 事实区用的** ✓ 图例里写明 ✓）
    而 `brief()` 直接 `%s` ⇒ 头部变成 `n1=['qq:123', '小明|小明']` ✗
    ⇒ 白费 token ✗ 且模型得自己猜那是什么 ✓（默认 fact_view 就是分组视图 ⇒ **默认路径上** ✓）
    """
    p = build(tmp_path)
    perception = {
        "scope": "global", "session": "s1",
        # 分组视图形态（现在真实用的就是它 ✓）
        "names": {"n1": ["qq:123", "小明|小明"], "n2": ["qq:456", "小红|小红"]},
        "facts": {"n1": [["pf", "喜欢甜食", 9]]},
    }
    text = m_q.brief(perception)
    assert "[" not in text.split("\n")[0], "简报头里出现了列表 repr ✗：%s" % text.split("\n")[0]
    assert "n1=小明|小明" in text, "短码没有渲染成名字 ✗：%s" % text.split("\n")[0]
    assert "n2=小红|小红" in text
    # 扁平视图形态（字符串名字 ✓）也要照旧正确 ✓
    flat = {"scope": "global", "session": "s1", "names": {"n1": "小明|小明"}, "facts": []}
    assert "n1=小明|小明" in m_q.brief(flat)
