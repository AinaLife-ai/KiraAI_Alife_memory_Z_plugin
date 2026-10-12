"""真 PluginManager 加载：插件要能在**真 App 的加载路径**上活下来 ✓

为什么不能只用手搓的假 ctx
--------------------------
本仓库多数测试是"手搓 ctx + 直接 new 插件" ✓ 快、稳 ✓
但真实 App 走的是另一条路：**发现插件目录 → 解析 manifest → 导入包 → 关联数据目录 → 实例化 → initialize**
⇒ 这条路上任何一环变化（manifest 字段、包名规则、`get_plugin_data_dir()` 的关联方式 ✗）
   手搓测试都发现不了 ✗，而真机会直接"插件不工作" ✗

2026-10-12 的 rc.1 审计就用它验过：rc.1 与 2.x **结果完全一致** ✓

★ 踩坑留档：若 `ctx.plugin_mgr` 没接上，框架的 `get_plugin_data_dir()` 会返回 None ✗
  ⇒ 插件报 "did not associate plugin data directory" ✗ —— **那是桩的问题，不是插件的问题** ✗
  真 App 在 `core/lifecycle.py:312` 接的正是这一句 ✓ 本测试照抄 ✓
"""
import asyncio
import dataclasses
import inspect
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = os.environ.get("KIRA_CORE", "")
pytestmark = pytest.mark.skipif(not CORE, reason="需要 KIRA_CORE 指向 KiraAI 框架目录")

if CORE and CORE not in sys.path:
    sys.path.insert(0, CORE)

PLUGIN_ID = "alife_memory_z"
# 真加载只需要这些（web/ 前端与 tools/ 脚本不参与启动 ✓）
NEEDED = ("manifest.json", "schema.json", "icon.png")


def _stage_plugin(tmp_path):
    """把插件铺成真 App 认识的样子：<plugin_dir>/<插件名>/ ✓"""
    target = tmp_path / "plugins" / PLUGIN_ID
    target.mkdir(parents=True)
    for path in ROOT.glob("*.py"):
        shutil.copy2(path, target / path.name)
    for name in NEEDED:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, target / name)
    return target


def _ctx_kwargs():
    """按当前世代真实的 PluginContext 字段搭桩 ✓（2.x 与 3.0 字段不同 ✓ 所以是运行时读的 ✓）"""
    from core.plugin.plugin_context import PluginContext

    if dataclasses.is_dataclass(PluginContext):
        names = [f.name for f in dataclasses.fields(PluginContext)]
    else:
        params = inspect.signature(PluginContext.__init__).parameters
        names = [n for n in params if n != "self"]

    kwargs = {}
    for name in names:
        if name in ("tool_mgr", "tool_manager"):
            kwargs[name] = types.SimpleNamespace(register_tool=lambda *a, **k: True,
                                                 get_tools=lambda *a, **k: [])
        elif name == "provider_mgr":
            kwargs[name] = types.SimpleNamespace(get_model_info=lambda m: None, list_models=lambda: [])
        elif name == "persona_mgr":
            kwargs[name] = types.SimpleNamespace(list_personas=lambda: [], get_persona=lambda i: None,
                                                 set_active_persona=lambda i: True, personas=[])
        elif name == "config":
            kwargs[name] = {}
        else:
            kwargs[name] = None
    return kwargs


def test_plugin_loads_through_real_plugin_manager(tmp_path):
    """★ 真加载：发现 → 导入 → 实例化 → initialize，一步都不少 ✓"""
    from core.plugin import PluginContext, PluginManager

    _stage_plugin(tmp_path)
    ctx = PluginContext(**_ctx_kwargs())
    mgr = PluginManager(ctx)
    mgr.plugin_dir = tmp_path / "plugins"
    # ★ 真 App 也是这么接的（core/lifecycle.py:312）—— 缺了它插件取不到数据目录 ✗
    ctx.plugin_mgr = mgr

    async def run():
        await mgr.init()
        inst = (getattr(mgr, "plugin_instances", None) or {}).get(PLUGIN_ID)
        errors = getattr(mgr, "get_plugin_load_errors", lambda: {})()
        err = errors.get(PLUGIN_ID)
        if inst is not None and hasattr(inst, "terminate"):
            try:
                await inst.terminate()
            except Exception:
                pass
        return inst, err

    inst, err = asyncio.run(run())
    assert err is None, "真加载器报错：%s" % err
    assert inst is not None, "插件没有出现在 plugin_instances 里 ✗（真 App 里就等于「插件没加载」✗）"


def test_initialized_plugin_has_engine_and_store(tmp_path):
    """★ 加载后：引擎/库/设置/钩子 都就位 ✓（等于"插件真的能干活" ✓）"""
    from core.plugin import PluginContext, PluginManager

    _stage_plugin(tmp_path)
    ctx = PluginContext(**_ctx_kwargs())
    mgr = PluginManager(ctx)
    mgr.plugin_dir = tmp_path / "plugins"
    ctx.plugin_mgr = mgr

    async def run():
        await mgr.init()
        inst = (getattr(mgr, "plugin_instances", None) or {}).get(PLUGIN_ID)
        result = {
            "engine": getattr(inst, "engine", None) is not None,
            "store": getattr(inst, "store", None) is not None,
            "settings": getattr(inst, "settings", None) is not None,
            "hooks": len(getattr(inst, "handlers", {}) or {}) or len(
                [a for a in dir(inst) if a.startswith("on_")]),
        }
        if inst is not None and hasattr(inst, "terminate"):
            try:
                await inst.terminate()
            except Exception:
                pass
        return result

    r = asyncio.run(run())
    assert r["engine"], "引擎没就位 ✗"
    assert r["store"], "记忆库没就位 ✗"
    assert r["settings"], "设置没读到 ✗"
    assert r["hooks"], "一个钩子都没注册 ✗"


def test_no_load_error_when_ctx_missing_optional_managers(tmp_path):
    """★ 容错：可选依赖缺席（没有 persona_mgr / provider_mgr）时也应**加载成功** ✓

    真机上用户可能没配人格库/提供商 ⇒ 插件必须能起来（功能降级可以 ✓，起不来不行 ✗）
    """
    from core.plugin import PluginContext, PluginManager

    _stage_plugin(tmp_path)
    kwargs = _ctx_kwargs()
    for optional in ("persona_mgr", "provider_mgr", "session_mgr"):
        if optional in kwargs:
            kwargs[optional] = None
    ctx = PluginContext(**kwargs)
    mgr = PluginManager(ctx)
    mgr.plugin_dir = tmp_path / "plugins"
    ctx.plugin_mgr = mgr

    async def run():
        await mgr.init()
        inst = (getattr(mgr, "plugin_instances", None) or {}).get(PLUGIN_ID)
        err = (getattr(mgr, "get_plugin_load_errors", lambda: {})() or {}).get(PLUGIN_ID)
        if inst is not None and hasattr(inst, "terminate"):
            try:
                await inst.terminate()
            except Exception:
                pass
        return err

    assert asyncio.run(run()) is None, "缺少可选管理器时加载失败 ✗（真机会表现为插件不工作 ✗）"
