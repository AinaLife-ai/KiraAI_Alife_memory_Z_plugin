"""Read-only legacy adapters; transactional, source-addressed imports.

KiraOS TOML is the authority, never its rebuildable SQLite index. No provider
calls, source rewrites, inferred compression depth, or invented entity IDs.
"""

from __future__ import annotations
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from .contracts import Fact, dump
from . import identity
from .storage import search_body_of

logger = logging.getLogger(__name__)

SIMPLE = "kira_plugin_simple_memory"
KIRAOS = "kira_plugin_kiraos"
# 海马体记忆（LyaQanYi，已归档并入 KiraOS）。它的 TOML 树与 KIRAOS **完全同构** ✓
# 实测：拿仿真海马体数据直接喂 KIRAOS 分支 → 3 文件 / 9 条 / 0 错误，逐条映射正确 ✓
HIPPOCAMPUS = "kira_plugin_hippocampus_memory"
SOURCES = (SIMPLE, KIRAOS, HIPPOCAMPUS)


def source_roots(data_root, plugin_data_root):
    """每个来源的数据根。

    - simple_memory / KiraOS 都写在共享的 ``data/memory`` ✓
    - 海马体写在自己的插件数据目录 ``data/plugin_data/<id>/memory`` ✓（main.py:110）
    布局（``global/{facts,self,skills}`` + ``entities/<类型>_<编码ID>/{facts,reflections,skills}``
    + ``profile.json``）**KiraOS / 海马体一致** ✓ 所以那条分支可复用 ✓

    ⚠️ **默认记忆（simple_memory）不是这个格式** ✗✓ —— 它只有一个
    ``core.txt`` **纯文本** ✓ 逐**行**读 ✓ 每行写死类别 ``fact`` ✓
    （源数据里本来就没有类别信息 ✓ 所以不是"偷懒归类" ✗ 是格式使然 ✓）
    2026-09-17 用户提醒核对时发现本注释此前把三者写成"一致" ✗ 已更正 ✓
    """
    data_root = Path(data_root)
    return {
        SIMPLE: data_root,
        KIRAOS: data_root,
        HIPPOCAMPUS: Path(plugin_data_root) / HIPPOCAMPUS / "memory",
    }


def aged_importance(value, when, *, now=None, half_life_days=365):
    """导入时按**事实年龄**折算一次 importance（海马体原本有持续衰减，我们没有）。

    - ``half_life_days <= 0`` → 不折算，原样返回 ✓（配置项可关）
    - 每过一个半衰期，importance 减半（向下取整，最低 1）✓
    - 时间取不到（None/0）→ 不折算 ✓ 绝不让导入失败
    """
    try:
        base = int(value)
    except (TypeError, ValueError):
        return 1
    if half_life_days is None or half_life_days <= 0:
        return max(1, min(10, base))
    try:
        stamp = float(when or 0)
    except (TypeError, ValueError):
        stamp = 0.0
    if stamp <= 0:
        return max(1, min(10, base))
    if stamp > 1e11:          # 毫秒时间戳兜底 ✓（我们的库用秒 ✓ 别的来源可能不同）
        stamp = stamp / 1000.0
    now = time.time() if now is None else float(now)
    if not math.isfinite(stamp) or not math.isfinite(now):
        return max(1, min(10, base))
    if now > 1e11:            # now 也可能是毫秒（调用方不同）→ 一并归一
        now = now / 1000.0
    age_days = max(0.0, (now - stamp) / 86400.0)
    # 封顶 100 年：单位混用时差额可能离谱 → 会让 2**x 直接 OverflowError ✗
    age_days = min(age_days, 36500.0)
    folded = base / (2.0 ** (age_days / float(half_life_days)))
    return max(1, min(10, round(folded)))


class Rejected(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def clean(value, limit):
    if not isinstance(value, str):
        raise Rejected("invalid_text")
    # Check the original decoded content before normalization, never truncate.
    if len(value) > limit:
        raise Rejected("too_long")
    value = value.strip()
    if not value or not any(c.isalnum() for c in value):
        raise Rejected("empty")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        raise Rejected("control_characters")
    if value.startswith(("```", "{", "[", "<")) or re.search(r"</?\w+[^>]*>", value):
        raise Rejected("markup_or_serialized_output")
    if value in {
        "暂无",
        "暂无信息",
        "暂无画像信息",
        "无",
        "未知",
        "无有效信息",
        "None",
        "null",
    }:
        raise Rejected("placeholder")
    if len(value) > 12 and len(set(value)) < 3:
        raise Rejected("repetition")
    return value


def timestamp(value, fallback):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime):
            # Naive source times are explicitly interpreted as UTC, not host-local.
            return value.replace(
                tzinfo=value.tzinfo or timezone.utc
            ).timestamp(), "source.time"
        if type(value) in (int, float) and 0 < value < 32503680000:
            return float(value), "source.timestamp"
    except (ValueError, OverflowError):
        pass
    return fallback, "file_mtime_estimate"


