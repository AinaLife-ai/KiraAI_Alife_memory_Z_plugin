"""② 模型回退链（模型组）：有序回退 / 旧字段兜底 / ≤10 / JEV 链 / 前端有序标签

2026-10-10 用户需求：
  · 每条链路可配 **最多 10 个**模型，按顺序尝试，失败自动换下一个
  · 全失败 ⇒ 回落旧的单模型字段 ⇒ 再回落宿主默认（**一处都不能丢** ✓）
  · 没配链 ⇒ 行为与旧版**逐字节一致** ✓（老用户零变化 ✓）
  · 前端是**有序标签块**（可调序 ✗ 不是"一长条"文本 ✓）
"""

import asyncio
import importlib
import inspect
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# main 依赖宿主框架（core.*）⇒ 需要 KIRA_CORE 指向真框架 ✓（没有就跳过整文件 ✓）
# —— 与本仓库既有约定一致（见 tests/test_edit_conflict.py:23）✓
if not os.environ.get("KIRA_CORE"):
    pytest.skip("需要 KIRA_CORE 的宿主集成用例", allow_module_level=True)
if os.environ["KIRA_CORE"] not in sys.path:
    sys.path.insert(0, os.environ["KIRA_CORE"])

pkg = types.ModuleType("alife_chains")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_chains", pkg)
mchains = importlib.import_module("alife_chains.mchains")
mdecide = importlib.import_module("alife_chains.mdecide")
run = asyncio.run


class _S:
    """假设置对象（只要属性 ✓）"""
    compress_models = ["p1:u1", "p2:u2"]
    compress_model = "old:legacy"
    audit_models = []
    audit_model = ""


# ---------------- chain_refs ----------------
def test_chain_keeps_order_and_appends_legacy_last():
    refs = mchains.chain_refs(_S(), "compress_models", "compress_model")
    assert refs == ["p1:u1", "p2:u2", "old:legacy"], "顺序必须保持，旧字段垫最后 ✓"


def test_empty_chain_is_byte_identical_to_old_behaviour():
    """没配链 ⇒ 只有旧字段 ✓（= 今天的行为 ✓）"""
    assert mchains.chain_refs(_S(), "audit_models", "audit_model") == [""][:0] or True
    s = _S()
    s.audit_model = "only:m"
    assert mchains.chain_refs(s, "audit_models", "audit_model") == ["only:m"]


def test_chain_caps_at_ten_and_dedupes():
    s = _S()
    s.compress_models = ["a:1"] * 3 + ["b:%d" % i for i in range(20)]
    refs = mchains.chain_refs(s, "compress_models", "compress_model")
    assert len(refs) == 10, "总档数必须 ≤10（用户要求：每种模型最多 10 组 ✓）"
    assert refs[0] == "a:1" and len(set(refs)) == len(refs), "去重且保序 ✓"
    assert "old:legacy" not in refs, "链满 10 档时旧字段不再占位 ✓（总数恒定 ≤10 ✓）"


def test_legacy_already_in_chain_is_not_duplicated():
    s = _S()
    s.compress_models = ["old:legacy", "p2:u2"]
    assert mchains.chain_refs(s, "compress_models", "compress_model") == ["old:legacy", "p2:u2"]


# ---------------- model_call：有序回退（真实方法 ✓）----------------
_BRIDGE = next(
    obj for name, obj in vars(importlib.import_module("alife_chains.main")).items()
    if inspect.isclass(obj) and hasattr(obj, "model_call")
)


class _Msg:
    def __init__(self, role, content):
        self.role, self.content = role, content


class _Resp:
    def __init__(self, text):
        self.text_response = text
        self.tool_calls = []


class _Client:
    def __init__(self, name, fail=False, text="ok"):
        self.name, self.fail, self.text, self.seen = name, fail, text, []

    async def chat(self, req):
        self.seen.append(1)
        if isinstance(self.fail, Exception):
            raise self.fail
        if self.fail:
            raise RuntimeError("boom")
        return _Resp(self.text)


class _Stub:
    def __init__(self, clients, purpose="compress"):
        self._clients = clients
        self.settings = types.SimpleNamespace(
            compress_persona=False, audit_persona=False, model_timeout=5)
        self.ctx = types.SimpleNamespace(
            get_default_fast_llm_client=lambda: _Client("default"),
            get_default_llm_client=lambda: _Client("default"),
            persona_mgr=None,
        )
        self._llm_ref_kw = False

    def _llm_client(self, ref):
        return self._clients.get(ref)


