"""前端静态一致性：id 引用不能悬空，选择器不能互相覆盖。

这类问题在浏览器里才暴露（而且往往要点到某个按钮才发作），所以用静态检查兜住。
"""

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
# 运行期由 JS 动态创建的元素，本来就不在 index.html 里
DYNAMIC_IDS = {"graphClear", "newSid"}


def test_every_referenced_id_exists():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
    missing = sorted(used - ids - DYNAMIC_IDS)
    assert missing == [], "JS 引用了 index.html 里不存在的 id: %s" % missing


def test_job_detail_button_does_not_reuse_manual_job_attribute():
    """「明细」按钮与手动排队按钮必须用不同属性。

    两者都用 data-job 时，bindJobButtons() 会把「压缩存档 / 审计与合并 / 合并相似永久记忆」
    这些手动按钮的点击覆盖成「打开明细」，于是请求 /job/audit → 404。
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'data-jobdetail="' in js, "任务卡片明细按钮应使用 data-jobdetail"
    assert '$$("[data-jobdetail]").forEach' in js, "明细绑定应只选取 data-jobdetail"
    assert js.count('$$("[data-job]")') == 1, "data-job 只应由手动排队按钮使用一次"


def test_job_detail_handler_reads_jobdetail_attribute():
    """明细处理器必须读 dataset.jobdetail。

    v2.5.8 把按钮属性改名成 data-jobdetail 后，处理器仍读 dataset.job（= undefined），
    请求打到 /job/undefined → 404「操作失败，请检查连接或登录状态」。
    属性名一改两处必须同时改，所以在这里钉死。
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    binding = js.split('$$("[data-jobdetail]")', 1)[1][:260]
    assert "dataset.jobdetail" in binding, "明细处理器应读 dataset.jobdetail"
    assert "dataset.job;" not in binding and "dataset.job)" not in binding, (
        "明细处理器不应读 dataset.job（那是手动排队按钮的属性）"
    )


