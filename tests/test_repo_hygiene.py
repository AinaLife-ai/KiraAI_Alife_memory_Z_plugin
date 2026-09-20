"""仓库卫生：运行日志、字节码、缓存**不许进仓库**（2026-09-20 用户抓到的 ✗）

背景：data/log.log（插件运行日志）曾被 git add -A 扫进 PR ✗
⇒ 这条守卫让它以后**自动**报红 ✓，不靠人的注意力 ✓
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BAD = [
    (re.compile(r"(^|/)[^/]*\.log(\.\d+)?$"), "运行日志"),
    (re.compile(r"__pycache__/"), "字节码缓存"),
    (re.compile(r"\.pyc$"), "字节码文件"),
    (re.compile(r"(^|/)\.pytest_cache/"), "pytest 缓存"),
    (re.compile(r"\.(sqlite3|db)$"), "数据库文件"),
]


def _tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def test_no_log_or_cache_files_tracked():
    files = _tracked()
    if files is None:
        return  # 没有 git（如打包环境）⇒ 跳过 ✓
    bad = []
    for f in files:
        for rx, why in BAD:
            if rx.search(f):
                bad.append("%s (%s)" % (f, why))
    assert not bad, "这些不该被提交：\n  " + "\n  ".join(bad)


def test_gitignore_covers_runtime_logs():
    gi = (ROOT / ".gitignore")
    assert gi.exists(), "缺少 .gitignore"
    txt = gi.read_text(encoding="utf-8")
    for need in ["data/", "data/*.log", "tests/data/*.log", "__pycache__/"]:
        assert need in txt, ".gitignore 少了 %s（运行产物会再进仓库 ✗）" % need


def test_guard_selfcheck_positive_and_negative():
    """反向自检：坏名字必须被抓、好名字必须放过 ✓（防守卫写空 ✓）"""
    def _hit(name):
        return any(rx.search(name) for rx, _ in BAD)
    assert _hit("data/log.log"), "守卫抓不到 log ✗"
    assert _hit("tests/data/log.log.1"), "守卫抓不到轮转日志 ✗"
    assert _hit("main/__pycache__/x.pyc"), "守卫抓不到 pyc ✗"
    assert not _hit("main.py") and not _hit("tools/generate_schema.py"), "守卫误伤正常文件 ✗"
    assert not _hit("README.md") and not _hit("tests/test_rotate_budget.py"), "守卫误伤正常文件 ✗"
