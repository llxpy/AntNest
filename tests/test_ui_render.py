# -*- coding: utf-8 -*-
import unittest

import ui_render


class RenderChatTest(unittest.TestCase):
    def test_sys_role_renders_event_chip(self):
        html = ui_render.render_chat([{"role": "sys", "text": "🐜 蚁后派出工蚁「w1」"}])
        self.assertIn('class="bubble sys"', html)
        self.assertIn("派出工蚁", html)
        self.assertNotIn("avatar", html)

    def test_sys_role_empty_text_skipped(self):
        html = ui_render.render_chat([{"role": "sys", "text": ""}])
        self.assertNotIn("bubble sys", html)

    def test_user_and_queen_unaffected(self):
        html = ui_render.render_chat([
            {"role": "user", "text": "你好"},
            {"role": "queen", "text": "在的"},
        ])
        self.assertIn("bubble user", html)
        self.assertIn("bubble queen", html)
        self.assertIn("蚁后 · Queen", html)

    def test_sys_xss_safe(self):
        html = ui_render.render_chat([{"role": "sys", "text": "<script>alert(1)</script>"}])
        self.assertNotIn("<script>", html)

    def test_bubbles_have_copy_button_sys_excluded(self):
        html = ui_render.render_chat([{"role": "queen", "text": "x"}])
        self.assertIn("msg-copy", html)
        html2 = ui_render.render_chat([{"role": "sys", "text": "event"}])
        self.assertNotIn("msg-copy", html2)

    def test_empty_chats_fallback(self):
        html = ui_render.render_chat([])
        self.assertIn("开始和蚁后对话", html)


if __name__ == "__main__":
    unittest.main()
