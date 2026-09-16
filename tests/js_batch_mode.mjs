// v2.18.17：压缩分批模式是**二选一**的 ✓ 选一种就把另一套参数收起来 ✗
//
// ⚠️ 血泪教训：v2.18.15 这版用 `el.hidden = true` ✗ 真实页面**根本没藏住** ✗
//    因为 `.field { display: flex }` 会盖掉 `[hidden]` 的 display:none ✓
//    而当时的测试查的是 `hidden` **属性** ✗ → 假绿 ✓（用户截图才发现 ✗）
//    ⇒ 现在断言的是**真正生效的机制**：`.hide` 类 ✓（style.css 里是 display:none !important ✓）
//    ⇒ 并且真浏览器里已复核过 computedStyle.display === "none" ✓
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "web", "app.js"), "utf8");
const css = readFileSync(join(here, "..", "web", "style.css"), "utf8");

// ① 机制前提：.hide 必须真的能盖住 .field 的 display ✓
if (!/\.hide\s*\{[^}]*display:\s*none\s*!important/.test(css)) {
  console.log("style.css 里的 .hide 不再是无条件隐藏 ✗ 分批模式的互斥会失效");
  process.exit(1);
}
if (!/\.field\s*\{[^}]*display:\s*flex/.test(css)) {
  console.log("注意：.field 不再是 flex ✗ 请复核隐藏机制是否仍然成立");
}
// ② 不许再用 hidden 属性（那个盖不过 CSS ✗）
const start = src.indexOf("const BATCH_MODE_FIELDS");
const end = src.indexOf("\nfunction renderConfig(");
if (start < 0 || end < 0) { console.log("提取失败 ✗"); process.exit(1); }
const code = src.slice(start, end);
if (/\.hidden\s*=/.test(code)) {
  console.log("又用回了 el.hidden ✗ 它盖不过 .field 的 display ✗ 请用 .hide 类");
  process.exit(1);
}

// ③ 行为：两种模式下 3 个字段的收/放 ✓
const make = () => {
  const set = new Set();
  return {
    get hidden() { return set.has("hide"); },
    classList: {
      contains: (c) => set.has(c),
      toggle: (c, on) => { if (on === undefined) { set.has(c) ? set.delete(c) : set.add(c); } else if (on) { set.add(c); } else { set.delete(c); } },
    },
    _set: set,
  };
};
const fields = {};
const modeEl = { value: "rounds" };
global.document = {
  querySelector: (sel) => {
    if (sel === '[data-key="compress_batch_mode"]') return modeEl;
    const m = sel.match(/\[data-field="(.+)"\]/);
    if (m) return fields[m[1]] || (fields[m[1]] = make());
    return null;
  },
};
const applyBatchMode = new Function(code + "\n; return applyBatchMode;")();

const shown = (k) => (fields[k]._set.has("hide") ? "收起" : "显示");
const report = () => ["compress_rounds", "threshold", "batch_size"]
  .map((k) => k + "=" + shown(k)).join("  ");

modeEl.value = "rounds";
applyBatchMode();
console.log("模式 rounds  →", report());
const ok1 = shown("compress_rounds") === "显示" && shown("threshold") === "收起" && shown("batch_size") === "收起";

modeEl.value = "records";
applyBatchMode();
console.log("模式 records →", report());
const ok2 = shown("compress_rounds") === "收起" && shown("threshold") === "显示" && shown("batch_size") === "显示";

console.log();
console.log(ok1 && ok2 ? "互斥显示正确 ✓（用的是 .hide 类 ✓ 与真实页面一致 ✓）" : "不通过 ✗");
process.exit(ok1 && ok2 ? 0 : 1);
