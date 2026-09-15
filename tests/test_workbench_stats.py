"""整理工作台的两个指示牌：L0 容量 / 召回用量。

真实事故（用户实测）：工作台上「L0 容量 —」永远不变 ✗
两层原因：
1. `capacity_stats()` 用 `self.conn / _conn / db` 取连接 ✗ —— 本项目的 store 只有
   `self.connect()` 上下文管理器 → 永远拿到 None → 永远 `return {}` ✓
2. 标签是运行时 createElement 插到 #searchIndex 旁边的 ✗ → 那块 DOM 一重渲染就丢 ✓
静态断言（"代码里有 xxx 字样"）对这两类**完全无效** ✗ 所以这里做行为级验证 ✓
"""

import importlib
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("alife_wb_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alife_wb_test", package)
s = importlib.import_module("alife_wb_test.storage")


class CapacityCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = s.Store(Path(self.temp.name) / "m.db")
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_returns_real_numbers_not_empty_dict(self):
        """真·回归：旧实现永远返回 {} → 界面永远「L0 容量 —」✓"""
        self.store.capture(
            "qq:gm:1", "t",
            [{"role": "user", "content": "库里的一条", "users": ["qq:9"], "time": 1.0}],
        )
        cap = self.store.capacity_stats()
        self.assertTrue(cap, "不能是空 dict ✗（self.conn/_conn/db 在本 store 里不存在）")
        self.assertGreater(cap.get("db_bytes", 0), 0, "库大小必须 > 0")
        self.assertTrue(cap.get("levels"), "每层条数必须非空")
        self.assertTrue(cap.get("years"), "逐年增长必须非空")
        self.assertIn("fts_rows", cap)

    def test_accepts_explicit_conn(self):
        with self.store.connect() as db:
            cap = self.store.capacity_stats(conn=db)
        self.assertIn("db_bytes", cap)

    def test_empty_db_is_safe(self):
        cap = self.store.capacity_stats()
        self.assertIsInstance(cap, dict)
        self.assertIn("db_bytes", cap)


class ContractCase(unittest.TestCase):
    def test_frontend_backend_field_contract(self):
        """前端从 /status 读的每个字段，后端都得真的给 ✓（防只改一边）"""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "status_contract.py")],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_no_dynamic_tag_injection(self):
        """两个指示牌必须是 HTML 里的静态元素 ✓ 不许再 createElement 插 ✓"""
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="capTag"', html)
        self.assertIn('id="recallTag"', html)
        self.assertNotIn("capacityEl", js, "旧的运行时插入写法必须清掉 ✗")
        self.assertNotIn("usageEl", js, "旧的运行时插入写法必须清掉 ✗")
        self.assertIn('const capEl = $("#capTag")', js)
        self.assertIn('const recallEl = $("#recallTag")', js)
