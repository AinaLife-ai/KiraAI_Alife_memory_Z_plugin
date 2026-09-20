"""观测回归：召回路径与耗时必须真的被记录（2026-09-20 加的 P0 观测）★

目的：日志是"卡回复"排查的唯一线索 ⇒ 不许被无意改坏 ✗
若哪天日志没了、或两条路径都不再记录 ⇒ 这条会红 ✓
"""

import importlib.util
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _store():
    pkg = types.ModuleType("alife_memory_z")
    pkg.__path__ = [str(ROOT)]
    sys.modules["alife_memory_z"] = pkg
    spec = importlib.util.spec_from_file_location("alife_memory_z.storage", ROOT / "storage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    d = Path(tempfile.mkdtemp())
    st = mod.Store(d / "m.db")
    st.initialize()
    for i in range(6):
        st.memorize("S1", "第%d条 苹果 项目 记录" % i, ["U1"], 1737000000 + i, 1737000000 + i, importance=8)
    return st


def _logs(caplog):
    return [r.getMessage() for r in caplog.records if "[recall]" in r.getMessage()]


def test_fast_path_is_logged(caplog):
    st = _store()
    with caplog.at_level(logging.DEBUG):
        st.prepare_search_index()
        st.search(sid="S1", lexical="苹果")
    text = " ".join(_logs(caplog))
    assert "path=fast" in text, "快路径必须被记录 ✗（没记录就查不出'卡'在哪）"
    assert "ms=" in text, "必须记录耗时 ✗"
    assert "hits=" in text, "必须记录候选条数 ✗"


def test_fallback_path_is_logged_with_reason(caplog):
    st = _store()
    with caplog.at_level(logging.DEBUG):
        st.abandon_search_index()
        st.search(sid="S1", lexical="苹果")
    text = " ".join(_logs(caplog))
    assert "path=fallback" in text, "全表路径必须被记录 ✗"
    assert "fts_state=unavailable" in text, "必须写明**为什么**走了全表 ✗"


def test_branch_is_logged(caplog):
    st = _store()
    with caplog.at_level(logging.DEBUG):
        st.search(sid="S1", lexical="苹果")
    assert "分支=" in " ".join(_logs(caplog)), "取数分支必须被记录 ✗"


def test_observability_does_not_change_results(caplog):
    """★ 观测本身**不能改变结果**（这是它能进生产的前提 ✓）"""
    st = _store()
    st.prepare_search_index()
    with caplog.at_level(logging.DEBUG):
        a = [str(r) for r in st.search(sid="S1", lexical="苹果")]
    with caplog.at_level(logging.CRITICAL):   # 静音后再跑一次
        b = [str(r) for r in st.search(sid="S1", lexical="苹果")]
    assert len(a) >= 1, "两次都空 ⇒ 证明不了什么 ✗"
    assert a == b, "开/关日志不能改变召回结果 ✗（返回行不是 dict，所以用整体字符串比较 ✓）"