def _web_audit():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "web_audit.py"
    spec = importlib.util.spec_from_file_location("web_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dataset_attributes_and_routes_are_consistent():
    """属性/接口错位的全量静态检查（详见 tools/web_audit.py）。

    - dataset 读了没人写的属性 → 请求里会拼出 undefined
    - 绑定 [data-x] 却读 dataset.y → 点击发到错误路径（v2.5.8 明细 404 就是这类）
    - 前端调用的接口在 main.py 里没有对应路由 / 方法
    """
    result = _web_audit().audit()
    assert result["missing_writer"] == [], result["missing_writer"]
    assert result["binding_mismatch"] == [], result["binding_mismatch"]
    assert result["route_mismatch"] == [], result["route_mismatch"]


def test_style_braces_balanced():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert css.count("{") == css.count("}"), "style.css 大括号不配对"


def test_job_kind_labels_cover_every_queued_kind():
    """任务名映射必须覆盖后端所有会排队的 kind，且全站只有一份。

    踩过的坑：tasksHtml() 里另有一份只有 6 项的内联映射，
    于是 fact_merge / tidy 在任务卡片里显示英文（明细弹窗却是中文）。
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    engine = (Path(__file__).resolve().parents[1] / "engine.py").read_text(encoding="utf-8")
    main = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    kinds = set(re.findall(r'enqueue\("([a-z_]+)"', engine + main))
    assert kinds, "没找到后端排队的任务类型"

    block = js[js.index("const JOB_KINDS = {") : js.index("};", js.index("const JOB_KINDS = {"))]
    labeled = set(re.findall(r"^\s*([a-z_]+):", block, re.M))
    missing = kinds - labeled
    assert not missing, f"前端任务名缺映射：{sorted(missing)}"

    # 只允许一份 kind→中文 映射：按「键: "标签"」的形式数，避免误伤配置帮助文案
    for entry in ('compress: "分层压缩"', 'audit: "事实审计"', 'fact_merge: "事实合并"'):
        assert js.count(entry) == 1, f"重复的任务名映射：{entry} 出现 {js.count(entry)} 次"


def test_manual_job_buttons_match_backend_contract():
    """工作台每个手动按钮的 kind，后端契约都必须接受（否则点了就是 422）。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    buttons = set(re.findall(r'data-job="([a-z_]+)"', html))
    assert buttons, "没找到手动排队按钮"

    import importlib
    import types
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    package = types.ModuleType("alife_btn_test")
    package.__path__ = [str(root)]
    sys.modules.setdefault("alife_btn_test", package)
    contracts = importlib.import_module("alife_btn_test.contracts")
    allowed = set(contracts.Job.model_fields["kind"].annotation.__args__)
    missing = buttons - allowed
    assert not missing, f"按钮 kind 未被后端接受：{sorted(missing)}"
    # 「整理永久记忆」必须真的在
    assert "tidy" in buttons, "工作台缺少「整理永久记忆」按钮"


def test_asset_change_uses_versioned_reload():
    """插件更新后要带版本参数跳转，否则 WebView 会拿缓存，用户看不到新功能。"""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "location.replace(url)" in js or "location.replace(" in js, "更新后应带版本跳转"
    assert "?v=" in js and "next.assets" in js, "跳转要带上资源指纹"
    # 旧的裸 reload 在资源变化分支里必须已经不存在
    segment = js[js.index("asset && asset !== next.assets") :]
    segment = segment[: segment.index("return;")]
    assert "location.reload();" not in segment, "不要再用裸 reload（会吃缓存）"


def test_status_reports_version_for_self_check():
    """界面要能显示当前插件版本，便于用户自查是不是旧版。"""
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert '_plugin_version' in source and 'status["version"]' in source
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "next.version" in js, "前端要显示版本号"


def test_capacity_meter_is_rendered_from_status():
    """容量仪表必须真被前端消费（后端写了字段、界面不显示 = 白做 ✓）且必须在 poll() 内用返回值。"""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "next.capacity" in js and "capTag" in js
    assert "db_bytes" in js and "fts_rows" in js and "levels" in js
    poll = js[js.index("async function poll()"):]
    assert "next.capacity" in poll[:3000], "容量渲染必须在 poll() 内（否则 next 未定义 ✗）"

    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="capTag"' in html, "标签必须写在 HTML 里 ✗ 运行时插入会被重渲染冲掉（真实事故）"
    assert 'const capEl = $("#capTag")' in js, "必须直接更新 HTML 里已有的标签 ✗（别再运行时插）"
    main_src = (WEB.parent / "main.py").read_text(encoding="utf-8")
    assert 'status["capacity"]' in main_src, "后端必须真的提供 /status.capacity"


def test_no_shell_expansion_damage():
    """抓「补丁被 shell 吃掉」的痕迹：`$(...)` 被展开后会留下 `= ;`、`$(` 消失等 ✗

    这类损伤是**运行时**错误（按钮全死、数据不刷新），括号平衡测试查不出来 —— 必须单独钉住。
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "= ;" not in js, "app.js 出现空赋值：补丁疑似被 shell 展开吃掉 ✗"
    assert 'typeof $ ===' in js or "const $ = " in js or "function $((" in js or "$(" in js, "选择器函数疑似缺失"
    assert '$("#capTag")' in js, "状态区选择器必须完整（曾被 shell 展开吃掉成 `= ;` ✗）"


def test_recall_usage_is_rendered():
    """第 6 项前端：召回用量必须被真的消费（后端写了字段、界面不显示 = 白做）

    且必须在 poll() 内使用返回值（写在函数外会导致 next 未定义 → 按钮全死 ✗，2.17.4 的教训）
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "next.recall_usage" in js and "recallTag" in js
    assert "total_calls" in js and "total_chars" in js
    poll = js[js.index("async function poll()"):]
    assert "next.recall_usage" in poll[:3000], "用量渲染必须在 poll() 内"
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="recallTag"' in html, "标签必须写在 HTML 里 ✗"
    assert 'const recallEl = $("#recallTag")' in js, "必须直接更新 HTML 里已有的标签 ✗"
    main_src = (WEB.parent / "main.py").read_text(encoding="utf-8")
    assert 'status["recall_usage"]' in main_src, "后端必须真的提供该字段"


def test_config_conflict_ui():
    """版本冲突（409）必须给出**说得清 + 走得出**的提示 ✗

    旧文案让人"重新打开最新版本后再保存" ✓ 但刷新会带回旧版本号 ✗
    → 再点保存**仍然冲突** ✓ 是一句错误指引 ✓（用户只会更懵）
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    # 不再出现那句走不通的指引 ✗
    assert "请重新打开最新版本后再保存" not in js, "旧的错误指引必须删掉 ✗"
    assert "err.status = 409" in js, "409 要带上状态码，供上层分辨 ✓"
    # 三句话要说清：发生了什么 / 你的输入还在 / 两条出路 ✓
    assert "设置已在别处被改过" in js
    assert "你的输入没有丢" in js
    # 常驻条与两个按钮（写在 HTML 里 ✓ 可见即可操作 ✓）
    for el in ('id="conflict"', 'id="conflictOverwrite"', 'id="conflictDiscard"'):
        assert el in html, el
    assert '$("#conflictOverwrite").onclick' in js
    assert '$("#conflictDiscard").onclick' in js
    # 两条出路都要**真的能用** ✓
    assert "config.revision = fresh.revision" in js, "覆盖=借最新版本号，不能直接放弃 ✓"
    assert "await loadConfig()" in js, "丢弃=重新载入并渲染 ✓"


def test_every_tab_has_a_title_entry():
    """每个页签都必须在 `titles` 表里有标题 ✓（2026-09-18 用户实测 ✗）

    现象：切到「事实体检」时报 `Cannot read properties of undefined (reading '0')`
    根因：加了页签按钮（`data-tab="health"`）却没在 `selectTab` 的 `titles` 表里加条目 ✗
          ⇒ `titles[name][0]` 读到 undefined 的索引 ✗
    ⇒ 这条守卫把"页签 ↔ 标题表"钉死 ✓（web_audit 当初没覆盖这个面 ✗）
    """
    root = Path(__file__).resolve().parent.parent
    html = (root / "web/index.html").read_text(encoding="utf-8")
    js = (root / "web/app.js").read_text(encoding="utf-8")
    tabs = sorted(set(re.findall(r'data-tab="([a-z_]+)"', html)))
    assert tabs, "没找到任何页签 ✗"
    block = js[js.find("const titles = {"):]
    block = block[:block.find("};")]
    for tab in tabs:
        assert re.search(r'\b%s:\s*\["' % tab, block), (
            "页签 %s 没有 titles 条目 ✗ ⇒ 一点它就报 reading '0' ✓" % tab)


# ★ 2026-09-18：**调用了没定义的函数**这类 bug 必须被静态挡住 ✓
#   实测事故：`loadHealth()` 被调用 3 处，函数从来没定义 ✗
#   ⇒ 体检页永远"正在加载…" ✓ 后来我误加调用 ⇒ 页面直接报 "loadHealth is not defined" ✗
def _undefined_called_functions(src):
    """返回被调用但**没有定义**的 load*/render*/open* 函数名 ✓"""
    defined = set(re.findall(r"(?:function|const|let|var)\s+((?:load|render|open)[A-Z]\w*)", src))
    called = set(re.findall(r"\b((?:load|render|open)[A-Z]\w*)\s*\(", src))
    return sorted(called - defined)


def test_every_called_loader_is_defined():
    src = (WEB / "app.js").read_text(encoding="utf-8")
    missing = _undefined_called_functions(src)
    assert missing == [], "这些函数被调用了但没有定义：%s" % missing


def test_the_guard_can_actually_fail():
    """反向自检 ✓：判据在"确实缺定义"时必须报红（否则它是摆设 ✗）"""
    bad = "async function loadFacts() {} loadHealth(); loadFacts();"
    assert _undefined_called_functions(bad) == ["loadHealth"]
    good = "async function loadFacts() {} async function loadHealth() {} loadHealth();"
    assert _undefined_called_functions(good) == []


def test_history_toggle_defaults_to_checked_and_hides_history_cards():
    """v2.18.64：「显示历史存档」开关（默认勾选，负责隐藏历史存档 / 冷归档卡片）"""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id="includeHistory" checked' in js, "开关必须默认勾选 ✓"
    assert "显示历史存档" in js, "标签文案应为「显示历史存档」✓"
    assert 'document.querySelector("#includeHistory")' in js, "要读这个开关的状态 ✓"
    assert "include_history:" in js, "状态要传给后端（后端按它过滤，分页计数才准）✓"
    tail = js.split("function ensureHistoryToggle")[1][:420]
    assert 'document.querySelector("#includeTools")' in tail, (
        "新开关要插在「显示工具步」之后（用户看到的顺序：工具步在左、历史存档在右）✓"
    )
    assert "ensureHistoryToggle();" in js.split("async function loadArchives()")[1][:220], (
        "loadArchives 里要调用 ensureHistoryToggle ✓"
    )


def test_trash_page_shows_preview_and_explains_tabs():
    """v2.18.65 回收站：卡片正文能显示 + 页签判据写清 + 冷归档控件只在冷归档页签"""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert "row.content || row.summary || row.preview" in js, "卡片正文要三级回退 ✗"
    assert js.count("row.content || row.summary || row.preview") >= 2, (
        "「彻底删除」确认框也要回退 ✗ 否则弹窗正文是空的"
    )
    assert "存档（已删除）" in html and "冷归档（仍生效）" in html, "页签要写清各自判据 ✗"
    assert 'id="coldTools"' in html, "冷归档四个按钮要独立容器 ✗"
    assert 'coldTools.classList.toggle("hide"' in js, "冷归档按钮只在冷归档页签显示 ✗"
    assert "冷归档 · 正文在冷库" in js and "已删除 · 可还原" in js, "状态词要统一一套 ✗"
    assert 'id="trashNote"' in html and "trashNote" in js, "每个页签要有判据说明行 ✗"
    assert "gap: 10px" in css and "flex-wrap: wrap" in css, "footer 按钮要有间距并允许换行 ✗"


def test_trash_note_has_fallback():
    """页签说明取不到值时要有兜底 ⇒ 不能把 "undefined" 显示给用户"""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert '}[trashKind] || ""' in js, "说明行必须兜底成空串"


def test_archive_browse_card_body_falls_back_to_content():
    """v2.18.65 审计追加：浏览页（记忆存档）卡片正文同样不能只靠 summary

    实测：/search 同时返回 summary 与 content ⇒ 摘要为空的记录原本卡片一片空白 ✗
    编辑弹窗**不**用 content 预填（否则保存会把原文误写成摘要 ✗）⇒ 只加 placeholder ✓
    """
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'esc(r.summary || r.content || r.preview || "")' in js, "浏览页卡片正文要回退到 content ✗"
    assert '$("#editText").placeholder' in js, "没有摘要时编辑框要给提示 ✗"
    assert '$("#editText").value = r.summary;' in js, "编辑框仍只装 summary ✗（不能拿 content 预填）"
