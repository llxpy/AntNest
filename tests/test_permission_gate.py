# -*- coding: utf-8 -*-
"""AntNest · 权限闸门接线测试（端到端驱动 agent_single_loop）

Step 1 交付了权限模型，Step 2 交付了注册表，本文件验证两者**真的接进了
agent_single_loop**——也就是全仓库唯一的工具派发点（antnest_loop.py）。

这些用例用假 LLM 驱动真实循环，不需要网络与 API key 往返，但走的是真实的
权限判定、真实的工具结果回灌、真实的 break_loop 逻辑。
"""
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mock_models_open(*_args, **_kwargs):
    body = json.dumps({"data": [{"id": "deepseek-v4-flash", "context_length": 128000}]}).encode()

    class _Resp:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    return _Resp()


def _tool_call(name: str, args: dict, tid: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": tid,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }


def _usage() -> dict:
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}

def _llm_result(msg, usage):
    """llm_chat_stream 返回 (message, usage, finish_reason)。

    第三位按有无 tool_calls 给真实值：统一填 "stop" 会在将来有测试
    真的走失败归因时喂进一个假的 finish_reason。
    """
    return msg, usage, ("tool_calls" if (msg or {}).get("tool_calls") else "stop")



class _Harness:
    """把 agent_single_loop 跑起来，接住执行过的工具与最终 messages。"""

    def __init__(self, an, script, tools_whitelist=None):
        self.an = an
        self.script = list(script)
        self.executed: list[tuple[str, dict]] = []
        self.iterations = 0

    def _llm(self, _messages, tools=None):
        self.iterations += 1
        if self.script:
            return _llm_result(self.script.pop(0), _usage())
        return _llm_result({"role": "assistant", "content": "已结束"}, _usage())

    def run(self):
        an = self.an
        an.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "测试任务"},
        ]
        an.AGENT_CANCEL = False
        an.COMPACT_PANIC = False
        an.TOKEN_CAP = 1_000_000
        an.COMPACT_THRESH = 0.99
        an.agent_reset_cancel()

        # 记录真实被调用的执行器（把 tool_executors 换成记账包装）
        real = dict(an.tool_executors)
        for name, fn in real.items():
            def _wrap(_fn=fn, _name=name):
                def _inner(**kw):
                    self.executed.append((_name, kw))
                    return _fn(**kw)
                return _inner
            an.tool_executors[name] = _wrap()

        os.environ["ANT_MAX_ROUNDS"] = "4"
        try:
            with mock.patch.object(an, "llm_chat_stream", self._llm):
                an.agent_single_loop()
        finally:
            an.tool_executors.clear()
            an.tool_executors.update(real)
        return an.messages


class PermissionGateWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)

    def setUp(self):
        self.an.PERMISSION_ENGINE.reset_task()
        self.an.PERMISSION_ENGINE.reset_turn()
        self.an.mod_level_backup = self.an.PERMISSION_ENGINE.policy.level

    # ---------------- 参数敏感拦截（核心） ----------------

    def test_self_source_write_is_not_executed(self):
        """硬约束 C2 的端到端验证：写自身源码必须被拦下，且执行器不得被调用。"""
        h = _Harness(self.an, [
            _tool_call("write_file", {
                "path": str(ROOT / "antnest_config.py"),
                "content": "# 被劫持\n",
            }),
            {"role": "assistant", "content": "好的，等你确认"},
        ])
        messages = h.run()
        executed_names = [n for n, _ in h.executed]
        self.assertNotIn(
            "write_file", executed_names,
            "写自身源码的 write_file 被真正执行了——这是安全回归",
        )
        blob = json.dumps(messages, ensure_ascii=False)
        self.assertIn("approval_required", blob)
        self.assertIn("核心源码", blob)

    def test_self_source_replace_is_not_executed(self):
        h = _Harness(self.an, [
            _tool_call("search_replace", {
                "path": str(ROOT / "antnest_loop.py"),
                "old_string": "def agent_single_loop", "new_string": "def hacked",
            }),
            {"role": "assistant", "content": "等待确认"},
        ])
        h.run()
        self.assertNotIn("search_replace", [n for n, _ in h.executed])

    def test_dangerous_command_is_not_executed(self):
        h = _Harness(self.an, [
            _tool_call("spawn_clone", {"command": "rm -rf /"}),
            {"role": "assistant", "content": "该命令太危险"},
        ])
        h.run()
        self.assertNotIn("spawn_clone", [n for n, _ in h.executed])
        blob = json.dumps(self.an.messages, ensure_ascii=False)
        self.assertIn("高危", blob)

    def test_shell_redirect_to_self_source_blocked(self):
        """绕过 write_file 直接拼 shell 写核心源码也必须拦下。"""
        h = _Harness(self.an, [
            _tool_call("spawn_clone", {"command": "echo hacked > antnest_loop.py"}),
            {"role": "assistant", "content": "等待确认"},
        ])
        h.run()
        self.assertNotIn("spawn_clone", [n for n, _ in h.executed])

    # ---------------- 正常路径不受影响 ----------------

    def test_normal_tool_still_executes(self):
        h = _Harness(self.an, [
            _tool_call("list_dir", {"path": ".", "max_entries": 3}),
            {"role": "assistant", "content": "看完了"},
        ])
        h.run()
        self.assertIn("list_dir", [n for n, _ in h.executed])

    def test_read_only_command_on_self_source_executes(self):
        """只读查看核心源码不应被拦——过度拦截会让 Agent 无法工作。"""
        h = _Harness(self.an, [
            _tool_call("spawn_clone", {"command": "cat antnest_loop.py"}),
            {"role": "assistant", "content": "读完了"},
        ])
        h.run()
        self.assertIn("spawn_clone", [n for n, _ in h.executed])

    # ---------------- fail-closed ----------------

    def test_unregistered_tool_is_denied(self):
        """未登记的工具必须 fail-closed，否则新增工具忘记登记就会被静默放行。"""
        self.an.tool_executors["mystery_tool"] = lambda **kw: json.dumps({"status": "ok"})
        try:
            h = _Harness(self.an, [
                _tool_call("mystery_tool", {"x": 1}),
                {"role": "assistant", "content": "被拒了"},
            ])
            h.run()
            self.assertNotIn("mystery_tool", [n for n, _ in h.executed])
            self.assertIn("fail-closed", json.dumps(self.an.messages, ensure_ascii=False))
        finally:
            self.an.tool_executors.pop("mystery_tool", None)

    # ---------------- 状态词汇表 ----------------

    def test_denied_is_not_reported_as_ok(self):
        from antnest_loop import NON_OK_STATUS, _result_status

        self.assertIn("denied", NON_OK_STATUS)
        self.assertEqual(
            _result_status(json.dumps({"status": "denied"})), "denied",
            "被拒绝的操作被记成 ok，审计与统计会说谎",
        )
        self.assertEqual(_result_status(json.dumps({"status": "ok"})), "ok")
        self.assertEqual(_result_status("not json"), "ok")

    # ---------------- 降权模式 ----------------

    def test_level_zero_blocks_writes_and_network(self):
        """level=0（只读审阅模式）应拦住写入与联网。"""
        from antnest_permissions import PermLevel, PermissionEngine, PermissionsConfig, PermissionPolicy

        engine = PermissionEngine(
            PermissionsConfig(level=PermLevel.READ),
            PermissionPolicy(
                level=PermLevel.READ,
                ask_above_level=PermLevel.READ,
                self_source_names=frozenset({"antnest_config.py"}),
                self_source_root=str(ROOT),
            ),
        )
        self.assertTrue(
            engine.check_tool("view_file", {"path": "a.py"}, required=PermLevel.READ).allowed
        )
        self.assertEqual(
            engine.check_tool("write_file", {"path": "a.py"}, required=PermLevel.WRITE).status,
            "denied",
        )
        self.assertEqual(
            engine.check_tool("web_fetch", {"url": "https://x"}, required=PermLevel.NETWORK).status,
            "denied",
        )

    # ---------------- 结构完整性 ----------------

    def test_denied_call_leaves_no_dangling_tool_message(self):
        """闸门 break_loop 后，每个 tool_call 必须仍有 tool 响应，
        否则下一轮会被 API 以 insufficient tool messages 400 拒绝。"""
        h = _Harness(self.an, [
            _tool_call("write_file", {
                "path": str(ROOT / "antnest_config.py"), "content": "x",
            }),
            {"role": "assistant", "content": "等确认"},
        ])
        messages = h.run()
        responded = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    self.assertIn(tc["id"], responded, f"{tc['id']} 缺少 tool 响应")

    def test_denied_does_not_trigger_reflection_prompt(self):
        """被拒调用不应触发「换个方法重试」的自我反思提示。"""
        h = _Harness(self.an, [
            _tool_call("write_file", {
                "path": str(ROOT / "antnest_config.py"), "content": "x",
            }),
            {"role": "assistant", "content": "被拒"},
        ])
        messages = h.run()
        blob = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("自我反思", blob, "被硬拒绝的操作触发了「换方法重试」引导")

    def test_loop_terminates_on_denial(self):
        """拒绝后必须立刻结束循环，而不是让模型在同一批次里反复试。

        approval_required / denied 都会置 break_loop=True 退出 while 循环，
        因此后续的脚本消息不会被消费——这正是期望行为（与 v1.3.1 的
        自身源码确认流程一致）。
        """
        h = _Harness(self.an, [
            _tool_call("write_file", {
                "path": str(ROOT / "antnest_config.py"), "content": "x",
            }, tid="call_1"),
            _tool_call("write_file", {
                "path": str(ROOT / "antnest_config.py"), "content": "y",
            }, tid="call_2"),
            {"role": "assistant", "content": "停止"},
        ])
        h.run()
        self.assertEqual(h.iterations, 1, "拒绝后仍在同一回合继续派发工具")
        self.assertEqual(len(h.executed), 0, "有工具在拒绝后仍被执行了")

    def test_queen_write_file_gate_returns_approval(self):
        """直接调用 write_file（不经 loop）也必须被门禁拦下。"""
        out = json.loads(self.an.write_file(str(ROOT / "antnest_config.py"), "x"))
        self.assertEqual(out["status"], "approval_required")
        self.assertEqual(out["scope"], "self_source")


class SpawnCloneGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)

    def setUp(self):
        self.an.PERMISSION_ENGINE.reset_task()
        os.environ.pop("AN_DEPTH", None)

    def test_danger_command_gated_before_spawn(self):
        out = json.loads(self.an.spawn_clone("rm -rf /"))
        self.assertEqual(out["status"], "approval_required")
        self.assertEqual(out["scope"], "danger_command")

    def test_self_source_command_gated(self):
        out = json.loads(self.an.spawn_clone("echo x > antnest_loop.py"))
        self.assertEqual(out["status"], "approval_required")
        self.assertEqual(out["scope"], "self_source")

    def test_depth_block_still_precedes_permission(self):
        """深度硬拦截必须仍在权限判定之前（它更基础）。"""
        os.environ["AN_DEPTH"] = "99"
        try:
            out = json.loads(self.an.spawn_clone("echo hi"))
            self.assertEqual(out["status"], "blocked")
            self.assertIn("深度限制", out["error"])
        finally:
            os.environ.pop("AN_DEPTH", None)

    def test_no_blocking_stdin_in_ui_mode(self):
        """回归：UI 模式下绝不能调用 input()。

        旧实现在管理员模式对每条危险命令调 get_user_confirmation()，其内部是
        input()。桌面 UI 的 _turn 跑在本进程线程里，用户没有任何界面能应答，
        会冻结整个 Agent。
        """
        an = self.an
        with mock.patch.object(an, "admin_utils") as fake_admin:
            fake_admin.analyze_command.return_value = {
                "is_dangerous": True, "confirmation_required": True, "description": "x",
            }
            with mock.patch.object(an, "_admin_info", {"is_admin": True}):
                with mock.patch.object(an, "ALLOW_ALL_CLI", False):
                    with mock.patch("builtins.input", side_effect=AssertionError("阻塞式 input 被调用了")):
                        out = json.loads(an.spawn_clone("format c:"))
        self.assertEqual(out["status"], "approval_required")
        fake_admin.get_user_confirmation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
