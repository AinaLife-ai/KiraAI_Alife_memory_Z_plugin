"""JEV（TypeSafe System-One）决策层 —— **可选增强**，缺省全程不启用。

设计硬约束（与《方案 v4》一致，逐条可测）：
1. **永不抛异常**：任何失败（未配置/超时/网络/额度/返回异常）一律返回 None
   ⇒ 调用方永远走原有逻辑，功能不受影响。
2. **绝不进热路径**：只允许在 prewarm（用户打字时）或后台任务里调用，且带硬超时。
3. **影子模式**：默认只记录"JEV 会怎么判"，一个字节都不改现有行为。
4. **连接解析顺序**：显式 base_url+api_key > 用户选中的 KiraAI 模型（取其 provider_config）。

原生接口（OpenAI 格式**不支持**，实测 400，故必须走这条）：
    POST {base_url}/v1/systemone
    {"model": "...", "state": "...", "questions": {...}}
    -> {"model": "...", "answers": {...}, "usage": {"input_tokens": N, ...}}

★ 计费实测：state **只算一次**（不随问题数放大）；约 86 token/问。
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

try:  # 框架内运行时用宿主 logger；独立运行/测试环境自动降级（不硬依赖）
    from core.logging_manager import get_logger
except Exception:  # pragma: no cover
    import logging as _logging

    def get_logger(*_a, **_k):
        return _logging.getLogger("alife_memory_jev")

def _make_logger():
    """框架 logger 需要可写的日志目录；拿不到时降级到标准库（测试/独立运行）。"""
    try:
        return get_logger("alife_memory_jev", "light_purple")
    except Exception:  # pragma: no cover
        import logging

        return logging.getLogger("alife_memory_jev")


logger = _make_logger()

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"

# ── 阈值：全部来自 2026-09-24 实测（真 key / 中英各约 150 条标注样本），
#    上线后仍应在机标定；此处是**保守起点**，宁可弃权不可误判 ──
SAME_HIGH = 0.60      # ≥ 视为"同一件事"
SAME_LOW = 0.30       # ≤ 视为"明确不是同一件事"
NEW_HIGH = 0.60       # ≥ 视为"带来实质新信息"
DILUTE_HIGH = 0.70    # ≥ 视为"并入正文会稀释重点"
                      #   实测：该合并的档位稀释 0.30~0.58，真会稀释的 0.77~0.84
                      #   ⇒ 阈值取 0.70 才不误杀（0.60 会卡在贴边的 0.58 上 ✗）
TRIGGER_HIGH = 0.50   # 召回触发阈值（实测阈值 0.5 命中 8/8）
RELEVANT_HIGH = 0.50  # 候选相关性阈值（实测相关 0.75~0.98 / 干扰 0.02~0.24）

# 四级重要度 → 1~10 分（进"重要度×2"的现有公式；≥8 触发"永不沉"保护）
IMPORTANCE_LEVELS = {"核心": 9, "重要": 7, "一般": 5, "琐碎": 3}
IMPORTANCE_OPTIONS = {
    "核心": "影响健康安全或长期关系，必须永远记住",
    "重要": "稳定的个人情况或长期约定",
    "一般": "背景信息，有用但不关键",
    "琐碎": "闲聊或一次性事务，很快没用",
}

# ────────────────────────── 客户端 ──────────────────────────


class JevClient:
    """最小原生客户端：同步 urllib 跑在 to_thread 里（零新增依赖，可硬超时）。

    ★ 熔断：连续失败 3 次 → 冷却 300 秒内直接返回 None（不再占用时间）。
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 4.0):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or ""
        self.model = model or DEFAULT_MODEL
        self.timeout = float(timeout)
        self.fails = 0
        self.opened_at = 0.0
        self.tokens = 0          # 累计输入 token（面板可观测）

    # ---------- 状态 ----------
    @property
    def ready(self) -> bool:
        if not self.api_key:
            return False
        if self.fails >= 3 and (time.time() - self.opened_at) < 300:
            return False
        return True

    def _ok(self) -> None:
        self.fails = 0

    def _bad(self) -> None:
        self.fails += 1
        if self.fails == 3:
            self.opened_at = time.time()
            logger.warning("JEV 连续失败 3 次，冷却 300 秒（期间自动走原逻辑）")

    # ---------- 同步实现 ----------
    def _post_sync(self, payload: dict, timeout: float) -> Optional[dict]:
        req = urllib.request.Request(
            self.base_url + "/v1/systemone",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)

    # ---------- 对外 ----------
    async def call(self, state: str, questions: dict, timeout: Optional[float] = None) -> Optional[dict]:
        """返回 {"answers": {...}, "usage": {...}}；**任何失败都返回 None**。"""
        if not self.ready or not questions:
            return None
        payload = {"model": self.model, "state": state, "questions": questions}
        limit = float(timeout or self.timeout)
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(self._post_sync, payload, limit), timeout=limit + 0.5
            )
        except Exception as exc:  # noqa: BLE001  —— 契约：任何失败都不得外泄
            self._bad()
            logger.info(f"JEV 调用未成功，本轮走原逻辑：{type(exc).__name__}")
            return None
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            self._bad()
            return None
        self._ok()
        usage = data.get("usage") or {}
        try:
            self.tokens += int(usage.get("input_tokens") or 0)
        except (TypeError, ValueError):
            pass
        return data

