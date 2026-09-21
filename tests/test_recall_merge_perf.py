"""召回侧合并的性能守卫（2026-09-21）

用户关心的命题："召回也合并，不会拖慢回复吧？" —— 这里把它钉成两条可执行断言：
  ① DB 操作**不在事件循环线程里**跑（确定性判定，不依赖计时，永不 flaky）
  ② 判定期间事件循环**仍在推进**（只有真被堵住才会红）
"""

import asyncio
import threading
import time

import pytest

from test_memory import c, s


@pytest.mark.asyncio
async def test_store_call_runs_off_the_event_loop(tmp_path):
    """走 store.call 的 DB 操作必须在工作线程里执行（不在 loop 线程）"""
    store = s.Store(tmp_path / "db")
    store.initialize()
    seen = {}

    def probe():
        seen["worker"] = threading.get_ident()
        return "ok"

    store.probe_thread = probe
    seen["loop"] = threading.get_ident()
    assert await store.call("probe_thread") == "ok"
    assert seen["worker"] != seen["loop"], (
        "DB 操作跑到事件循环线程里了 ✗ ⇒ 会阻塞其它并发请求"
    )


@pytest.mark.asyncio
async def test_recall_side_merge_keeps_the_loop_alive(tmp_path):
    """召回侧判定（flag_similar_pairs）期间，事件循环必须仍在推进"""
    store = s.Store(tmp_path / "db")
    store.initialize()
    sid = "a:dm:perf"
    rows = []
    for i in range(120):
        body = ("同一件事反复提到" + "甲乙丙丁"[i % 4]) if i % 2 else ("独立内容第%d条" % i)
        rows.append(dict(
            category="fact", subject="a:u", content=body, reason="",
            scenario="", tags=[], relations=[], source_ids=[], importance=5,
        ))
    ids = store.add_facts(sid, rows)

    ticks = 0
    stop = False

    async def heartbeat():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.001)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    started = time.perf_counter()
    flagged = await store.call("flag_similar_pairs", ids, 0.25, 0.25)
    elapsed = time.perf_counter() - started
    stop = True
    await beat

    assert flagged, "120 条里一半是近重复 ⇒ 应该能命中（顺带验证功能）"
    assert elapsed < 0.5, "召回侧判定慢到 0.5 秒以上 ✗"
    assert ticks >= 1 or elapsed < 0.005, "判定期间事件循环一次都没跑 ⇒ 被堵住了 ✗"
