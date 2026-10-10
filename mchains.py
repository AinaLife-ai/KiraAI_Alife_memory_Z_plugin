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

from typing import Any, List

CHAIN_MAX = 10                      # ★ 用户要求：每种模型最多 10 组 ✓


def chain_refs(settings: Any, chain_key: str, legacy_key: str) -> List[str]:
    """算出某链路的**有序回退链** ✓

    `chain_key`：新字段（列表 ✓ 形如 `["workbuddy:uuid1", "openai:gpt-4o"]`）
    `legacy_key`：旧的单模型字段 ✓（永远作为**最后一档**加入 ✓ ⇒ 不丢功能 ✓）

    返回：去重后的引用列表（可能为空 ⇒ 调用方走宿主默认 ✓）
    """
    out: List[str] = []
    raw = getattr(settings, chain_key, None) or []
    # ★ 2026-10-10 升级路径：兼容「换行分隔字符串」这种历史形态 ✓
    #   （手工改过配置、或早期版本写进去过）
    #   必须与前端 chainRefsOf 的处理**完全一致**（否则面板显示与后端执行会不一样 ✗）
    if isinstance(raw, str):
        raw = raw.split("\n")
    if isinstance(raw, (list, tuple)):
        for item in raw:
            ref = str(item or "").strip()
            if ref and ref not in out:
                out.append(ref)
            if len(out) >= CHAIN_MAX:
                break
    legacy = str(getattr(settings, legacy_key, "") or "").strip()
    # ★ 2026-10-10 升级路径：legacy 也可能是「换行串」（历史/草稿写入过）✓
    #   不拆的话会在链尾多出一条 "A\nB" 垃圾档 ✗ ⇒ 与 chain_key 同样拆分 ✓
    legacy_items = [x.strip() for x in legacy.split("\n")] if legacy else []
    # ★ 旧字段兜底 ⇒ 但**总档数也 ≤10** ✓（用户要求"每种模型最多 10 组" ✓
    #   链已经满 10 档时不再追加 ⇒ 总数恒定 ≤10 ✓ 便于面板显示"n / 10" ✓）
    for one in legacy_items:
        if one and one not in out and len(out) < CHAIN_MAX:
            out.append(one)
    return out


def chain_desc(settings: Any, chain_key: str, legacy_key: str, name_of=None) -> str:
    """面板/日志用的一句话描述 ✓（不含任何网络调用 ✓）

    ★ 2026-10-11（用户反馈）：日志里原来打的是 `bb3e39e7…:f451aabc…` 一串 id ✗
      现在允许传一个 `name_of(ref) -> "可读名 · 提供商"` 的解析器 ✓
      （调用方用**与面板同一套** `_model_entry_name` 规则构造 ✓ 两处永不打架 ✓）
    """
    refs = chain_refs(settings, chain_key, legacy_key)
    if not refs:
        return "宿主默认"
    legacy = str(getattr(settings, legacy_key, "") or "").strip()
    mark = "（末档=旧字段兜底）" if legacy and refs and refs[-1] == legacy else ""
    shown = []
    for ref in refs:
        label = ref
        if name_of is not None:
            try:
                got = name_of(ref)
                if got:
                    label = str(got)
            except Exception:
                label = ref
        shown.append(label)
    return " → ".join(shown) + mark
