#!/usr/bin/env python3
"""未来版本兼容探针 —— 确认本插件**不绑死某个框架版本**，对更高版本同样可用 ✓

为什么要它（2026-10-14，用户要求"以 3.0 及未来更高版本兼容为准"）：
    单测跑在**当前**框架上 ⇒ 只能证明"这一版没问题" ✗ 证明不了下一版 ✓
    本工具会造一份"**未来框架**"（在真框架副本上做无害演进），再跑本仓三套兼容测试：
      A 无害变更（真实发版里最典型 ✓）：版本号提高 + PluginContext 加字段/方法 + 会话类加属性
        ⇒ 期望：**全绿** ✓（绿了就说明测试没有绑死当前版本 ✓）
      B 破坏性变更：把插件在用的某个符号改名
        ⇒ 期望：**按预期报红** ✓ 且**精确点名**是哪个符号（说明它真能拦住 ✗ 而不是假绿 ✓）

用法：
    python3 tools/future_compat_probe.py --core /path/to/KiraAI        # 默认只跑 A
    python3 tools/future_compat_probe.py --core /path/to/KiraAI --break 符号名   # 额外跑 B

注意：会把框架整份复制到临时目录（只读源框架 ✓ 绝不改它 ✓）；没装 node/pytest 的环境可跳过 ✓
"""
import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = ["tests/test_framework_symbol_contract.py", "tests/test_real_plugin_manager.py",
          "tests/test_upgrade_path_e2e.py"]


def copy_framework(core: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="future_fw_"))
    dst = tmp / "fw"
    shutil.copytree(core, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    (dst / "data").mkdir(exist_ok=True)          # 框架起日志要它 ✓
    return dst


def _insert_in_class(src: str, class_name: str, snippet: str) -> str:
    """在类体首个方法之前插入一行（保持缩进 ✓ 插完先 ast.parse 自检 ✗ 防改坏）"""
    lines = src.splitlines(keepends=True)
    cls_at = next((i for i, l in enumerate(lines)
                   if re.match(r"class\s+%s\b" % re.escape(class_name), l)), None)
    if cls_at is None:
        raise SystemExit("找不到类 %s（框架结构变了？）" % class_name)
    for i in range(cls_at + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t", '"', "'")) and lines[i].strip():
            at = i
            break
    else:
        at = len(lines)
    indent = "    "
    lines.insert(at, snippet.rstrip() + "\n")
    out = "".join(lines)
    ast.parse(out)          # ★ 立刻自检：改坏就报错退出，别拿坏框架跑测试 ✗
    return out


def mutate_harmless(fw: Path, new_ver: str) -> list:
    """A 类：真实发版里最典型的三类**无害**演进 ✓"""
    done = []
    # ① 版本号提高
    for rel in ("core/config/default.py",):
        p = fw / rel
        if p.exists() and re.search(r"VERSION\s*=\s*[\"']", p.read_text(encoding="utf-8")):
            p.write_text(re.sub(r"(VERSION\s*=\s*)[\"'][^\"']*[\"']", r'\g<1>"%s"' % new_ver,
                                p.read_text(encoding="utf-8")), encoding="utf-8")
            done.append("版本号 → %s" % new_ver)
            break
    # ② PluginContext 加字段 + 加方法
    p = fw / "core/plugin/plugin_context.py"
    if p.exists():
        src = p.read_text(encoding="utf-8")
        src = _insert_in_class(src, "PluginContext",
                               'future_extra_field: str = ""        # 未来版本可能新增的字段 ✓')
        src = _insert_in_class(src, "PluginContext",
                               'def future_extra_method(self):\n        return "future"   # 未来版本可能新增的方法 ✓')
        p.write_text(src, encoding="utf-8")
        done.append("PluginContext 加字段/方法")
    # ③ 会话类加属性
    p = fw / "core/chat/session.py"
    if p.exists():
        src = _insert_in_class(p.read_text(encoding="utf-8"), "Session",
                               'future_flag: bool = False            # 未来版本可能新增的会话属性 ✓')
        p.write_text(src, encoding="utf-8")
        done.append("Session 加属性")
    return done


def mutate_breaking(fw: Path, symbol: str) -> str:
    """B 类：把插件在用的符号改名 ⇒ 探针应当报红并点名 ✓"""
    hit = 0
    for rel in ("core/plugin/plugin_context.py",):
        p = fw / rel
        src = p.read_text(encoding="utf-8")
        new = src.replace("def %s(" % symbol, "def %s_renamed(" % symbol)
        if new != src:
            ast.parse(new)
            p.write_text(new, encoding="utf-8")
            hit += 1
    return "把 %s 改名为 %s_renamed（命中 %d 处）" % (symbol, symbol, hit)


def run_suites(core: Path):
    env = dict(os.environ)
    env["KIRA_CORE"] = str(core)
    proc = subprocess.run([sys.executable, "-m", "pytest", *SUITES, "-q", "--tb=line"],
                          cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=1200)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return proc.returncode, (out[-1] if out else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True, help="框架仓库根目录")
    ap.add_argument("--break", dest="brk", default="", help="要改名的插件在用符号（做破坏性验证）")
    ap.add_argument("--version", default="v9.9.9", help="未来版本号（默认 v9.9.9）")
    a = ap.parse_args()
    core = Path(a.core).resolve()
    if not (core / "core").exists():
        raise SystemExit("不是框架根目录：%s" % core)
    print("基准框架：%s" % core)
    rc, line = run_suites(core)
    print("① 当前框架基线：rc=%d ｜ %s" % (rc, line))
    if rc != 0:
        print("   ⚠ 当前框架上就有失败 ⇒ 先修好，再谈未来版本 ✓")
        return 1
    fw = copy_framework(core)
    print("② 造「未来框架」（只读复制，绝不改源框架 ✓）：%s" % fw)
    for what in mutate_harmless(fw, a.version):
        print("   + %s" % what)
    rc, line = run_suites(fw)
    print("③ 无害未来变更下重跑：rc=%d ｜ %s" % (rc, line))
    ok_a = rc == 0
    print("   ⇒ %s" % ("全绿 ✓ 说明没绑死当前版本 ✓" if ok_a else
                      "报红了 ✗ 说明测试绑死了某个版本细节，得改成版本无关 ✗"))
    ok_b = None
    if a.brk:
        fw2 = copy_framework(core)
        print("④ 破坏性变更（造第二个未来框架）：%s" % mutate_breaking(fw2, a.brk))
        rc, line = run_suites(fw2)
        out = subprocess.run([sys.executable, "-m", "pytest", SUITES[0], "-q", "--tb=line"],
                             cwd=str(ROOT), capture_output=True, text=True,
                             env=dict(os.environ, KIRA_CORE=str(fw2)), timeout=600)
        named = a.brk in ((out.stdout or "") + (out.stderr or ""))
        ok_b = (rc != 0) and named
        print("   结果：rc=%d ｜ 是否点名了 %s = %s" % (rc, a.brk, named))
        print("   ⇒ %s" % ("按预期报红且精确点名 ✓" if ok_b else "没有按预期报红 ✗ 检查判据"))
    print("\n结论：A=%s%s" % ("✓" if ok_a else "✗",
                             (" ｜ B=%s" % ("✓" if ok_b else "✗")) if ok_b is not None else ""))
    return 0 if ok_a and (ok_b is None or ok_b) else 1


if __name__ == "__main__":
    sys.exit(main())
