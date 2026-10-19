"""日志格式化小工具（**无依赖** ⇒ 可被单测直接导入 ✓）。

★ 2026-10-15（用户要求）：
  提供商一多，启动日志里"模型来源"原来会**全量打印** ⇒ 刷屏 ✗
  现在最多显示 **首 5 + 尾 5（共 10 个）**，中间 `……`，末尾 `等共 N 个` ✓
  例：`甲(2)、乙(1)、丙(3)、丁(1)、戊(2)、……、子(1)、丑(2)、寅(1)、卯(3)、辰(2)等共 31 个`
"""


def provider_log_brief(items, show=10, head=5):
    """把 `["甲(2)", "乙(1)", ...]` 压成**首+尾**摘要 ✓

    :param items: 已格式化好的条目（如 `"名字(模型数)"`）
    :param show:  最多显示多少个（默认 10 ✓）
    :param head:  从头取几个（其余从尾部补齐 ✓ 默认 5+5 ✓）
    """
    items = list(items)
    n = len(items)
    if n <= show:
        return "、".join(items)
    tail = max(0, show - head)
    parts = list(items[:head]) + ["……"]
    if tail:
        parts += list(items[-tail:])
    return "、".join(parts) + "等共 %d 个" % n
