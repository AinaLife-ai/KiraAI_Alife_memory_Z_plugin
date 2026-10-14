"""前端 JS 逻辑测试：把 web/app.js 里的**真函数**抠出来在 node 里跑（没有 node 就跳过 ✓）

为什么要有它：像"两个模型芯片长得一模一样"这种问题，Python 侧测不到 ✗
只测后端会漏掉**纯前端**的显示/交互逻辑 ✗ —— 用户问题 1（同名模型分不清）正是这一类 ✓
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not APP_JS.exists(),
    reason="需要 node 与 web/app.js",
)

DRIVER = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function extract(name) {
  // 支持 function f(){} / const f = (…) => …; / const f = function…; 三种写法 ✓
  let head = "function " + name + "(";
  let i = src.indexOf(head);
  let arrow = false;
  if (i < 0) { head = "const " + name + " = ("; i = src.indexOf(head); arrow = i >= 0; }
  if (i < 0) { head = "const " + name + " = function"; i = src.indexOf(head); arrow = i >= 0; }
  if (i < 0) throw new Error("抠不到函数 " + name + "（改名了？）");
  if (arrow) {
    let depth = 0;
    for (let j = i + head.length; j < src.length; j++) {
      const c = src[j];
      if ("([{".includes(c)) depth++;
      else if (")]}".includes(c)) depth--;
      else if (c === ";" && depth <= 0) return src.slice(i, j + 1);
    }
    throw new Error("箭头函数 " + name + " 找不到结尾分号");
  }
  let depth = 0, started = false;
  for (let j = i; j < src.length; j++) {
    const c = src[j];
    if (c === "{") { depth++; started = true; }
    else if (c === "}") { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
  }
  throw new Error("函数 " + name + " 大括号不平衡");
}

const want = ["esc", "modelLabel", "marqueeName", "markMarquees", "chainLookup", "chainKeyOf", "chainRefsOf", "chainNameOf",
              "chainRowHtml", "chainListHtml"];
const code = want.map(extract).join("\n");
const sandbox = { Set, Map, Array, String, Object, JSON, console, config: { settings: {} } };
require("vm").createContext(sandbox);
require("vm").runInContext(code + "\n;globalThis.__fn={modelLabel,chainLookup,chainListHtml,chainRefsOf,marqueeName,markMarquees};", sandbox);
const F = sandbox.__fn;

const dup = [
  { id: "pid-a:uuid-1", name: "gpt-4o", model: "gpt-4o", provider: "Workbuddy", kind: "llm", legacy: [], dup: true, tag: "pid-a" },
  { id: "pid-b:uuid-2", name: "gpt-4o", model: "gpt-4o", provider: "Workbuddy", kind: "llm", legacy: [], dup: true, tag: "pid-b" },
  { id: "pid-c:uuid-3", name: "gpt-4o", model: "gpt-4o", provider: "Trae", kind: "llm", legacy: [] },
  { id: "pid-d:emb", name: "text-emb", model: "text-emb", provider: "Trae", kind: "embedding", legacy: [] },
];
const labels = dup.map(F.modelLabel);
const uniq = new Set(labels);
const html = F.chainListHtml([], dup, false, F.chainLookup(dup));
const rows = (html.match(/data-ref=/g) || []).length;
// ★ 长名字滚动（用户要求）：结构 + 只有放不下才滚 ✓
const mq = F.marqueeName("gpt-4o · Workbuddy · pid-a");
const toggled = [];
F.markMarquees({ querySelectorAll: () => [
  { scrollWidth: 300, clientWidth: 120, classList: { toggle: (_c, on) => { toggled.push(on); } } },
  { scrollWidth: 100, clientWidth: 120, classList: { toggle: (_c, on) => { toggled.push(on); } } },
]});
console.log(JSON.stringify({
  labels, unique: uniq.size, rows,
  legacy_fallback: F.chainRefsOf("compress_model", "workbuddy:gpt-4o").length,
  // ★ 恢复默认后的状态：值清空 ⇒ 链必须为空（= 跟随宿主默认 ✓ 不是"零档" ✗）
  empty_chain: F.chainRefsOf("compress_models", "").length,
  empty_legacy: F.chainRefsOf("compress_model", "").length,
  marquee: {
    copies: (mq.match(/mmq-tx/g) || []).length,
    hasInner: mq.indexOf("mmq-in") >= 0,
    hasTitle: mq.indexOf('title="') >= 0,
    escaped: F.marqueeName("a<b>&c"),
  },
  toggled,
}));
"""


