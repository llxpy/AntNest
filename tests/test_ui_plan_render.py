# -*- coding: utf-8 -*-
"""AntNest · Plan DAG 渲染测试

渲染函数是纯函数（无 webview 依赖），所以可以直接断言 HTML 结构。

除了结构正确，重点是**注入安全**：所有文本都过 esc()，模型产出的 title /
result_summary 里可能带尖括号、脚本标签，不能透传。
"""
import html
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui_render as R  # noqa: E402


def _plan(**kw):
    base = {"goal": "修复测试失败", "progress": {"percent": 0}}
    base.update(kw)
    return base


def _node(nid, title, status="pending", deps=None, workers=None, **kw):
    n = {
        "id": nid, "title": title, "status": status,
        "depends_on": deps or [], "worker_ids": workers or [],
        "result_summary": "", "error": "", "detail": "",
    }
    n.update(kw)
    return n


class RenderPlanTest(unittest.TestCase):
    def test_empty_plan_returns_empty(self):
        self.assertEqual(R.render_plan({}), "")
        self.assertEqual(R.render_plan({"nodes": []}), "")

    def test_non_dict_returns_empty(self):
        self.assertEqual(R.render_plan(None), "")
        self.assertEqual(R.render_plan("not a plan"), "")
        self.assertEqual(R.render_plan([]), "")

    def test_nodes_not_list_returns_empty(self):
        self.assertEqual(R.render_plan({"nodes": "oops"}), "")

    def test_renders_goal_and_nodes(self):
        out = R.render_plan(_plan(nodes=[
            _node("P1", "扫描项目", "completed", result_summary="183 files"),
            _node("P2", "执行测试", "running", deps=["P1"], workers=[2, 3]),
        ], progress={"percent": 50}))
        self.assertIn("修复测试失败", out)
        self.assertIn("扫描项目", out)
        self.assertIn("执行测试", out)
        self.assertIn("183 files", out)
        self.assertIn("50%", out)
        self.assertIn("工蚁 2", out)
        self.assertIn("工蚁 3", out)

    def test_status_glyphs(self):
        out = R.render_plan(_plan(nodes=[
            _node("A", "a", "completed"),
            _node("B", "b", "running"),
            _node("C", "c", "pending"),
            _node("D", "d", "failed"),
            _node("E", "e", "skipped"),
        ]))
        for glyph in ("✓", "●", "○", "✗", "−"):
            self.assertIn(glyph, out, f"缺少状态字形 {glyph}")

    def test_indentation_reflects_depth(self):
        out = R.render_plan(_plan(nodes=[
            _node("P1", "root"),
            _node("P2", "child", deps=["P1"]),
            _node("P3", "grandchild", deps=["P2"]),
        ]))
        self.assertIn("margin-left:0px", out)
        self.assertIn("margin-left:16px", out)
        self.assertIn("margin-left:32px", out)

    def test_error_is_shown(self):
        out = R.render_plan(_plan(nodes=[
            _node("P1", "修复", "failed", error="pytest 仍失败"),
        ]))
        self.assertIn("pytest 仍失败", out)
        self.assertIn("plan-error", out)

    def test_progress_bar_width(self):
        out = R.render_plan(_plan(
            nodes=[_node("P1", "a")], progress={"percent": 42}
        ))
        self.assertIn("width:42%", out)

    def test_missing_progress_is_tolerated(self):
        out = R.render_plan({"nodes": [_node("P1", "a")]})
        self.assertIn("plan-panel", out)
        self.assertIn("width:0%", out)

    def test_detail_becomes_tooltip(self):
        out = R.render_plan(_plan(nodes=[_node("P1", "a", detail="补充说明")]))
        self.assertIn("补充说明", out)
        self.assertIn("title=", out)

    def test_malformed_nodes_are_skipped_not_fatal(self):
        out = R.render_plan(_plan(nodes=[
            "not a dict", None, _node("P1", "好的"),
        ]))
        self.assertIn("好的", out)


