"""P5-扩展：预热与实时注入**必须共用同一个 key 生成器**（否则预热永不命中 = 白算 ✗）

不变式（比解析元组更本质 ✓）：
  ① 存在唯一的 _search_memo_key 生成器，返回以 "search" 开头的元组 ✓
  ② on_request 的实时调用用它生成 key ✓
  ③ _prefetch_search 的预热调用用它生成 key ✓
  ④ 两侧的 search 参数清单一致（lexical/scope/users/limit/exclude_sid/active/
     cold_after_days/skip_media ✓）—— 参数不同也会导致 key 不同 ⇒ 必须一致 ✓
  ⑤ 反向自检：抽掉任一处 helper 调用 ⇒ 必须报红 ✓
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "main.py").read_text(encoding="utf-8")


def test_single_key_generator_exists():
    n = SRC.count("def _search_memo_key(")
    assert n == 1, "key 生成器必须唯一（现在 %d 个）✗" % n
    body = SRC[SRC.index("def _search_memo_key("):][:1200]
    assert '("search", sid' in body, "生成器必须返回以 search 开头的元组 ✗"
    for k in ["cfg.recall_scope", "cfg.search_active_only", "cfg.cold_after_days",
              "cfg.recall_skip_media", "prefer"]:
        assert k in body, "key 生成器少了 %s ⇒ 两侧可能不一致 ✗" % k


def test_both_sides_use_the_generator():
    assert re.search(r"_mkey = self\._search_memo_key\(", SRC), \
        "on_request 没用 key 生成器 ✗（预热将永不命中）"
    pre = SRC[SRC.index("async def _prefetch_search("):][:1600]
    assert re.search(r"self\._search_memo_key\(", pre), \
        "_prefetch_search 没用 key 生成器 ✗（与实时侧可能不一致）"


def test_search_params_align_between_prefetch_and_realtime():
    pre = SRC[SRC.index("async def _prefetch_search("):][:1600]
    real = SRC[SRC.index("_mkey = self._search_memo_key("):][:1400]
    for p in ['"search"', "lexical=query", "scope=", "users=users", "exclude_sid=sid",
              "active=", "cold_after_days=", "skip_media="]:
        assert p in pre, "预热侧缺参数 %s ✗" % p
        assert p in real, "实时侧缺参数 %s ✗" % p


def test_guard_selfcheck_negative():
    def _ok(text):
        return (text.count("def _search_memo_key(") == 1
                and bool(re.search(r"_mkey = self\._search_memo_key\(", text)))
    assert _ok(SRC), "守卫对真实源码必须通过 ✗"
    assert not _ok(SRC.replace("_mkey = self._search_memo_key(", "_mkey = (").replace(
        "self._search_memo_key(sid, query, users, cfg, keyword_hit, prefer)", "(nothing)")), \
        "守卫发现不了【实时侧改用别的 key】✗"
