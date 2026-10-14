"""框架符号契约：把插件用到的框架符号/属性**真导入、真反射**核对一遍 ✓

为什么要有这个测试
------------------
KiraAI 每次发版（例如 3.0 `alpha.4` → `3.0.0-rc.1`）都可能出现三类"沉默杀手"：

1. 框架**删掉/改名**了某个符号（`fetch_memory`、`get_session_info` 在 rc.1 里就没了 ✗）
2. 某个方法**从同步变成 async** ✗ ⇒ 插件当同步用 ⇒ 拿到 coroutine 对象 ⇒ **不报错但行为错** ✗
3. 事件/会话**对象属性**没了（插件 `getattr` 拿不到 ⇒ 静默降级 ✗）

这三类都不会让插件"启动失败"，但会在真机上悄悄坏掉 ⇒ 只能在发版时**机械化核对**一遍 ✓
（2026-10-12 的 rc.1 审计结论就是靠它得出的：**0 处不兼容** ✓）

本测试**不需要宿主也能收集**（没有 KIRA_CORE 就 skip ✓）
"""
import ast
import importlib
import inspect
import os
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = os.environ.get("KIRA_CORE", "")
pytestmark = pytest.mark.skipif(not CORE, reason="需要 KIRA_CORE 指向 KiraAI 框架目录")

if CORE and CORE not in sys.path:
    sys.path.insert(0, CORE)


def _member_names(cls):
    """类的"可用成员"= dataclass 字段 ∪ 类属性 ✓

    ★ 坑：dataclass 里**无默认值**的字段，`hasattr(cls, 名字)` 是 False ✗
      （它只存在于 __init__ 签名里 ✓）⇒ 直接 hasattr 会误报"框架把属性删了" ✗
    """
    import dataclasses
    names = {a for a in dir(cls) if not a.startswith("__")}
    if dataclasses.is_dataclass(cls):
        names |= {f.name for f in dataclasses.fields(cls)}
    return names


def _find_class(class_name):
    """在框架源码里按 `class <名字>` 定位并导入它 ✓（不硬编码模块路径 ⇒ 框架挪文件也不误报 ✓）"""
    # 先 core/chat（会话/事件类都在那儿 ✓），再全 core —— 避免抓到同名的 DB 会话类 ✗
    for root in (Path(CORE) / "core" / "chat", Path(CORE) / "core", Path(CORE)):
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not re.search(r"^class\s+%s\b" % re.escape(class_name), text, re.M):
                continue
            rel = path.relative_to(Path(CORE)).with_suffix("")
            mod_name = ".".join(rel.parts)
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                continue
            cls = getattr(mod, class_name, None)
            if isinstance(cls, type):
                return cls
    return None


def _plugin_sources():
    return sorted(p for p in ROOT.glob("*.py") if p.name != "conftest.py")


def _core_imports():
    """从插件源码里抠出所有 `from core... import A, B` ✓（正则足够，且会跟着代码自动更新 ✓）"""
    found = set()
    for path in _plugin_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for mod, names in re.findall(r"^from\s+(core[\w\.]*)\s+import\s+([^\n(]+)", text, re.M):
            for raw in names.split(","):
                name = raw.strip().split(" as ")[0].strip()
                if name and name != "*":
                    found.add((mod, name, path.name))
    return sorted(found)


def _ctx_usages():
    """插件里用到的 `ctx.<成员>`（含 self.ctx.<成员>）✓ 以及**是否被 await** ✓"""
    used = {}
    for path in _plugin_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(?:self\.)?ctx\.([a-z_][\w]*)", text):
            name = m.group(1)
            # ★ 只看**紧邻左侧**是否 await ✓
            #   （曾踩坑：`await self.ctx.plugin_mgr.set_plugin_enabled(...)` 里的 await 属于
            #     后面的方法 ✗，若按"整行含 await"判断就会把 ctx.plugin_mgr 误判成 async ✗）
            left = text[max(0, m.start() - 40):m.start()]
            # 只有"成员后面紧跟 ( "才是对成员本身的调用 ✓
            # `ctx.plugin_mgr.foo()` 这种属性取值 ✗ —— 此时 await 属于 foo ✓ 不能算在成员头上 ✓
            direct_call = text[m.end():m.end() + 8].lstrip().startswith("(")
            await_it = bool(re.search(r"await\s*$", left)) and direct_call
            used.setdefault(name, []).append((path.name, await_it))
    return used


def test_every_framework_import_still_exists():
    """① 插件 import 的每个框架符号，在框架里**真导入**得出来 ✓"""
    bad = []
    for mod_name, sym, where in _core_imports():
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:
            bad.append("%s: import %s 失败（%s）← %s" % (mod_name, mod_name, type(exc).__name__, where))
            continue
        if not hasattr(mod, sym):
            bad.append("%s 里没有 %s ✗ ← %s" % (mod_name, sym, where))
    assert not bad, "框架符号已变化，插件必须跟着改：\n  " + "\n  ".join(bad)


