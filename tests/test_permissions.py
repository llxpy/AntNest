# -*- coding: utf-8 -*-
"""AntNest · 权限模型测试

重点覆盖 docs/v1.4-DESIGN.md 评审发现的两个致命项：

  C4/硬约束 C2：静态 ToolSpec.level 不得单独作为放行依据。
      `write_file("antnest_config.py")` 在 level=L1(WRITE) 时必须 ASK，
      绝不能 ALLOW——否则等于给了 Agent 一把改写自身源码的钥匙。
  C5：判定顺序。`explicit_confirm` 必须排在分级之前，否则 level=L5 会让
      「改自身源码永远需要人确认」彻底失效。
"""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from antnest_permissions import (  # noqa: E402
    Action,
    PermLevel,
    PermissionEngine,
    PermissionPolicy,
    PermissionsConfig,
    decide,
    describe_grants,
    has_write_signal,
    match_danger,
)


_SELF_NAMES = frozenset({"antnest_config.py", "AntNest.py", "antnest_loop.py"})


def _engine(level=PermLevel.NETWORK, ask_above=PermLevel.NETWORK, approved=False):
    """构造一个指向仓库根的引擎，便于测「核心源码」路径。"""
    policy = PermissionPolicy(
        level=level,
        ask_above_level=ask_above,
        project_dir=str(ROOT),
        self_source_names=_SELF_NAMES,
        self_source_root=str(ROOT),
        self_mod_approved_cb=lambda: approved,
    )
    return PermissionEngine(PermissionsConfig(level=level, ask_above_level=ask_above), policy)


class PermLevelParseTest(unittest.TestCase):
    def test_accepts_int_and_l_prefix(self):
        self.assertEqual(PermLevel.parse(2), PermLevel.EXECUTE)
        self.assertEqual(PermLevel.parse("3"), PermLevel.NETWORK)
        self.assertEqual(PermLevel.parse("L0"), PermLevel.READ)
        self.assertEqual(PermLevel.parse("l5"), PermLevel.SELF_MOD)

    def test_accepts_semantic_names(self):
        self.assertEqual(PermLevel.parse("network"), PermLevel.NETWORK)
        self.assertEqual(PermLevel.parse("workspace_write"), PermLevel.WRITE)
        self.assertEqual(PermLevel.parse("SELF_MODIFICATION"), PermLevel.SELF_MOD)

    def test_garbage_falls_back_to_most_conservative(self):
        self.assertEqual(PermLevel.parse("wat"), PermLevel.READ)
        self.assertEqual(PermLevel.parse(99), PermLevel.READ)
        self.assertEqual(PermLevel.parse(None), PermLevel.READ)
        self.assertEqual(PermLevel.parse("wat", PermLevel.WRITE), PermLevel.WRITE)

    def test_bool_is_not_a_level(self):
        self.assertEqual(PermLevel.parse(True), PermLevel.READ)
        self.assertEqual(PermLevel.parse(False), PermLevel.READ)

    def test_levels_are_ordered(self):
        self.assertLess(PermLevel.READ, PermLevel.WRITE)
        self.assertLess(PermLevel.WRITE, PermLevel.EXECUTE)
        self.assertLess(PermLevel.EXECUTE, PermLevel.NETWORK)
        self.assertLess(PermLevel.NETWORK, PermLevel.SYSTEM)
        self.assertLess(PermLevel.SYSTEM, PermLevel.SELF_MOD)


