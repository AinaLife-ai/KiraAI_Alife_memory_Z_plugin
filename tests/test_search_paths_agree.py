"""铁律 B 回归：召回的两条路径必须**逐条一致**

storage.py `_fts_hits` 的 docstring 承诺：
  「回填失败时降级：索引不再参与检索（**全表路径始终是正确的那个**）。」
  「返回 None 只表示**不优化**……两条路径的结果始终一致。」

本文件把这个承诺钉成回归 ✓（纯测试，不改任何生产代码 ✓）
做法：同一份数据、同一个 query、同一组参数
  ① 索引就绪 ⇒ 拿"快路径"结果
  ② abandon_search_index() 破坏索引 ⇒ 拿"慢路径"结果
  ③ 断言两者**逐条一致**（id 序列完全相同 ✓）
④ 并加"两边都空"的假绿防线 ✓（否则空对空也会"通过" ✗）
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_storage():
    """按包路径加载 storage.py（它内部有 from .retrieval import … 的相对导入）"""
    name = "z_perf_probe_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(ROOT)]
        sys.modules[name] = pkg
    full = name + ".storage"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, ROOT / "storage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


DOC = [
    "今天在楼下买了三个苹果，脆甜，摊主说是刚到的。",
    "苹果手机的电量有点不够用了，考虑换块电池。",
    "讨论了一下苹果派的配方，肉桂要少放一点。",
    "香蕉和梨都涨价了，苹果价格还算稳定。",
    "上次说要给院子里的苹果树剪枝，拖到现在。",
    "关于那个项目的排期，周四之前给结论。",
    "周五团建改到下午三点，地点还是老地方。",
    "文档里那处笔误已经改了，顺手把格式也整了。",
    "测了一下新网线，延迟稳定在 9 毫秒左右。",
    "咖啡豆快用完了，记得补一包深烘的。",
]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    mod = _load_storage()
    path = tmp_path_factory.mktemp("agree") / "memory.db"
    s = mod.Store(path)
    s.initialize()
    for i, text in enumerate(DOC):
        s.memorize("S1", text, ["U1"], 1_700_000_000 + i, 1_700_000_060 + i, importance=8)
    return s


def _ids(rows):
    """把每行转成**可比较的签名** ✓

    用整行内容做签名（而不是只看 id）—— 因为行里到底叫 id / record_id / rid
    我不想猜 ✗；比整行更严格 ✓ 也顺带比了内容与顺序 ✓
    """
    out = []
    for r in rows:
        try:
            d = dict(r)
        except Exception:
            d = {"_raw": str(r)}
        out.append(tuple(sorted((k, str(v)[:120]) for k, v in d.items())))
    return out


def _pair(store, **kw):
    """同一 query 分别走快路径与慢路径 ✓"""
    store.prepare_search_index()          # 就绪 ⇒ 快路径
    fast = store.search(sid="S1", **kw)
    store.abandon_search_index()          # 破坏 ⇒ 慢路径（全表，参照真相 ✓）
    slow = store.search(sid="S1", **kw)
    store.prepare_search_index()          # 还原，避免影响后续用例
    return _ids(fast), _ids(slow)


def test_keyword_hit_paths_agree(store):
    """① 有关键词命中时，两条路径逐条一致 ✓（且**不为空** ⇒ 不是假绿 ✓）"""
    fast, slow = _pair(store, lexical="苹果")
    assert any(any(ch.isalnum() for ch in str(v)) for sig in fast for _, v in sig), "签名里必须含真实内容 ⇒ 否则是假绿 ✗"
    assert fast == slow, "两条路径结果不一致 ✗ 快路径：%s / 慢路径：%s" % (fast, slow)
    assert len(fast) >= 1, "这个 query 本该命中（苹果出现多次）⇒ 空结果说明测试无效 ✗"


def test_rare_keyword_paths_agree(store):
    """② 只命中一条时仍一致 ✓（候选极少的边界 ✓）"""
    fast, slow = _pair(store, lexical="肉桂")
    assert fast == slow
    assert len(fast) >= 1


def test_no_hit_paths_agree(store):
    """③ 完全不命中时也一致 ✓（两边都空 ⇒ 一致性成立 ✓）"""
    fast, slow = _pair(store, lexical="量子力学相对论")
    assert fast == slow


def test_listing_paths_agree(store):
    """④ 不带关键词（列举型）也一致 ✓"""
    fast, slow = _pair(store, lexical="")
    assert fast == slow
    assert len(fast) >= 1, "列举型本该返回至少一条 ⇒ 空说明测试无效 ✗"


def test_guard_selfcheck_detects_mismatch():
    """⑤ 反向自检：若两条路径真的不同，断言必须能发现 ✓（防守卫写空 ✓）"""
    fast, slow = ["a", "b", "c"], ["a", "c", "b"]
    assert fast != slow, "顺序不同必须被判为不一致 ✓"
    assert ["a", "b"] != ["a", "b", "c"], "条数不同必须被判为不一致 ✓"
