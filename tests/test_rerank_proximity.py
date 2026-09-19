"""B：同现/邻近重排（★ 2026-09-19，用户实测引出）

现象：搜 `doro` 命中 **784** 条 ⇒ 每条 4 分**完全并列** ✗ ⇒ 返回顺序≈随机
      ⇒ 有信息量的行被随机埋掉 ✓

B 的做法：
  · **只在 Python 侧重排** ✗ 不进 SQL ✓（SQL 打分有"与 Python 逐字一致"的判据守着 ✓）
  · **同段(±30字)出现两个查询词** ⇒ 加权 ⇒ 顶上来 ✓
  · **没有任何同现 ⇒ 原样返回** ✓（首要安全属性：绝不破坏既有排序契约 ✓）
  · 用**稳定排序** ⇒ 其余行的相对顺序**分毫不动** ✓
  · **只增不删** ✗ ⇒ 绝不减少召回 ✓
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_pkg = types.ModuleType("z_rerank")
_pkg.__path__ = [str(ROOT)]
sys.modules["z_rerank"] = _pkg
st = __import__("importlib").import_module("z_rerank.storage")
r = __import__("importlib").import_module("z_rerank.retrieval")


def _item(i, text, imp=5, end=0):
    return {"id": "id%d" % i, "summary": text, "content": "", "importance": imp, "end": end}


def test_proximity_bonus_math():
    toks = r.score_tokens("doro 艾莉")
    assert st.proximity_bonus("群里澄清过：doro 的 bot 叫 艾莉，最乖的天使王", toks) > 0
    assert st.proximity_bonus("doro" + "x" * 80 + "艾莉", toks) == 0     # 太远 ⇒ 无加成 ✓
    assert st.proximity_bonus("doro 开发了插件", toks) == 0              # 只命中一个 ⇒ 无加成 ✓
    assert st.proximity_bonus("", toks) == 0


class _Store(st.Store):
    """只借 _rerank，不碰数据库 ✓"""

    def __init__(self):
        pass


def _rerank(items, lexical, want=5, offset=0):
    return _Store()._rerank({"total": len(items), "items": list(items)}, lexical, want, offset)


def test_co_occurring_row_is_promoted():
    """★ 核心：有同现的行必须被顶上来 ✓"""
    items = [
        _item(1, "doro 写过表情包插件"),
        _item(2, "doro 说自己干了网络搜索插件"),
        _item(3, "群里澄清过：doro 的 bot 叫 艾莉"),      # ★ 同现
        _item(4, "doro 与天使组合的讨论"),
    ]
    out = _rerank(items, "doro 艾莉")
    assert out["items"][0]["id"] == "id3", "同现那条必须排第一：%r" % [x["id"] for x in out["items"]]
    assert len(out["items"]) == 4, "**绝不能减少召回** ✗"
    assert out.get("reranked") is True


def test_no_cooccurrence_keeps_order_untouched():
    """★ 首要安全属性：没有同现 ⇒ **原样返回**（不许动既有排序契约 ✓）"""
    items = [_item(1, "doro 写过插件"), _item(2, "doro 做过生图"), _item(3, "doro 说过话")]
    out = _rerank(items, "doro 艾莉")
    assert [x["id"] for x in out["items"]] == ["id1", "id2", "id3"], "顺序必须分毫不动 ✓"
    assert "reranked" not in out


def test_stable_among_equal_and_respects_limit():
    items = [_item(1, "doro 插件"), _item(2, "doro 生图"), _item(3, "doro 的 bot 叫 艾莉")]
    out = _rerank(items, "doro 艾莉", want=2)
    assert len(out["items"]) == 2, "必须尊重 limit ✓"
    assert out["items"][0]["id"] == "id3"
    assert out["items"][1]["id"] == "id1", "其余行保持**原相对顺序** ✓"


def test_single_keyword_query_is_left_alone():
    """单词查询没有"同现"可言 ⇒ 原样返回 ✓（不折腾 ✓）"""
    items = [_item(1, "doro 插件"), _item(2, "doro 生图")]
    out = _rerank(items, "doro")
    assert [x["id"] for x in out["items"]] == ["id1", "id2"]
    assert "reranked" not in out


def test_rerank_never_raises():
    """重排只是优化 ⇒ 任何异常都必须**退回原顺序** ✓（绝不能让检索失败 ✗）"""
    bad = [{"id": None, "summary": None, "content": None, "importance": "x", "end": None}]
    out = _rerank(bad, "doro 艾莉")
    assert out["items"] == bad
