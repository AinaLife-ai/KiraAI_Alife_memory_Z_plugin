"""Strict validation with bounded, content-free repair feedback."""

import json
from pydantic import ValidationError

class OutputRejected(ValueError):
    def __init__(self, diagnostic):
        super().__init__("structured_output_rejected")
        self.diagnostic = diagnostic

# Static hints only: they never echo model-generated content, but they do tell
# the model what the contract expects, so a retry has a real chance to succeed.
ENUM_HINTS = {
    "category": "只能是 event/fact/preference/commitment/relationship/profile/resource/self",
    "action": "只能是 keep/correct/merge",
}
NEST_HINTS = {
    "predicate": "关系必须写在 relations 数组内：relations:[{subject,predicate,object}]",
    "object": "关系必须写在 relations 数组内：relations:[{subject,predicate,object}]",
}
# Messages raised by our own validators (static text, safe to echo back).
OWN_MESSAGES = {
    "谓词没有表达具体关系；请按原文补全，无法确定时删除这条连线",
    "关系两端相同，需核对身份",
    "merged content required",
    "merge requires content",
}


def _hint(error):
    location = error.get("loc") or ()
    name = str(location[-1]) if location else ""
    kind = error.get("type", "")
    if kind == "literal_error" and name in ENUM_HINTS:
        return ENUM_HINTS[name]
    if kind == "extra_forbidden" and name in NEST_HINTS:
        return NEST_HINTS[name]
    if kind == "value_error":
        message = str((error.get("ctx") or {}).get("error", ""))
        if message in OWN_MESSAGES:
            return message
    return ""


def diagnostic(exc):
    if isinstance(exc, OutputRejected):
        # v2.18.57：已是"最终被拒"形态 ⇒ 直接透传真诊断 ✓
        # 否则 str(OutputRejected)=="structured_output_rejected" 不在 known 里 ✗
        # ⇒ 掉进兜底句 ⇒ 真因被吞 ✗ 重试提示也跟着变成空话 ✗✗
        return exc.diagnostic
    if isinstance(exc, ValidationError):
        # Never echo invalid values or model-generated extra keys.
        fields = {
            "summary",
            "facts",
            "category",
            "subject",
            "content",
            "reason",
            "scenario",
            "tags",
            "relations",
            "source_ids",
            "predicate",
            "object",
            "actions",
            "action",
            "target_id",
        }
        parts = []
        for err in exc.errors(include_input=False, include_context=True)[:5]:
            location = ".".join(
                str(p) if isinstance(p, int) or p in fields else "<field>"
                for p in err["loc"]
            )
            hint = _hint(err)
            parts.append(
                location + ": " + err["type"] + ("（" + hint + "）" if hint else "")
            )
        return "; ".join(parts)[:500]
    if isinstance(exc, json.JSONDecodeError):
        return f"JSON语法错误 line={exc.lineno} column={exc.colno}；仅返回完整JSON对象"
    known = {
        "duplicate JSON key",
        "non-finite JSON number",
        "model output too large",
        "unknown source",
        "unknown audit evidence",
        "overlapping audit actions",
        "invalid merge",
        "cross-scope or cross-category merge is forbidden",
        "unexpected_tool_call",
        "invalid audit source group",
        "mixed visibility cannot be compressed",
        "source changed during compression",
        "unknown source id",
    }
    if str(exc) in known:
        return str(exc)
    # v2.18.57：我们自己抛的 ValueError 全是**静态文本**（逐处核对过 ✓ 不含模型内容 ✓）
    # ⇒ 直接回显真因 ✓（例："unknown classification source" 不再被兜底句盖掉 ✗✗）
    # 兜底句仅留作最后手段：不是 ValueError、含换行、或过长 ✓
    message = str(exc).strip()
    if isinstance(exc, ValueError) and message and "\n" not in message and len(message) <= 200:
        return message
    return "输出不是契约要求的JSON对象或类型"

def validate_audit(candidates, output):
    by_id = {r["id"]: r for r in candidates}
    touched = set()
    for action in output["actions"]:
        target = by_id.get(action["target_id"])
        if not target or not set(action["source_ids"]) <= by_id.keys():
            raise ValueError("unknown audit evidence")
        if action["target_id"] in touched:
            raise ValueError("overlapping audit actions")
        group = {action["target_id"], *action["source_ids"]}
        if action["action"] != "merge" and group != {action["target_id"]}:
            raise ValueError("invalid audit source group")
        if action["action"] == "merge":
            if len(group) < 2 or touched & group:
                raise ValueError("invalid merge")
            if any(
                (by_id[k]["sid"], by_id[k]["subject"], by_id[k]["category"])
                != (target["sid"], target["subject"], target["category"])
                for k in group
            ):
                raise ValueError("cross-scope or cross-category merge is forbidden")
        touched.update(group)


def strip_schema_titles(schema):
    """Pydantic 给每个字段都加了 title，对模型是纯噪音，去掉省 token。"""
    if isinstance(schema, dict):
        return {
            key: strip_schema_titles(value)
            for key, value in schema.items()
            if key != "title"
        }
    if isinstance(schema, list):
        return [strip_schema_titles(item) for item in schema]
    return schema
