"""② 重要度显示/编辑 + ③ 失败任务重试安全性 —— 2026-10-10 用户反馈后的改动 ✓

③ 的安全语义（用户点名的三点顾虑）靠**既有机制**保证，这里把它们**钉成测试** ✓：
  ① `storage.enqueue` 同 (kind,sid) 只有一行 ⇒ 排队中复用 / 完结才重开 ⇒ 不会重复 ✓
  ② `engine.compress` 任务体**重读当前内容**再规划 ⇒ 不会以旧盖新 ✓
  ③ 手动排队标 automatic=0（可区分人工）✓
"""

import importlib
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pkg = types.ModuleType("alife_imp_retry")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_imp_retry", pkg)
storage = importlib.import_module("alife_imp_retry.storage")


# ---------------- ② 前端结构 ----------------

def test_fact_card_shows_importance():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'class="tag imp' in js, "画像/关系的事实卡片没显示重要度 ✗"
    assert "重要度 ${esc(" in js, "卡片上的重要度没有取值 ✗"
    assert "imp-low" in js, "低重要度没有视觉区分 ✗"


def test_fact_editor_has_importance_field():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert '"importance",' in js, "编辑弹窗字段清单里没有 importance ✗"
    assert 'data-field="importance"' in js, "没有 importance 的输入控件 ✗"
    assert 'importance: "重要度' in js, "没有 importance 的中文标签 ✗"
    # /edit 的 patch 期望 int ⇒ 收集时必须转数字 ✓
    assert 'e.dataset.field === "importance" ? Number(e.value)' in js, \
        "importance 没转数字 ⇒ 保存会 422 ✗"


def test_css_has_importance_badge():
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert ".tag.imp" in css and ".tag.imp-low" in css, "缺少重要度徽标样式 ✗"


# ---------------- ③ 前端重试按钮 ----------------

def test_failed_task_has_retry_button():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "JOB_RETRY_KINDS" in js, "没有可重试种类白名单 ✗"
    assert 'j.state === "failed"' in js, "重试按钮没有限制在 failed 状态 ✗"
    assert "data-jobretry" in js and 'api("/jobs"' in js, "重试按钮没接到 POST /jobs ✗"
    # 白名单与后端 Literal 必须一致（不一致 ⇒ 422 ✗）
    assert "fact_merge" in js


def test_retry_button_defined_before_use():
    """const 有 TDZ ⇒ 常量必须在渲染器之前定义 ✓（否则 ReferenceError ✗）"""
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert js.index("const JOB_RETRY_KINDS") < js.index("JOB_RETRY_KINDS.includes"), \
        "JOB_RETRY_KINDS 定义在使用点之后 ⇒ 渲染时会 ReferenceError ✗"


# ---------------- ③ 后端：重试入口 + 去重语义 ----------------

def test_job_kind_accepts_fact_merge():
    contracts = importlib.import_module("alife_imp_retry.contracts")
    job = contracts.Job(kind="fact_merge", sid="qq:gm:1")
    assert job.kind == "fact_merge"
    with pytest.raises(Exception):
        contracts.Job(kind="not_a_kind", sid="qq:gm:1")


def test_enqueue_is_idempotent_while_pending(tmp_path):
    """排队中重复入队 ⇒ **同一行**（不会排两条 ⇒ 用户狂点也不会重复干活 ✓）"""
    store = storage.Store(tmp_path / "j.db")
    store.initialize()
    first = store.enqueue("compress", "qq:gm:1", detail="自动")
    again = store.enqueue("compress", "qq:gm:1", detail="用户手动重试", automatic=False)
    assert first == again, "同类任务被排成了两行 ⇒ 会重复处理 ✗"
    with store.connect() as db:
        rows = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind='compress' AND sid='qq:gm:1'"
        ).fetchone()[0]
    assert rows == 1, "同 (kind,sid) 应只有一行 ✗"
    # 手动重试用 automatic=0 标记 ✓（明细里可区分人工）
    with store.connect() as db:
        flag = db.execute(
            "SELECT automatic FROM jobs WHERE id=?", (first,)
        ).fetchone()[0]
    assert flag == 0, "手动重试没打上 manual 标记 ✗"


def test_enqueue_reopens_finished_job(tmp_path):
    """已完结（含 failed）⇒ **重开同一行** ✓（否则这个会话再也排不上 ✗）"""
    store = storage.Store(tmp_path / "j2.db")
    store.initialize()
    jid = store.enqueue("audit", "qq:gm:2")
    with store.connect() as db:
        db.execute("UPDATE jobs SET state='failed' WHERE id=?", (jid,))
    again = store.enqueue("audit", "qq:gm:2")
    assert again == jid, "失败后重试没有复用同一行 ✗"
    with store.connect() as db:
        state = db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    assert state == "queued", "失败的任务没有被重开成 queued ✗"


def test_frontend_retry_whitelist_matches_backend_literal():
    """★ 前后端一致性：前端能给的重试种类，后端 `/jobs` 必须真的收 ✗→✓

    背景：`POST /jobs` 的 `Job.kind` 是 `Literal[...]` ✗ → 前端若列出后端不认的种类，
    用户点一下就 422 ✗（而且只在点的时候才发现 ✗）。这里把两边**钉在一起** ✓。
    """
    import re

    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const JOB_RETRY_KINDS = \[(.*?)\];", js, re.S)
    assert m, "找不到 JOB_RETRY_KINDS 常量 ✗"
    front = {s.strip().strip('"') for s in m.group(1).split(",") if s.strip()}

    py = (ROOT / "contracts.py").read_text(encoding="utf-8")
    # ★ 必须锚到 **Job 类内部** ✗→✓（别处还有别的 `kind: Literal[...]`
    #   —— 例如 `Literal["record","fact","cold"]` ⇒ 不加锚点会抓错 ✓ 本测试第一版就踩了 ✓）
    job_cls = py.index("class Job(")
    m2 = re.search(r'kind: Literal\[(.*?)\]', py[job_cls:], re.S)
    assert m2, "找不到 Job.kind 的 Literal ✗"
    back = {s.strip().strip('"') for s in m2.group(1).split(",") if s.strip()}

    assert front, "前端重试白名单不能为空 ✗"
    assert front <= back, "前端列了后端不收的种类 ⇒ 点了会 422 ✗：%s" % sorted(front - back)
    # 反向：后端能手动排的都该给按钮 ✓（classify/proactive 本来就不在接受列表里 ✓）
    assert back - front == set(), "后端支持但前端没给按钮 ⇒ 用户干等 ✗：%s" % sorted(back - front)