# ────────────────────────── 配置与解析 ──────────────────────────


def _env(value: str) -> str:
    """支持 `$$ENV_NAME` 形式（与框架的敏感配置一致）。"""
    v = (value or "").strip()
    if v.startswith("$$"):
        import os

        return os.environ.get(v[2:], "") or ""
    return v


def split_uuid(uuid: str) -> tuple[str, str]:
    """`provider_id:model_id` → 两段（model_id 允许含冒号）。"""
    pid, _, mid = (uuid or "").partition(":")
    return pid.strip(), mid.strip()


def endpoint_of(base_url: str) -> str:
    """把提供商 base_url 规整成 systemone 端点（容忍结尾带/不带 /v1）。"""
    b = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if b.endswith("/v1"):
        b = b[:-3].rstrip("/")
    return b + "/v1/systemone"


class JevConfig:
    """不可变快照：一次事件只解析一次，避免热路径反复读配置。"""

    __slots__ = ("enabled", "shadow", "base_url", "api_key", "model", "timeout", "sample", "uuid")

    def __init__(self, enabled=False, shadow=True, base_url="", api_key="", model="",
                 timeout=4.0, sample=1.0, uuid=""):
        self.enabled = bool(enabled)
        self.shadow = bool(shadow)
        self.base_url = base_url or DEFAULT_BASE_URL
        self.api_key = api_key or ""
        self.model = model or DEFAULT_MODEL
        self.timeout = max(0.5, float(timeout or 4.0))
        self.sample = min(1.0, max(0.0, float(sample if sample is not None else 1.0)))
        self.uuid = uuid or ""

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.api_key)

    def client(self) -> Optional[JevClient]:
        return JevClient(self.base_url, self.api_key, self.model, self.timeout) if self.ready else None


def resolve_config(settings: Any, provider_mgr: Any = None) -> JevConfig:
    """解析 JEV 连接信息：显式字段优先，其次取所选 KiraAI 模型的 provider_config。"""
    get = lambda name, default="": getattr(settings, name, default) or default  # noqa: E731
    uuid = str(get("jev_model")).strip()
    base = str(get("jev_base_url")).strip()
    key = _env(str(get("jev_api_key")))
    model = str(get("jev_model_name")).strip()
    if uuid and provider_mgr is not None:
        try:
            pid, mid = split_uuid(uuid)
            info = provider_mgr.get_model_info(pid, mid)
            cfg = (getattr(info, "provider_config", None) or {}) if info else {}
            base = base or str(cfg.get("base_url") or "")
            key = key or _env(str(cfg.get("api_key") or ""))
            model = model or mid
        except Exception:  # noqa: BLE001 —— 解析失败不得影响插件
            pass
    try:
        timeout = float(get("jev_timeout_ms", 4000)) / 1000.0
    except (TypeError, ValueError):
        timeout = 4.0
    try:
        sample = float(get("jev_sample", 1.0))
    except (TypeError, ValueError):
        sample = 1.0
    return JevConfig(
        enabled=bool(get("jev_enabled", False)),
        shadow=bool(get("jev_shadow", True)),
        base_url=base, api_key=key, model=model,
        timeout=timeout, sample=sample, uuid=uuid,
    )

