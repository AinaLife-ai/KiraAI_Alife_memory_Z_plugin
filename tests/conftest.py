"""测试全局隔离（2026-10-11）

★ 背景：插件启动时会做**一次性配置迁移**（存量升级用 ✓）⇒ 它会写"宿主全局"的
   插件配置文件（`main.get_config_path()`）✗
   在真实机器上那是用户自己的配置 ✓ 无害；但在测试里**同一进程的用例共用一个路径** ✗
   ⇒ 会互相污染：实测某用例把 `jev_enabled=True` 写进去 ⇒ 后续用例读到它 ⇒ 假失败 ✗
   （上游原版也会因此失败 ⇒ 属于测试隔离缺陷，不是产品问题 ✓）

⇒ 这里用 autouse 夹具把该路径**统一隔离到每个用例自己的临时目录** ✓
   这样：任何用例触发的迁移写入都只影响它自己 ✓ 相邻用例互不干扰 ✓
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_plugin_config_path(tmp_path):
    # ★ 测试里插件常被导成 `alife_xxx.main`（不是 `main` ✗）⇒ 扫所有 *.main ✓
    targets = [
        m for name, m in list(sys.modules.items())
        if m is not None and (name == "main" or name.endswith(".main"))
        and hasattr(m, "get_config_path")
    ]
    if not targets:
        yield                      # 该用例没导入插件主模块 ⇒ 无需隔离 ✓
        return
    saved = [(m, m.get_config_path) for m in targets]
    for m, _ in saved:
        m.get_config_path = lambda: tmp_path / "cfg"
    try:
        yield
    finally:
        for m, fn in saved:
            m.get_config_path = fn
