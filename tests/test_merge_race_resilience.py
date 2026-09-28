"""合并/撤回的**并发韧性**守卫（v2.21.2）。

背景（全部为真实现象，不是假想）：
  · 合并任务先把一组的判定算好，再**花一次模型调用**（~24 秒），最后才落笔写库。
    这中间只要有人动了组里的**任意一条**（审计撤回 / 面板删除 / forget 工具 /
    同期的 tidy 改写 / 回收站清理），旧代码的"重读保护"就会失效 ✗：
        `if _fresh and len(_fresh) == len(group): group = _fresh`
      —— 条数不等 ⇒ **整块丢弃新鲜数据** ⇒ 继续用旧 revision 写 ⇒ 必然
      `Conflict("record changed; reload before saving")` ⇒ 整组白跑 ✗
  · "统一类别"那步给每条 revision +1，drop 那步用
      `row["revision"] + (1 if unified else 0)` **手工补偿** ✗
    只要那步没真的 +1（该行类别本就一致 ⇒ 整个循环被跳过 ✗）⇒ **自己撞自己**
    ⇒ 撤回**静默失败**（明细一条都不写 ⇒ 面板看不出"该撤没撤" ✗）。
  · 失败文案写"已恢复可见 / 稍后自动重试"：
    前者对**已被删除**的成员不成立（mark_merge_pending 的 SQL 带 `AND deleted=0`），
    后者没有定时器 —— 真相是"**再次被召回且仍相似时**才会自动重新判定"。

本文件把这些**钉死**；旧代码跑本文件必须精确变红（见每条 docstring 末尾 ✓）。

⚠️ 注意：送给模型的载荷里 id 是**短别名**（g1-1…）✗ ⇒ 测试要用「内容」反查真实 id ✓。
"""

import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_race_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_race_test", package)
c = importlib.import_module("alife_race_test.contracts")
s = importlib.import_module("alife_race_test.storage")
e = importlib.import_module("alife_race_test.engine")


def run(coro):
    return asyncio.run(coro)


class MergeRaceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "m.db")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    # ---- 工具 ----
    def seed(self, texts, sid="qq:gm:1", subject="qq:9", category="preference"):
        messages = [
            {"role": "user", "content": text, "users": [subject], "time": float(i)}
            for i, text in enumerate(texts)
        ]
        self.store.capture(sid, "turn", messages)
        records = self.store.active(sid)
        ids = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for index, text in enumerate(texts):
                ids.append(self.store._add_fact(db, sid, {
                    "category": category, "subject": subject, "content": text,
                    "reason": "", "scenario": "", "tags": [], "relations": [],
                    "source_ids": [records[index]["id"]],
                    "importance": 5 + index,
                }))
        return ids

    def engine(self, cfg, model):
        return e.Engine(self.store, lambda: cfg, model, None, None)

    def job(self, sid="qq:gm:1"):
        job_id = self.store.enqueue("fact_merge", sid)
        self.store.claim(kind="fact_merge")
        return job_id

    def reply(self, rows, action=None, category=None):
        out = {
            "target_id": rows[0]["id"],          # 载荷里是别名 ✓ 由代码还原 ✓
            "source_ids": [r["id"] for r in rows],
            "content": "萤火对花生严重过敏",
            "reason": "合并重复",
        }
        if action:
            out["action"] = action
        if category:
            out["category"] = category
        return json.dumps({"groups": [out]}, ensure_ascii=False)

    async def a_soft_delete(self, fact_id, reason="审计并发撤回"):
        """用与审计完全相同的途径撤回一条事实（走版本守卫 ✓）。

        ⚠️ 必须是 **async**（在模型回调里 await ✓）：
           在事件循环里调 asyncio.run 会 RuntimeError ⇒ 模型调用被判失败 ⇒
           引擎退回"原文拼接"兜底 ⇒ 测试测的就不是并发场景了 ✗（踩过 ✓）
        """
        row = self.store.facts_by_ids([fact_id])[0]
        return await self.store.call(
            "edit", "fact", fact_id, row["revision"], {"deleted": True}, reason
        )

    def real_id(self, content, ids):
        """载荷里给模型的是短别名 ✗ ⇒ 用内容反查真实 id ✓。"""
        for fid in ids:
            if self.store.facts_by_ids([fid])[0]["content"] == content:
                return fid
        raise AssertionError("找不到内容为 %r 的事实" % content)

    def deleted_ids(self):
        with self.store.connect() as db:
            return {r[0] for r in db.execute(
                "SELECT id FROM facts WHERE deleted=1").fetchall()}

    def live_ids(self):
        with self.store.connect() as db:
            return {r[0] for r in db.execute(
                "SELECT id FROM facts WHERE deleted=0").fetchall()}

    def pick_other(self, keep_id, ids):
        return next(fid for fid in ids if fid != keep_id)

    # =====================================================================
    # ① 组员（非目标）在模型思考期间被并发删除 ⇒ 剩下的仍必须合并
    #    旧代码：整组丢旧数据 ⇒ Conflict ⇒ merged == 0 ⇒ 变红 ✗
    # =====================================================================
    def test_deleted_member_midflight_still_merges_survivors(self):
        ids = self.seed(["萤火对花生过敏", "萤火对花生严重过敏", "萤火花生重度过敏"])
        job = self.job()
        holder = {}

        async def model(*args):
            rows = args[-1]["groups"][0]["facts"]
            target = self.real_id(rows[0]["content"], ids)
            victim = self.pick_other(target, ids)
            holder["victim"] = victim
            await self.a_soft_delete(victim)
            return self.reply(rows)

        engine = self.engine(c.Settings(), model)
        run(engine.queue_fact_merges("qq:gm:1", 0))
        merged = run(engine.merge_facts("qq:gm:1", job))
        self.assertEqual(merged, 1, "组内一条被并发删除 ⇒ 剩下的仍必须合并 ✓")
        self.assertEqual(len(self.live_ids()), 1, "应当只剩合并后的那一条 ✓")
        self.assertIn(holder["victim"], self.deleted_ids(),
                      "被并发撤回的那条必须保持撤回（不许被写回 ✓）")

    # =====================================================================
    # ② 只剩 1 条 ⇒ 零写入 + 如实留痕
    #    旧代码：整组丢旧数据 ⇒ Conflict ⇒ note 是"合并失败…（已恢复可见，稍后自动重试）"⇒ 变红 ✗
    # =====================================================================
    def test_group_left_with_one_member_writes_nothing_and_traces(self):
        ids = self.seed(["萤火对花生过敏", "萤火对花生严重过敏"])
        job = self.job()
        holder = {}

        async def model(*args):
            rows = args[-1]["groups"][0]["facts"]
            target = self.real_id(rows[0]["content"], ids)
            victim = self.pick_other(target, ids)
            holder["survivor"] = target
            holder["content"] = rows[0]["content"]
            await self.a_soft_delete(victim)
            return self.reply(rows)

        engine = self.engine(c.Settings(), model)
        run(engine.queue_fact_merges("qq:gm:1", 0))
        self.assertEqual(run(engine.merge_facts("qq:gm:1", job)), 0)

        items = self.store.job_items(job)
        keep = [i for i in items if i["action"] == "keep"]
        self.assertTrue(keep, "必须留痕（前端点开不能是空的）")
        self.assertIn("未合并", keep[0]["note"])
        self.assertIn("已被并发删除", keep[0]["note"])
        # 幸存者零写入：还活着、内容没变 ✓
        self.assertEqual(self.live_ids(), {holder["survivor"]})
        self.assertEqual(
            self.store.facts_by_ids([holder["survivor"]])[0]["content"],
            holder["content"],
        )

    # =====================================================================
    # ③ 目标在模型思考期间被删 ⇒ 整组跳过（不换目标、不赌）+ 如实留痕
    # =====================================================================
    def test_target_deleted_midflight_skips_group(self):
        ids = self.seed(["萤火对花生过敏", "萤火对花生严重过敏", "萤火花生重度过敏"])
        job = self.job()
        holder = {}

        async def model(*args):
            rows = args[-1]["groups"][0]["facts"]
            target = self.real_id(rows[0]["content"], ids)
            holder["target"] = target
            await self.a_soft_delete(target)
            return self.reply(rows)

        engine = self.engine(c.Settings(), model)
        run(engine.queue_fact_merges("qq:gm:1", 0))
        self.assertEqual(run(engine.merge_facts("qq:gm:1", job)), 0)
        notes = " ".join(i["note"] for i in self.store.job_items(job))
        self.assertIn("目标事实已被并发删除", notes)
        self.assertEqual(self.deleted_ids(), {holder["target"]},
                         "目标消失时不许动任何幸存者（也不许多删 ✓）")
        self.assertEqual(len(self.live_ids()), 2, "两条幸存者必须原样活着 ✓")

    # =====================================================================
    # ④ drop 判定必须用**刚读回的**版本号（不再手工 +1）
    #    构造：模型给的 category 与现有类别**相同** ⇒ 旧代码的统一循环被跳过
    #           ⇒ `revision + 1` 自撞 ⇒ 撤回静默失败 ⇒ 变红 ✗
    # =====================================================================
    def test_drop_uses_fresh_revision_and_really_retracts(self):
        ids = self.seed(["萤火对花生过敏", "萤火对花生严重过敏"])
        job = self.job()

        async def model(*args):
            rows = args[-1]["groups"][0]["facts"]
            return self.reply(rows, action="drop", category="preference")

        engine = self.engine(c.Settings(), model)
        run(engine.queue_fact_merges("qq:gm:1", 0))
        run(engine.merge_facts("qq:gm:1", job))
        self.assertEqual(len(self.deleted_ids()), 1, "撤回必须真的落库 ✓")
        self.assertEqual(len(self.live_ids()), 1, "保留的那条必须还活着 ✓")
        actions = {i["action"] for i in self.store.job_items(job)}
        self.assertIn("retract", actions)
        self.assertNotIn("retract_failed", actions)

    # =====================================================================
    # ⑤ 撤回真被并发挡住 ⇒ 必须**留痕**（不许静默）
    #    旧代码：动作是 keep + "合并失败…"，看不出"该撤没撤" ⇒ 变红 ✗
    # =====================================================================
    def test_retract_failure_leaves_trace(self):
        ids = self.seed(["萤火对花生过敏", "萤火对花生严重过敏"])
        job = self.job()
        holder = {}

        async def model(*args):
            rows = args[-1]["groups"][0]["facts"]
            holder["target"] = self.real_id(rows[0]["content"], ids)
            return self.reply(rows, action="drop")

        orig = self.store.call

        async def flaky(action, *a, **k):
            # edit(kind, target, revision, patch, reason) —— 只打"软删"那一笔
            if (action == "edit" and len(a) >= 4 and isinstance(a[3], dict)
                    and a[3].get("deleted")):
                raise s.Conflict("record changed; reload before saving")
            return await orig(action, *a, **k)

        engine = self.engine(c.Settings(), model)
        run(engine.queue_fact_merges("qq:gm:1", 0))
        self.store.call = flaky
        try:
            run(engine.merge_facts("qq:gm:1", job))
        finally:
            self.store.call = orig
        items = self.store.job_items(job)
        failed = [i for i in items if i["action"] == "retract_failed"]
        self.assertTrue(failed, "撤回失败必须留痕 ✗（此前静默 ✗）")
        self.assertIn("未撤回", failed[0]["note"])
        self.assertIn("record changed", failed[0]["note"])
        # 目标本身仍然完好 ✓（不许因为一条撤不掉就把整组搞乱）
        self.assertIn(holder["target"], self.live_ids())

    # =====================================================================
    # ⑥ 文案不许有悬空承诺（只看代码行 ✓ 注释里保留说明是允许的 ✓）
    # =====================================================================
    def test_wording_makes_no_dangling_promise(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.split("\n") if not line.strip().startswith("#")
        )
        self.assertEqual(code.count("稍后自动重试"), 0,
                         "没有定时器就别承诺'稍后自动重试' ✗")
        self.assertEqual(code.count("已恢复可见"), 0,
                         "已被删除的成员恢复不了 ⇒ 不许写'已恢复可见' ✗")
        self.assertGreaterEqual(code.count("若再次被召回且仍相似"), 1)

    # =====================================================================
    # ⑦ 失败组不会被"藏死"，且**再次被召回时会被重新判定**（自愈 ✓）
    #    这是用户坚持要保住的设计：不靠人工、不靠定时器 ✓
    # =====================================================================
    def test_failed_group_is_refetched_by_recall_path(self):
        ids = self.seed(["萤火对花生过敏", "萤火对花生严重过敏", "萤火花生重度过敏"])
        cfg = c.Settings()
        job = self.job()

        async def model(*args):
            return self.reply(args[-1]["groups"][0]["facts"])

        engine = self.engine(cfg, model)
        run(engine.queue_fact_merges("qq:gm:1", 0))

        # 第一次：让存储层拒绝这一组（模拟并发/守卫冲突）
        orig = self.store.call
        state = {"done": False}

        async def flaky(action, *a, **k):
            if action == "merge_facts" and not state["done"]:
                state["done"] = True
                raise s.Conflict("merge source changed")
            return await orig(action, *a, **k)

        self.store.call = flaky
        try:
            self.assertEqual(run(engine.merge_facts("qq:gm:1", job)), 0)
        finally:
            self.store.call = orig

        # 失败后：事实**没有**被藏起来（放出来 ⇒ 正常参与召回 ✓）
        visible = self.store.facts("qq:gm:1", hide_pending=cfg.merge_pending_hide)
        self.assertEqual(len(visible), 3, "失败组必须回到可见状态 ✓")

        # 下一次召回：走的就是 main.queue_recall_merges 的那两步（判重 → 标记 → 入队）
        flagged = self.store.flag_similar_pairs(
            ids, cfg.fact_merge_threshold, cfg.fact_merge_cross_threshold
        )
        self.assertTrue(flagged, "召回侧必须能重新发现这一对相似 ✓")
        run(self.store.call("mark_merge_pending", flagged, 1))
        job2 = self.job()
        self.assertEqual(run(engine.merge_facts("qq:gm:1", job2)), 1,
                         "再被召回后必须能合并成功 ✓（自愈，不需要人工 ✗）")

    # =====================================================================
    # ⑧ 合并与审计并发跑：安全（不抛、不留僵尸隐藏标记）
    # =====================================================================
    def test_concurrent_merge_and_audit_are_safe(self):
        self.seed(["萤火对花生过敏", "萤火对花生严重过敏", "萤火花生重度过敏"])
        cfg = c.Settings()
        merge_job = self.job()

        async def model(*args):
            payload = args[-1]
            if isinstance(payload, dict) and payload.get("facts"):
                return json.dumps({"actions": []}, ensure_ascii=False)
            return self.reply(payload["groups"][0]["facts"])

        engine = self.engine(cfg, model)
        run(engine.queue_fact_merges("qq:gm:1", 0))

        async def both():
            audit_job = self.store.enqueue("audit", "qq:gm:1")
            self.store.claim(kind="audit")
            return await asyncio.gather(
                engine.merge_facts("qq:gm:1", merge_job),
                engine.audit("qq:gm:1", audit_job),
                return_exceptions=True,
            )

        results = run(both())
        for item in results:
            self.assertNotIsInstance(item, Exception, "并发跑不许抛异常 ✓")
        with self.store.connect() as db:
            pending = db.execute(
                "SELECT count(*) FROM facts WHERE merge_pending=1 AND deleted=0"
            ).fetchone()[0]
        self.assertEqual(pending, 0,
                         "不许留下'待合并'僵尸标记（会被排除在召回之外）✗")


if __name__ == "__main__":
    unittest.main()