# ────────────────── 问题构造（★两个实测踩过的坑，必须遵守）──────────────────
#   坑1：候选/事实必须**自包含**放进 instructions —— 非生成模型没有指代消解，
#        问"上面那条"必错（实测同一任务从 AUC 1.00 退化成一坨）。
#   坑2：choice 的 criteria 必须**扁平** {选项名: 描述}，嵌套会直接报错。


def q_noul(text: str, true_hint: str, false_hint: str) -> dict:
    return {"type": "noul", "instructions": text, "criteria": {"true": true_hint, "false": false_hint}}


def q_choice(text: str, options: dict) -> dict:
    return {"type": "choice", "instructions": text, "criteria": dict(options)}


def parse_noul(answers: dict, key: str) -> Optional[float]:
    item = (answers or {}).get(key) or {}
    try:
        return float(item.get("noul"))
    except (TypeError, ValueError):
        return None


def parse_choice(answers: dict, key: str) -> tuple[Optional[str], float]:
    item = (answers or {}).get(key) or {}
    try:
        conf = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return (item.get("choice"), conf)


# ── 1) 召回筛选（被动）：参照物=当前上下文，逐候选问"是否直接相关" ──
def build_recall_filter(context: str, hits: list[tuple[str, str]]) -> dict:
    """上下文放 state（只计一次费），每条候选自带正文 —— 实测这样区分度才好。

    判据必须**具体到"不知道它会不会答错"**；写成"是否有用"这类笼统措辞，
    实测打分会被压成一团（0.10~0.18），区分度消失。
    """
    qs: dict = {}
    for key, text in hits:
        qs["hit_" + key] = q_noul(
            f"[候选记忆] {text} [/候选记忆]\n"
            "参考当前对话，这条候选记忆里有能让本轮回复更准确的关键信息吗？",
            "有：直接关系到现在谈的人物/偏好/约定/禁忌/事实，不知道它就可能答错或答偏",
            "没有：与现在谈的事无关，知不知道都不影响这轮回复",
        )
    return qs


# ── 2) 合并路由：三信号 → 代码组合（绝不问"该怎么办"）──
def build_merge_route(primary: str, cands: list[tuple[str, str]]) -> dict:
    qs: dict = {}
    for key, text in cands:
        pre = f"[主事实] {primary} [/主事实]\n[候选] {text} [/候选]\n"
        # ★ same 的判据必须写"可以带更多细节"，否则加了细节的同一件事会被判成不同事实
        qs["same_" + key] = q_noul(
            pre + "候选讲的是同一个事实吗（可以带更多细节）？",
            "同一个事实", "另一件不同的事",
        )
        qs["new_" + key] = q_noul(
            pre + "候选里有主事实没有的实质信息吗？",
            "有：主事实没提到的严重程度、后果、应对方式、时间或更具体的信息",
            "没有：和主事实是同一层信息，只是换了说法或更短的表达",
        )
        qs["dilute_" + key] = q_noul(
            pre + "如果把候选并进主事实的正文，会让主事实的重点变模糊吗？",
            "会稀释重点", "不会稀释",
        )
    return qs


