"""⑤ 自我认知门槛 **端到端**：不漏挡、也不错挡（2026-10-11 大修后重写 ✓）

背景（两次踩坑，值得留档）
- 坑①：`src_user` 原来取 `records.users[0]` ✗ —— 但 `users` 是**可见范围**（谁能看到），
  **不是发言人** ✗ ⇒ 私聊里可见范围就是对方 ⇒ 把**用户**当成了机器人自己 ✗
  （用户实测截图：周武说的话被标成「自我 / 不参与召回」✗）
- 坑②：面板请求没有事件 ⇒ 我一度"从 users[0] 推断 bot id" ✗ ⇒ 推断出的是用户 ✗✗
- 修法：
  · 记录里**真存发言人 id**（新列 `records.speaker_id` ✓ 捕获时写入 ✓）
  · `src_user` = 该列 ✓；老记录没有 ⇒ 空 = **未知** ⇒ 不判自我 ✓（宁可不挡，不错挡 ✓）
  · 面板只用**事件里缓存/持久化**的 self_id ✓ 不猜 ✓
"""
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

import asyncio
import importlib

pkg = types.ModuleType("alife_selfgate")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_selfgate", pkg)
m = importlib.import_module("alife_selfgate.main")
storage = importlib.import_module("alife_selfgate.storage")
retrieval = importlib.import_module("alife_selfgate.retrieval")

SID = "qq:gm:e2e"
BOT = "qq:9999"          # 机器人自己的 id（形如 adapter:id ✓）
USER = "qq:1001"         # 用户
run = asyncio.run


def _plugin(tmp_path, **cfg):
    m.get_data_path = lambda: tmp_path
    # ★ 迁移会写「宿主全局」的插件配置 ✗ ⇒ 测试必须隔离它，否则会污染同进程里其它用例 ✓
    m.get_config_path = lambda: tmp_path / "cfg"
    ctx = types.SimpleNamespace(
        get_plugin_data_dir=lambda: tmp_path,
        plugin_mgr=types.SimpleNamespace(plugin_configs={}),
    )
    settings = {"alife": {"probability": 0.0, "audit_enabled": False}}
    settings["alife"].update(cfg)
    p = m.AlifeMemoryPlugin(ctx, settings)
    run(p.initialize())
    return p, p.store


def _seed(store, content, importance, speaker_id, speaker_name="某人"):
    """造一条**真记录**（含真正的发言人 id ✓）+ 一条指向它的事实 ✓"""
    rid = "rec-" + str(abs(hash(content)) % 10**8)
    now = time.time()
    with store.connect() as db:
        db.execute(
            "INSERT INTO records(id,sid,role,level,start,end,summary,content,users,"
            "speaker,speaker_id,position,created,search_body) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, SID, "assistant" if speaker_id == BOT else "user", "L0", now, now,
             content, content, json.dumps([USER]), speaker_name, speaker_id, 0, now, content),
        )
        db.execute(
            "INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,"
            "relations,sources,fingerprint,importance,created,revision,deleted,"
            "merge_pending,audited) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f-" + rid, SID, "state", "x", content, "", "", "[]", "[]",
             json.dumps([rid]), "fp-" + rid, importance, now, 1, 0, 0, 0),
        )
    return rid


def _facts(store):
    rows = [dict(r) for r in run(store.call("facts", sid=SID, limit=50))]
    return run(store.call("attach_evidence", rows))


def test_src_user_is_the_true_speaker_not_visibility(tmp_path):
    """★ 坑①回归：src_user 必须是**真正的发言人**，不能拿"可见范围"充数 ✗"""
    p, store = _plugin(tmp_path)
    _seed(store, "用户说的话", 8, USER, "周武")
    row = _facts(store)[0]
    assert row["src_user"] == USER, "用户说的话 ⇒ src_user 应是用户 id ✗（可见范围里只有用户 ⇒ 以前会误判成自己 ✗）"
    assert row["src_user"] != BOT


def test_no_missed_filtering_for_bot_self_talk(tmp_path):
    """★ 不漏挡：机器人自己说的 + 重要度 ≤ 门槛 ⇒ **必须**被挡 ✓"""
    p, store = _plugin(tmp_path, self_fact_filter_enabled=True)   # 显式打开（默认关 ✓）
    _seed(store, "我把记忆更新了一下（自述）", 3, BOT, "香里")
    row = _facts(store)[0]
    assert retrieval.low_value_self(row, BOT, 4) is True, "机器人自述 + 3 ≤ 4 ⇒ 应被挡 ✗"