def test_ctx_members_exist_and_await_matches():
    """② 插件用到的 ctx 成员都存在，且**async 状态与调用方式一致** ✓

    反向也要查：本来同步的、插件却 await 了 ⇒ 同样会坏 ✗
    """
    from core.plugin.plugin_context import PluginContext

    # ★ 无默认值的 dataclass 字段用 hasattr 查不到 ✗ ⇒ 字段名 ∪ 类属性都要算 ✓
    known = _member_names(PluginContext)

    problems = []
    for name, sites in _ctx_usages().items():
        if name not in known:
            problems.append("ctx.%s 在**当前框架**的 PluginContext 上不存在 ✗（疑似改名/删除）← %s" % (name, sites[0][0]))
            continue
        member = inspect.getattr_static(PluginContext, name, None)
        async_member = inspect.iscoroutinefunction(member)
        for where, awaited in sites:
            if async_member and not awaited:
                problems.append("ctx.%s 已变为 async，但 %s 里没 await ✗" % (name, where))
            if (not async_member) and awaited:
                problems.append("ctx.%s 是同步的，但 %s 里 await 了 ✗" % (name, where))
    assert not problems, "ctx 契约变化：\n  " + "\n  ".join(sorted(set(problems)))


def test_event_and_session_attributes_exist():
    """③ 插件在事件/会话对象上用到的属性都存在 ✓

    这里只钉住**插件真正依赖**的那些（按代码里的实际用法列 ✓），
    改动插件用法时记得同步这份清单 ✓
    """
    import dataclasses

    event_cls = _find_class("KiraMessageBatchEvent")
    session_cls = _find_class("Session")
    msg_cls = _find_class("KiraIMMessage") or _find_class("KiraMessage")
    assert event_cls is not None, "框架里找不到 KiraMessageBatchEvent 类 ✗（疑似改名）"
    assert session_cls is not None, "框架里找不到会话类 Session ✗（疑似改名）"
    assert msg_cls is not None, "框架里找不到消息类 KiraIMMessage ✗（疑似改名）"

    # ★ 这份清单**就是插件代码里的真实用法**（`grep -ohE "event\.[a-z_]+" main.py` 数出来的 ✓）
    #   改动插件用法时同步这里 ✓ —— 不要凭印象写 ✗（我第一次就把 stop/discard 写到事件类上 ✗，
    #   实际插件用的是 `engine.stop()` 和 `set.discard()` ✓）
    used_event = {"sid", "messages", "event_id", "session", "timestamp"}
    used_msg = {"chain", "timestamp", "is_mentioned"}
    used_session = {"adapter_name", "sid"}
    missing = []
    for attr in sorted(used_event):
        if attr not in _member_names(event_cls):
            missing.append("%s.%s ✗" % (event_cls.__name__, attr))
    for attr in sorted(used_msg):
        if attr not in _member_names(msg_cls):
            missing.append("%s.%s ✗" % (msg_cls.__name__, attr))
    for attr in sorted(used_session):
        if attr not in _member_names(session_cls):
            missing.append("%s.%s ✗" % (session_cls.__name__, attr))
    assert not missing, "事件/会话属性变化：\n  " + "\n  ".join(missing)


def test_plugin_has_no_stale_dunder_or_framework_dataclass_assumptions():
    """④ 防回归：插件里不该再出现"给框架对象的只读 property 赋值"这类写法 ✓

    历史事故：`event.is_mentioned = False` ⇒ 对只读 property 赋值 ⇒ AttributeError
              ⇒ **整条消息被静默丢弃** ✗（真机上表现为"bot 不理人" ✗）
    """
    offenders = []
    for path in _plugin_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                        and target.value.id in ("event", "session")
                        and isinstance(target.ctx, ast.Store)):
                    offenders.append("%s:%d  event/session 属性赋值" % (path.name, target.lineno))
    assert not offenders, "不要给框架对象赋值（可能是只读 property ✗）：\n  " + "\n  ".join(offenders)


def test_manifest_version_spec_allows_current_and_future_frameworks():
    """★ 声明只能有**下界**，不能有上界 —— 否则将来框架一升版插件就装不上 ✗

    背景（2026-10-14，用户要求"以 3.0 正式版及未来更高版本兼容为准"）：
      框架加载期用 PEP 440 规范符 + `prereleases=True` 校验 manifest 的 `core_version`
      ⇒ `>=x.y.z` 这种**只有下界**的写法对 3.0 正式版与未来版本一律放行 ✓
      ⇒ 一旦有人写成 `<3.1` / `>=2,<3` / `==3.0.0` 之类 ✗ 未来版本就会被挡在门外 ✗
    这里把这条约定钉成测试 ✓（配合 `tools/future_compat_probe.py` 的实测 ✓）
    """
    import json as _json

    manifest = _json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    spec = str(manifest.get("core_version") or "").strip()
    assert spec, "manifest 必须有 core_version（否则框架不校验 ⇒ 也不该留空 ✗）"
    assert spec.startswith(">="), "core_version 必须是 >=x.y.z 形式（只有下界）✗ 实际 %r" % spec
    for bad in ("<", ",", "==", "~=", "!=", "*"):
        assert bad not in spec, (
            "core_version 里出现了会挡住未来版本的写法 %r ✗（%s）"
            "—— 只能写下界，未来版本一律放行 ✓" % (bad, spec))
    # 顺带确认兼容测试套件本身也**不硬编码**框架路径（只认 KIRA_CORE ✓）
    _suites = ["tests/test_framework_symbol_contract.py", "tests/test_real_plugin_manager.py",
               "tests/test_upgrade_path_e2e.py"]
    for f in _suites:
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "KIRA_CORE" in src, "%s 必须从 KIRA_CORE 取框架路径 ✓" % f
