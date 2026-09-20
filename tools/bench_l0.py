"""L0 库测量 v2：体积构成（**含向量表** ✓）+ 召回查询耗时

重要修正：storage.py 里有 `vectors` 表（我上一轮说"没有向量"是错的 ✗）
所以本脚本量**两种场景**：
  A) 只有文本      → 看纯文本占多少
  B) 文本 + 向量   → 看向量占多少（8322 × 1024 维 float32 ≈ 34MB）
⇒ 一眼看出用户的 41.5MB 到底是"文本大"还是"向量大" ✓
"""

import random
import sqlite3
import statistics
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N_ROWS = 8322
TARGET_MB = 41.5

WORDS = ("今天 明天 昨天 感觉 觉得 好像 其实 因为 所以 但是 而且 然后 那个 这个 一下 有点 特别 真的 "
         "喜欢 讨厌 想 要 会 能 可以 应该 也许 大概 反正 总之 爱奈丽 记忆 插件 面板 召回 轮换 档案 事实 "
         "天气 心情 工作 代码 测试 版本 推送 审计 性能 优化 文件 数据 时间 记录 对话 回复 消息").split()


def sentence(rng, n=None):
    n = n or rng.randint(6, 26)
    return "".join(rng.choice(WORDS) for _ in range(n)) + "。"


def make_content(rng, kb):
    target = int(kb * 1024 / 3)
    out, n = [], 0
    while n < target:
        s = sentence(rng)
        out.append(s)
        n += len(s)
    return "".join(out)


def mb(path):
    return path.stat().st_size / 1048576.0


def stat(name, xs):
    if not xs:
        print("   %-20s 无数据" % name)
        return
    xs = sorted(xs)
    print("   %-20s p50=%.2fms  p95=%.2fms  max=%.2fms" % (
        name, statistics.median(xs), xs[int(len(xs) * .95) - 1], max(xs)))


def build(db_path, with_vectors, index_grams):
    if db_path.exists():
        db_path.unlink()
    rng = random.Random(20260920)
    db = sqlite3.connect(str(db_path))
    src = (ROOT / "storage.py").read_text(encoding="utf-8")
    import re
    for m in re.finditer(r"(CREATE\s+(?:VIRTUAL\s+)?TABLE[^;]*;)", src, re.I | re.S):
        try:
            db.execute(m.group(1))
        except Exception:
            pass
    for m in re.finditer(r"(CREATE\s+(?:UNIQUE\s+)?INDEX[^;]*;)", src, re.I | re.S):
        try:
            db.execute(m.group(1))
        except Exception:
            pass

    per_kb = TARGET_MB * 1024 / N_ROWS
    rows = []
    for i in range(N_ROWS):
        content = make_content(rng, max(.5, per_kb * rng.uniform(.6, 1.4)))
        summary = "".join(sentence(rng, rng.randint(3, 8)) for _ in range(2))
        rows.append(("id%06d" % i, "sid%d" % (i % 7), "user" if i % 2 else "assistant",
                     "L0", 1750000000 + i * 200, 1750000000 + i * 200, summary, content,
                     "user%d" % (i % 3), "爱奈丽", 0, 0, 1, 0, 1, 0, i, "ev%06d" % i,
                     1750000000 + i * 200, 50))
    db.executemany("insert into records values (%s)" % ",".join("?" * 20), rows)

    vec_bytes = 0
    if with_vectors:
        vcols = [c[1] for c in db.execute("pragma table_info(vectors)")]
        import array
        blob_col = next((c for c in vcols if c in ("vec", "vector", "embedding", "data", "value")), None)
        dim = 1024
        packed = array.array("f", [rng.random() for _ in range(dim)]).tobytes()
        vec_bytes = len(packed)
        if blob_col:
            ins = []
            for i in range(N_ROWS):
                vals = []
                for c in vcols:
                    if c == "id":
                        vals.append("id%06d" % i)
                    elif c == blob_col:
                        vals.append(packed)
                    elif "model" in c:
                        vals.append("bge-m3")
                    elif "dim" in c:
                        vals.append(dim)
                    elif c == 'revision':
                        vals.append(1)
                    elif 'id' in c or 'key' in c:
                        vals.append('v%06d' % i)
                    else:
                        vals.append(0)
                ins.append(tuple(vals))
            db.executemany("insert or replace into vectors values (%s)" % ",".join("?" * len(vcols)), ins)
        else:
            print("   ⚠️ vectors 表里没找到明显的 blob 列:", vcols)
    db.commit()
    db.execute("analyze")
    db.commit()
    return db, rows, vec_bytes


