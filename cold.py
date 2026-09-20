"""冷归档（P7 · v7 定案）：**骨架行 + content 外置**

定案依据：shared/memory_plan/方案_v7_P7定案_骨架行.md
  · 热库 records 行**全部保留** ⇒ 外键（edges/vectors/migration_items）**永远成立** ✓
  · 只把唯一的大字段 **content** 搬进冷库（≈5KB/行 ⇒ 你 41.5MB 的绝大部分 ✓）
  · 冷库表 **不建外键**（纯存档 ✓ 没有约束要满足 ✓）
  · 召回/打分读 summary（保持原样 ✓）；整理/提取只碰 active=1 AND cold=0 ✓
  ⇒ 三者对本次迁移**完全无感** ✓
  · 默认**关闭**（由设置项控制 ✓）⇒ 不启用时行为与今天逐字节一致 ✓

为什么不是"整行搬迁" ✗（真跑测试推翻 ✓）：
  edges 可以**跨边界**（from=冷行、to=热行）⇒ 整行搬走时这条关系放哪边都违反外键 ✗
"""

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TBL = "cold_content"      # 冷库唯一需要的表 ✓ 无外键 ✓

# ★ 方案 B（2026-09-20）：只有**搬走 ≥5 MB** 才 VACUUM
#   VACUUM 会独占数据库锁几秒 ✗ ⇒ 小批量不值得为它动锁
#   （小批量留下的空闲页会被 SQLite 复用 ✓ 不浪费空间 ✓）
VACUUM_MIN_BYTES = 5 * 1024 * 1024


def ensure_cold(cold_path):
    """幂等建冷库与表（无外键 ⇒ 不会与热库约束冲突 ✓）"""
    p = Path(cold_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(p), timeout=20)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS %s ("
            " record_id TEXT PRIMARY KEY, content TEXT NOT NULL, moved_at REAL NOT NULL)" % TBL)
        db.commit()
    finally:
        db.close()
    return p


def _cold_ids(src, days, now, include_deleted=True):
    """要搬的 id：① `cold=1` 且（archived_at=0 或已满 days 天）② 回收站里的（`deleted=1`）

    为什么回收站也能外置 ✓：回收站的行**不参与召回**（search 里 `deleted=0` ✓）
    且 `undelete()` 会在还原时**从冷库取回正文**（已接线 ✓）⇒ 用户无感 ✓
    条件带 `content <> ''` ⇒ 已搬过的自然跳过 ⇒ **天然幂等** ✓
    """
    cond = "cold=1 AND (archived_at=0 OR archived_at<=?)"
    args = []
    if days and int(days) > 0:
        args.append(now - int(days) * 86400)
    else:
        cond = "cold=1"
    if include_deleted:
        cond = "(%s OR deleted=1)" % cond
    rows = src.execute(
        "SELECT id FROM records WHERE %s AND content IS NOT NULL AND content <> ''" % cond,
        args).fetchall()
    return [r[0] for r in rows]


def preview(src, days=180, now=None):
    """干跑：将搬多少条 / 预计释放多少字节（**只读** ✓ 一个字节都不写 ✓）"""
    now = now or time.time()
    ids = _cold_ids(src, days, now)
    if not ids:
        return {"records": 0, "bytes": 0, "ids": []}
    ph = ",".join("?" * len(ids))
    size = src.execute(
        "SELECT COALESCE(SUM(LENGTH(content)),0) FROM records WHERE id IN (%s)" % ph,
        ids).fetchone()[0]
    return {"records": len(ids), "bytes": int(size or 0), "ids": ids}


def spill(src, cold_path, days=180, now=None, dry=False, chunk=500):
    """把冷行的 content 搬进冷库并在热库置空 —— **同一事务** ✓ 可重跑 ✓ 失败回滚 ✓

    顺序：先写冷库（提交）⇒ 再同事务里把热库置空 ⇒ 提交 ✓
    ⇒ 任何时刻"冷库有原文"都成立 ⇒ **永不丢内容** ✓（失败可安全重跑 ✓）
    """
    now = now or time.time()
    info = preview(src, days, now)
    ids = info["ids"]
    if not ids:
        return {"moved": 0, "bytes": 0, "ok": True, "reason": "nothing-to-move"}
    if dry:
        return {"moved": 0, "bytes": info["bytes"], "ok": True, "dry": True, "would": len(ids)}
    cold_path = ensure_cold(cold_path)
    cdb = sqlite3.connect(str(cold_path), timeout=20)
    moved = 0
    try:
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            ph = ",".join("?" * len(part))
            rows = src.execute(
                "SELECT id, content FROM records WHERE id IN (%s)" % ph, part).fetchall()
            cdb.executemany(
                "INSERT OR REPLACE INTO %s(record_id, content, moved_at) VALUES(?,?,?)" % TBL,
                [(r[0], r[1], now) for r in rows])
            cdb.commit()
            src.execute("BEGIN IMMEDIATE")
            src.execute("UPDATE records SET content='' WHERE id IN (%s)" % ph, part)
            src.commit()
            moved += len(rows)
        # ★ 方案 B：搬得多才缩文件（VACUUM 独占锁几秒 ⇒ 小批量不动锁 ✓ 空闲页会被复用 ✓）
        vacuumed = False
        if moved and info["bytes"] >= VACUUM_MIN_BYTES:
            src.execute("VACUUM")
            vacuumed = True
        return {"moved": moved, "bytes": info["bytes"], "ok": True, "vacuumed": vacuumed}
    except Exception:
        try:
            src.rollback()
        except Exception:
            pass
        logger.warning("[cold] 搬迁中断：热库已回滚；冷库可能留有副本（重跑安全 ✓）", exc_info=True)
        return {"moved": moved, "bytes": 0, "ok": False}
    finally:
        cdb.close()


