"""⑥ 模型显示名（3.0 uuid ✗ → 可读名 ✓）与 ① 双世代取客户端适配器 ✓

背景（2026-10-10 审查）
- 3.0（model_config_version=2）的 `model_config[kind]` 键是 **uuid** ✗
  可读名在 `entry["model_name"]` ✓，旧引用在 `entry["legacy_ids"]` ✓
  ⇒ 旧代码直接把 uuid 当名字返回 ✗ ⇒ 面板显示 `workbuddy · f451aabc…`（用户看不懂 ✗ 截图实证）
- 3.0 的 `get_llm_client` 第一参数叫 **model_uuid** ✓（官方内置插件一律写关键字 ✓）
  2.x 只有位置参数 ✗ ⇒ 用 inspect 看签名，**一次判定、结果缓存** ✓
"""

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
pkg = types.ModuleType("alife_memz_a")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_memz_a", pkg)
m = importlib.import_module("alife_memz_a.main")

_BRIDGE = next(
    obj for name, obj in vars(m).items()
    if inspect.isclass(obj) and hasattr(obj, "_llm_client")
)
name_of = _BRIDGE._model_entry_name


def test_30_entry_returns_readable_name_and_legacy():
    """3.0 v2：uuid 键 + model_name ⇒ 必须拿到可读名 ✓ legacy 也要带出来 ✓"""
    entry = {"model_name": "gpt-4o", "config": {"temperature": 0.2}, "legacy_ids": ["gpt-4o", "old-name"]}
    assert name_of("f451aabc23ea5724ab64a85", entry) == ("gpt-4o", ["gpt-4o", "old-name"])


def test_2x_entry_falls_back_to_key():
    """2.x / v1：键本身就是可读名 ⇒ 原样返回 ✓（零回归 ✓）"""
    assert name_of("gpt-4o", {"api_key": "x", "base_url": "https://x"}) == ("gpt-4o", [])
    assert name_of("gpt-4o", None) == ("gpt-4o", [])
    assert name_of("gpt-4o", "not-a-dict") == ("gpt-4o", [])


def test_empty_model_name_falls_back():
    """3.0 里 model_name 缺失/空白 ⇒ 回落键 ✓（绝不返回空标签 ✗）"""
    assert name_of("uuid-1", {"model_name": "  ", "legacy_ids": ["a"]}) == ("uuid-1", [])


def _stub(ctx):
    obj = type("Stub", (), {})()
    obj.ctx = ctx
    return obj


class _Ctx30:
    """3.0 形状：model_uuid 关键字 ✓"""
    def __init__(self):
        self.calls = []

    def get_llm_client(self, model_uuid=None, llm_type=None):
        self.calls.append(("kw", model_uuid, llm_type))
        return {"client": model_uuid}


class _Ctx20:
    """2.x 形状：只有位置参数 ✗（传 model_uuid= 会 TypeError）"""
    def __init__(self):
        self.calls = []

    def get_llm_client(self, model):
        self.calls.append(("pos", model))
        return {"client": model}


def test_ref_uses_keyword_on_30():
    ctx = _Ctx30()
    got = _BRIDGE._llm_client(_stub(ctx), "workbuddy:uuid1")
    assert got == {"client": "workbuddy:uuid1"} and ctx.calls == [("kw", "workbuddy:uuid1", None)]


def test_ref_uses_positional_on_2x():
    ctx = _Ctx20()
    got = _BRIDGE._llm_client(_stub(ctx), "workbuddy:gpt-4o")
    assert got == {"client": "workbuddy:gpt-4o"} and ctx.calls == [("pos", "workbuddy:gpt-4o")]


def test_ref_probes_signature_once():
    """世代判定只做一次 ✓（热路径不反复判 ✓ "不冗余" ✓）"""
    ctx = _Ctx30(); obj = _stub(ctx)
    seen = []
    orig = inspect.signature
    inspect.signature = lambda f: (seen.append(1), orig(f))[1]
    try:
        for _ in range(3):
            _BRIDGE._llm_client(obj, "p:m")
    finally:
        inspect.signature = orig
    assert len(seen) == 1, "signature 只该看一次 ✗（实际 %d 次）" % len(seen)


def test_frontend_shows_readable_name_first_and_matches_legacy():
    """结构判据：下拉必须「可读名在前」✓ 且能按 legacy 回显旧配置 ✓"""
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "m.name || m.model || m.id" in js, "标签没把可读名放最前 ✗"
    assert "legacy.includes(value)" in js, "没按 legacy 回显（老配置会看着像没配 ✗）"
    assert '" · 向量"' in js, "embedding 没标种类徽标 ✗"