class RenderPlanSecurityTest(unittest.TestCase):
    """模型产出的文本必须转义——它可能含尖括号或脚本标签。"""

    def test_title_is_escaped(self):
        out = R.render_plan(_plan(nodes=[
            _node("P1", '<script>alert(1)</script>'),
        ]))
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_goal_is_escaped(self):
        out = R.render_plan(_plan(
            goal='"><img src=x onerror=alert(1)>',
            nodes=[_node("P1", "a")],
        ))
        self.assertNotIn("<img", out)

    def test_result_summary_is_escaped(self):
        out = R.render_plan(_plan(nodes=[
            _node("P1", "a", "completed", result_summary="<b>bold</b>"),
        ]))
        self.assertNotIn("<b>bold</b>", out)

    def test_error_is_escaped(self):
        out = R.render_plan(_plan(nodes=[
            _node("P1", "a", "failed", error="<iframe src=x>"),
        ]))
        self.assertNotIn("<iframe", out)

    def test_long_text_is_clipped(self):
        out = R.render_plan(_plan(nodes=[
            _node("P1", "x" * 500, "completed", result_summary="y" * 5000),
        ]))
        self.assertLess(len(out), 4000, "超长文本没有截断")


class RenderPermissionsTest(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(R.render_permissions([]), "")
        self.assertEqual(R.render_permissions(None), "")

    def test_granted_and_denied_marks(self):
        out = R.render_permissions([
            {"level": 0, "label": "L0 只读", "granted": True, "note": "已授予"},
            {"level": 5, "label": "L5 修改自身", "granted": False, "note": "始终需确认"},
        ])
        self.assertIn("✓", out)
        self.assertIn("✗", out)
        self.assertIn("perm-on", out)
        self.assertIn("perm-off", out)
        self.assertIn("始终需确认", out)

    def test_labels_are_escaped(self):
        out = R.render_permissions([
            {"label": "<script>x</script>", "granted": True, "note": ""},
        ])
        self.assertNotIn("<script>", out)

    def test_malformed_rows_skipped(self):
        out = R.render_permissions(["nope", None, {"label": "ok", "granted": True}])
        self.assertIn("ok", out)


class RenderTimelineTest(unittest.TestCase):
    def _recs(self):
        return [
            {"event": "TASK_CREATED", "ts": 1758000000.0, "worker_id": None,
             "data": {"goal": "修复测试失败"}},
            {"event": "WORKER_COMPLETED", "ts": 1758000003.0, "worker_id": 2,
             "data": {"status": "ok", "duration_ms": 1234}},
        ]

    def test_empty_returns_empty(self):
        self.assertEqual(R.render_timeline([]), "")
        self.assertEqual(R.render_timeline(None), "")

    def test_renders_events(self):
        out = R.render_timeline(self._recs())
        self.assertIn("TASK_CREATED", out)
        self.assertIn("WORKER_COMPLETED", out)
        self.assertIn("goal=修复测试失败", out)
        self.assertIn("duration_ms=1234", out)
        self.assertIn("#2", out)

    def test_accepts_eventrecord_objects(self):
        import shutil
        import tempfile

        import antnest_events as ev
        # 必须指向临时目录：EventLog(base_dir) 会真的往 base_dir/events/ 写文件，
        # 用 "." 会在仓库根下留下 events/ 垃圾目录。
        tmp = tempfile.mkdtemp(prefix="antnest_tl_")
        try:
            log = ev.EventLog(tmp)
            rec = log.emit(ev.Event.TASK_CREATED, task_id="t1", goal="x")
            out = R.render_timeline([rec])
            self.assertIn("TASK_CREATED", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_categories_get_glyphs(self):
        out = R.render_timeline(self._recs())
        self.assertIn("tl-task", out)
        self.assertIn("tl-worker", out)

    def test_permission_events_highlighted(self):
        out = R.render_timeline([{
            "event": "PERMISSION_DENIED", "ts": 1758000000.0, "worker_id": None,
            "data": {"tool": "write_file", "action": "deny"},
        }])
        self.assertIn("tl-permission", out)
        self.assertIn("PERMISSION_DENIED", out)

    def test_details_are_escaped(self):
        out = R.render_timeline([{
            "event": "TOOL_CALL", "ts": 1758000000.0, "worker_id": None,
            "data": {"tool": "<script>x</script>"},
        }])
        self.assertNotIn("<script>", out)

    def test_long_detail_is_clipped(self):
        out = R.render_timeline([{
            "event": "TOOL_CALL", "ts": 1758000000.0, "worker_id": None,
            "data": {"reason": "z" * 5000},
        }])
        self.assertLess(len(out), 2000)


class CssPresenceTest(unittest.TestCase):
    """样式必须真的进 app.css，否则面板会裸奔（无缩进、无配色）。"""

    def setUp(self):
        self.css = (ROOT / "ui_assets" / "app.css").read_text(encoding="utf-8")

    def test_plan_styles_present(self):
        for cls in (".plan-panel", ".plan-item", ".plan-glyph", ".plan-bar-fill"):
            self.assertIn(cls, self.css, f"app.css 缺少 {cls}")

    def test_permission_styles_present(self):
        for cls in (".perm-panel", ".perm-row", ".perm-on", ".perm-off"):
            self.assertIn(cls, self.css, f"app.css 缺少 {cls}")

    def test_timeline_styles_present(self):
        for cls in (".timeline", ".tl-item", ".tl-event"):
            self.assertIn(cls, self.css, f"app.css 缺少 {cls}")

    def test_reduced_motion_respected(self):
        self.assertIn("prefers-reduced-motion", self.css)

    # 组件前缀：这些前缀下的类名约定是「自定义组件」，必须真有 CSS 规则。
    # 工具类（flex/row/muted/primary…）不在此列，否则断言会淹没在噪音里。
    # topbar- 是 v1.4.1 加的（模型切换移到顶栏）——新前缀必须同时加进这里，
    # 否则新写的顶栏样式可以完全没有规则而无人察觉。
    _COMPONENT_PREFIXES = ("btn-", "tl-", "perm-", "plan-", "health-", "topbar-")

    # 豁免：只被 JS 选中、靠继承父元素取样式、**故意**不给规则的类。
    # 加进这里等于声明「我确认过它是钩子不是组件」，是个需要走心的决定。
    _JS_HOOK_CLASSES = {
        "btn-lbl": "querySelector('.btn-lbl') 换按钮文案，继承 .btn 样式",
    }

    # 组件类的两个产地：ui_render.py 生成面板 HTML，prototype 负责页面骨架。
    # 只扫后者会漏掉 26 个里的 25 个。
    _SOURCES = ("ui_render.py", "prototype_antnest.py")

    def test_no_dead_component_classes(self):
        """引用了但 app.css 里没有规则的组件类 = 静默裸奔。

        v1.4.1 的根因：`.btn-expand` 与 `.card-h2` 被引用了几十处却零规则，
        于是「轨迹」按钮被列向 flex 的 align-items:stretch 拉满整宽，
        渲染成一个像输入框的方块——而没有任何测试发现它。
        逐个类断言存在，比补单个类更能防住下一次。
        """
        used = set()
        for name in self._SOURCES:
            src = (ROOT / name).read_text(encoding="utf-8")
            for pat in (r"class=\"([^\"]+)\"", r"cls=\"([^\"]+)\""):
                for m in re.finditer(pat, src):
                    for tok in m.group(1).split():
                        # f-string 模板（tl-{category}）是运行期拼的，
                        # 具体规则形如 .tl-permission，无静态规则可断言
                        if "{" in tok:
                            continue
                        if tok.startswith(self._COMPONENT_PREFIXES):
                            used.add(tok)

        self.assertTrue(used, "未扫描到任何组件类，扫描规则本身可能已失效")
        # 两处产地都要扫到，否则扫描范围悄悄缩了也没人知道
        self.assertGreaterEqual(len(used), 20, f"只扫到 {len(used)} 个组件类，扫描范围可能已失效")

        # 豁免清单不得腐烂：条目若已不再被引用，说明钩子改名了，该清理
        stale = [c for c in self._JS_HOOK_CLASSES if c not in used]
        self.assertEqual([], stale, f"豁免清单里这些类已无人使用，应移除：{stale}")

        dead = sorted(c for c in used
                      if ("." + c) not in self.css and c not in self._JS_HOOK_CLASSES)
        self.assertEqual([], dead, f"这些组件类在 app.css 中无规则，会裸奔：{dead}")


class SelfSourceGateTest(unittest.TestCase):
    """新增的 UI 渲染代码不得削弱自身源码门禁。"""

    def test_ui_render_is_self_source(self):
        import AntNest  # noqa: F401
        from antnest_permissions import PermissionEngine, PermissionPolicy

        engine = PermissionEngine(policy=PermissionPolicy(
            self_source_names=frozenset({"ui_render.py"}),
            self_source_root=str(ROOT),
        ))
        d = engine.check_write_path(str(ROOT / "ui_render.py"), tool="write_file")
        self.assertEqual(d.status, "approval_required")


if __name__ == "__main__":
    unittest.main()
