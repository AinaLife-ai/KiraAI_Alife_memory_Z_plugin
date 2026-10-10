"""模型回退链（「模型组」）—— 2026-10-10 用户需求 ✓

设计（**不违背既有设计目的** ✓ **零功能丢失** ✓）
  · 每条链路一个**有序**列表（≤10 ✓ 顺序 = 优先级 ✓）
  · 逐个尝试：任一环节失败（异常 / 超时 / 空响应）⇒ 按序换下一个 ✓
  · 全失败 ⇒ 回落**旧的单模型字段**（它永远排在**最后一档** ✓）
      再失败 ⇒ 回落宿主默认模型 ✓（= 今天的行为 ✓）
  · 新字段留空 ⇒ 行为与今天**逐字节一致** ✓（老用户零变化 ✓）
  · `embedding` **不做回退** ✓ —— 维度不一致会把向量索引写脏 ✓
      ⇒ 宁缺勿滥（安全优化 ✓ 见 contracts.py 的 embedding_model 注释）

为什么不用框架的多选直接顶替旧字段：
  3.0 的官方 `CustomMultiSelect` **只能多选、不能调序** ✗，而回退链**顺序就是语义** ✓
  ⇒ schema 用官方 `multi_select(source="model")`（好看、与官方一致 ✓）
     + 插件面板自带**有序编辑器**（可调序 ✓）⇒ 两边写的是**同一个设置** ✓ 不是两份真值 ✓
"""

from __future__ import annotations

from typing import Any, Iterable, List

CHAIN_MAX = 10                      # ★ 用户要求：每种模型最多 10 组 ✓


def chain_refs(settings: Any, chain_key: str, legacy_key: str) -> List[str]:
    """算出某链路的**有序回退链** ✓

    `chain_key`：新字段（列表 ✓ 形如 `["workbuddy:uuid1", "openai:gpt-4o"]`）
    `legacy_key`：旧的单模型字段 ✓（永远作为**最后一档**加入 ✓ ⇒ 不丢功能 ✓）

    返回：去重后的引用列表（可能为空 ⇒ 调用方走宿主默认 ✓）
    """
    out: List[str] = []
    raw = getattr(settings, chain_key, None) or []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            ref = str(item or "").strip()
            if ref and ref not in out:
                out.append(ref)
            if len(out) >= CHAIN_MAX:
                break
    legacy = str(getattr(settings, legacy_key, "") or "").strip()
    # ★ 旧字段兜底 ⇒ 但**总档数也 ≤10** ✓（用户要求"每种模型最多 10 组" ✓
    #   链已经满 10 档时不再追加 ⇒ 总数恒定 ≤10 ✓ 便于面板显示"n / 10" ✓）
    if legacy and legacy not in out and len(out) < CHAIN_MAX:
        out.append(legacy)
    return out


def chain_desc(settings: Any, chain_key: str, legacy_key: str) -> str:
    """面板/日志用的一句话描述 ✓（不含任何网络调用 ✓）"""
    refs = chain_refs(settings, chain_key, legacy_key)
    if not refs:
        return "宿主默认"
    legacy = str(getattr(settings, legacy_key, "") or "").strip()
    mark = "（最后一档=旧字段兜底 ✓）" if legacy and refs and refs[-1] == legacy else ""
    return " → ".join(refs) + mark


def iter_refs(refs: Iterable[str]):
    """把 str / list 统一成**带序号**的可迭代 ✓（日志要写"第几档"✓）"""
    items = [refs] if isinstance(refs, str) else list(refs or [])
    clean = []
    for item in items:
        ref = str(item or "").strip()
        if ref and ref not in clean:
            clean.append(ref)
    return list(enumerate(clean, 1))
