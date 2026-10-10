"""线上事故回归：老库缺 `speaker_id` 列 ⇒ 启动必须**自动补列**且**绝不崩**（2026-10-11）

事故现场（用户真机）：
    sqlite3.OperationalError: no such column: speaker_id
      at storage.attach_evidence → 整条记忆链路直接崩 ✗
根因（我的锅 ✗）：那条 `ALTER TABLE ... ADD COLUMN speaker_id` 被**错误地塞进**
    上面 `speaker` 的 db.execute 里当第二个参数 ✗，而且嵌套在
    `if "speaker" not in columns` 里 ⇒ 老库（已有 speaker 列）**永远进不去** ✗
    ⇒ 存量库从来没有这一列 ✗

本测试钉住两件事：
    ① 迁移必须**独立执行**（老库启动后列自动补上 ✓）
    ② 万一列缺失，读路径**必须兜底**（按"未知发言人"处理 ⇒ 不崩 ✓ 宁可不挡也不错挡 ✓）
"""
import asyncio
import importlib
import os
import sqlite3
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

pkg = types.ModuleType("alife_mig")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_mig", pkg)
m = importlib.import_module("alife_mig.main")
run = asyncio.run


def _boot(tmp):
    m.get_data_path = lambda: tmp
    ctx = types.SimpleNamespace(
        get_plugin_data_dir=lambda: tmp,
        plugin_mgr=types.SimpleNamespace(plugin_configs={}),
    )
    p = m.AlifeMemoryPlugin(ctx, {"alife": {"probability": 0.0, "audit_enabled": False}})
    run(p.initialize())
    return p


def _cols(db):
    with sqlite3.connect(db) as c:
        return [r[1] for r in c.execute("PRAGMA table_info(records)")]


def test_old_db_without_speaker_id_self_heals(tmp_path):
    db = tmp_path / "alife-v2.sqlite3"          # ★ 插件真正用的库名（别写成 store.sqlite3 ✗）
    p = _boot(tmp_path)
    run(p.store.call("capture", "qq:gm:1", "input",
                     [{"role": "assistant", "content": "助手说过的话", "time": time.time(),
                       "users": ["qq:1"], "speaker": "香里", "speaker_id": "qq:9999"}]))
    with sqlite3.connect(db) as c:
        rid = c.execute("SELECT id FROM records LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,relations,"
                  "sources,fingerprint,importance,created,revision,deleted,merge_pending,audited)"
                  " VALUES('f1','qq:gm:1','state','x','助手说过的话','','','[]','[]',?, 'fp1',3,?,1,0,0,0)",
                  ('["%s"]' % rid, time.time()))
    # 复刻线上坏库：把列删掉
    with sqlite3.connect(db) as c:
        c.execute("ALTER TABLE records DROP COLUMN speaker_id")
    assert "speaker_id" not in _cols(db), "复刻失败（列还在）✗"

    p2 = _boot(tmp_path)                        # ★ 修复前：这里之后 attach_evidence 崩 ✗
    assert "speaker_id" in _cols(db), "启动必须**自动补列** ✗（线上事故根因）"
    rows = [dict(r) for r in run(p2.store.call("facts", sid="qq:gm:1", limit=10))]
    ev = run(p2.store.call("attach_evidence", rows))
    assert len(ev) == 1, "attach_evidence 必须正常返回（不崩）✗"
    assert ev[0].get("src_user") == "", "补列后老行的发言人是空的（未知）⇒ 不判自我 ✗"


def test_attach_evidence_survives_missing_column(tmp_path):
    """★ 兜底路径：列真缺失时也要能跑（按未知处理）⇒ 任何局部结构问题都不该让主流程崩 ✓"""
    db = tmp_path / "alife-v2.sqlite3"
    p = _boot(tmp_path)
    run(p.store.call("capture", "qq:gm:1", "input",
                     [{"role": "user", "content": "用户说的话", "time": time.time(),
                       "users": ["qq:1"], "speaker": "周武", "speaker_id": "qq:1001"}]))
    with sqlite3.connect(db) as c:
        rid = c.execute("SELECT id FROM records LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO facts(id,sid,category,subject,content,reason,scenario,tags,relations,"
                  "sources,fingerprint,importance,created,revision,deleted,merge_pending,audited)"
                  " VALUES('f2','qq:gm:1','state','x','用户说的话','','','[]','[]',?, 'fp2',3,?,1,0,0,0)",
                  ('["%s"]' % rid, time.time()))
        c.execute("ALTER TABLE records DROP COLUMN speaker_id")
    with sqlite3.connect(db) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(records)")]
    if "speaker_id" in cols:
        pytest.skip("该 SQLite 版本不支持 DROP COLUMN")
    # 直接调（不重启 ⇒ 不会补列）⇒ 必须走兜底而不是抛异常 ✓
    rows = [dict(r) for r in run(p.store.call("facts", sid="qq:gm:1", limit=10))]
    ev = run(p.store.call("attach_evidence", rows))
    assert len(ev) == 1 and ev[0].get("src_user") == "", "缺列时必须兜底为「未知」而不是崩 ✗"
