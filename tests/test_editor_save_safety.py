"""编辑器保存路径的空值安全（★ 用户实测 2026-10-11）

用户现象：编辑事实点保存 ⇒ **保存其实成功了**，但右下角报
    Cannot read properties of null (reading 'row')
根因（本次实测定位）：
    `saveEdit()` 里先 `current = null;` 再读 `if (current.row && current.row.cold)` ✗
    ⇒ 必抛 TypeError ⇒ **保存后的全部收尾被炸掉**：
      成功提示看不到、`poll()` 不刷新、`loadArchives()/loadFacts()` 不刷新 ✗
    （用户因此以为"出错了"，其实数据已落库 ✓）

本测试把修法与同类隐患一起钉住 ✓
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def _save_edit_body() -> str:
    i = JS.index("async function saveEdit(")
    j = JS.index("\n}\n", i)                 # 顶层函数结束
    return JS[i:j]


def _code_only(src: str) -> str:
    """只留**代码行**：注释里会写明旧写法（便于后人理解）✓ 不该被断言误伤 ✗"""
    out = []
    for line in src.split("\n"):
        s = line.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(line)
    return "\n".join(out)


def test_row_is_captured_before_current_is_cleared():
    """★ 必须先留一份再清 current；清空之后不得再读 current.row ✓"""
    body = _code_only(_save_edit_body())
    assert "const savedRow = current.row;" in body, "没有先留一份 ⇒ 会重演 reading 'row' of null ✗"
    clear = body.index("current = null;")
    keep = body.index("const savedRow = current.row;")
    assert keep < clear, "留一份必须在清空**之前** ✗"
    after = _code_only(body[clear:])
    assert "current.row" not in after, "清空 current 之后还在读 current.row ⇒ 必崩 ✗"
    assert "savedRow" in after, "清空后应该用 savedRow ✓"


def test_forget_button_is_guarded():
    """同类隐患：编辑器已关（current=null）时点"不再过滤"会崩 ✗
    且**不能**把 undefined 当 false ⇒ 那会误改成"不过滤" ✗ ⇒ 应当直接不做事 ✓"""
    i = JS.index('$("#forget").onclick')
    body = JS[i:i + 320]
    assert "if (!current || !current.row) return;" in body, "forget 按钮没做空值守卫 ✗"
    assert "!current.row.active" in body, "守卫后仍要按原语义取反 ✓"


def test_boot_path_does_not_deref_api_result():
    """启动路径上的 /models：取不到也不该把面板整体炸掉 ✓"""
    assert '(await api("/models"))?.models || []' in JS, "启动路径要可选链 + 回落 ✓"


def test_chain_paint_guards_missing_nodes():
    """我的链控件：paint() 时结构可能已被重渲染 ⇒ 先守卫再写 ✓"""
    i = JS.index("const paint = () => {")
    body = JS[i:i + 420]
    assert "if (!boxTx || !cntEl) return;" in body, "链控件 paint 没守卫节点 ✗"