class DecideOrderTest(unittest.TestCase):
    """C5：判定顺序。"""

    def test_explicit_confirm_beats_even_max_level(self):
        """level=L5 时自源码仍必须 ASK——这是整个模型最关键的不变量。"""
        d = decide(
            PermLevel.SELF_MOD,
            level=PermLevel.SELF_MOD,
            ask_above_level=PermLevel.SELF_MOD,
            explicit_confirm=True,
        )
        self.assertEqual(d.action, Action.ASK)
        self.assertTrue(d.needs_approval)

    def test_unknown_tool_is_denied_fail_closed(self):
        d = decide(
            PermLevel.READ, level=PermLevel.SELF_MOD,
            ask_above_level=PermLevel.SELF_MOD, unknown=True,
        )
        self.assertEqual(d.action, Action.DENY)
        self.assertEqual(d.code, "PERM-UNKNOWN-TOOL")

    def test_granted_level_allows(self):
        d = decide(
            PermLevel.WRITE, level=PermLevel.WRITE, ask_above_level=PermLevel.WRITE
        )
        self.assertEqual(d.action, Action.ALLOW)

    def test_above_ask_threshold_is_hard_denied(self):
        d = decide(
            PermLevel.SYSTEM, level=PermLevel.READ, ask_above_level=PermLevel.READ
        )
        self.assertEqual(d.action, Action.DENY)
        self.assertEqual(d.code, "PERM-OVER-LEVEL")

    def test_between_level_and_threshold_is_ask(self):
        d = decide(
            PermLevel.WRITE, level=PermLevel.READ, ask_above_level=PermLevel.WRITE
        )
        self.assertEqual(d.action, Action.ASK)

    def test_status_vocabulary(self):
        allow = decide(PermLevel.READ, level=PermLevel.READ, ask_above_level=PermLevel.READ)
        ask = decide(PermLevel.SELF_MOD, level=PermLevel.READ,
                     ask_above_level=PermLevel.READ, explicit_confirm=True)
        deny = decide(PermLevel.SELF_MOD, level=PermLevel.READ, ask_above_level=PermLevel.READ)
        self.assertEqual(allow.status, "ok")
        self.assertEqual(ask.status, "approval_required")
        # 新增词汇：denied 若不登记会被 antnest_loop 记成 ok
        self.assertEqual(deny.status, "denied")
        self.assertIn("denied", {"error", "blocked", "approval_required", "denied"})


class BackwardCompatMatrixTest(unittest.TestCase):
    """level=3 必须逐项复刻 v1.3.1 的实际行为（设计文档 §5.4）。"""

    def setUp(self):
        self.e = _engine(level=PermLevel.NETWORK)

    def test_read_write_exec_network_allowed(self):
        self.assertTrue(self.e.check_level(PermLevel.READ).allowed)
        self.assertTrue(self.e.check_level(PermLevel.WRITE).allowed)
        self.assertTrue(self.e.check_level(PermLevel.EXECUTE).allowed)
        self.assertTrue(self.e.check_level(PermLevel.NETWORK).allowed)

    def test_danger_command_asks_not_denies(self):
        d = self.e.check_command("rm -rf /")
        self.assertEqual(d.action, Action.ASK)
        self.assertEqual(d.code, "PERM-DANGER-COMMAND")

    def test_self_source_write_asks(self):
        d = self.e.check_write_path(str(ROOT / "antnest_config.py"))
        self.assertEqual(d.action, Action.ASK)
        self.assertEqual(d.code, "PERM-SELF-SOURCE")

    def test_normal_workspace_write_allowed(self):
        d = self.e.check_write_path(str(ROOT / "docs" / "v1.4-DESIGN.md"))
        self.assertTrue(d.allowed)

    def test_plain_command_allowed(self):
        self.assertTrue(self.e.check_command("pytest -q").allowed)