def content_of(cold_path, ids, chunk=500):
    """读时回填：从冷库取回正文（只返回**找到的**键 ✓ 找不到的空着 ⇒ 调用方保持原值 ✓）

    用途：那 4 个"能看见冷行"的入口（面板浏览 / 工具 / 回收站 / 取回）✓
    """
    out = {}
    p = Path(cold_path)
    ids = [i for i in (ids or []) if i]
    if not p.exists() or not ids:
        return out
    cdb = sqlite3.connect(str(p), timeout=20)
    try:
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            ph = ",".join("?" * len(part))
            for rid, text in cdb.execute(
                    "SELECT record_id, content FROM %s WHERE record_id IN (%s)" % (TBL, ph), part):
                out[rid] = text
    finally:
        cdb.close()
    return out


def stats(cold_path):
    """冷库概况（面板/工具展示用 ✓ 只读 ✓）"""
    p = Path(cold_path)
    if not p.exists():
        return {"exists": False, "records": 0, "bytes": 0}
    cdb = sqlite3.connect(str(p), timeout=20)
    try:
        n, b = cdb.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0) FROM %s" % TBL).fetchone()
        return {"exists": True, "records": int(n or 0), "bytes": int(b or 0),
                "path": str(p), "size": p.stat().st_size}
    finally:
        cdb.close()


def restore(src, cold_path, ids=None):
    """把冷库正文写回热库（事务 ✓）并从冷库删除副本 —— 用于"单条取回"或"整批回滚" ✓

    先写热库成功提交 ⇒ 再删冷库副本 ✓ ⇒ 中途失败最坏是"冷库仍有副本"（重跑安全 ✓）
    """
    p = Path(cold_path)
    if not p.exists():
        return {"restored": 0, "ok": True, "reason": "no-cold-db"}
    cdb = sqlite3.connect(str(p), timeout=20)
    try:
        if ids:
            ids = list(ids)
            ph = ",".join("?" * len(ids))
            rows = cdb.execute(
                "SELECT record_id, content FROM %s WHERE record_id IN (%s)" % (TBL, ph),
                ids).fetchall()
        else:
            rows = cdb.execute("SELECT record_id, content FROM %s" % TBL).fetchall()
        if not rows:
            return {"restored": 0, "ok": True}
        src.execute("BEGIN IMMEDIATE")
        src.executemany("UPDATE records SET content=? WHERE id=?", [(c, r) for r, c in rows])
        src.commit()
        cdb.executemany("DELETE FROM %s WHERE record_id=?" % TBL, [(r,) for r, _c in rows])
        cdb.commit()
        return {"restored": len(rows), "ok": True}
    except Exception:
        try:
            src.rollback()
        except Exception:
            pass
        logger.warning("[cold] 取回失败：热库已回滚（冷库副本未删 ✓）", exc_info=True)
        return {"restored": 0, "ok": False}
    finally:
        cdb.close()


def default_path(hot_db_path):
    """冷库默认位置：与热库同目录的 memory_cold.db ✓"""
    return Path(hot_db_path).with_name("memory_cold.db")


def fill_contents(rows, cold_path, key="id", field="content"):
    """把冷库里的正文**回填**进一批行（原地 ✓ 只填空的 ✓ 找不到就保持原样 ✓）

    用途：① 面板浏览（include_cold=True ✓）② 单条详情 ③ 从回收站还原
    ⇒ 让"看见冷行"的入口拿到的仍是**完整原文** ✓（对上层语义零变化 ✓）
    """
    if not rows:
        return rows
    ids = []
    for r in rows:
        if isinstance(r, dict):
            v = r.get(key)
            if v and not str(r.get(field) or "").strip():
                ids.append(v)
    if not ids:
        return rows
    texts = content_of(cold_path, ids)
    if not texts:
        return rows
    for r in rows:
        if not isinstance(r, dict):
            continue
        v = r.get(key)
        if v and not str(r.get(field) or "").strip() and v in texts:
            r[field] = texts[v]
    return rows