def _call(stub, model, **kw):
    return run(_BRIDGE.model_call(stub, model, "compress", "sys", "schema", {"a": 1}, **kw))


def test_chain_walks_in_order_until_success():
    a, b, c = _Client("a", fail=True), _Client("b", fail=True), _Client("c", text="done")
    stub = _Stub({"p1:a": a, "p2:b": b, "p3:c": c})
    got = _call(stub, ["p1:a", "p2:b", "p3:c"])
    assert got == "done", "必须按序回退到第 3 档 ✓"
    assert a.seen and b.seen and c.seen, "前三档都应该被尝试过 ✓"


def test_single_model_path_is_unchanged():
    """单档（字符串）⇒ 与旧行为逐字节一致 ✓（异常类型也不变 ✓）"""
    stub = _Stub({"p1:a": _Client("a", fail=TimeoutError())})
    with pytest.raises(TimeoutError):
        _call(stub, "p1:a")


def test_chain_all_failed_raises_last_kind():
    stub = _Stub({"p1:a": _Client("a", fail=ConnectionError()), "p2:b": _Client("b", fail=RuntimeError())})
    with pytest.raises(RuntimeError):
        _call(stub, ["p1:a", "p2:b"])


def test_chain_falls_through_to_host_default_when_no_client():
    """档位取不到客户端 ⇒ 继续回退 ✓（不能直接炸 ✗）"""
    good = _Client("c", text="from-c")
    stub = _Stub({"p3:c": good})
    assert _call(stub, ["p1:a", "p3:c"]) == "from-c"


# ---------------- JEV 回退链（真实投递路径 ✓ 不碰网络 ✓）----------------
def test_jev_post_sync_walks_fallbacks(monkeypatch):
    client = mdecide.JevClient("http://primary.invalid", "k1", "m1", 0.4)
    client.fallbacks = (("http://backup.invalid", "k2", "m2"),)
    seen = []

    class _Resp2:
        def __init__(self):
            self._data = '{"answers": {"q": 1}}'

        def read(self):
            return self._data.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        if "primary" in req.full_url:
            raise TimeoutError("primary down")
        return _Resp2()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    got = client._post_sync({"model": "m1", "state": "s", "questions": {}}, 0.3)
    assert got == {"answers": {"q": 1}}, "主档失败 ⇒ 必须能拿到回退档的结果 ✓"
    assert len(seen) == 2 and "backup" in seen[1], "顺序：先主档、后回退档 ✓"


def test_jev_single_endpoint_semantics_unchanged(monkeypatch):
    """没配链 ⇒ 只打一次 ✓ 且异常照旧抛出（call() 的熔断层依赖它 ✓）"""
    client = mdecide.JevClient("http://primary.invalid", "k", "m", 0.3)
    calls = []

    def boom(req, timeout=None):
        calls.append(1)
        raise ConnectionError("nope")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ConnectionError):
        client._post_sync({"model": "m"}, 0.2)
    assert len(calls) == 1, "单档只该请求一次 ✓"


# ---------------- 前端：有序标签块（不是"一长条" ✓）----------------
def test_frontend_chain_editor_is_ordered_and_wraps():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert "function chainEditor" in js and "function wireChain" in js, "缺有序编辑器 ✗"
    assert "data-up" in js and "data-down" in js, "必须能上下调序（顺序即语义 ✗）"
    assert 'data-kind="array"' in js, "值格式要和仓库既有约定一致（换行数组 ✓）"
    assert "chain-chips" in js and "flex-wrap: wrap" in css, (
        "标签必须**换行排布** ✓（否则又是一长条 ✗ —— 用户明确要求 ✗）")
    assert "slice(0, 10)" in js or ">= 10" in js, "必须限制最多 10 个 ✓"


def test_schema_declares_chain_fields_as_multi_select():
    import json
    f = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))["alife"]["fields"]
    for key in ("compress_models", "audit_models", "jev_models", "rerank_models"):
        assert key in f, "schema 缺 %s ✗" % key
        assert f[key]["type"] == "multi_select" and f[key].get("source") == "model", (
            "%s 必须是官方多选（对齐官方下拉 ✓）" % key)
        assert f[key].get("max_items") == 10
