"""对账：前端从 /status 读的字段 vs 后端 api_status 真正给的字段。

真实问题（用户实测）：首页「L0 容量 / 召回统计」没生效 ✗
- 后端确实写了 status["capacity"] / status["recall_usage"] ✓
- 但 capacity_stats() 拿连接的方式在本项目里根本不存在 ✗ → 永远返回 {} ✓
⇒ 症状是「标签在、数字永远空」 ✓ 光看接线两边都在，扫不出来 ✗

本脚本做两件事：
1. 前端读的 next.<name> 必须能在后端 api_status / store.status() 里找到 ✓
2. 检测 capacity_stats 之类「取连接方式与 store 不一致」→ 值恒空 ✗
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

JS_BUILTIN = {
    "length", "includes", "push", "textContent", "title", "className", "forEach",
    "map", "filter", "join", "sort", "slice", "split", "toFixed", "replace",
    "parentNode", "insertBefore", "createElement", "nextSibling", "then", "catch",
    "value", "checked", "dataset", "style", "remove", "add", "appendChild",
}


def frontend_reads() -> dict[str, set[str]]:
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    reads: dict[str, set[str]] = {}
    for var in ("next", "status"):
        names = set(re.findall(r"\b%s\.([A-Za-z_]\w*)" % var, js))
        names |= set(re.findall(r'\b%s\[\s*"([A-Za-z_]\w*)"\s*\]' % var, js))
        if names:
            reads[var] = names
    return reads


def backend_emits() -> set[str]:
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    start = src.index('path="/status"')
    end = src.index('path="/config"', start)
    body = src[start:end]
    return set(re.findall(r'status\[\s*"([A-Za-z_]\w*)"\s*\]', body))


def store_keys() -> set[str]:
    src = (ROOT / "storage.py").read_text(encoding="utf-8")
    m = re.search(r"\n    def status\(self\):(.*?)\n    def ", src, re.S)
    if not m:
        return set()
    return set(re.findall(r'"([A-Za-z_]\w*)"\s*:', m.group(1)))


def main() -> int:
    reads = frontend_reads()
    emitted = backend_emits()
    store = store_keys()
    bad: list[str] = []

    for var, names in sorted(reads.items()):
        for name in sorted(names - JS_BUILTIN):
            if name not in emitted and name not in store:
                bad.append("%s.%s" % (var, name))

    total_reads = sum(len(v - JS_BUILTIN) for v in reads.values())
    print("后端 api_status 直接写出:", len(emitted), "个字段")
    print("store.status() 供出:", len(store), "个字段")
    print("前端读取字段:", total_reads, "个")

    st = (ROOT / "storage.py").read_text(encoding="utf-8")
    conn_bug = 'getattr(self, "conn", None) or getattr(self, "_conn"' in st
    if conn_bug:
        print('\n✗ capacity_stats 用 self.conn/_conn/db 取连接 ✗ 但本项目只有 self.connect() 上下文管理器')
        print('  → 永远拿到 None → 永远 return {} → 界面永远「L0 容量 —」')
        bad.append("capacity_stats：连接获取方式与 store 不一致")

    if bad:
        print("\n✗ 问题:")
        for b in bad:
            print("   -", b)
        return 1
    print("\n✓ 字段契约一致、连接获取方式正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
