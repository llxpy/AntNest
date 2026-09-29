# -*- coding: utf-8 -*-
"""技能表的不变量守卫（docs/v1.4.1-SKILL-TABLE-DESIGN.md §5）。

多数断言守的是「v1 设计稿自己犯过的错」：
  * 「内置 17」是假的（exposed_specs() 是 12）
  * 「L5 是某工具的等级」是假的（L4/L5 没有任何工具行）
  * display 是第二处需同步的清单（AGENTS.md §4 的老问题）
  * display_name 来自模型输出 = 不可信输入
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ANT_API_KEY", "test-key")

import antnest_registry as reg  # noqa: E402
import antnest_toolforge as forge  # noqa: E402
import ui_render as R  # noqa: E402


class DisplayNameTest(unittest.TestCase):
    """S1-S4：可读名挂在 ToolSpec 上，且不腐烂。"""

    def test_every_tool_has_display(self):
        missing = [t.name for t in reg.TOOL_SPECS if not t.display]
        self.assertEqual([], missing, f"这些工具没有可读名：{missing}")

    def test_display_unique(self):
        seen, dup = set(), []
        for t in reg.TOOL_SPECS:
            if t.display in seen:
                dup.append(t.display)
            seen.add(t.display)
        self.assertEqual([], dup, f"可读名重复，用户分不清哪一行是哪一行：{dup}")

    def test_display_differs_from_name(self):
        same = [t.name for t in reg.TOOL_SPECS if t.display == t.name]
        self.assertEqual([], same,
                         f"这些工具的可读名等于机器名，等于没做这件事：{same}")

    def test_queen_visible_tools_have_display(self):
        for t in reg.TOOL_SPECS:
            if t.queen_visible:
                self.assertTrue(t.display, f"蚁后可见的 {t.name} 缺可读名")


class HonestCountTest(unittest.TestCase):
    """守 v1 的两个事实错误。"""

    def test_counts_match_reality(self):
        """面板标题不能写死「内置 17」。"""
        total = len(reg.TOOL_SPECS)
        visible = sum(1 for t in reg.TOOL_SPECS if t.queen_visible)
        payload = forge.build_skill_payload(list(reg.TOOL_SPECS), [])
        self.assertEqual(visible, payload["counts"]["builtin"])
        # 17 是全量，12 是 exposed —— 两者都不等于对方，UI 必须标明口径
        self.assertLess(payload["counts"]["builtin"], total)
        self.assertNotEqual(17, payload["counts"]["builtin"],
                            "若某天恰好等于 17，说明本测试该更新口径了")

    def test_worker_only_not_counted_as_builtin(self):
        payload = forge.build_skill_payload(list(reg.TOOL_SPECS), [])
        names = {r["name"] for r in payload["builtin"]}
        for w in ("run_cli", "run_python"):
            self.assertNotIn(w, names, f"{w} 是工蚁专用，不该算进蚁后能做的")
            self.assertIn(w, {r["name"] for r in payload["worker_only"]})

    def test_no_phantom_levels_rendered(self):
        """L4/L5 在 PermLevel 里存在但没有工具行 —— 画出来是捏造。"""
        used = {int(t.level) for t in reg.TOOL_SPECS}
        phantom = used - set(R._SKILL_LEVELS)
        self.assertEqual(set(), phantom,
                         f"这些等级没有配色，会渲染成 L?：{phantom}")
        # 反向：也不该为不存在的等级准备配色
        from antnest_permissions import PermLevel
        real = {int(p) for p in PermLevel}
        for lv in R._SKILL_LEVELS:
            self.assertIn(lv, real, f"配色里出现了 PermLevel 不存在的等级 L{lv}")


class FallbackChainTest(unittest.TestCase):
    """S5：display_name 缺失时的回退。"""

    def test_falls_back_to_name(self):
        payload = forge.build_skill_payload([], [
            {"name": "pdf_extract", "display_name": "", "description": "x"},
        ])
        self.assertEqual("pdf_extract", payload["learned"][0]["display"])

    def test_prefers_display_name(self):
        payload = forge.build_skill_payload([], [
            {"name": "pdf_extract", "display_name": "PDF 文本提取", "description": "x"},
        ])
        self.assertEqual("PDF 文本提取", payload["learned"][0]["display"])

    def test_empty_library_is_fine(self):
        payload = forge.build_skill_payload(list(reg.TOOL_SPECS), [])
        self.assertEqual(0, payload["counts"]["learned"])

    def test_pure_no_global_access(self):
        """build_skill_payload 是纯函数：改输入不改输出，不读全局。"""
        metas = [{"name": "a", "display_name": "甲", "description": ""}]
        p1 = forge.build_skill_payload([], metas)
        p2 = forge.build_skill_payload([], metas)
        self.assertEqual(p1, p2)


class IterToolMetaTest(unittest.TestCase):
    """S6：真文件系统 + 真 fail-soft。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _mk(self, name, meta, with_py=True):
        d = self.base / name
        d.mkdir(parents=True, exist_ok=True)
        if meta is not None:
            (d / "tool.json").write_text(meta, encoding="utf-8")
        if with_py:
            (d / "tool.py").write_text("def f():\n    pass\n", encoding="utf-8")

    def test_missing_dir_returns_empty(self):
        self.assertEqual([], forge.iter_tool_meta(str(self.base / "nope")))

    def test_broken_json_is_skipped_not_raised(self):
        self._mk("good", '{"name": "good", "display_name": "好的"}')
        self._mk("bad", "{ this is not json")
        out = forge.iter_tool_meta(str(self.base))
        names = [m["name"] for m in out]
        self.assertIn("good", names)
        # 坏的那条也不能让整个调用炸掉
        self.assertEqual(2, len(out))
        broken = [m for m in out if m["broken"]]
        self.assertEqual(1, len(broken), "损坏的 tool.json 应被标记")

    def test_missing_file_still_yields_entry(self):
        self._mk("nofile", None)
        out = forge.iter_tool_meta(str(self.base))
        self.assertEqual(1, len(out))
        self.assertTrue(out[0]["broken"])

    def test_does_not_read_source(self):
        """不读 tool.py：画名字表不该为每个工具 open 一次源码。"""
        d = self.base / "x"
        d.mkdir()
        (d / "tool.json").write_text('{"name": "x"}', encoding="utf-8")
        (d / "tool.py").write_text("raise RuntimeError('被读了')\n", encoding="utf-8")
        out = forge.iter_tool_meta(str(self.base))   # 若读了源码会抛
        self.assertEqual("x", out[0]["name"])

    def test_no_import_of_antnest(self):
        """iter_tool_meta 不许经 _A() 触发 import AntNest（可能 sys.exit）。"""
        import inspect
        src = inspect.getsource(forge.iter_tool_meta)
        self.assertNotIn("_A()", src,
                         "iter_tool_meta 走了 _A()，核心未加载时会打死 GUI")


