"""工具的 keyword 入口改用**词法打分**（2026-09-26 用户拍板）

背景：工具原来只给 keyword ⇒ 走 LIKE **硬过滤** ⇒ 要求**完整短语命中** ✗
      实测（同库同查询）：真相关只召回 2/4 ✗
      （"吃花生会起疹子""误吃花生酱后进急诊" 不含完整短语"花生过敏" ⇒ 搜不到 ✗）
改后：keyword 也当查询文本走 lexical 词法打分 ✓（与被动召回一致 ✓）
      实测：候选 16 → 28 ✓、真相关 2/4 → 4/4 ✓、无关项不增加 ✓
      速度：小库 +14ms（可忽略 ✓）；**2517 条库反而更快**（52.4 → 26.7 ms ✓）
"""

import importlib
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
cfg_core = os.environ.get("KIRA_CORE")
if cfg_core and cfg_core not in sys.path:
    sys.path.insert(0, cfg_core)
try:
    from test_name_refresh import _plugin          # noqa: E402
except Exception as exc:                            # noqa: BLE001
    pytest.skip("需要宿主框架：%r" % (exc,), allow_module_level=True)


class _NoopEngine:
    async def enqueue(self, *a, **k):
        return None


@pytest.mark.asyncio
async def test_keyword_entry_now_uses_lexical_scoring(tmp_path):
    """★ 措辞不同（不含完整短语）的记录也必须能被搜到 ✓"""
    plugin, store = _plugin(tmp_path)
    plugin.engine = _NoopEngine()
    sid = "qq:gm:LEX"
    store.capture(sid, "t1", [
        {"role": "user", "content": "用户吃花生会起疹子，医生让完全避开",
         "time": 1.0, "users": []},
        {"role": "user", "content": "用户喜欢打游戏", "time": 2.0, "users": []},
    ])
    ev = SimpleNamespace(sid="qq:gm:ME", event_id="e", messages=[],
                         session=SimpleNamespace(adapter_name="qq"))
    text = await plugin.search_archive(ev, keyword="花生过敏", count=5)
    assert "起疹子" in text, (
        "措辞不同但语义相关的记录没被搜到 ✗ —— keyword 又走回 LIKE 硬过滤了？")


@pytest.mark.asyncio
async def test_offtopic_rows_do_not_crowd_out_when_keyword_only(tmp_path):
    """纯 off-topic 的行（不含任何查询词元）不应被算进命中 ✓"""
    plugin, store = _plugin(tmp_path)
    plugin.engine = _NoopEngine()
    sid = "qq:gm:LEX2"
    store.capture(sid, "t1", [
        {"role": "user", "content": "用户对花生过敏，吃了会休克", "time": 1.0, "users": []},
        {"role": "user", "content": "用户上周去看了球赛", "time": 2.0, "users": []},
    ])
    ev = SimpleNamespace(sid="qq:gm:ME2", event_id="e", messages=[],
                         session=SimpleNamespace(adapter_name="qq"))
    text = await plugin.search_archive(ev, keyword="花生过敏", count=5)
    assert "花生过敏" in text
    assert "球赛" not in text, "完全无关的行不该被召回 ✓（打分 0 应被排除）"
