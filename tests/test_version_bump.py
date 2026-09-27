"""版本与更新日志的守卫（2026-09-27 用户约定 ✓）

用户约定：**没有特别说明时**，每次改动都要
  ① manifest.json 版本号 **前进 0.0.1**（patch ✓）
  ② README 里写对应的**更新日志**（`### v<版本>` 小节 ✓）
  ③ 前端静态资源带版本号（防浏览器缓存旧 JS ✓，跟着 manifest 走 ✓）

这套测试把"可自动化的那一半"守住 ✓：
  · 版本号 ⇄ README 最新小节 ⇄ 前端 ?v= 三者必须一致 ✓
  · 更新日志必须**倒序排列**且当前版本在最上面 ✓
（"改动就该升版"属于流程约定 ✗ 无法从代码里自动判定 ✓；
  但只要升了版，就**必须**有对应日志 —— 这条守住就够挡掉绝大多数疏漏 ✓）
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.json"
README = ROOT / "README.md"
INDEX = ROOT / "web" / "index.html"


def _version():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def _sections():
    """README 里所有 `### vX.Y.Z` 小节（按出现顺序 = 从新到旧 ✓）"""
    text = README.read_text(encoding="utf-8")
    return re.findall(r"^### v(\d+\.\d+\.\d+)", text, re.M)


def _key(v):
    return tuple(int(x) for x in v.split("."))


def test_manifest_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version()), _version()


def test_readme_has_a_section_for_current_version():
    """★ 升了版就必须写更新日志 ✓（没写就红 ✗）"""
    v = _version()
    assert v in _sections(), (
        "manifest 是 %s，但 README 里没有 `### v%s` 小节 ✗\n"
        "升级版本号时**必须**同步写更新日志（用户约定 ✓）\n"
        "README 现有小节（前几个）：%s" % (v, v, _sections()[:4]))


def test_changelog_is_newest_first():
    """更新日志必须倒序（最新在最上面 ✓）—— 顺手防"插错位置"✗"""
    secs = _sections()
    assert secs, "README 里一个小节都没有 ✗"
    keys = [_key(s) for s in secs]
    assert keys == sorted(keys, reverse=True), (
        "更新日志不是倒序排列 ✗：%s" % secs[:6])


def test_newest_section_is_the_manifest_version():
    """最新那条日志必须就是当前版本 ✓（否则会出现"写了但写的是旧版本"✗）"""
    secs = _sections()
    assert secs and secs[0] == _version(), (
        "最新日志是 v%s，但 manifest 是 v%s ✗ —— 两者必须一致 ✓"
        % (secs[0] if secs else "（无）", _version()))


def test_frontend_assets_carry_the_version():
    """前端静态资源必须带 ?v=<版本> ✓（防浏览器缓存旧 JS ✗）"""
    html = INDEX.read_text(encoding="utf-8")
    v = _version()
    for asset in ("app.js", "graph.js"):
        assert re.search(re.escape(asset) + r"\?v=" + re.escape(v), html), (
            "%s 没有带 ?v=%s ✗ ⇒ 用户可能一直用缓存里的旧前端 ✗"
            % (asset, v))
