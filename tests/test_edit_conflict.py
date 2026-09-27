"""编辑的版本比对：**纯删除 / 明确覆盖 ⇒ 跳过**，真编辑仍拦（2026-09-27 用户实测）

现象：从「任务明细 → 查看与编辑」进编辑器时带的是**快照里的旧版本号** ✗
      ⇒ 想删一条事实却被 409「别的地方已经改了」拦住 ✗
      （用户："我还得到事实页那边搜索了自己手动删" ✗）
修：① **纯删除不参与版本比对** ✓（删的是"删除"本身，不覆盖别人的内容 ✓ 且回收站可还原 ✓）
    ② UI 明确确认「以我的版本覆盖」时传 force ✓ 同样跳过 ✓
    ③ 真·编辑撞旧版本 ⇒ **仍然 409** ✓（这条安全线不能丢 ✗）
"""

import importlib
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("alife_editc")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_editc", pkg)
# main 依赖宿主框架（core.*）⇒ 需要 KIRA_CORE 指向真框架 ✓（没有就跳过整文件 ✓）
if os.environ.get("KIRA_CORE") and os.environ["KIRA_CORE"] not in sys.path:
    sys.path.insert(0, os.environ["KIRA_CORE"])
try:
    main = importlib.import_module("alife_editc.main")
    contracts = importlib.import_module("alife_editc.contracts")
except Exception as _exc:                            # noqa: BLE001
    pytest.skip("需要宿主框架（设 KIRA_CORE）：%r" % (_exc,), allow_module_level=True)
SKIP = main.edit_skip_revision


def _edit(**kw):
    base = dict(kind="fact", target="f1", patch={"content": "x"}, reason="t")
    base.update(kw)
    return contracts.Edit(**base)


def test_pure_delete_skips_revision_check():
    """★ 纯删除 ⇒ 跳过比对 ✓（想删就删，不被"别处改过"拦住 ✓）"""
    payload, skip = SKIP(_edit(patch={"deleted": True}, revision=3))
    assert skip, "纯删除必须跳过版本比对 ✓"
    assert payload["revision"] == 3, "跳过由调用方把 revision 置空 ✓（纯函数不改它 ✓）"


def test_delete_with_extra_keys_is_not_pure_delete():
    """删除**同时**还改了别的字段 ⇒ 不算纯删除 ✓（那就还是会比对 ✓ 安全 ✓）"""
    _p, skip = SKIP(_edit(patch={"deleted": True, "importance": 3}, revision=3))
    assert not skip


def test_force_skips_and_strips_force_from_payload():
    """★ force ⇒ 跳过 ✓，且 **force 不能透传给 store.edit** ✗（它不认这个参数 ⇒ TypeError）"""
    payload, skip = SKIP(_edit(patch={"content": "覆盖"}, revision=3, force=True))
    assert skip, "force 必须跳过版本比对 ✓"
    assert "force" not in payload, "force 必须从 payload 里去掉 ✗（否则 store.edit TypeError ✗）"


def test_real_edit_keeps_revision_check():
    """★★ 安全线：真·编辑 ⇒ **不跳过** ✓（仍然比对版本 ✓）"""
    payload, skip = SKIP(_edit(patch={"content": "改一下"}, revision=3))
    assert not skip, "真编辑不能跳过比对 ✗"
    assert payload["revision"] == 3