class ArgSensitivityTest(unittest.TestCase):
    """硬约束 C2：参数敏感判定必须覆盖静态等级。"""

    def test_write_to_self_source_asks_even_at_write_level(self):
        """评审 C4 的核心用例：level=WRITE 时写自身源码必须 ASK，绝不 ALLOW。"""
        e = _engine(level=PermLevel.WRITE, ask_above=PermLevel.WRITE)
        d = e.check_tool(
            "write_file",
            {"path": str(ROOT / "antnest_config.py"), "content": "rm -rf /"},
            required=PermLevel.WRITE,
        )
        self.assertEqual(d.action, Action.ASK, "写自身源码被放行了——这是安全回归")
        self.assertFalse(d.allowed)

    def test_write_to_normal_file_allowed_at_write_level(self):
        e = _engine(level=PermLevel.WRITE, ask_above=PermLevel.WRITE)
        d = e.check_tool(
            "write_file", {"path": str(ROOT / "notes.md"), "content": "hi"},
            required=PermLevel.WRITE,
        )
        self.assertTrue(d.allowed)

    def test_danger_command_via_spawn_asks_even_at_execute_level(self):
        e = _engine(level=PermLevel.SYSTEM, ask_above=PermLevel.SYSTEM)
        d = e.check_tool("spawn_clone", {"command": "format c:"}, required=PermLevel.EXECUTE)
        self.assertEqual(d.action, Action.ASK)
        self.assertEqual(d.code, "PERM-DANGER-COMMAND")

    def test_shell_redirect_to_self_source_asks(self):
        e = _engine(level=PermLevel.EXECUTE, ask_above=PermLevel.EXECUTE)
        d = e.check_tool(
            "run_cli", {"command": "echo x > antnest_loop.py"}, required=PermLevel.EXECUTE
        )
        self.assertEqual(d.action, Action.ASK)
        self.assertEqual(d.scope, "self_source")

    def test_read_only_command_on_self_source_is_not_flagged(self):
        """只读命令不应因为提到核心文件名就触发自源码门禁。"""
        e = _engine(level=PermLevel.EXECUTE, ask_above=PermLevel.EXECUTE)
        d = e.check_tool(
            "run_cli", {"command": "cat antnest_loop.py"}, required=PermLevel.EXECUTE
        )
        self.assertTrue(d.allowed, "只读查看核心源码被误拦")

    def test_unregistered_tool_denied(self):
        e = _engine(level=PermLevel.SELF_MOD, ask_above=PermLevel.SELF_MOD)
        d = e.check_tool("mystery_tool", {}, required=PermLevel.READ, registered=False)
        self.assertEqual(d.action, Action.DENY)


class DangerPatternTest(unittest.TestCase):
    def test_blocks_system_level_destructive(self):
        for cmd in (
            "rm -rf /", "rm -rf C:/", "rm -rf ~", "rm -rf ..", "rm -rf .",
            "rm -rf *", "del /sqf C:\\Windows", "format c:", "shutdown /s",
            "mkfs.ext4 /dev/sda", "dd if=/dev/zero of=/dev/sda",
            "Remove-Item -Recurse -Force C:\\temp",
        ):
            with self.subTest(cmd=cmd):
                hit, why = match_danger(cmd)
                self.assertTrue(hit, f"应拦截：{cmd}")
                self.assertTrue(why)

    def test_allows_project_relative(self):
        for cmd in ("rm -rf node_modules", "rm -r build", "rm -rf ./dist"):
            with self.subTest(cmd=cmd):
                hit, _ = match_danger(cmd)
                self.assertFalse(hit, f"项目内相对路径不应拦截：{cmd}")

    def test_write_signal_detection(self):
        self.assertTrue(has_write_signal("open('a.py','w')"))
        self.assertTrue(has_write_signal("Set-Content a.py 'x'"))
        self.assertTrue(has_write_signal("rm a.py"))
        self.assertTrue(has_write_signal("shutil.rmtree('x')"))
        self.assertFalse(has_write_signal("cat a.py"))
        self.assertFalse(has_write_signal("pytest -q"))


class GrantLifecycleTest(unittest.TestCase):
    """授权按作用域签发，不按工具签发。"""

    def test_grant_once_allows_specific_scope(self):
        e = _engine(level=PermLevel.READ, ask_above=PermLevel.READ)
        target = str(ROOT / "antnest_config.py")
        self.assertEqual(e.check_write_path(target).action, Action.ASK)
        e.grant_once(f"write:{_norm(target)}")
        self.assertTrue(e.check_write_path(target).allowed)

    def test_grant_does_not_leak_to_other_paths(self):
        e = _engine(level=PermLevel.READ, ask_above=PermLevel.READ)
        e.grant_task(f"write:{_norm(str(ROOT / 'antnest_config.py'))}")
        other = e.check_write_path(str(ROOT / "antnest_loop.py"))
        self.assertEqual(other.action, Action.ASK, "授权泄漏到了别的文件")

    def test_reset_turn_clears_once_keeps_task(self):
        e = _engine(level=PermLevel.READ, ask_above=PermLevel.READ)
        e.grant_once("write:a")
        e.grant_task("write:b")
        e.reset_turn()
        self.assertEqual(e.grants()["once"], [])
        self.assertEqual(e.grants()["task"], ["write:b"])

    def test_reset_task_clears_everything(self):
        e = _engine()
        e.grant_once("write:a")
        e.grant_task("write:b")
        e.reset_task()
        self.assertEqual(e.grants(), {"once": [], "task": []})

    def test_self_mod_approved_via_callback(self):
        target = str(ROOT / "antnest_config.py")
        denied = _engine(level=PermLevel.NETWORK, approved=False).check_write_path(target)
        self.assertEqual(denied.action, Action.ASK)
        approved = _engine(level=PermLevel.NETWORK, approved=True).check_write_path(target)
        self.assertTrue(approved.allowed, "已获批准后仍要求再次确认")


