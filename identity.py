"""Canonical identity resolution for migrated memories (AGPL-3.0).

Older KiraOS data was imported under synthetic buckets such as
``legacy:user:123`` or ``legacy:group:qq:456``. Those buckets are not real
entities: they describe an account or a chat that already exists (or will
exist) under the live ``adapter:number`` identifier. This module maps every
synthetic identifier onto the live one so the same number merges instead of
staying a separate "old archive".

Nothing here talks to a database or an adapter; callers pass the entities and
adapter names they know about, which keeps the rules unit-testable.
"""

from __future__ import annotations

from urllib.parse import unquote

LEGACY = "legacy:"
PENDING = "unresolved:"
GLOBAL = "global"
SELF = "self"
UNSCOPED = "unscoped"

GLOBAL_ID = LEGACY + "global"
SELF_ID = LEGACY + "self"
UNSCOPED_ID = LEGACY + "unscoped"

_SESSION_TYPES = {"dm", "gm", "pm"}


def split_adapter(entity_id):
    """Return ``(adapter, number, session_type)`` for ``adapter[:dm|gm]:number``."""
    parts = (entity_id or "").split(":")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1], ""
    if len(parts) == 3 and all(parts) and parts[1] in _SESSION_TYPES:
        return parts[0], parts[2], "dm" if parts[1] == "pm" else parts[1]
    return "", entity_id or "", ""


def _shape(prefix, entity_id):
    if not entity_id or not entity_id.startswith(prefix):
        return None
    kind, _, rest = entity_id[len(prefix) :].partition(":")
    if kind not in ("user", "group") or not rest:
        return None
    if ":" in rest:
        adapter, _, number = rest.partition(":")
    else:
        adapter, number = "", rest
    if not number or any(c.isspace() or c == ":" for c in number):
        return None
    return kind, adapter, number


def legacy_shape(entity_id):
    """Describe a synthetic identifier, or ``None`` when it is already stable."""
    if entity_id in (GLOBAL_ID, SELF_ID, UNSCOPED_ID):
        return entity_id[len(LEGACY) :], "", ""
    return _shape(LEGACY, entity_id)


def pending_shape(entity_id):
    """Describe an ``unresolved:user:123`` placeholder, or ``None``."""
    return _shape(PENDING, entity_id)


def synthetic(entity_id):
    return bool(entity_id) and (
        entity_id.startswith(LEGACY) or entity_id.startswith(PENDING)
    )


def source_shape(source_key):
    """Parse a migration source key such as ``entities/user_qq:1/facts.toml#a``."""
    path = (source_key or "").split("#", 1)[0]
    parts = path.split("/")
    if parts and parts[0] == "global":
        return ("self", "", "") if "self" in parts else ("global", "", "")
    if len(parts) > 1 and parts[0] == "entities":
        kind, _, entity = parts[1].partition("_")
        entity = unquote(entity)
        if kind not in ("user", "group") or not entity:
            return None
        if ":" in entity:
            adapter, _, number = entity.partition(":")
        else:
            adapter, number = "", entity
        if not number:
            return None
        return kind, adapter, number
    return None


class Resolver:
    """Resolve synthetic identifiers against known entities and live adapters."""

    def __init__(self, entities=(), adapters=()):
        self.adapters = [a for a in dict.fromkeys(adapters) if a]
        self.users = {}
        self.groups = {}
        for item in entities:
            entity_id, kind = item if isinstance(item, (tuple, list)) else (item, "")
            adapter, number, session = split_adapter(entity_id)
            if not adapter or not number:
                continue
            if kind == "user" and not session:
                self.users.setdefault(number, set()).add(entity_id)
            elif kind == "session" and session == "gm":
                self.groups.setdefault(number, set()).add(entity_id)

    def _pick(self, adapter, number, pool, suffix):
        candidates = pool.get(number, set())
        explicit = f"{adapter}{suffix}{number}" if adapter else ""
        if explicit and explicit in candidates:
            return explicit
        if len(candidates) == 1:
            return next(iter(candidates))
        if adapter and adapter in self.adapters:
            return explicit
        if len(self.adapters) == 1:
            return f"{self.adapters[0]}{suffix}{number}"
        return ""

    def user(self, adapter, number):
        target = self._pick(adapter, number, self.users, ":")
        if not target:
            return f"{PENDING}user:{number}", UNSCOPED, "user"
        return target, f"{target.split(':', 1)[0]}:dm:{number}", "user"

    def group(self, adapter, number):
        target = self._pick(adapter, number, self.groups, ":gm:")
        if not target:
            return f"{PENDING}group:{number}", UNSCOPED, "session"
        return target, target, "session"

    def resolve(self, entity_id):
        """Return ``(entity_id, session_sid, kind)``; unchanged ids pass through."""
        shape = legacy_shape(entity_id)
        if shape is None:
            shape = pending_shape(entity_id)
        if shape is None:
            return entity_id, "", ""
        kind, adapter, number = shape
        if kind == "global":
            return GLOBAL, GLOBAL, "global"
        if kind == "self":
            return SELF, SELF, "self"
        if kind == "unscoped":
            return UNSCOPED, UNSCOPED, "session"
        if kind == "user":
            return self.user(adapter, number)
        return self.group(adapter, number)

    def canonical_location(self, sid, visibility, subject, users):
        """Map one migration location onto live identifiers."""
        hint = ""
        if sid and not synthetic(sid) and sid != UNSCOPED:
            adapter, _number, session = split_adapter(sid)
            if adapter and session:
                hint = adapter
        shape = legacy_shape(subject)
        if shape is None:
            shape = pending_shape(subject)
        if shape and shape[0] == "user" and not shape[1] and hint:
            entity, entity_sid, kind = self.user(hint, shape[2])
        elif shape:
            entity, entity_sid, kind = self.resolve(subject)
        else:
            # The subject is already stable; only the archive bucket may be synthetic.
            bucket, bucket_sid, bucket_kind = self.resolve(sid)
            if bucket_kind == "":
                return sid, visibility, subject, list(users)
            if bucket_kind == "user":
                names = list(dict.fromkeys([*users, subject]))
                return bucket_sid or sid, visibility, subject, names
            return bucket_sid, visibility, subject, list(users)
        if kind == "global":
            return GLOBAL, "global", GLOBAL, []
        if kind == "self":
            return SELF, "global", SELF, []
        if kind == "unscoped":
            return UNSCOPED, visibility, UNSCOPED, list(users)
        if kind == "user":
            names = list(dict.fromkeys([*users, entity]))
            session = sid
            if entity_sid and entity_sid != UNSCOPED:
                # Keep the archive on the same account's private session.
                session = entity_sid
            return session, visibility, entity, names
        if kind == "session":
            return entity_sid, visibility, entity, list(users)
        return sid, visibility, subject, list(users)

