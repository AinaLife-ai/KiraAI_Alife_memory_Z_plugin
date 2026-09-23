"""v2.18.72：迁移读旧数据必须**不比旧插件更脆弱**。

线上事故（2026-09-23）：KiraOS 老用户装 Z 后长期 memory_paused。
根因是 `entities/group_548464960/profile.json` 里 `entity_id` 写成新格式
`"qq:548464960"`，而目录名是旧格式（纯数字）；迁移把"目录名与副本不一致"
当成致命错误 ⇒ 整个迁移失败 ⇒ 永久暂停。

原则（本文件逐条钉死）：旧插件（KiraOS）自己能读的数据，我们都必须能读。
- 它读 profile.json **从不校验**目录名与 entity_id 是否一致 ⇒ 我们也不该
- 它的 `normalize_tags` 对 null/数字/空白是**过滤**，非 list 是当空 ⇒ 对齐
- 它的 `from_toml_dict` 对非 dict 的 source 是回退成 {} ⇒ 对齐
- 它读到坏文件是**跳过不阻塞** ⇒ 个别的坏文件也不该阻塞我们的迁移
"""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_migration_tol")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_migration_tol", package)
m = importlib.import_module("alife_migration_tol.migration")
s = importlib.import_module("alife_migration_tol.storage")


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ★ 线上那个文件的逐字节复刻（路径与内容都照用户提供的样子）
LEGACY_GROUP_PROFILE = {
    "entity_id": "qq:548464960",
    "entity_type": "group",
    "name": "个人资料群 我们仨",
    "nickname": "",
    "description": "三人群聊建立: 群号548464960，群名'个人资料存储⭐'，成员包括末冬时2960741480、礼雪3383811454和念安2296153174",
    "platform": "QQ",
    "traits": [],
    "preferences": {},
    "relationships": {},
    "facts": [],
    "aliases": [],
    "interaction_count": 0,
    "last_interaction": 1778898770.3317044,
    "metadata": {},
}


@pytest.fixture
def store(tmp_path):
    store = s.Store(tmp_path / "new" / "alife.sqlite3")
    store.initialize()
    return store


def contents(snap):
    return [item["content"] for item in snap["items"] if not item.get("reason")]


def test_legacy_group_profile_id_mismatch_is_tolerated(tmp_path, store):
    """目录名（旧格式）与 profile.json 的 entity_id（新格式）不一致时：
    旧插件照读不误 —— 我们也必须照读，且**不报错**。"""
    root = tmp_path / "memory"
    write(
        root,
        "entities/group_548464960/profile.json",
        json.dumps(LEGACY_GROUP_PROFILE, ensure_ascii=False),
    )
    write(
        root,
        "entities/group_548464960/facts/topic.toml",
        'type = "fact"\ntext = "这个群常聊个人资料整理"\n',
    )
    snap = m.snapshot(root, m.KIRAOS, 120)
    assert snap["errors"] == [], "目录名与副本不一致不该是读取错误"
    joined = "\n".join(contents(snap))
    assert "个人资料群 我们仨" in joined       # 画像内容照常接上
    assert "个人资料整理" in joined
    store.import_legacy(snap)
    assert store.search(scope="global", keyword="个人资料群")["total"] >= 1


def test_mixed_tags_and_non_dict_source_are_normalized(tmp_path, store):
    """tags 混入 null/数字/空白、source 写成字符串：对齐旧插件的就地规整。"""
    root = tmp_path / "memory"
    write(
        root,
        "entities/user_test%3Au/facts/tags.toml",
        'type = "fact"\ntext = "有一条带乱 tag 的记忆"\n'
        'tags = ["正常", 1, "   ", "另一个"]\nsource = "oops"\n',
    )
    snap = m.snapshot(root, m.KIRAOS, 120)
    assert snap["errors"] == [], "tags/source 脏值不该判死整个文件"
    (item,) = [i for i in snap["items"] if not i.get("reason")]
    fact_tags = item["fact"]["tags"]
    assert "正常" in fact_tags and "另一个" in fact_tags
    assert 1 not in fact_tags and "   " not in fact_tags


def test_profile_field_type_mismatch_only_skips_that_field(tmp_path, store):
    """画像某个字段类型不符 ⇒ 只跳过该字段，名字/描述等照常接上。"""
    root = tmp_path / "memory"
    bad = dict(LEGACY_GROUP_PROFILE)
    bad["traits"] = "耐心,技术向"        # 应是 list
    bad["facts"] = {"a": "b"}            # 应是 list
    write(
        root,
        "entities/group_548464960/profile.json",
        json.dumps(bad, ensure_ascii=False),
    )
    snap = m.snapshot(root, m.KIRAOS, 120)
    assert snap["errors"] == []
    joined = "\n".join(contents(snap))
    assert "个人资料群 我们仨" in joined
    assert "三人" in joined


def test_unreadable_file_reports_detail_and_does_not_hide_good_ones(tmp_path, store):
    """真读不了的（语法坏）仍要上报 —— 但要带具体原因，且不连坐好文件。"""
    root = tmp_path / "memory"
    write(root, "entities/user_test%3Au/facts/broken.toml", 'text = "unclosed')
    write(
        root,
        "entities/user_test%3Au/facts/good.toml",
        'type = "fact"\ntext = "好文件必须照常可读"\n',
    )
    snap = m.snapshot(root, m.KIRAOS, 120)
    assert len(snap["errors"]) == 1
    err = snap["errors"][0]
    assert err["reason"] == "TOMLDecodeError"
    assert err["detail"], "必须给出具体原因，用户才能自己修"
    assert "好文件必须照常可读" in contents(snap)