class RenderTest(unittest.TestCase):
    """S7-S9：转义、截断、空态。"""

    def _payload(self, **kw):
        base = {
            "status": "ok",
            "builtin": [{"name": "spawn_clone", "display": "派出工蚁",
                         "level": 2, "summary": "派工蚁执行"}],
            "worker_only": [{"name": "run_cli", "display": "执行 Shell",
                            "level": 2, "summary": "仅工蚁"}],
            "learned": [],
            "counts": {"builtin": 1, "worker_only": 1, "learned": 0},
        }
        base.update(kw)
        return base

    def test_escapes_hostile_display_name(self):
        """display_name 来自模型输出 = 不可信输入。"""
        p = self._payload(learned=[{
            "name": "x", "display": '<img src=x onerror="alert(1)">',
            "description": "", "category": "general", "updated_at": "",
            "broken": False,
        }], counts={"builtin": 1, "worker_only": 1, "learned": 1})
        html = R.render_skill_table(p)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn("onerror=", html.replace("&quot;", "").replace("alert(1)", ""))

    def test_truncates_long_name(self):
        p = self._payload(learned=[{
            "name": "x", "display": "超长名字" * 40, "description": "",
            "category": "general", "updated_at": "", "broken": False,
        }], counts={"builtin": 1, "worker_only": 1, "learned": 1})
        html = R.render_skill_table(p)
        self.assertIn("…", html)
        self.assertLess(len(html), 4000, "超长名字没被截断，DOM 会膨胀")

    def test_escapes_hostile_description(self):
        p = self._payload(learned=[{
            "name": "x", "display": "名字",
            "description": "<script>alert(1)</script>",
            "category": "general", "updated_at": "", "broken": False,
        }], counts={"builtin": 1, "worker_only": 1, "learned": 1})
        html = R.render_skill_table(p)
        self.assertNotIn("<script>", html)

    def test_empty_learned_shows_guidance(self):
        html = R.render_skill_table(self._payload())
        self.assertIn("自撰工具", html)
        self.assertIn("还没自撰过", html)

    def test_not_ready_degrades(self):
        """核心未加载时也要能渲染出可读提示，而不是崩。"""
        html = R.render_skill_table({"status": "not_ready"})
        self.assertIn("skill-empty", html)
        html2 = R.render_skill_table({})
        self.assertIn("skill-empty", html2)

    def test_broken_tool_marked(self):
        p = self._payload(learned=[{
            "name": "x", "display": "名字", "description": "",
            "category": "general", "updated_at": "", "broken": True,
        }], counts={"builtin": 1, "worker_only": 1, "learned": 1})
        self.assertIn("skill-broken", R.render_skill_table(p))

    def test_legend_only_lists_used_levels(self):
        """图例不许列出没用到的等级。"""
        p = self._payload(builtin=[
            {"name": "a", "display": "甲", "level": 0, "summary": ""},
        ], counts={"builtin": 1, "worker_only": 1, "learned": 0})
        html = R.render_skill_table(p)
        self.assertIn("L0", html)
        self.assertNotIn("L3", html, "图例列了没用到的等级")