# ── 身份绑定（2026-09-18 批次 3）──────────────────────────────
#  目标：把同一个人在数据里的**多种写法**归到同一个**规范键** ✓
#  原则（用户拍板）：**宁可少绑，不可绑错** ✓
#   · 确定性判据（结构）优先 ✓ 名字类猜测只允许"同现 + 唯一" ✓
#   · 群号 ≠ 人号 ✓（`qq:gm:<号>` 绝不当人 ✓）
SELF_ALIASES = ("我", "自己", "本机", "机器人", "bot")
SELF_KEYS = ("self", "legacy:self")


def normalize_structured(key, self_id="", legacy_adapter="qq"):
    """**结构化归一** ✓ —— 确定性判据，不需要任何"猜" ✓ 判不了返回 "" ✓

    · ``qq:769690776``          ⇒ 它自己（人是 2 段：`adapter:number` ✓）
    · ``qq:dm:769690776``       ⇒ ``qq:769690776``（私聊会话 ⇒ **它的归属人** ✓）
    · ``qq:gm:427674145``       ⇒ **原样返回** ✗（那是**群** ✓ 群号与人号恰好相同也不许合 ✓）
    · ``legacy:self``/``self``  ⇒ ``self_id or "self"``（自身 ✓）
    · ``legacy:user:<数字>``    ⇒ ``<legacy_adapter>:<数字>`` ✓（**只有能确定适配器时才归** ✓
      调用方不确定就传空字符串 ⇒ 原样返回 ✓ 保守 ✓）
    · 其它（``unresolved:`` / 名字 / 会话内码 `u-3` ✓）⇒ "" ✓（交给上层：绑定表 或 原样 ✓）
    """
    raw = str(key or "").strip()
    if not raw:
        return ""
    if raw in SELF_KEYS:
        return self_id or "self"
    shape = legacy_shape(raw)
    if shape:
        kind, _, number = shape
        if kind == "self":
            return self_id or "self"
        if kind == "user" and number and legacy_adapter:
            return "%s:%s" % (legacy_adapter, number) if number.isdigit() else ""
        return ""
    adapter, number, session_type = split_adapter(raw)
    if not adapter or not number or not number.isdigit():
        return ""
    if session_type == "dm":
        return "%s:%s" % (adapter, number)      # 私聊会话 ⇒ 归属人 ✓
    if session_type:
        return ""                                # gm 等 ⇒ **群/别的对象** ✗ 不许当人 ✓
    return "%s:%s" % (adapter, number)          # 人 ✓ 规范键就是它自己 ✓


def canonical_key(key, links=None, self_id="", legacy_adapter="qq"):
    """把任意"主体写法"解析成规范键 ✓（解析不了就**原样返回** = 未绑定 ✓）

    顺序（越靠前越确定 ✓）：
      ① 绑定表（含人工 ✓ 最高优先）
      ② 结构化归一 ✓（`qq:` / `dm:` / `legacy:` / `self` ✓ 确定性 ✓）
      ③ 自身别名 ✓（我/自己/bot… **且**绑定表里没有它的其它归属 ✓）
      ④ 原样 ✓
    ⚠️ 纯函数 ✓：`links` 由调用方一次查好传进来 ✓（便于测试与缓存 ✓）
    """
    raw = str(key or "").strip()
    if not raw:
        return ""
    link = (links or {}).get(raw)
    if link:
        return str(link)
    structured = normalize_structured(raw, self_id=self_id, legacy_adapter=legacy_adapter)
    if structured:
        return structured
    if raw.lower() in SELF_ALIASES:
        return self_id or "self"               # 没有别的归属 ⇒ 归自身 ✓（可撤销 ✓）
    return raw