def test_no_false_filtering_for_user_words(tmp_path):
    """★ 不错挡：**用户说的**（哪怕内容关于机器人）+ 低重要度 ⇒ **绝不能**被挡 ✓
    注意：这里**开关是开的** ✓ —— 也就是说"开着重也不误伤用户"才是真本事 ✓"""
    p, store = _plugin(tmp_path, self_fact_filter_enabled=True)
    _seed(store, "哥要求你以后用音乐卡片推送", 3, USER, "周武")
    row = _facts(store)[0]
    assert retrieval.low_value_self(row, BOT, 4) is False, "用户说的话不该被当自我事实挡掉 ✗（实测踩过 ✓）"


def test_old_records_without_speaker_id_are_never_filtered(tmp_path):
    """老记录没有 speaker_id ⇒ 未知 ⇒ **不判自我** ✓（宁可不挡，也不错挡 ✓）"""
    p, store = _plugin(tmp_path)
    _seed(store, "老数据（没有发言人 id）", 2, "", "")
    row = _facts(store)[0]
    assert row["src_user"] == "", "没有 speaker_id 时应为空（未知）✗"
    assert retrieval.low_value_self(row, BOT, 4) is False, "未知不能判成自我 ✗"


def test_switch_off_means_nothing_filtered(tmp_path):
    """开关关掉 ⇒ 一条都不挡（与旧行为一致 ✓）"""
    p, store = _plugin(tmp_path)
    _seed(store, "机器人自述", 3, BOT, "香里")
    row = _facts(store)[0]
    assert retrieval.low_value_self(row, BOT, 0) is False, "门槛传 0 = 关闭 ⇒ 不该挡 ✓"


def test_panel_markers_match_recall_exactly(tmp_path):
    """面板标记必须与召回判据**逐条一致** ✓（同一函数 ✓ 不另写逻辑 ✗）"""
    p, store = _plugin(tmp_path, self_fact_filter_enabled=True)
    _seed(store, "机器人自述的话", 3, BOT, "香里")
    _seed(store, "用户说的话", 3, USER, "周武")
    # ★ 真实部署里 bot 的 id 必然已知（事件里记下 / 持久化读回 ✓）
    #   面板**不猜** ✗ ⇒ 测试必须如实模拟这一步 ✓
    run(store.call("set_state", "self_id", BOT))
    rows = run(p.api_facts(sid=SID))
    assert len(rows) == 2
    for r in rows:
        assert bool(r["recall_filtered"]) == bool(
            retrieval.low_value_self(r, BOT, 4)), "面板标记与召回判据不一致 ✗"
    got = {("自动" if r["self_fact"] else "用户"): r["recall_filtered"] for r in rows}
    assert got["自动"] is True and got["用户"] is False, "自我/用户 的判定反了 ✗"


def test_default_is_off_so_upgrade_changes_nothing(tmp_path):
    """★ 默认**关**（用户 2026-10-11 定）：一条都不挡 ⇒ 升级后与旧行为完全一致 ✓"""
    p, store = _plugin(tmp_path)                      # 全默认
    assert p._self_drop_max() == 0, "默认必须等效于「不挡」（返回 0）✗"
    _seed(store, "机器人自述", 3, BOT, "香里")
    row = _facts(store)[0]
    assert retrieval.low_value_self(row, BOT, p._self_drop_max()) is False


def test_config_migration_is_one_time_and_conservative(tmp_path):
    """★ 存量配置迁移：只动"当年的默认值"，用户特意设过的**绝不碰** ✓ 且只跑一次 ✓"""
    p, store = _plugin(tmp_path, self_fact_max_importance=6)   # 模拟老配置（旧默认 6 + 没有总开关）
    run(p._migrate_self_fact_settings())
    assert p.settings.self_fact_max_importance == 4, "旧默认 6 ⇒ 应迁移到 4 ✗"
    assert p.settings.self_fact_filter_enabled is False, "应补上总开关=关 ✗"
    assert str(run(store.call("get_state", "cfg_migrate_self_fact", ""))) == "1", "迁移应记标记（只跑一次）✗"
    # 用户特意设过的值 ⇒ 不动 ✓
    # （配置路径是**宿主全局**的 ⇒ 不能靠不同 tmp_path 隔离 ✗
    #   改成：重置迁移标记 + 把值改成 8 ⇒ 再跑一次迁移 ⇒ 必须原样保留 ✓）
    from contracts import Settings as _S
    run(store.call("set_state", "cfg_migrate_self_fact", ""))
    p.settings = _S(**{**p.settings.model_dump(), "self_fact_max_importance": 8})
    run(p._migrate_self_fact_settings())
    assert p.settings.self_fact_max_importance == 8, "用户设过的值不能被迁移改掉 ✗"