class RegisterToolMergeTest(unittest.TestCase):
    """K4：覆盖更新不能抹掉 display_name。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        import AntNest  # noqa: F401  让 _A() 可用
        # 把工具库指到临时目录，避免污染真实 .antnest
        import AntNest as an
        self._orig = an.PROJECT_ANT_DIR
        an.PROJECT_ANT_DIR = self.tmp.name

    def tearDown(self):
        import AntNest as an
        an.PROJECT_ANT_DIR = self._orig
        self.tmp.cleanup()

    def test_re_register_keeps_display_name(self):
        import json
        r1 = json.loads(forge.register_tool(
            "mytool", description="第一版", code="def f(): pass",
            display_name="我的工具"))
        self.assertEqual("ok", r1["status"])
        self.assertEqual("我的工具", r1["display_name"])

        # 蚁后改进自己的工具时不会重复传 display_name
        r2 = json.loads(forge.register_tool(
            "mytool", description="第二版", code="def f(): return 1"))
        self.assertTrue(r2["replaced"])
        self.assertEqual("我的工具", r2["display_name"],
                         "覆盖更新把 display_name 抹掉了")
        meta = json.loads((self.base / "tools" / "mytool" / "tool.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual("我的工具", meta["display_name"])


class SchemaSyncTest(unittest.TestCase):
    """模型必须真的能看到 display_name，否则功能静默失效。"""

    def test_register_tool_schema_exposes_display_name(self):
        import antnest_schemas as S
        props = S.register_tool_schema["function"]["parameters"]["properties"]
        self.assertIn("display_name", props,
                      "schema 里没有 display_name，蚁后永远传不了这个字段")


if __name__ == "__main__":
    unittest.main()
