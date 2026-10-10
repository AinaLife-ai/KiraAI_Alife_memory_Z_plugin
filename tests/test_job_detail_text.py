"""后台任务详情文案：**绝不许把 None 漏给用户**（2026-10-11 用户实测）

用户现象（截图）：后台任务里两条「事实审计」都显示
    「本次审计 None 条事实」✗
看着像出了故障，其实是：**这次没有需要审计的事实**（都已审过 ✓）

根因两处（都是我/历史留下的）：
  1. `engine.audit()` 在"没有候选"时**裸 return** ⇒ None ✗
  2. `audit_summary()` 的兜底写成 `"本次审计 %s 条事实" % counts` ✗
     ⇒ 把 None 当数字格式化 ⇒ 把内部状态漏给了用户 ✗
修法：空转**给出人话原因**（与 compress_summary 同风格 ✓）；兜底也说人话 ✓
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if not __import__("os").environ.get("KIRA_CORE"):
    pytest.skip("需要 KIRA_CORE 的宿主集成用例", allow_module_level=True)
if __import__("os").environ["KIRA_CORE"] not in sys.path:
    sys.path.insert(0, __import__("os").environ["KIRA_CORE"])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pkg = types.ModuleType("alife_detail")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_detail", pkg)
engine = importlib.import_module("alife_detail.engine")


def test_none_never_leaks_to_user():
    """★ 兜底：counts 是 None/别的类型时，**绝不能**出现 "None 条事实" ✗"""
    for bad in (None, [], (), 0, "x"):
        text = engine.audit_summary(bad)
        assert "None" not in text, "把 None 漏给用户了 ✗：%r" % text
        assert "本次审计" in text
    assert "无待审内容" in engine.audit_summary(None)


def test_empty_audit_explains_why():
    """★ 空转要讲**为什么**（只写"0 条事实"等于没说 ✓）"""
    text = engine.audit_summary({
        "scanned": 0,
        "why": "没有待审校的事实（本会话还没有事实，或都已在复查周期内审校过）",
    })
    assert "没有待审校的事实" in text and "本次审计 0 条" in text


def test_real_counts_still_render():
    """真实统计照旧 ✓（别把正常路径改坏 ✗）"""
    text = engine.audit_summary({"scanned": 12, "correct": 1, "kept": 11})
    assert "12 条" in text and "修正 1" in text


def test_audit_empty_candidates_returns_reason_not_none():
    """★ 直接钉住根因：空候选必须返回 dict（带 why），**不能是 None** ✗"""
    src = (ROOT / "engine.py").read_text(encoding="utf-8")
    i = src.index("async def audit(self")
    seg = src[i:i + 1400]
    assert "if not candidates:" in seg
    after = seg[seg.index("if not candidates:") :][:320]
    assert "return {" in after and '"why"' in after, "空候选仍是裸 return ⇒ 会再漏 None ✗"
    assert "return\n" not in after.split("return {")[0], "空候选不该裸 return ✗"
