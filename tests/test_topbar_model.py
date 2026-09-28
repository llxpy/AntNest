# -*- coding: utf-8 -*-
"""面板头按钮去重 + 顶栏模型切换的位置不变量。

两件事都是用户从实机截图上指出来的，且都属于「结构错了但测试全绿」那一类：

1. 子任务/工蚁面板各有两个功能完全相同的「展开」按钮。多出来的那个是
   列向 flex 容器的直接子节点，被 align-items:stretch 拉满整宽，渲染成
   一个像文本框的方块。这正是 Step 0 修的那个 bug 的另一半——上次只把
   h2 里那个收进了 h2，兄弟节点那个一直留着。

2. 「选择模型」原先在输入栏里，被 .composer-bar 规则去掉边框，渲染成
   一段裸文字悬在输入框中间。模型是**会话级**设置，不是每条消息的临时
   开关（Skill 才是），位置和它的生命周期不符，已移到顶栏状态胶囊旁。
"""
import io
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PANEL_BUTTONS = (
    ("子任务", "openSubtasksModal"),
    ("工蚁", "openWorkersModal"),
    ("执行计划", "openTimelineModal"),
)


class PanelHeaderButtonTest(unittest.TestCase):
    """每个面板的「展开/轨迹」按钮恰好一个，且收在 h2 内。"""

    def setUp(self):
        self.src = (ROOT / "prototype_antnest.py").read_text(encoding="utf-8")
        self.lines = self.src.split("\n")

    def test_exactly_one_button_per_panel(self):
        for label, fn in PANEL_BUTTONS:
            with self.subTest(panel=label):
                self.assertEqual(
                    1, self.src.count(f'onclick="{fn}()"'),
                    f"{label} 面板的按钮出现多次：说明重复按钮又被加回来了",
                )

    def test_no_stray_sibling_button(self):
        """h2 之外的裸 ui.raw 按钮会被列向 flex 拉满整宽。

        必须按行判定「是否在 h2 内」——h2 那行本身也含 ui.raw('<button…')，
        只按子串搜索会把正确的那处也报成 stray（这个坑踩过一次）。
        """
        strays = []
        for n, line in enumerate(self.lines, 1):
            if "ui.h2()[" in line:
                continue
            for _label, fn in PANEL_BUTTONS:
                if "ui.raw('<button" in line and f'onclick="{fn}()"' in line:
                    strays.append(f"line {n}: {fn} -> {line.strip()[:70]}")
        self.assertEqual([], strays,
                         f"这些按钮仍是 h2 的兄弟节点，会被拉满整宽：{strays}")

    def test_button_is_inside_h2(self):
        """反向确认：按钮确实在 h2 的 [...] 里，而不是碰巧只有一处。"""
        for _label, fn in PANEL_BUTTONS:
            with self.subTest(fn=fn):
                hits = [ln for ln in self.lines
                        if "ui.h2()[" in ln and f'onclick="{fn}()"' in ln]
                self.assertEqual(1, len(hits),
                                 f"{fn} 的按钮不在 h2 内（位置错了就会退回整宽拉伸）")


class TopbarModelSwitchTest(unittest.TestCase):
    """模型切换在顶栏，不在输入栏。"""

    def setUp(self):
        self.src = (ROOT / "prototype_antnest.py").read_text(encoding="utf-8")
        self.css = (ROOT / "ui_assets" / "app.css").read_text(encoding="utf-8")

    def test_single_instance(self):
        self.assertEqual(
            1, self.src.count('id="model-select"'),
            "id=model-select 必须唯一（app.js 按它查 DOM）",
        )

    def test_lives_in_topbar_after_status_pill(self):
        topbar = self.src.index('ui.div(cls="topbar')
        model = self.src.index('id="model-select"')
        winctrl = self.src.index('class="win-ctrl"', topbar)
        self.assertTrue(topbar < model < winctrl,
                        "model-select 应在顶栏、状态胶囊之后、窗口控件之前")

    def test_removed_from_composer_bar(self):
        row = self.src.index('ui.div(cls="chat-input-row")')
        bar = self.src.index("composer-bar", row)
        end = self.src.index('ui.div(cls="spacer")', bar)
        self.assertNotIn("model-select", self.src[bar:end + 400],
                         "输入栏的 composer-bar 内仍残留 model-select")

    def test_topbar_select_is_no_drag(self):
        """顶栏是 pywebview 拖拽区。控件不在 no-drag 列表里的话，
        点击会变成拖窗口而不是展开下拉。"""
        self.assertIn(
            "no-drag", self.css.split(".topbar .topbar-model-wrap{")[1].split("}")[0],
            ".topbar-model-wrap 未设 no-drag：点击会拖窗口而非展开下拉",
        )

    def test_topbar_styles_exist(self):
        for rule in (".topbar .topbar-model-wrap", ".topbar .topbar-model"):
            self.assertIn(rule, self.css, f"app.css 缺少 {rule}")
        # 长模型名必须能截断，否则会挤掉窗口控件
        self.assertIn("text-overflow:ellipsis", self.css)

    def test_narrow_screen_keeps_dropdown_reachable(self):
        """窄屏应收窄而非隐藏——隐藏就等于没有换模型的入口了。"""
        m = re.search(r"@media \(max-width:620px\)\{(.*?)\n\s*\}", self.css, re.S)
        self.assertIsNotNone(m, "未找到窄屏媒体查询")
        block = m.group(1)
        self.assertIn("topbar-model", block, "窄屏未处理模型切换")
        self.assertNotIn("topbar-model{display:none", block.replace(" ", ""),
                         "窄屏隐藏了模型切换：换模型将无入口")


if __name__ == "__main__":
    unittest.main()