def _run_driver():
    with tempfile.TemporaryDirectory() as tmp:
        driver = Path(tmp) / "driver.js"
        driver.write_text(DRIVER, encoding="utf-8")
        out = subprocess.run(["node", str(driver), str(APP_JS)],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, "node 跑失败：%s" % (out.stderr[-800:] or out.stdout[-800:])
        return json.loads(out.stdout.strip().splitlines()[-1])


def test_same_name_models_render_distinguishable_labels():
    """★ 同名/同名不同型提供商的模型，标签必须各不相同（否则用户分不清、易删错 ✗）"""
    data = _run_driver()
    labels = data["labels"]
    assert len(labels) == 4, "标签数量不对 ✗"
    assert len(set(labels)) == 4, "有名模型的标签重复了 ✗ ⇒ 前端仍然分不清：%s" % labels
    assert any("Workbuddy · pid-a" in x for x in labels), "重名条目没带上区分标识 ✗：%s" % labels
    assert any("向量" in x for x in labels), "向量模型标签应标注类型 ✗：%s" % labels
    assert data["rows"] == 3, "链编辑器应只列出 llm 模型（3 个）✗ 实际 %d" % data["rows"]


def test_legacy_single_model_still_counts_as_first_link():
    """★ 旧配置只有单模型 ⇒ 仍要显示成"第 1 档"（不能因为改造而丢 ✗）"""
    data = _run_driver()
    assert data["legacy_fallback"] == 1, "旧单模型值没有回退成第 1 档 ✗"


def test_long_model_name_marquee_is_ready_for_scrolling():
    """★ 用户要求：名字太长（名+提供商+撞车后缀）⇒ 悬停与选中时**横向滚动** ✓

    结构必须是「两份文本 + 可动画容器」✓（CSS 位移 -50% 正好一份 ⇒ 无缝循环 ✓）
    并且要有 title（悬停能看到完整名 ✓）
    """
    data = _run_driver()
    mq = data["marquee"]
    assert mq["copies"] == 2, "marquee 里必须放**两份**文本，否则滚动不无缝 ✗"
    assert mq["hasInner"], "缺少可动画的容器 .mmq-in ✗"
    assert mq["hasTitle"], "缺少 title ⇒ 鼠标悬停看不到完整名 ✗"
    assert "&lt;" in mq["escaped"] and "&amp;" in mq["escaped"], "名字没转义 ⇒ 有 XSS 风险 ✗"


def test_marquee_only_scrolls_when_text_overflows():
    """★ 只有**真的放不下**才滚 ✓（短名字乱动很难看 ✗）"""
    data = _run_driver()
    assert data["toggled"] == [True, False], (
        "溢出判定不对 ✗（长名字应 True、短名字应 False）实际 %s" % data["toggled"])


def test_marquee_css_exists_in_stylesheet():
    """★ CSS 里必须有滚动动画 + 悬停/选中/悬停显示框三条触发规则 ✓"""
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert "@keyframes mmq-run" in css, "缺少滚动关键帧 ✗"
    assert "translateX(-50%)" in css, "位移动画必须是 -50%（一份宽 ⇒ 无缝 ✓）✗"
    assert ".mmq.is-wide:hover .mmq-in" in css, "悬停触发规则缺失 ✗"
    assert ".chain-item.is-on" in css, "已选中条目常驻滚动规则缺失 ✗"
    assert "prefers-reduced-motion" in css, "应尊重系统「减少动态效果」 ✗"


def test_restore_default_clears_the_chain_so_host_default_applies():
    """★ 用户问的："前端点恢复默认并保存，真的恢复默认并正常使用了吗？"

    恢复默认 ⇒ 值被清空 ⇒ 前端解析出的链必须是**空**（= 跟随宿主默认 ✓）
    而不是残留旧值 ✗（那样保存后还在用旧模型 ⇒ 看起来"恢复了"其实没有 ✗）
    """
    data = _run_driver()
    assert data["empty_chain"] == 0, "清空后链里还有残留 ✗ 实际 %s 档" % data["empty_chain"]
    assert data["empty_legacy"] == 0, "清空后旧单模型字段还有残留 ✗"
    # 而"有值"时仍然照旧解析成 1 档（不能把正常配置也清掉 ✗）
    assert data["legacy_fallback"] == 1, "有旧单模型值时应当解析成第 1 档 ✗"
