"""多形态对拍：快路径(FTS) vs 慢路径(全表) 必须逐条一致（铁律 B）★ 2026-09-20

依据 storage.py 的自述契约：「全表路径始终是正确的那个」「两条路径的结果始终一致」。
本文件覆盖多种查询形态，任何一种对不上 ⇒ 直接红 ✓
"""

import importlib.util
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROWS = [
    "第一条 苹果 香蕉 项目 记录 内容",
    "第二条 苹果 橙子 会议 纪要",
    "第三条 香蕉 项目 进度 汇报",
    "第四条 肉桂 香料 笔记",
    "第五条 苹果 项目 复盘 总结",
    "第六条 无关内容 随便写写",
]


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
    for i, text in enumerate(ROWS):
        st.memorize("S1", text, ["U1"], 1737000000 + i, 1737000000 + i, importance=8)
    return st


def _sig(rows):
    """整行签名（不猜字段名 ✓ 比只看 id 更严格 ✓）"""
    out = []
    for r in rows:
        try:
            d = dict(r)
        except Exception:
            d = {"_raw": str(r)}
        out.append(tuple(sorted((k, str(v)[:120]) for k, v in d.items())))
    return out


def _pair(store, **kw):
    """先拿快路径，再破坏索引拿全表路径 ⇒ 返回两者的签名"""
    store.prepare_search_index()
    fast = _sig(store.search(sid="S1", **kw))
    store.abandon_search_index()
    slow = _sig(store.search(sid="S1", **kw))
    return fast, slow


CASES = [
    ("单词元", dict(lexical="苹果")),
    ("双词元", dict(lexical="苹果 香蕉")),
    ("长句", dict(lexical="苹果 香蕉 项目 记录 内容 汇报 复盘 总结 之类 很长")),
    ("带实体词", dict(lexical="项目 进度")),
    ("不命中", dict(lexical="量子力学相对论")),
    ("空 lexical", dict(lexical="")),
    ("带 users 过滤", dict(lexical="苹果", users=["U1"])),
    ("带 limit", dict(lexical="苹果", limit=2)),
    ("带 offset", dict(lexical="苹果", offset=1, limit=2)),
]


@pytest.mark.parametrize("name,kw", CASES, ids=[c[0] for c in CASES])
def test_paths_agree_for_shape(name, kw):
    st = _store()
    fast, slow = _pair(st, **kw)
    # 防假绿：不能两边都是空 ✓
    assert len(fast) >= 1 or "不命中" in name or "空" in name, \
        "%s：两边都空 ⇒ 这条测试证明不了什么 ✗" % name
    assert fast == slow, "%s：快慢路径不一致 ✗（条数 %d vs %d）" % (name, len(fast), len(slow))


def test_guard_selfcheck():
    """反向自检：条数/顺序/内容不同都必须被判为不一致 ✓"""
    a = _sig([{"id": "1"}, {"id": "2"}])
    assert a != _sig([{"id": "1"}]), "少一条要判红"
    assert a != _sig([{"id": "2"}, {"id": "1"}]), "顺序不同要判红"
    assert a != _sig([{"id": "1"}, {"id": "3"}]), "内容不同要判红"
    assert a == _sig([{"id": "1"}, {"id": "2"}]), "完全相同要判过"
