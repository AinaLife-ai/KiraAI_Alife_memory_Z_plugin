// 前端运行时冒烟：在 node 里用最小 DOM 桩**真跑一遍 app.js**，并模拟一次 /status 响应
// 目的：抓 2.17.4 那类「语法/引用坏掉 → poll() 抛错 → 按钮全死」（node --check 抓不到的那类）
import fs from "fs";
import path from "path";
import vm from "vm";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const src = fs.readFileSync(path.join(root, "web", "app.js"), "utf8");

const created = [];
function makeEl(tag = "div") {
  const el = {
    tagName: tag, textContent: "", value: "", className: "", title: "", id: "",
    children: [], dataset: {},
    style: { setProperty() {}, removeProperty() {}, getPropertyValue: () => "" },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    appendChild(c) { el.children.push(c); return c; },
    insertBefore(c) { el.children.push(c); return c; },
    add() {}, remove: () => {}, removeChild() {}, setAttribute() {}, getAttribute: () => null,
    addEventListener() {}, removeEventListener() {}, focus() {}, click() {},
    replaceChildren() {}, cloneNode() { return makeEl(); }, contains: () => false,
    matches: () => false, animate: () => ({ finished: Promise.resolve() }),
    insertAdjacentHTML() {}, scrollTo() {}, scrollIntoView() {},
    getContext: () => null, closest: () => null,
    querySelector: () => makeEl(), querySelectorAll: () => [],
    getBoundingClientRect: () => ({ width: 0, height: 0, top: 0, left: 0 }),
  };
  created.push(el);
  return el;
}

const document = {
  body: makeEl("body"), head: makeEl("head"),
  documentElement: makeEl("html"),
  createElement: (t) => makeEl(t),
  createTextNode: (t) => ({ textContent: t }),
  querySelector: () => makeEl(),
  querySelectorAll: () => [],
  getElementById: (id) => makeEl("span"),
  addEventListener() {}, removeEventListener() {},
  readyState: "complete",
};

const statusFixture = {
  enabled: true, revision: 7, sessions: ["qq:gm:1"], sessions_total: 1,
  capacity: { levels: { "0": 42 }, years: { "2026": 42 }, db_bytes: 12_500_000, fts_rows: 42 },
  recall_usage: { total_calls: 3, total_chars: 5200, sessions: 1 },
  audit_usage: { rounds: 2, round_sessions: 1, last_round_at: 1, calls_today: 1 },
  search_index: "ready",
};

const timers = [];
const sandbox = {
  MutationObserver: class { observe() {} disconnect() {} takeRecords() { return []; } },
  Option: class { constructor(text, value) { this.text = text; this.value = value; } },
  Event: class { constructor(type) { this.type = type; } },
  CustomEvent: class { constructor(type) { this.type = type; } },
  IntersectionObserver: class { observe() {} disconnect() {} },
  ResizeObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: (fn) => { timers.push(fn); return timers.length; },
  cancelAnimationFrame() {},
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  crypto: { randomUUID: () => "00000000-0000-0000-0000-000000000000" },
  clearInterval() {}, clearTimeout() {},
  document, window: { addEventListener() {}, location: { href: "" }, matchMedia: () => ({ matches: false, addEventListener() {} }) },
  navigator: { userAgent: "node", clipboard: { writeText: async () => {} }, language: "zh-CN" },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  fetch: async () => ({ ok: true, status: 200, json: async () => statusFixture, text: async () => "" }),
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  setInterval: (fn) => { timers.push(fn); return 0; },
  console, JSON, Math, Date, Object, Array, String, Number, Boolean, Promise, Error, Map, Set, RegExp,
};
sandbox.globalThis = sandbox;

let failure = "";
try {
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "app.js" });
  // 真跑一遍定时器回调（含状态刷新 poll()）——只跑一轮，避免自排重入 ✗
  const queued = timers.splice(0, timers.length);
  for (const fn of queued.slice(0, 6)) {
    fn();
  }
  const ran = queued.length;
  if (ran > 0) console.log("JS-SMOKE-OK timers=" + ran);
  // 真跑一遍状态刷新（app.js 通常暴露 refresh/poll 或自己启动；这里把常见入口都试一遍）
  // （旧的"按名字找入口"逻辑已由上面的定时器队列取代 ✗ 避免重复声明）
} catch (e) {
  failure = String((e && e.stack) || e);
}

if (failure) {
  console.error("JS-SMOKE-FAIL " + failure.split("\n").slice(0, 12).join("\n   "));
  process.exit(1);
}
console.log("JS-SMOKE-OK loaded");