def main():
    sys.path.insert(0, str(ROOT))
    try:
        from retrieval import index_grams
    except Exception as e:
        index_grams = None
        print("✗ index_grams 加载失败:", e)

    print("═══ 场景 A：只有文本 ═══")
    pathA = Path("/tmp/l0_textonly.db")
    dbA, rows, _ = build(pathA, False, index_grams)
    print("   文件 %.1f MB（%d 条 × %.1f KB 目标）" % (mb(pathA), N_ROWS, TARGET_MB * 1024 / N_ROWS))
    for t in ("records", "facts", "vectors", "versions", "jobs", "job_items", "entities", "edges"):
        try:
            n = dbA.execute('select count(*) from "%s"' % t).fetchone()[0]
            print("      %-12s 行=%s" % (t, n))
        except Exception:
            pass

    print()
    print("═══ 场景 B：文本 + 向量（8322 × 1024 维 float32）═══")
    pathB = Path("/tmp/l0_withvec.db")
    dbB, _, vbytes = build(pathB, True, index_grams)
    print("   文件 %.1f MB   单条向量 %d 字节 ⇒ 全部向量约 %.1f MB" % (
        mb(pathB), vbytes, vbytes * N_ROWS / 1048576.0))
    try:
        n = dbB.execute("select count(*) from vectors").fetchone()[0]
        print("      vectors 行=%s" % n)
    except Exception as e:
        print("      vectors ✗", e)

    print()
    print("═══ 召回耗时（在场景 B 的库上量）═══")
    rng = random.Random(7)
    terms = [sentence(rng, 2).rstrip("。") for _ in range(12)]
    t_instr = []
    for term in terms:
        t0 = time.perf_counter()
        dbB.execute("select count(*) from records where deleted=0 and active=1 "
                    "and (instr(summary,?)>0 or instr(content,?)>0)", (term, term)).fetchone()
        t_instr.append((time.perf_counter() - t0) * 1000)

    t_ctx = []
    for i in range(12):
        t0 = time.perf_counter()
        dbB.execute("select id,summary,content from records where sid=? and deleted=0 and active=1 "
                    "order by position desc limit 40", ("sid%d" % (i % 7),)).fetchall()
        t_ctx.append((time.perf_counter() - t0) * 1000)

    t_vec = []
    if dbB.execute("select count(*) from vectors").fetchone()[0]:
        vcols = [c[1] for c in dbB.execute("pragma table_info(vectors)")]
        bc = next((c for c in vcols if c not in ("id", "model") and "dim" not in c), "vec")
        for i in range(12):
            t0 = time.perf_counter()
            dbB.execute('select v."%s" from vectors v join records r on r.id=v.id '
                        "where r.sid=? limit 40" % bc, ("sid%d" % (i % 7),)).fetchall()
            t_vec.append((time.perf_counter() - t0) * 1000)

    stat("instr 全表打分", t_instr)
    stat("按 sid 取近期上下文", t_ctx)
    stat("取向量（若需）", t_vec)

    print()
    print("═══ VACUUM（无损）═══")
    before = mb(pathB)
    dbB.execute("delete from records where rowid % 20 = 0")
    dbB.commit()
    mid = mb(pathB)
    t0 = time.time()
    dbB.execute("vacuum")
    after = mb(pathB)
    print("   删 5%% 后 %.1f MB ⇒ VACUUM 后 %.1f MB（还回 %.1f MB / %.0f%%）耗时 %.1fs" % (
        mid, after, mid - after, 100 * (mid - after) / mid, time.time() - t0))
    print("   （注意：本例是造出来的数据，删除的是整行 ⇒ 收益偏乐观 ✓ 你的库要看实际空洞 ✓）")

    print()
    print("═══ 重复内容（白捡收益）═══")
    dc = dbB.execute("select count(*) from (select content from records group by content having count(*)>1)").fetchone()[0]
    ds = dbB.execute("select count(*) from (select summary from records group by summary having count(*)>1)").fetchone()[0]
    print("   重复 content 组=%d  重复 summary 组=%d" % (dc, ds))

    print()
    print("═══ 参考：正文 zlib（仅参考 ✗ 会破坏 FTS/instr）═══")
    sample = dbB.execute("select content from records limit 300").fetchall()
    raw = sum(len(s[0].encode()) for s in sample)
    comp = sum(len(zlib.compress(s[0].encode(), 6)) for s in sample)
    print("   压缩率 %.0f%% ⇒ 41.5MB 文本对应约 %.1f MB" % (
        100 * comp / raw, TARGET_MB * comp / raw))
    dbA.close(); dbB.close()


if __name__ == "__main__":
    main()