def route_merge(same: Optional[float], new: Optional[float], dilute: Optional[float]) -> str:
    """返回 "merge" / "drop" / "keep"；信号缺失一律 keep（不猜）。

    实测校准（2026-09-24，6/6 符合预期）：
      纯重复          same 0.91 new 0.26          -> drop
      同事实+重伤信息  same 0.89 new 0.97 稀释0.46 -> merge
      同事实+无新信息  same 0.93 new 0.52          -> drop
      同事实+应用细节  same 0.80 new 0.88 稀释0.31 -> merge
      不同事实        same 0.05                    -> keep
    """
    if same is None or new is None:
        return "keep"
    if same < SAME_HIGH:
        return "keep"                      # 不是同一件事 ⇒ 本来就不该合，也不会膨胀
    if new >= NEW_HIGH and (dilute is None or dilute < DILUTE_HIGH):
        return "merge"                     # 该合的照合（减少事实数量）
    return "drop"                          # 同一件事 + 无实质新信息 ⇒ 软删进回收站


# ── 3) 重要度（写入时定级，进"重要度×2"的现有公式）──
def build_importance(facts: list[tuple[str, str]]) -> dict:
    qs: dict = {}
    for key, text in facts:
        qs["imp_" + key] = q_choice(
            f"[事实] {text} [/事实]\n这条事实对长期记忆的价值等级？", IMPORTANCE_OPTIONS
        )
    return qs


def importance_of(level: Optional[str], default: int = 5) -> int:
    return IMPORTANCE_LEVELS.get(level or "", default)


# ── 4) 召回触发（主动）：替代字面关键词（实测阈值 0.5 命中 8/8）──
def build_recall_trigger(message: str) -> dict:
    return {"trig": q_noul(
        f"[用户消息] {message} [/用户消息]\n用户在要求回忆过去的事情吗？",
        "在让我回忆以前说过/发生过的事", "不是回忆请求（闲聊、提问、办事等）",
    )}


# ── 5) 审计预筛：逐对判"矛盾/重复/过时"，可疑的才喂审计模型 ──
def build_audit_pairs(pairs: list[tuple[str, str, str]]) -> dict:
    qs: dict = {}
    for key, left, right in pairs:
        pre = f"[事实甲] {left} [/事实甲]\n[事实乙] {right} [/事实乙]\n"
        qs["bad_" + key] = q_noul(
            pre + "这两条事实互相矛盾、重复，或有一条已过时吗？",
            "存在矛盾/重复/过时，需要修正", "两条都正常，互不冲突",
        )
    return qs

# ────────────────────────── 影子记录 ──────────────────────────