def _importance(value):
    """Clamp a legacy importance value into the 1-10 range."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 5
    return max(1, min(10, number))


def raw_location(relative, source):
    """Raw, source-faithful bucket; hashing and dedup depend on this shape."""
    parts = relative.parts
    if parts[0] == "global":
        return (
            "legacy:global",
            "global",
            "legacy:self" if "self" in parts else "legacy:global",
            [],
        )
    raw_sid = source.get("session", "")
    sid = "legacy:unscoped"
    if isinstance(raw_sid, str):
        split = raw_sid.split(":", 2)
        if len(split) == 3 and all(split) and split[1] in ("dm", "gm", "pm"):
            sid = f"{split[0]}:{'dm' if split[1] == 'pm' else split[1]}:{split[2]}"
    entity_type, _, entity = parts[1].partition("_") if len(parts) > 1 else ("", "", "")
    entity = unquote(entity)
    # Only adapter-qualified entity IDs can be matched to live users.
    if entity_type == "user" and re.fullmatch(r"[^:\s]+:[^\s]+", entity):
        return "legacy:user:" + entity, "user", entity, [entity]
    if entity_type == "group" and re.fullmatch(r"[^:\s]+:[^\s]+", entity):
        adapter, identifier = entity.split(":", 1)
        sid = f"{adapter}:gm:{identifier}"
    return sid, "session", f"legacy:{entity_type}:{entity}" if entity else sid, []


def profile_entries(data):
    for key in ("name", "nickname", "description"):
        if data.get(key):
            yield (
                key,
                f"{key}: {data[key]}" if isinstance(data[key], str) else data[key],
                "profile",
                [],
            )
    for key, category in (
        ("traits", "profile"),
        ("aliases", "profile"),
        ("facts", "fact"),
    ):
        values = data.get(key, [])
        if not isinstance(values, list):
            # ★ 2026-09-23：字段类型不符时**跳过这个字段**即可，不该判死整份
            #   画像（KiraOS 的 from_dict 连类型都不校验）。宁可少一条，
            #   也别把用户的名字/描述/其余事实一起丢掉。
            logger.warning("[记忆·Z] 画像字段 %s 类型不符，已跳过该字段", key)
            continue
        for index, value in enumerate(values):
            yield f"{key}/{index}", value, category, []
    for key, category in (
        ("preferences", "preference"),
        ("relationships", "relationship"),
    ):
        values = data.get(key, {})
        if not isinstance(values, dict):
            logger.warning("[记忆·Z] 画像字段 %s 类型不符，已跳过该字段", key)
            continue
        for name, value in values.items():
            if not isinstance(value, str):
                yield f"{key}/{name}", value, category, []
            else:
                yield (
                    f"{key}/{name}",
                    f"{name}: {value}",
                    category,
                    [(name, value)] if key == "relationships" else [],
                )


def newest_legacy_mtime(root) -> float:
    """旧记忆源文件里最新的修改时间（没有文件返回 0）。只 stat，不解析。"""
    root = Path(root)
    newest = 0.0
    core = root / "core.txt"
    try:
        if core.is_file():
            newest = max(newest, core.stat().st_mtime)
    except OSError:
        pass
    for folder in ("entities", "global"):
        base = root / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix != ".toml" and path.name != "profile.json":
                continue
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                pass
    return newest


def snapshot(root: Path, plugin_id: str, limit: int, resolver=None, *, decay_days=365):
    """Read only named memory sources; each rejected item has an auditable reason.

    ``resolver`` maps synthetic buckets onto live identifiers for new imports.
    Without it the raw legacy identifiers are returned unchanged, and the
    digest stays identical either way so an upgrade never re-imports data.
    """
    root = root.resolve()
    # 年龄折算**只对海马体生效** ✓（用户决策 2026-09-15）
    # 初衷是"补上海马体自己的衰减" ✓ KIRAOS / simple_memory 用户并没有要这个 ✗
    # → 其它来源传 0：aged_importance 原样返回（只做 1..10 归一）✓
    decay_days = decay_days if plugin_id == HIPPOCAMPUS else 0
    paths = (
        [root / "core.txt"]
        if plugin_id == SIMPLE
        else sorted(
            [
                p
                for folder in ("entities", "global")
                for p in (root / folder).rglob("*")
                if p.suffix == ".toml" or p.name == "profile.json"
            ]
        )
    )
    items, files, errors = [], {}, []
    for path in paths:
        if not path.exists():
            continue
        relative = path.relative_to(root)
        if "skills" in relative.parts:
            continue
        key = relative.as_posix()
        try:
            if not path.resolve().is_relative_to(root) or path.is_symlink():
                raise ValueError("unsafe_source_path")
            if path.stat().st_size > 2_000_000:
                raise ValueError("source_too_large")
            raw = path.read_bytes()
            files[key] = hashlib.sha256(raw).hexdigest()
            mtime = path.stat().st_mtime
            text = raw.decode("utf-8-sig")
            if plugin_id == SIMPLE:
                entries = [
                    (str(i), line, "fact", [])
                    for i, line in enumerate(text.splitlines(), 1)
                ]
                raw_sid, raw_visibility, raw_subject, raw_users = (
                    "legacy:global",
                    "global",
                    "legacy:global",
                    [],
                )
                ts, time_basis, tags, metadata = mtime, "file_mtime_estimate", [], {}
            else:
                data = (
                    json.loads(text, object_pairs_hook=unique_object)
                    if path.name == "profile.json"
                    else tomllib.loads(text)
                )
                if not isinstance(data, dict):
                    raise ValueError("invalid_source_document")
                source = data.get("source", {})
                if not isinstance(source, dict):
                    # ★ 2026-09-23：与 KiraOS 自己的读取回退保持一致（它也是
                    #   非 dict ⇒ {}），不必因此判死整个文件。
                    source = {}
                raw_sid, raw_visibility, raw_subject, raw_users = raw_location(
                    relative, source
                )
                ts, time_basis = timestamp(
                    source.get("time", data.get("last_interaction")), mtime
                )
                # ★ 2026-09-23：对齐 KiraOS 的 normalize_tags —— 非 list ⇒ 空；
                #   混进 null/数字/空白 ⇒ 就地过滤，而不是判死整个文件。
                raw_tags = data.get("tags", [])
                if not isinstance(raw_tags, list):
                    raw_tags = []
                tags = list(
                    dict.fromkeys(
                        t.strip()
                        for t in raw_tags
                        if isinstance(t, str) and t.strip()
                    )
                )[:11]
                metadata = {
                    "session": source.get("session", ""),
                    "importance": data.get("importance"),
                    "type": data.get("type"),
                }
                if path.name == "profile.json":
                    # ★ 2026-09-23：目录名才是定位基准（raw_location 也按它推导），
                    #   profile.json 里的 entity_id 只是随文件走的副本。旧版 KiraOS
                    #   用纯数字 ID（目录如 `group_548464960`），新版带适配器前缀
                    #   （`group_qq%3A548464960`）——升级后同一条数据的目录名与副本
                    #   不一致是**历史常态**，不该因此丢掉整份画像 ⇒ 忽略副本，只留痕。
                    _, _, folder_id = relative.parts[1].partition("_")
                    stated = data.get("entity_id")
                    if stated and stated != unquote(folder_id):
                        logger.warning(
                            "[记忆·Z] 画像目录名与 entity_id 不一致，按目录名继续：%s（%r ≠ %r）",
                            relative.as_posix(),
                            stated,
                            unquote(folder_id),
                        )
                    entries = list(profile_entries(data))
                else:
                    category = {
                        "entity": "profile",
                        "task": "commitment",
                        "source": "resource",
                        "reflection": "self" if raw_subject == "legacy:self" else "profile",
                    }.get(data.get("type"), data.get("type", "fact"))
                    if category not in (
                        "event",
                        "fact",
                        "preference",
                        "commitment",
                        "relationship",
                        "profile",
                        "resource",
                        "self",
                    ):
                        category = "fact"
                    entries = [
                        (str(data.get("id", "text")), data.get("text"), category, [])
                    ]
            if resolver is None:
                sid, visibility, subject, users = (
                    raw_sid,
                    raw_visibility,
                    raw_subject,
                    raw_users,
                )
            else:
                sid, visibility, subject, users = resolver.canonical_location(
                    raw_sid, raw_visibility, raw_subject, raw_users
                )
            for entry_key, value, category, relationships in entries:
                item = {
                    "key": key + "#" + entry_key,
                    "file": key,
                    "file_hash": files[key],
                }
                item["hash"] = hashlib.sha256(
                    dump(
                        [
                            value,
                            raw_sid,
                            raw_subject,
                            category,
                            tags,
                            relationships,
                            metadata,
                        ]
                    ).encode()
                ).hexdigest()
                try:
                    content = clean(value, 16000 if plugin_id == SIMPLE else limit)
                    relations = [
                        {"subject": subject, "predicate": relation, "object": target}
                        for target, relation in relationships
                    ]
                    fact = Fact(
                        category=category,
                        subject=subject,
                        content=content,
                        reason="",
                        scenario="",
                        tags=[*tags, "legacy_import"],
                        relations=relations,
                        source_ids=["pending"],
                        importance=aged_importance(
                            _importance(metadata.get("importance")),
                            ts,
                            half_life_days=decay_days,
                        ),
                    ).model_dump()
                    item.update(
                        sid=sid,
                        visibility=visibility,
                        subject=subject,
                        users=users,
                        raw_sid=raw_sid,
                        raw_subject=raw_subject,
                        content=content,
                        fact=fact,
                        time=ts,
                        time_basis=time_basis,
                        metadata=metadata,
                        reason="",
                    )
                except (ValueError, TypeError) as exc:
                    item["reason"] = (
                        str(exc) if isinstance(exc, Rejected) else "invalid_fields"
                    )
                items.append(item)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            # ★ 2026-09-23：带上具体原因（原来只有异常类名，用户拿着
            #   "ValueError" 无从下手）；detail 只截前 200 字，够定位即可。
            errors.append(
                {
                    "file": key,
                    "reason": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            )
    return {"source": plugin_id, "items": items, "files": files, "errors": errors}


def import_snapshot(store, snapshot):
    """Records, facts and the receipt commit together; retries never revive deletions."""
    counts = {"imported": 0, "duplicate": 0, "skipped": 0, "unscoped": 0}
    source = snapshot["source"]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for item in snapshot["items"]:
            previous = db.execute(
                "SELECT record_id FROM migration_items WHERE source=? AND source_key=? AND digest=?",
                (source, item["key"], item["hash"]),
            ).fetchone()
            if previous and (previous[0] or item["reason"]):
                counts["duplicate" if previous[0] else "skipped"] += 1
                continue
            if previous:
                db.execute(
                    "DELETE FROM migration_items WHERE source=? AND source_key=? AND digest=?",
                    (source, item["key"], item["hash"]),
                )
            record_id = None
            if item["reason"]:
                counts["skipped"] += 1
            else:
                fingerprint = hashlib.sha256(
                    dump(
                        [
                            item.get("raw_sid", item["sid"]),
                            item.get("raw_subject", item["subject"]),
                            item["content"],
                            item["fact"]["category"],
                            item["fact"]["relations"],
                        ]
                    ).encode()
                ).hexdigest()
                record_id = "legacy-" + fingerprint
                store._ensure_entities(db, item["sid"], item["users"])
                if item["sid"] in (identity.GLOBAL, identity.SELF):
                    db.execute(
                        "UPDATE entities SET kind=? WHERE id=?",
                        (item["sid"], item["sid"]),
                    )
                existing = db.execute(
                    "SELECT id FROM records WHERE id=?", (record_id,)
                ).fetchone()
                if not existing:
                    position = db.execute(
                        "SELECT coalesce(max(position),0)+1 FROM records WHERE sid=?",
                        (item["sid"],),
                    ).fetchone()[0]
                    evidence = dump(
                        {
                            "legacy_text": item["content"],
                            "source": source,
                            "file": item["file"],
                            "digest": item["file_hash"],
                            "time_basis": item["time_basis"],
                            "metadata": item["metadata"],
                        }
                    )
                    db.execute(
                        """INSERT INTO records (id,sid,role,level,start,end,summary,content,users,position,created,visibility,search_body)
                        VALUES (?,?,'user',0,?,?,?,?,?,?,?,?,?)""",
                        (
                            record_id,
                            item["sid"],
                            item["time"],
                            item["time"],
                            item["content"],
                            evidence,
                            dump(item["users"]),
                            position,
                            time.time(),
                            item["visibility"],
                            search_body_of(item["content"]),
                        ),
                    )
                    item["fact"]["source_ids"] = [record_id]
                    store._add_fact(db, item["sid"], item["fact"])
                    counts["imported"] += 1
                else:
                    counts["duplicate"] += 1
                if item["sid"] == "legacy:unscoped":
                    counts["unscoped"] += 1
            db.execute(
                "INSERT INTO migration_items VALUES (?,?,?,?,?,?,?,?)",
                (
                    source,
                    item["key"],
                    item["hash"],
                    record_id,
                    item["reason"],
                    item["file_hash"],
                    dump(item.get("metadata", {})),
                    time.time(),
                ),
            )
        counts["unscoped"] = db.execute(
            """SELECT count(DISTINCT records.id) FROM records JOIN migration_items
            ON migration_items.record_id=records.id WHERE migration_items.source=? AND records.sid='legacy:unscoped'
            AND records.deleted=0""",
            (source,),
        ).fetchone()[0]
        report = {
            "source": source,
            **counts,
            "errors": snapshot["errors"],
            "files": len(snapshot["files"]),
            "updated": time.time(),
        }
        db.execute(
            "INSERT OR REPLACE INTO migration_reports VALUES (?,?)",
            (source, dump(report)),
        )
        store.bump(db)
    return report
