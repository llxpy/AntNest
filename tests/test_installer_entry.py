# -*- coding: utf-8 -*-
"""exe 启动入口的不变量守卫（installer/antnest_boot.ps1）。

为什么是源码字符串断言而不是行为测试：CI 有 ubuntu-latest（test.yml:13），
不能调 PowerShell；而且 ps2exe 产物在 Linux 上根本不存在。沿用仓库既有
惯例（tests/test_ui_wiring.py 的源码断言）。

诚实的边界：这里证明「该有的在、不该有的没了」，**不证明 PowerShell
运行时行为正确**。真实验证见 docs/v1.4.1-BOOT-DESIGN.md 遗留 R11。

每条断言都对应 v1 设计稿里被评审推翻或修正的某个具体错误，注释里写明
它当初是为了挡住什么。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "installer"
BOOT = INSTALLER / "antnest_boot.ps1"
HELPER = INSTALLER / "uv_helper.ps1"
ISS = INSTALLER / "AntNest.iss"
BUILD = INSTALLER / "build_launcher.ps1"
RELEASE_CHECK = ROOT / "tools" / "release_check.ps1"

# antnest_boot.ps1 里允许存在的、uv_helper.ps1 没有的函数。
# 这就是 T1 的白名单——v1 设计稿要求「两边函数名集合一致」，
# 被它自己要求的 Show-Error / 日志写入 / 失败汇报否证了。
BOOT_EXTRA_FUNCTIONS = {
    "Show-Error",
    "Write-BootLog",
    "Open-BootLog",
    "Get-AppLogTail",
    "Report-ChildFailure",
    "Assert-InstallComplete",
    "Get-FreeSpaceMB",
    "Start-AntNest",
}

FUNC_RE = re.compile(r"^\s*function\s+([A-Za-z_][\w-]*)", re.M)

# uv_helper.ps1 里有、antnest_boot.ps1 刻意不抄的函数。
# Start-AntNestUi 在 helper 里只是「起进程 + 无限等」，而 boot 的启动段还要
# 记日志、查退出码、失败弹窗——语义已变，抄一份名字相同但行为不同的函数
# 反而会掩盖差异。所以这里显式记为有意省略，而不是让它静默缺失。
BOOT_KNOWN_OMISSIONS = {"Start-AntNestUi"}

# boot 启动的子进程不能设超时；但 Install-WebView2 等的是 WebView2 安装器，
# 那个等待必须有界（否则卡住整个启动）。区分：只看启动 App 的那段。
WEBVIEW2_WAIT = "WaitForExit(180000)"


def strip_ps_comments(text: str) -> str:
    """去掉 PowerShell 注释。

    「不得使用 X」这类断言必须扫去掉注释的正文：本文件的文件头**故意**
    写着为什么不用 -RedirectStandard* / 不用超时，直接扫原文会自我否定。
    """
    out = []
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def funcs(text: str):
    return set(FUNC_RE.findall(text))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


class BootFileExistsTest(unittest.TestCase):
    def test_boot_script_present(self):
        self.assertTrue(BOOT.exists(), "installer/antnest_boot.ps1 不存在")

    def test_removed_scripts_are_gone(self):
        """防死代码复活。

        v1 设计稿没有这一条，于是 launcher.ps1 / launch.ps1 被删后
        tools/release_check.ps1:46 的 $required 会让发版炸掉，而
        T6 那种析取断言照样通过。
        """
        for gone in ("launcher.ps1", "launch.ps1"):
            self.assertFalse(
                (INSTALLER / gone).exists(),
                f"installer/{gone} 已删除，不应复活（它与 antnest_boot.ps1 逻辑重复且无消费者）",
            )

    def test_release_check_does_not_require_removed_files(self):
        """发版闸门必须与删除同步——这是仓库里唯一覆盖安装器文件集的自动检查。"""
        src = read(RELEASE_CHECK)
        for gone in ("launcher.ps1", "launch.ps1"):
            # 只能出现在「已删除」的守卫里，不能出现在 $required 里
            for m in re.finditer(r"^\s*\"installer\\\\?([\w.]+)\"", src, re.M):
                self.assertNotEqual(gone, m.group(1),
                                    f"release_check.ps1 的 $required 仍要求 {gone}")
        self.assertIn("antnest_boot.ps1", src, "release_check.ps1 未要求 antnest_boot.ps1")


class BootPs2exeConstraintsTest(unittest.TestCase):
    """ps2exe 的硬约束：单文件自包含 + 编码。"""

    def setUp(self):
        self.raw = BOOT.read_bytes()
        self.text = read(BOOT)

    def test_has_utf8_bom(self):
        """无 BOM 会被 PowerShell 5.1 按系统 ANSI 代码页解码 → CJK 乱码。

        v1 设计稿的 T3 写的是 all(ord(c)<128)，那会否证带 BOM 的文件本身
        （首字符变成 \\ufeff）。正确做法是「BOM + 正文纯 ASCII」两条一起断言。
        """
        self.assertTrue(
            self.raw.startswith(b"\xef\xbb\xbf"),
            "antnest_boot.ps1 必须以 UTF-8 BOM 开头，否则按 ANSI 代码页解码",
        )

    def test_body_is_ascii_only(self):
        body = self.raw[3:] if self.raw.startswith(b"\xef\xbb\xbf") else self.raw
        text = body.decode("utf-8")
        bad = [(i, ch) for i, ch in enumerate(text) if ord(ch) >= 128]
        self.assertEqual(
            [], bad[:10],
            "正文含非 ASCII 字符；用户可见文案请放进 $Msg 表（见文件头说明）",
        )

    def test_no_dot_sourcing(self):
        """ps2exe 只能编译单文件，dot-source 外部 .ps1 会让编译产物失效。"""
        offenders = [ln for ln in self.text.splitlines()
                     if re.match(r"^\s*\.\s+\S", ln)]
        self.assertEqual([], offenders,
                         f"antnest_boot.ps1 必须自包含，不应 dot-source: {offenders}")

    def test_declares_fingerprint(self):
        """D7：tools/sync_release.ps1 注入预构建 exe 且不重跑构建脚本，
        于是「源码改了、exe 还是旧的」而所有测试都绿。指纹让这种情况可检测。"""
        self.assertIn("BOOT_SCHEMA", self.text, "缺少 BOOT_SCHEMA 指纹")
        m = re.search(r"\$BOOT_SCHEMA\s*=\s*\"([^\"]+)\"", self.text)
        self.assertIsNotNone(m, "BOOT_SCHEMA 未赋字符串值")


class BootZeroBlackBoxTest(unittest.TestCase):
    """launcher.ps1:94-97 把「零黑框」写成硬约束。以下三条守住它。"""

    def setUp(self):
        self.text = read(BOOT)
        self.code = strip_ps_comments(self.text)

    def test_no_redirect_standard(self):
        """-RedirectStandard* 强制 UseShellExecute=false，WindowStyle 随之失效；
        父进程是 -noConsole（无控制台），控制台子进程会新分配一个可见控制台。
        v1 设计稿的 §3.3 正是这么写的，等于每次启动都引入黑框。"""
        self.assertNotIn(
            "-RedirectStandard", self.code,
            "不得使用 -RedirectStandard*：父进程无控制台，子进程会新开可见控制台",
        )

    def test_no_child_timeout(self):
        """健康应用一直运行到用户关窗，任何 WaitForExit(毫秒) 都会在
        每次成功启动时误报「启动超时」。

        注意排除 WebView2 安装器的等待——那是有意设界的（否则卡死启动），
        它等的不是应用子进程。
        """
        code = self.code.replace(WEBVIEW2_WAIT, "")
        offenders = re.findall(r"WaitForExit\(\s*[^)\s]", code)
        self.assertEqual(
            [], offenders,
            f"不得对应用子进程等待设超时（健康运行会误报）: {offenders}",
        )

    def test_webview2_installer_wait_is_still_bounded(self):
        """反向守卫：确认上面的豁免是真的豁免，而不是把有界等待也去掉了。"""
        self.assertIn(
            WEBVIEW2_WAIT, self.code,
            "WebView2 安装器的等待应有界，否则卡住整个启动",
        )

    def test_launch_uses_shellexecute_hidden(self):
        self.assertIn("-WindowStyle Hidden", self.text,
                      "启动子进程必须用 -WindowStyle Hidden 以免黑框")


class BootDiagnosticsTest(unittest.TestCase):
    """G1/G2：失败必须让用户看见，且带日志位置。"""

    def setUp(self):
        self.text = read(BOOT)

    def test_has_top_level_try_catch(self):
        """launcher.ps1 有 $ErrorActionPreference=Stop 却**没有** try/catch，
        而 -noConsole 下没有控制台可打印——Add-Type / Start-Process
        抛出的终止性错误全部静默。v1 设计稿完全没提这条，是真正的问题。"""
        self.assertRegex(
            self.text, r"try\s*\{[^}]*Start-AntNest\s*\}",
            "antnest_boot.ps1 缺少顶层 try/catch：入口自身失败会静默",
        )
        self.assertIn("catch", self.text)

    def test_checks_child_exit_code(self):
        """launcher.ps1:99 的 WaitForExit() 之后不查退出码，
        于是 uv 同步失败 / pywebview import 失败 / venv 损坏全都无声。"""
        self.assertIn("ExitCode", self.text, "未检查子进程退出码")

    def test_reads_app_logs_not_child_stdout(self):
        """诊断信息来自应用自己的日志，而不是捕获子进程 stdout——
        零新增失败面，且 antnest.log 本就含完整原文。"""
        self.assertTrue(
            "antnest.log" in self.text or "ui_trace.log" in self.text,
            "失败汇报应读取应用自身日志（antnest.log / ui_trace.log）",
        )

    def test_error_dialog_mentions_log_location(self):
        self.assertIn("LogAt", self.text, "错误提示未包含日志位置")

    def test_show_error_has_fallback(self):
        """launcher.ps1:17 的 catch {} 吞掉 Show-Error 自身的失败，
        于是错误路径没有错误路径。"""
        fn = self._function_body("Show-Error")
        self.assertIn("catch", fn, "Show-Error 需要在 WinForms 不可用时降级")

    def test_does_not_preflight_api_key(self):
        """v1 设计稿的 P6（未配 Key 则阻止启动）会造成净倒退：
        拦住 → 应用不启动 → 设置页打不开，而 apply_settings
        （antnest_bridge.py:846-860）是唯一的自助修复路径。
        且缺 Key 本来就已在 antnest_bridge.py:1192 给出具体中文提示。"""
        lowered = self.text.lower()
        for token in ("ant_api_key", "config.json.key", "api_key"):
            self.assertNotIn(
                token, lowered,
                f"入口不应读取 {token}：R8 的表现已由 UI 上屏，预检会造成倒退",
            )

    def _function_body(self, name):
        m = re.search(r"^function\s+" + re.escape(name) + r"\b(.*?)^}",
                      self.text, re.M | re.S)
        self.assertIsNotNone(m, f"未找到函数 {name}")
        return m.group(1)


class BootHelperParityTest(unittest.TestCase):
    """T1：两份 uv/WebView2 助手不能静默分叉。

    ps2exe 单文件约束导致它们必然存在两份。T1 只能挡住删除/改名，
    实现分叉靠双向指纹注释约束——这点设计稿已承认。
    """

    def test_helper_functions_are_all_present_in_boot(self):
        boot = funcs(read(BOOT))
        helper = funcs(read(HELPER))
        missing = helper - boot - BOOT_KNOWN_OMISSIONS
        self.assertEqual(
            set(), missing,
            f"uv_helper.ps1 里的函数在 antnest_boot.ps1 中缺失，改一处必须改另一处: {missing}",
        )

    def test_boot_functions_are_accounted_for(self):
        """白名单之外的新函数必须是有意的，不是漏抄的。"""
        boot = funcs(read(BOOT))
        helper = funcs(read(HELPER))
        extra = boot - helper - BOOT_EXTRA_FUNCTIONS
        self.assertEqual(
            set(), extra,
            f"antnest_boot.ps1 多了未登记的函数 {extra}；"
            f"若是有意新增，请加入 BOOT_EXTRA_FUNCTIONS",
        )

    def test_omissions_are_still_justified(self):
        """BOOT_KNOWN_OMISSIONS 不能变成垃圾桶：每一条都要有理由。"""
        self.assertEqual(
            {"Start-AntNestUi"}, BOOT_KNOWN_OMISSIONS,
            "新增「有意省略」必须同时在本测试里写明理由",
        )

    def test_fingerprint_comments_point_at_each_other(self):
        """指纹注释写在函数**上方**，所以要连同上文注释一起取窗口。"""
        text = read(BOOT)
        for name in ("Find-Uv", "Test-WebView2Installed"):
            m = re.search(r"(?:^#.*\n)+^\s*function\s+" + re.escape(name) + r"\b",
                          text, re.M)
            self.assertIsNotNone(m, f"未找到 {name} 及其上方注释")
            self.assertIn(
                "uv_helper.ps1", m.group(0),
                f"{name} 缺指纹注释，读者无法知道还有一份必须同步的实现",
            )


class BootPreflightTest(unittest.TestCase):
    """预检只允许「修好或说清」，不允许「不让它起」——
    除非应用本来 100% 起不来（P1 缺文件、P3 无 uv）。"""

    def setUp(self):
        self.text = read(BOOT)

    def test_checks_install_completeness(self):
        self.assertIn("prototype_antnest.py", self.text)
        self.assertIn("pyproject.ps", self.text) if False else None
        self.assertIn("pyproject.toml", self.text)
        self.assertIn("antnest_bridge.py", self.text)

    def test_install_failure_names_the_missing_file(self):
        fn = re.search(r"^function\s+Assert-InstallComplete\b.*?(?=^function|\Z)",
                       self.text, re.M | re.S)
        self.assertIsNotNone(fn)
        self.assertIn("missing", fn.group(0),
                      "安装不完整时应报出缺哪个文件，而不是笼统说'安装损坏'")

    def test_webview2_probes_registry_before_recursive_scan(self):
        """Edge Update 维护 3 个注册表键，~1ms；递归扫 EdgeWebView\\Application
        要几百毫秒，而旧 launcher.ps1 每次双击都跑一遍，无 marker 缓存。"""
        fn = re.search(r"^function\s+Test-WebView2Installed\b.*?(?=^function|\Z)",
                       self.text, re.M | re.S)
        self.assertIsNotNone(fn)
        body = fn.group(0)
        self.assertLess(
            body.index("EdgeUpdate\\Clients"), body.index("Get-ChildItem"),
            "WebView2 探测应先查注册表（~1ms），再退回递归扫描（数百 ms）",
        )

    def test_webview2_failure_does_not_block_startup(self):
        """WebView2 缺失时 python 自己会给出更准的提示；在 PowerShell 里
        拦住只是把一条好提示换成一条差提示。"""
        fn = re.search(r"^function\s+Install-WebView2\b.*?(?=^function|\Z)",
                       self.text, re.M | re.S)
        self.assertIsNotNone(fn)
        # Install-WebView2 内部不得 exit / throw 未捕获
        self.assertNotIn("exit ", fn.group(0))

    def test_appdir_resolution_survives_uncompiled_run(self):
        """MainModule 在未编译运行时等于 powershell.exe，$app 会变成 System32，
        预检于是指向 C:\\Windows\\System32 报'安装不完整'。"""
        self.assertIn(
            "$PSScriptRoot", self.text,
            "应优先用 $PSScriptRoot 解析应用目录，否则未编译运行时 $app=System32",
        )


class PackagingWiringTest(unittest.TestCase):
    """AntNest.iss / build_launcher.ps1 必须与新入口同步。"""

    def setUp(self):
        self.iss = read(ISS)
        self.build = read(BUILD)

    def test_iss_ships_boot_not_old_scripts(self):
        for gone in ("launcher.ps1", "launch.ps1"):
            self.assertNotIn(
                f"Source: \".\\{gone}\"", self.iss,
                f"AntNest.iss 仍打包已删除的 {gone}",
            )
        self.assertIn('Source: ".\\antnest_boot.ps1"', self.iss,
                      "AntNest.iss 未打包 antnest_boot.ps1")

    def test_iss_still_ships_install_deps(self):
        """v1 设计稿写「改 AntNest.iss:91-92」，而 :92 是 install_deps.ps1 ——
        它被 [Run] 调用来装 uv 与预建 venv，误删会让安装在最后一步失败。"""
        self.assertIn('Source: ".\\install_deps.ps1"', self.iss)
        self.assertRegex(
            self.iss, r"\[Run\][\s\S]*install_deps\.ps1",
            "AntNest.iss 的 [Run] 应继续调用 install_deps.ps1",
        )

    def test_iss_still_ships_uv_helper(self):
        self.assertIn('Source: ".\\uv_helper.ps1"', self.iss,
                      "install_deps.ps1 / ensure_prereqs.ps1 依赖 uv_helper.ps1")

    def test_build_script_points_at_boot(self):
        m = re.search(r'\$input\s*=\s*Join-Path\s+\$root\s+"([^"]+)"', self.build)
        self.assertIsNotNone(m, "build_launcher.ps1 未找到 $input 赋值")
        self.assertEqual("antnest_boot.ps1", m.group(1),
                         "build_launcher.ps1 的 $input 未指向新入口")

    def test_build_script_refuses_missing_source(self):
        """没有这条的话，改漏了源文件会静默构建出上一次的 exe（D7）。"""
        self.assertIn(
            "Test-Path $input", self.build,
            "build_launcher.ps1 应在源文件缺失时拒绝构建，避免产出陈旧 exe",
        )


class StaleExeTest(unittest.TestCase):
    """D7：tools/sync_release.ps1 注入**预构建** exe 且不重跑构建脚本。

    于是「源码改了、exe 还是旧的」而所有其他检查都绿——版本号证明不了
    任何东西，同一版本的旧 exe 里也有那个字符串。唯一可靠的信号是引导脚本
    里的 $BOOT_SCHEMA 指纹。
    """

    def _boot_schema(self):
        m = re.search(r'\$BOOT_SCHEMA\s*=\s*"([^"]+)"', read(BOOT))
        self.assertIsNotNone(m, "引导脚本未声明 $BOOT_SCHEMA 指纹")
        return m.group(1)

    def test_release_check_greps_the_fingerprint(self):
        src = read(RELEASE_CHECK)
        self.assertIn(
            "BOOT_SCHEMA", src,
            "release_check.ps1 必须比对 exe 内的启动脚本指纹，否则陈旧 exe 无法检测",
        )
        self.assertIn("STALE", src, "陈旧 exe 的失败提示应说明如何修复")

    def test_committed_exe_matches_current_boot_script(self):
        """仓库里入库的 AntNest.exe 必须由当前引导脚本编译。"""
        exe = ROOT / "AntNest.exe"
        if not exe.exists():
            self.skipTest("AntNest.exe 不存在（未构建）")
        data = exe.read_bytes().decode("latin-1", errors="replace")
        schema = self._boot_schema()
        self.assertIn(
            schema, data,
            f"入库的 AntNest.exe 不含当前启动脚本指纹 {schema}，"
            f"它已陈旧。请运行 installer\\build_launcher.ps1 重新编译。",
        )


if __name__ == "__main__":
    unittest.main()