class ShadowLog:
    """把"JEV 会怎么判"记成 JSONL，供离线对比；**任何异常都吞掉**。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.errors = 0

    def write(self, kind: str, data: dict) -> None:
        if self.path is None or self.errors >= 3:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"t": round(time.time(), 3), "kind": kind, **data},
                              ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001 —— 记录失败绝不能影响主流程
            self.errors += 1


# ────────────────────────── 统一入口 ──────────────────────────


class Decisions:
    """所有 JEV 决策的唯一入口。

    契约（逐条可测）：
      · 未启用 / 未配置 key / 熔断中 / 调用失败 ⇒ 每个方法都返回 None
      · 返回 None ⇒ 调用方**必须**走原有逻辑（功能与今天完全一致）
      · `shadow=True`（默认）⇒ 调用方只记录、不应用
    """

    def __init__(self, settings: Any = None, provider_mgr: Any = None,
                 shadow_path: Optional[Path] = None):
        self.cfg = resolve_config(settings, provider_mgr) if settings is not None else JevConfig()
        self.shadow = self.cfg.shadow
        self._client = self.cfg.client()
        self.log = ShadowLog(shadow_path)
        self.calls = 0
        self.skipped = 0

    # ---------- 状态 ----------
    @property
    def ready(self) -> bool:
        return bool(self._client and self._client.ready)

    @property
    def tokens(self) -> int:
        return int(getattr(self._client, "tokens", 0) or 0)

    def _maybe(self, sample: float) -> bool:
        """按采样率决定是否真的发起调用（影子模式下用来控制成本）。"""
        if not self.ready:
            self.skipped += 1
            return False
        if sample >= 1.0:
            return True
        import random

        if random.random() >= sample:
            self.skipped += 1
            return False
        return True

    async def _ask(self, state: str, questions: dict, kind: str,
                   timeout: Optional[float] = None) -> Optional[dict]:
        if not self._maybe(self.cfg.sample):
            return None
        data = await self._client.call(state, questions, timeout=timeout)
        self.calls += 1
        if data is None:
            return None
        return data

    # ---------- 1) 召回筛选 ----------
    async def recall_filter(self, context: str, hits: list[tuple[str, str]],
                            timeout: Optional[float] = None) -> Optional[list[tuple[str, float]]]:
        """返回 [(key, 相关度)] 按分降序；失败返回 None（调用方用原排序）。"""
        if not hits:
            return None
        data = await self._ask(context or "lang: zh", build_recall_filter(context, hits),
                             "recall", timeout)
        if not data:
            return None
        answers = data.get("answers") or {}
        scored = []
        for key, _text in hits:
            score = parse_noul(answers, "hit_" + key)
            if score is not None:
                scored.append((key, score))
        if len(scored) != len(hits):
            return None                       # 有任何一条没解析出来 ⇒ 整体弃权
        scored.sort(key=lambda kv: -kv[1])
        return scored

    # ---------- 2) 合并路由 ----------
    async def merge_route(self, primary: str, cands: list[tuple[str, str]],
                          timeout: Optional[float] = None) -> Optional[dict[str, str]]:
        """返回 {候选key: "merge"|"drop"|"keep"}；失败返回 None（走原合并逻辑）。"""
        if not cands:
            return None
        data = await self._ask("lang: zh", build_merge_route(primary, cands), "merge", timeout)
        if not data:
            return None
        answers = data.get("answers") or {}
        out: dict[str, str] = {}
        for key, _text in cands:
            same = parse_noul(answers, "same_" + key)
            new = parse_noul(answers, "new_" + key)
            dilute = parse_noul(answers, "dilute_" + key)
            out[key] = route_merge(same, new, dilute)
        return out

    # ---------- 3) 重要度 ----------
    async def importance(self, facts: list[tuple[str, str]],
                         timeout: Optional[float] = None) -> Optional[dict[str, int]]:
        if not facts:
            return None
        data = await self._ask("lang: zh", build_importance(facts), "importance", timeout)
        if not data:
            return None
        answers = data.get("answers") or {}
        out: dict[str, int] = {}
        for key, _text in facts:
            level, conf = parse_choice(answers, "imp_" + key)
            if not level or conf < 0.5:
                continue                      # 置信不足 ⇒ 不写，保留原值
            out[key] = importance_of(level)
        return out or None

    # ---------- 4) 召回触发 ----------
    async def recall_trigger(self, message: str, timeout: Optional[float] = None) -> Optional[bool]:
        if not message.strip():
            return None
        data = await self._ask("lang: zh", build_recall_trigger(message), "trigger", timeout)
        if not data:
            return None
        score = parse_noul(data.get("answers") or {}, "trig")
        return None if score is None else (score >= TRIGGER_HIGH)

    # ---------- 5) 审计预筛 ----------
    async def audit_prescreen(self, pairs: list[tuple[str, str, str]],
                              timeout: Optional[float] = None) -> Optional[list[str]]:
        """返回可疑对的 key 列表（空列表=整批干净，可跳过审计模型）。"""
        if not pairs:
            return None
        data = await self._ask("lang: zh", build_audit_pairs(pairs), "audit", timeout)
        if not data:
            return None
        answers = data.get("answers") or {}
        hot = []
        for key, _l, _r in pairs:
            score = parse_noul(answers, "bad_" + key)
            if score is not None and score >= 0.5:
                hot.append(key)
        return hot
