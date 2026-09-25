"""主动召回扩池（2026-09-25）：查档案/看画像/overview 先扩候选再精修并截回 ✓

为什么要扩：精修只在**取回来的那批**里挑 ⇒ 池太小时，
「词法排不进前 N、但语义确实相关」的记忆根本没机会被看到 ✗
安全底线：未启用 JEV / 决策层未就绪 / inline 模式 ⇒ **逐字等于旧行为** ✓
"""

import importlib
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_toolx")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_toolx", pkg)

# main 依赖宿主框架（core.*）⇒ 需要 KIRA_CORE 指向真框架 ✓（没有就跳过整文件 ✓）
_CORE = os.environ.get("KIRA_CORE")
if _CORE and _CORE not in sys.path:
    sys.path.insert(0, _CORE)
try:
    main = importlib.import_module("alife_toolx.main")
except Exception as _exc:                        # noqa: BLE001
    pytest.skip("需要宿主框架（设 KIRA_CORE 指向真框架）：%r" % (_exc,),
                allow_module_level=True)

EXPAND = main.AlifeMemoryPlugin._tool_expand


class _Fake:
    def __init__(self, mode="expand", enabled=True, ready=True):
        self.settings = SimpleNamespace(
            tool_refine_mode=mode, jev_enabled=enabled, jev_recall=enabled)
        self.decisions = (SimpleNamespace(ready=True) if ready else None)


def test_expands_when_jev_ready():
    assert EXPAND(_Fake(), 20) == 60


def test_caps_pool_at_max():
    assert EXPAND(_Fake(), 50) == main.TOOL_POOL_MAX == 60


def test_tiny_limit_still_expands_but_not_below_want():
    assert EXPAND(_Fake(), 3) == 9
    assert EXPAND(_Fake(), 0) == 0


def test_inline_mode_never_expands():
    assert EXPAND(_Fake(mode="inline"), 20) == 20


def test_jev_disabled_never_expands():
    assert EXPAND(_Fake(enabled=False), 20) == 20


def test_not_ready_never_expands():
    """★ 关键安全线：配了但没就绪 ⇒ 不扩（否则扩了没人筛 ⇒ 反而塞更多 ✗）"""
    assert EXPAND(_Fake(ready=False), 20) == 20


def test_bad_settings_never_raise():
    class _Broken:
        @property
        def settings(self):
            raise RuntimeError("boom")

        decisions = None

    assert EXPAND(_Broken(), 20) == 20
