# -*- coding: utf-8 -*-
"""phtmlwin 窗口控制：frameless kwargs 组装 + 内置 win 路由。"""
import io
import unittest

import phtmlwin as P
from phtmlwin import Win


class DragRegionTest(unittest.TestCase):
    def test_topbar_has_pywebview_drag_region(self):
        # pywebview 拖动约定：.pywebview-drag-region（-webkit-app-region 无效）
        src = io.open("prototype_antnest.py", "r", encoding="utf-8", newline="").read()
        self.assertIn('cls="topbar pywebview-drag-region"', src)


class BuildWindowKwargsTest(unittest.TestCase):
    def test_default_no_frameless_keys(self):
        w = Win("t")
        kw = w._build_window_kwargs("<html>", object())
        self.assertNotIn("frameless", kw)
        self.assertEqual(kw["title"], "t")
        self.assertIn("<html>", kw["html"])

    def test_frameless_injects_three(self):
        w = Win("f", frameless=True)
        kw = w._build_window_kwargs("<html>2", object())
        self.assertIs(kw.get("frameless"), True)
        # easy_drag 关掉：整页拖动会破坏输入/滚动，用 CSS app-region 精准控制
        self.assertIs(kw.get("easy_drag"), False)
        # Windows frameless 保留系统阴影，质感更好
        self.assertIs(kw.get("shadow"), True)

    def test_legacy_signature_skips_frameless(self):
        # 老 pywebview 的 create_window(**kw) 签名不含 frameless → 安全降级，不注入
        w = Win("f", frameless=True)

        class FakeWV:
            def create_window(self, **kw):
                return None

        orig = P._webview_import
        P._webview_import = lambda: FakeWV()
        try:
            kw = w._build_window_kwargs("<html>3", object())
        finally:
            P._webview_import = orig
        self.assertNotIn("frameless", kw)

    def test_icon_gui_passthrough_on_legacy_signature(self):
        # 老版 pywebview 的 create_window 接收 icon/gui；新 6.x 移到 start()。
        # 模拟老签名可确认透传逻辑仍工作。
        w = Win("t", icon="a.ico", gui="edgechromium")

        import inspect

        class FakeWV:
            def create_window(self, title=None, html=None, width=0, height=0,
                              icon=None, gui=None, frameless=False, **kw):
                return None

        orig = P._webview_import
        P._webview_import = lambda: FakeWV()
        try:
            kw = w._build_window_kwargs("<html>", object())
        finally:
            P._webview_import = orig
        self.assertEqual(kw["icon"], "a.ico")
        self.assertEqual(kw["gui"], "edgechromium")


class WinRouteTest(unittest.TestCase):
    def test_builtin_win_route_registered(self):
        w = Win("r", frameless=True)
        w._register_builtin_routes()
        self.assertIn("win", w._routes)

    def test_browser_mode_win_route_silent(self):
        w = Win("r", frameless=True)
        w._register_builtin_routes()
        w._backend = "browser"
        w._routes["win"]({"action": "close"})  # 浏览器模式静默，不抛异常

    def test_win_route_bad_payload(self):
        w = Win("r")
        w._register_builtin_routes()
        w._backend = "browser"
        w._routes["win"](None)      # 非 dict 也安全
        w._routes["win"]("string")  # 字符串也安全


if __name__ == "__main__":
    unittest.main()