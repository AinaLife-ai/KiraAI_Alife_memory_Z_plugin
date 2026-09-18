"""Generate the KiraAI configuration schema from the validated settings contract."""

import importlib.util
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("contracts", root / "contracts.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
schema = module.Settings.model_json_schema()
help_spec = importlib.util.spec_from_file_location(
    "setting_help", root / "setting_help.py"
)
help_module = importlib.util.module_from_spec(help_spec)
help_spec.loader.exec_module(help_module)
names = dict(
    zip(
        module.Settings.model_fields,
        [
            "启用记忆系统",
            "记录对话与感知",
            "旧历史播种",
            "注入最近原文",
            "持续上下文注入",
            "事实视图",
            "压缩分批方式",
            "每批压缩轮数",
            "窄查询自动扩词召回",
            "首层压缩阈值",
            "首层每批条数",
            "自动压缩概率",
            "最大压缩层级",
            "压缩与合并模型",
            "审计模型",
            "可选向量模型（启用后可能计费）",
            "启用向量检索（默认关闭）",
            "后台审计",
            "审计间隔（秒）",
            "每批审计事实数",
            "审计冷却（天）",
            "审计每日调用上限",
            "模型超时（秒）",
            "模型失败重试次数",
            "压缩带人设",
            "审计带人设",
            "注入形态",
            "后台并发数",
            "上下文字符预算",
            "估算 Token 警告线",
            "回忆提示词",
            "Bot可访问范围",
            "感知事实批量（×10）",
            "定时主动感知",
            "主动感知间隔（秒）",
            "主动感知随机偏移（秒）",
            "每轮最少会话数",
            "每轮最多会话数",
            "轮流挑选会话",
            "主动感知会话",
            "压缩补充要求",
            "自动安全迁移旧记忆",
            "迁移成功后互斥旧插件",
            "旧记忆迁移字符上限",
            "导入时的记忆年龄折算（天）",
            "每批压缩输入预算（字符）",
            "打开面板时播放载入动画",
            "载入动画重播冷却（秒）",
            "全局召回优先当前会话",
            "保存永久记忆前先查事实覆盖",
            "永久记忆定期整理",
            "记住后立即整理",
            "永久记忆条数上限",
            "永久记忆字符预算",
            "每次整理条数",
            "同一条整理间隔（天）",
            "跨类别重复阈值",
            "去重判定附原文证据",
            "轮换召回槽位",
            "轮换槽位条数",
            "常驻下沉阈值",
            "轮换保留轮数",
            "轮换命中阈值",
            "轮换冷却轮数",
            "每轮注入时间预算（毫秒）",
            "永久记忆跨会话去重阈值",
            "自动合并相似永久记忆",
            "检测到相似就强制合并",
            "永久记忆相似度阈值",
            "事实内容匹配门槛",
            "检索默认只搜常驻",
            "召回跳过表情/图片-only 消息",
            "归档转入冷归档天数",
            "每批压缩的原文上限（字符）",
            "陈旧记忆几天后开始消化",
            "会话闲置几小时后收尾",
            "单个压缩任务最多连压几批",
            "收尾压缩的冷却（分钟）",
            "写入时自动合并事实",
            "事实合并触发阈值",
            "事实合并字数（提示词）",
            "事实合并字数（硬上限）",
            "事实合并理由（提示词）",
            "事实合并理由（硬上限）",
            "事实合并每批簇数",
            "事实合并提示词",
            "跨会话合并（身份类）",
            "合并前不参与检索",
            "永久记忆合并字数（提示词）",
            "永久记忆合并字数（硬上限）",
            "永久记忆合并理由（提示词）",
            "永久记忆合并理由（硬上限）",
            "永久记忆合并提示词",
            "画像摘要条数",
        ],
    )
)
_missing = [k for k in schema["properties"] if k not in names]
fields = {}
for key, p in schema["properties"].items():
    kind = {
        "boolean": "switch",
        "number": "float",
        "integer": "integer",
        "array": "list",
        "string": "string",
    }[p["type"]]
    field = {
        "type": kind,
        # v2.18.19：**缺标签时不再崩** ✗ 而是用键名兜底并**报警** ✓
        # 原来 `names[key]` 直接 KeyError ✗ ⇒ 生成器整个跑不通 ✓
        # ⇒ 任何新增配置都只能手改 schema.json ✓（这正是之前 4 个新配置漏进去的根因 ✓）
        "name": names.get(key) or ("⚠️缺标签:" + key),
        "default": module.Settings().model_dump()[key],
        "description": help_module.HELP[key],
    }
    for constraint in ("minimum", "maximum"):
        if constraint in p:
            field[constraint] = p[constraint]
    if p["type"] == "array":
        field["item_type"] = "string"
    if "enum" in p:
        field["options"] = p["enum"]
    if key.endswith("_model"):
        field["type"] = "model_select"
        field["model_type"] = "embedding" if key == "embedding_model" else "llm"
    fields[key] = field
(root / "schema.json").write_text(
    json.dumps(
        {
            "alife": {
                "type": "section",
                "name": "Alife 完整记忆移植",
                "collapsed": False,
                "fields": fields,
            }
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

if _missing:
    print("⚠️ 以下配置缺中文标签（已用占位符 ✓ 请补进 names 表）：")
    for _k in _missing:
        print("   -", _k)
