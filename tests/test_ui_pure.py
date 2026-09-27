# -*- coding: utf-8 -*-
"""antnest_ui_pure 单元测试：从 prototype_antnest 抽取的纯函数。"""
import unittest

from antnest_ui_pure import (
    _classify_command,
    _clip,
    _esc,
    _version_gt,
    _version_tuple,
    render_markdown,
)


class VersionTupleTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(_version_tuple("1.3.1"), (1, 3, 1))

    def test_v_prefix_digit_bond(self):
        # "v1" 段以 v 开头，\d+ 匹配失败 → 0（原行为，必须保留）
        self.assertEqual(_version_tuple("v1.3.1"), (0, 3, 1))

    def test_empty_none(self):
        # 原行为：空串 split 后为 [""]，匹配 \d+ 失败记 0 → (0,)
        self.assertEqual(_version_tuple(""), (0,))
        self.assertEqual(_version_tuple(None), (0,))

    def test_dot_dash_plus_separators(self):
        # beta 段匹配失败 → 0；+2024 是单字段
        self.assertEqual(_version_tuple("1.3.1-beta+2024"), (1, 3, 1, 0, 2024))


class VersionGtTest(unittest.TestCase):
    def test_greater(self):
        self.assertTrue(_version_gt("1.3.1", "1.3.0"))
        self.assertTrue(_version_gt("2.0", "1.9.9"))

    def test_less(self):
        self.assertFalse(_version_gt("1.3.1", "2.0"))
        self.assertFalse(_version_gt("1.3.0", "1.3.1"))

    def test_equal_not_greater(self):
        self.assertFalse(_version_gt("1.3.1", "1.3.1"))

    def test_v_prefix_behavior(self):
        # v1.3.1 解析为 (0,3,1)，不带前缀 1.3.0 解析为 (1,3,0) → False（原行为）
        self.assertFalse(_version_gt("v1.3.1", "1.3.0"))


class ClassifyCommandTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_classify_command(""), "执行命令")
        self.assertEqual(_classify_command(None), "执行命令")

    def test_powershell_ls(self):
        self.assertEqual(_classify_command("Get-ChildItem C:/src"), "列出目录：C:/src")
        self.assertEqual(_classify_command("Get-ChildItem"), "列出目录")

    def test_powershell_read(self):
        self.assertEqual(_classify_command("Get-Content a.py"), "读取文件：a.py")

    def test_powershell_write(self):
        self.assertEqual(_classify_command("Set-Content out.txt"), "写入文件：out.txt")
        self.assertEqual(_classify_command("Add-Content log.txt"), "写入文件：log.txt")

    def test_python(self):
        self.assertEqual(_classify_command("python main.py"), "执行 Python：main.py")
        # 裸 python（无脚本路径、无尾随空白）不命中 → 兜底返回原命令
        self.assertEqual(_classify_command("python"), "python")

    def test_git(self):
        self.assertEqual(_classify_command("git commit"), "Git 提交变更")
        self.assertEqual(_classify_command("git push origin"), "Git 推送代码")
        self.assertEqual(_classify_command("git status"), "Git 查看状态")
        # 未知名子命令原样返回
        self.assertEqual(_classify_command("git unknowncmd"), "Git unknowncmd")

    def test_npm(self):
        self.assertEqual(_classify_command("npm install"), "npm install")
        # 裸 npm 无尾随空白不匹配 → 兜底返回原命令
        self.assertEqual(_classify_command("npm"), "npm")

    def test_docker(self):
        self.assertEqual(_classify_command("docker ps"), "Docker ps")

    def test_test_build(self):
        self.assertEqual(_classify_command("pytest tests"), "运行测试")
        self.assertEqual(_classify_command("cargo build"), "构建项目")

    def test_pip(self):
        self.assertEqual(_classify_command("pip install requests"), "安装依赖：requests")
        # "pip list" 匹配 pip\s+ 但 list 后无包名 → group(2) 缺失 → "执行 pip"
        self.assertEqual(_classify_command("pip list"), "执行 pip")

    def test_echo(self):
        self.assertEqual(_classify_command("echo hello"), "输出内容")

    def test_long_fallback(self):
        long = "very long command " * 5  # 超 40 字符
        r = _classify_command(long)
        self.assertTrue(r.endswith("…"))
        self.assertLessEqual(len(r) - 1, 40)

    def test_case_insensitive(self):
        self.assertEqual(_classify_command("GIT COMMIT"), "Git 提交变更")
        self.assertEqual(_classify_command("Get-ChildItem /tmp"), "列出目录：/tmp")