class DangerNotSandboxTest(unittest.TestCase):
    """把安全边界钉死在测试里：权限拦截不是强 Sandbox。"""

    def test_danger_patterns_do_not_catch_obfuscated_payload(self):
        """危险模式只是提示，不是控制。

        一段看起来无害、实际做坏事的 Python 不该被宣称「已拦截」——
        这个用例存在的意义是防止文档/UI 夸大权限模型的能力。
        """
        cmd = "python - <<'EOF'\nimport shutil; shutil.rmtree('C:/Windows')\nEOF"
        hit, _ = match_danger(cmd)
        self.assertFalse(hit, "若此项变为 True，说明防线强度发生了变化，需重估文档措辞")


class DescribeGrantsTest(unittest.TestCase):
    def test_self_mod_never_granted(self):
        rows = {r["level"]: r for r in describe_grants(PermLevel.SELF_MOD)}
        self.assertFalse(rows[5]["granted"], "L5 不应显示为「已授予」")

    def test_system_never_granted(self):
        rows = {r["level"]: r for r in describe_grants(PermLevel.SELF_MOD)}
        self.assertFalse(rows[4]["granted"], "L4 不应显示为「已授予」")

    def test_low_levels_reflect_level(self):
        rows = {r["level"]: r for r in describe_grants(PermLevel.WRITE)}
        self.assertTrue(rows[0]["granted"])
        self.assertTrue(rows[1]["granted"])
        self.assertFalse(rows[2]["granted"])


class ConfigSchemaPermissionsTest(unittest.TestCase):
    """config.json → permissions 段的校验。"""

    def test_defaults_preserve_v131_behavior(self):
        import antnest_config_schema as s

        cfg = s.validate_config({})
        self.assertEqual(cfg.permissions.level, 3)
        self.assertEqual(cfg.permissions.ask_above_level, 3)
        self.assertTrue(cfg.permissions.audit)
        self.assertTrue(cfg.permissions.checkpoint_enabled)
        self.assertEqual(cfg.permissions.checkpoint_keep, 20)
        self.assertEqual(cfg.problems, [])

    def test_reads_valid_section(self):
        import antnest_config_schema as s

        cfg = s.validate_config({
            "permissions": {"level": 0, "audit": "false", "checkpoint_keep": 5}
        })
        self.assertEqual(cfg.permissions.level, 0)
        self.assertFalse(cfg.permissions.audit)
        self.assertEqual(cfg.permissions.checkpoint_keep, 5)

    def test_clamps_out_of_range_level(self):
        import antnest_config_schema as s

        cfg = s.validate_config({"permissions": {"level": 99}})
        self.assertEqual(cfg.permissions.level, 3, "越界等级应回退默认")
        self.assertTrue(any("permissions.level" in p for p in cfg.problems))

    def test_reports_unknown_keys(self):
        import antnest_config_schema as s

        cfg = s.validate_config({"permissions": {"nope": 1}})
        self.assertIn("nope", cfg.unknown_permissions_keys)

    def test_ask_above_inconsistent_is_flagged(self):
        import antnest_config_schema as s

        cfg = s.validate_config({"permissions": {"level": 1, "ask_above_level": 3}})
        self.assertTrue(
            any("ask_above_level" in p for p in cfg.problems),
            "阈值高于授予等级应给出提示（否则用户以为在收紧，实际在放宽）",
        )

    def test_non_dict_section_is_survivable(self):
        import antnest_config_schema as s

        cfg = s.validate_config({"permissions": "oops"})
        self.assertEqual(cfg.permissions.level, 3)
        self.assertTrue(any("permissions" in p for p in cfg.problems))


def _norm(path: str) -> str:
    import os
    from pathlib import Path as P

    try:
        return os.path.normcase(os.path.normpath(str(P(path).expanduser().resolve())))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(str(path)))


if __name__ == "__main__":
    unittest.main()
