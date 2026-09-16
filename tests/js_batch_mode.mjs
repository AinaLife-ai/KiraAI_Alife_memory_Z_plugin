// v2.18.15：压缩分批模式是二选一的 ✓ 选一种就把另一套参数收起来 ✓
// 这个检查用最小 DOM 桩**真跑** applyBatchMode ✓（不是看字符串在不在 ✓）
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
// ⚠️ 相对**脚本自身**取路径 ✗ 用 cwd 的话从 tests/ 跑就找不到文件 ✓（实测踩过 ✓）
const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "web", "app.js"), "utf8");
const start = src.indexOf("const BATCH_MODE_FIELDS");
const end = src.indexOf("\nfunction renderConfig(");
if (start < 0 || end < 0) { console.log("提取失败 ✗"); process.exit(1); }
const code = src.slice(start, end);

// 最小 DOM 桩：mode 选择器 + 四个字段包装 + 插入提示
const make = (extra = {}) => ({ hidden: false, className: "", textContent: "", dataset: {},
  closest: () => modeLabel, insertAdjacentElement: (pos, el) => { modeLabel.inserted = el; },
  ...extra });
const modeLabel = { inserted: null, insertAdjacentElement: (pos, el) => { modeLabel.inserted = el; } };
const fields = {};
let modeValue = "rounds";
const modeEl = { value: modeValue, closest: () => modeLabel };
global.document = {
  querySelector: (sel) => {
    if (sel === '[data-key="compress_batch_mode"]') return modeEl;
    if (sel === "#configForm .batch-mode-hint") return modeLabel.inserted;
    const m = sel.match(/\[data-field="(.+)"\]/);
    if (m) return fields[m[1]] || (fields[m[1]] = make());
    return null;
  },
  createElement: () => make(),
};
const applyBatchMode = new Function(code + "\n; return applyBatchMode;")();

const show = () => ["compress_rounds", "threshold", "batch_size"]
  .map((k) => k + "=" + (fields[k] && fields[k].hidden ? "收起" : "显示")).join("  ");

modeEl.value = "rounds"; applyBatchMode();
console.log("模式=rounds →", show(), "| 提示:", String(modeLabel.inserted && modeLabel.inserted.textContent).slice(0, 22));
const ok1 = fields.compress_rounds.hidden === false && fields.threshold.hidden === true && fields.batch_size.hidden === true;

modeEl.value = "records"; applyBatchMode();
console.log("模式=records →", show());
const ok2 = fields.compress_rounds.hidden === true && fields.threshold.hidden === false && fields.batch_size.hidden === false;

console.log();
console.log(ok1 && ok2 ? "互斥显示正确 ✓（切到哪个模式就只留那套参数 ✓）" : "不通过 ✗");
process.exit(ok1 && ok2 ? 0 : 1);
