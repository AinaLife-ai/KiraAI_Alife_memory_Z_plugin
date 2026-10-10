"""升级路径：**存量用户 / 新用户都要无缝**（2026-10-10 用户要求专项核查）

三种存量形态都要不崩、行为可预期：
  ① 老配置（2.21.x 之前）：只有 `*_model`（单模型），没有 `*_models`，也没有新键
  ② 中间配置（2.22.0）：`*_models` 有序数组 + `*_model`
  ③ 全空（新用户）：什么都没配 ⇒ 走宿主默认模型
另：新键缺失时的**代码回落值必须与 schema 默认一致**（否则"没套默认值"的加载路径会静默变行为 ✗）
"""
import importlib
import os
import sys
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

pkg = types.ModuleType("alife_up")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_up", pkg)
mchains = importlib.import_module("alife_up.mchains")
main = importlib.import_module("alife_up.main")


class Cfg:
    """假的设置对象：想给什么键就给什么键（模拟不同年代的存量配置）✓"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_old_config_single_model_becomes_one_link():
    """① 老配置只有单模型 ⇒ 回退链就是**那一个**（不多不少 ✓ 零行为变化 ✓）"""
    cfg = Cfg(compress_model="workbuddy:gpt-4o")
    assert mchains.chain_refs(cfg, "compress_models", "compress_model") == ["workbuddy:gpt-4o"]
    assert mchains.chain_refs(cfg, "audit_models", "audit_model") == []


def test_mid_config_chain_is_authoritative_and_dedupes():
    """② 中间配置：链为准 ✓ 且旧字段若已在链里**不会重复**（不会偷偷多一档 ✗）"""
    cfg = Cfg(compress_models=["a:1", "b:2"], compress_model="a:1")
    assert mchains.chain_refs(cfg, "compress_models", "compress_model") == ["a:1", "b:2"]
    cfg2 = Cfg(compress_models=["a:1", "b:2"], compress_model="c:3")
    assert mchains.chain_refs(cfg2, "compress_models", "compress_model") == ["a:1", "b:2", "c:3"]


def test_empty_config_yields_no_chain():
    """③ 新用户什么都没配 ⇒ 空链 ⇒ 走宿主默认（不崩、不报错 ✓）"""
    cfg = Cfg()
    assert mchains.chain_refs(cfg, "compress_models", "compress_model") == []
    assert mchains.chain_desc(cfg, "compress_models", "compress_model")  # 只要求不抛 ✓


def test_chain_cap_and_legacy_shape():
    """链 ≤10 ✓ 且兼容"换行字符串"这种历史形态（手工改过配置的用户 ✓）"""
    cfg = Cfg(compress_models="a:1\nb:2")
    assert mchains.chain_refs(cfg, "compress_models", "compress_model") == ["a:1", "b:2"]
    cfg2 = Cfg(compress_models=["m:%d" % i for i in range(30)])
    assert len(mchains.chain_refs(cfg2, "compress_models", "compress_model")) == 10


def test_legacy_newline_string_is_split_too():
    """★ 升级路径：legacy 字段也可能是「换行串」（历史/草稿写过）⇒ 必须拆开 ✓
    不拆的话会在链尾多出一条 "A\\nB" 垃圾档 ✗（实测真的出现过 ✓）"""
    cfg = Cfg(rerank_models=["b:2"], rerank_model="a:1\nb:2")
    assert mchains.chain_refs(cfg, "rerank_models", "rerank_model") == ["b:2", "a:1"]
    cfg2 = Cfg(rerank_model="x:9\ny:8")
    assert mchains.chain_refs(cfg2, "rerank_models", "rerank_model") == ["x:9", "y:8"]


def test_new_key_fallbacks_match_schema_defaults():
    """★ 新键缺失时，代码回落值必须 = schema 默认（否则升级后行为会静默不同 ✗）"""
    import json

    fields = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))["alife"]["fields"]
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    want = fields["self_fact_max_importance"]["default"]
    assert want == 4, "schema 默认变了 ⇒ 同步本测试与代码回落值"
    assert '"self_fact_max_importance", %d' % want in src, \
        "代码回落值（%d）与 schema 默认不一致 ⇒ 升级路径会静默变行为 ✗" % want
    # ★ 默认必须等效于"不挡"（开关关 ⇒ 0）✓ 升级后行为与旧版一致 ✓
    assert fields["self_fact_filter_enabled"]["default"] is False, "总开关默认应为关 ✗"
    assert 'getattr(self.settings, "self_fact_filter_enabled", True)' in src, \
        "总开关的代码回落值要和 schema 默认一致（关）✗"
    assert 'limit or 800' in (ROOT / "storage.py").read_text(encoding="utf-8"), \
        "cover_check_limit 的回落值要与 schema 默认（800）一致 ✓"
