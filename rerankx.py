"""重排适配器（**可选**）：优先用 KiraAI 里注册好的 Rerank 模型，其次 JEV，最后原样返回。

设计哲学与现有向量层一致：**任何一环缺失/失败 ⇒ 返回 None**，调用方保持原有顺序，
⇒ 不启用时行为与今天**逐字节一致**（可测）。

实测依据（第三方 zilliztech/memsearch：2172 中文 + 2172 英文查询）：
    冻结排序 Recall@5 0.7471 / MRR@10 0.6372
    Jev 重排 Recall@5 0.7930 / MRR@10 0.6885
    Voyage rerank-3 Recall@5 0.8211 → 专用重排模型更强，故两者都支持、由用户选。

★ 本模块只做"排序"，**不做任何决策**（是否合并/是否撤回由 mdecide 负责）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

try:  # 框架内运行时用宿主 logger；独立运行/测试环境自动降级（不硬依赖）
    from core.logging_manager import get_logger
except Exception:  # pragma: no cover
    import logging as _logging

    def get_logger(*_a, **_k):
        return _logging.getLogger("alife_memory_rerank")

from .mdecide import split_uuid

def _make_logger():
    """框架 logger 需要可写的日志目录；拿不到时降级到标准库（测试/独立运行）。"""
    try:
        return get_logger("alife_memory_rerank", "light_purple")
    except Exception:  # pragma: no cover
        import logging

        return logging.getLogger("alife_memory_rerank")


logger = _make_logger()


class Reranker:
    """三层后端：KiraAI 重排模型 → JEV → None（调用方保持原序）。"""

    def __init__(self, model_uuid: str = "", provider_mgr: Any = None,
                 decisions: Any = None, timeout: float = 1.5):
        # ★ 2026-10-10：有序回退链（str=单档=旧行为；list=依次回退）
        raw = [model_uuid] if isinstance(model_uuid, str) else list(model_uuid or [])
        self.model_uuids = [str(x or "").strip() for x in raw if str(x or "").strip()]
        self.model_uuid = self.model_uuids[0] if self.model_uuids else ""
        self.provider_mgr = provider_mgr
        self.decisions = decisions          # mdecide.Decisions（可选，用于兜底）
        self.timeout = max(0.3, float(timeout or 1.5))
        self.calls = 0
        self.fails = 0

    @property
    def ready(self) -> bool:
        return bool(self.model_uuids) or bool(self.decisions and self.decisions.ready)

    # ---------- 后端 1：KiraAI 已注册的 rerank 模型 ----------
    async def _provider_rank(self, query: str, items: list[tuple[str, str]],
                             top_n: Optional[int]) -> Optional[list[str]]:
        mgr = self.provider_mgr
        if mgr is None or not self.model_uuids:
            return None
        # ★ 2026-10-10：多档依次尝试，任一档成功即返回
        for _ref in self.model_uuids:
            _got = await self._provider_rank_one(_ref, query, items, top_n)
            if _got is not None:
                return _got
        return None

    async def _provider_rank_one(self, _ref, query, items, top_n):
        """单档重排（多档回退由 `_provider_rank` 负责 ✓ 2026-10-10）"""
        mgr = self.provider_mgr
        pid, mid = split_uuid(_ref)
        if not pid or not mid:
            return None
        try:
            client = mgr.get_model_client(pid, mid, "rerank")
        except Exception:  # noqa: BLE001 —— 取不到就当没有
            return None
        if client is None or not hasattr(client, "rerank"):
            return None
        texts = [t for _k, t in items]
        try:
            results = await asyncio.wait_for(client.rerank(query, texts, top_n=top_n),
                                             timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 —— 超时/报错一律回退
            self.fails += 1
            logger.info(f"重排模型调用未成功，本轮保持原顺序：{type(exc).__name__}")
            return None
        self.calls += 1
        try:
            ordered = sorted(results, key=lambda r: -float(getattr(r, "score", 0.0) or 0.0))
            keys = []
            for r in ordered:
                idx = int(getattr(r, "index", -1))
                if 0 <= idx < len(items):
                    keys.append(items[idx][0])
            return keys or None
        except Exception:  # noqa: BLE001
            return None

    # ---------- 后端 2：JEV（顺带也是重排后端） ----------
    async def _jev_rank(self, query: str, items: list[tuple[str, str]],
                        top_n: Optional[int]) -> Optional[list[str]]:
        if self.decisions is None:
            return None
        scored = await self.decisions.recall_filter(query, items, timeout=self.timeout)
        if not scored:
            return None
        keys = [k for k, _s in scored]
        return keys[:top_n] if top_n else keys

    # ---------- 对外 ----------
    async def rank(self, query: str, items: list[tuple[str, str]],
                   top_n: Optional[int] = None) -> Optional[list[str]]:
        """返回重排后的 key 列表；**任何失败都返回 None**（调用方保持原顺序）。"""
        if not items or not self.ready:
            return None
        keys = await self._provider_rank(query, items, top_n)
        if keys:
            return keys
        return await self._jev_rank(query, items, top_n)