class EscClipTest(unittest.TestCase):
    def test_esc_html(self):
        self.assertEqual(_esc("<b>&\"'"), "&lt;b&gt;&amp;&quot;&#x27;")

    def test_clip_short(self):
        self.assertEqual(_clip("short"), "short")

    def test_clip_long(self):
        r = _clip("x" * 150)
        self.assertEqual(len(r), 121)
        self.assertTrue(r.endswith("…"))
        self.assertEqual(_clip("x" * 150, n=10), "xxxxxxxxxx…")

    def test_clip_none(self):
        self.assertEqual(_clip(None), "")


class RenderMarkdownTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(render_markdown(""), "")
        self.assertEqual(render_markdown(None), "")

    def test_plain_text_unchanged(self):
        # 纯文本结果与原 esc() 一致（无 <br> 干扰单段）
        self.assertEqual(render_markdown("<b>hi</b>"), "&lt;b&gt;hi&lt;/b&gt;")

    def test_multiline_joined_br(self):
        self.assertEqual(render_markdown("a\nb"), "a<br>b")

    def test_bold(self):
        self.assertEqual(render_markdown("**重点**"), "<strong>重点</strong>")

    def test_italic(self):
        self.assertEqual(render_markdown("*斜体*"), "<em>斜体</em>")

    def test_italic_not_conflict_bold(self):
        # **x** 内层不套 em
        self.assertEqual(render_markdown("**x**"), "<strong>x</strong>")

    def test_inline_code(self):
        self.assertEqual(render_markdown("用 `pip install` 安装"),
                         "用 <code>pip install</code> 安装")

    def test_inline_code_xss_safe(self):
        # 行内代码内容转义，不透传标签
        self.assertEqual(render_markdown("`<script>alert(1)</script>`"),
                         "<code>&lt;script&gt;alert(1)&lt;/script&gt;</code>")

    def test_fence_block(self):
        html = render_markdown("代码：\n```python\nprint('x')\n```\n结束")
        self.assertIn("<pre><code>print(&#x27;x&#x27;)</code></pre>", html)
        self.assertIn("代码：", html)

    def test_fence_xss_safe(self):
        html = render_markdown("```\n<script>alert(1)</script>\n```")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_heading(self):
        self.assertEqual(render_markdown("# 标题一"), "<h4>标题一</h4>")
        self.assertEqual(render_markdown("## 标题二"), "<h4>标题二</h4>")

    def test_list(self):
        self.assertEqual(render_markdown("- 甲\n- 乙"), "<ul><li>甲</li><li>乙</li></ul>")

    def test_list_star_variant(self):
        self.assertEqual(render_markdown("* 一项"), "<ul><li>一项</li></ul>")

    def test_link_safe(self):
        self.assertEqual(
            render_markdown("[官网](https://antnest.dev)"),
            '<a href="https://antnest.dev" target="_blank" rel="noopener noreferrer">官网</a>',
        )

    def test_link_nontrusted_scheme_text(self):
        # javascript: 不放行为链接，原样转义
        out = render_markdown("[点我](javascript:alert(1))")
        self.assertNotIn("<a ", out)
        self.assertIn("javascript:alert(1)", out)

    def test_heading_plus_list_mixed(self):
        out = render_markdown("# 任务\n- 步骤1\n```\ncode\n```")
        self.assertIn("<h4>任务</h4>", out)
        self.assertIn("<ul><li>步骤1</li></ul>", out)
        self.assertIn("<pre><code>code</code></pre>", out)

    def test_html_never_passthrough(self):
        nasty = "<img src=x onerror=alert(1)><b>bold</b>"
        out = render_markdown(nasty)
        self.assertNotIn("<img", out)
        self.assertNotIn("<b>", out)
        self.assertIn("&lt;", out)


if __name__ == "__main__":
    unittest.main()