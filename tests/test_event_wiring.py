# -*- coding: utf-8 -*-
"""AntNest · 事件日志接线测试（端到端）

验证「跑一次真实回合 → 产出一条可重放的时间线」，也就是路线图 §5 的直接收益：

    21:03:01 Queen 创建任务
    21:03:02 创建 Worker #1
    21:03:05 Worker #1 完成
    ...
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import antnest_events as ev  # noqa: E402


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


def _tool_call(name: str, args: dict | None = None, tid: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": tid, "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args or {}, ensure_ascii=False),
            },
        }],
    }


def _usage() -> dict:
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}


class EventWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="antnest_evwire_")
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)
        # 把事件日志重定向到临时目录，避免污染仓库的 .antnest
        cls.log = ev.reset(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        ev.reset("")
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self.an.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "扫描项目并跑测试"},
        ]
        self.an.AGENT_CANCEL = False
        self.an.COMPACT_PANIC = False
        self.an.TOKEN_CAP = 1_000_000
        self.an.COMPACT_THRESH = 0.99
        self.an.agent_reset_cancel()
        self.an.PERMISSION_ENGINE.reset_task()
        # 每个用例独立 task_id：事件是按任务落盘的，共用 id 会互相污染断言
        self.tid = f"t_{self._testMethodName}"
        ev.set_current_task_id(self.tid)
        os.environ["ANT_MAX_ROUNDS"] = "4"

    def tearDown(self):
        ev.clear_current_task_id()

    def _run(self, script):
        it = iter(script)

        def _llm(_messages, tools=None):
            try:
                return next(it), _usage()
            except StopIteration:
                return {"role": "assistant", "content": "完成"}, _usage()

        with mock.patch.object(self.an, "llm_chat_stream", _llm):
            self.an.agent_single_loop()
        return self.log.records(self.tid)

    # ---------------- 基本接线 ----------------

    def test_tool_call_and_result_are_recorded(self):
        rows = self._run([
            _tool_call("list_tools"),
            {"role": "assistant", "content": "看完了"},
        ])
        kinds = [r.event for r in rows]
        self.assertIn("TOOL_CALL", kinds)
        self.assertIn("TOOL_RESULT", kinds)
        call = next(r for r in rows if r.event == "TOOL_CALL")
        self.assertEqual(call.data["tool"], "list_tools")
        result = next(r for r in rows if r.event == "TOOL_RESULT")
        self.assertEqual(result.data["status"], "ok")
        self.assertIn("duration_ms", result.data)

    def test_loop_rounds_are_recorded(self):
        rows = self._run([{"role": "assistant", "content": "直接回答"}])
        rounds = [r for r in rows if r.event == "LOOP_ROUND"]
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].data["round"], 1)

    def test_permission_denial_is_recorded(self):
        rows = self._run([
            _tool_call("write_file", {
                "path": str(ROOT / "antnest_config.py"), "content": "x",
            }),
            {"role": "assistant", "content": "等确认"},
        ])
        kinds = [r.event for r in rows]
        self.assertIn("PERMISSION_REQUESTED", kinds)
        req = next(r for r in rows if r.event == "PERMISSION_REQUESTED")
        self.assertEqual(req.data["tool"], "write_file")
        self.assertEqual(req.data["scope_name"], "self_source")
        self.assertEqual(req.data["action"], "ask")

    def test_hard_denial_is_recorded_as_denied(self):
        """level 降到 L0 时写文件应是硬拒绝（denied），不是询问。"""
        engine = self.an.PERMISSION_ENGINE
        from antnest_permissions import PermLevel, PermissionEngine, PermissionsConfig, PermissionPolicy

        saved = engine
        self.an.PERMISSION_ENGINE = PermissionEngine(
            PermissionsConfig(level=PermLevel.READ),
            PermissionPolicy(
                level=PermLevel.READ, ask_above_level=PermLevel.READ,
                self_source_names=frozenset({"antnest_config.py"}),
                self_source_root=str(ROOT),
            ),
        )
        try:
            rows = self._run([
                _tool_call("write_file", {"path": "notes.md", "content": "x"}),
                {"role": "assistant", "content": "被拒"},
            ])
        finally:
            self.an.PERMISSION_ENGINE = saved
        kinds = [r.event for r in rows]
        self.assertIn("PERMISSION_DENIED", kinds)
        denied = next(r for r in rows if r.event == "PERMISSION_DENIED")
        self.assertEqual(denied.data["action"], "deny")

    def test_max_rounds_is_recorded(self):
        script = [_tool_call("list_tools", tid=f"c{i}") for i in range(6)]
        rows = self._run(script)
        self.assertIn("LOOP_MAX_ROUNDS", [r.event for r in rows])

    def test_duplicate_call_is_recorded(self):
        call = _tool_call("list_tools", tid="dup")
        rows = self._run([call, call, call, {"role": "assistant", "content": "停"}])
        self.assertIn("LOOP_DUP_CALL", [r.event for r in rows])

    # ---------------- 重放 ----------------

    def test_replay_produces_timeline(self):
        self._run([
            _tool_call("list_tools"),
            {"role": "assistant", "content": "完成"},
        ])
        text = self.log.replay(self.tid)
        self.assertIn("LOOP_ROUND", text)
        self.assertIn("TOOL_CALL", text)
        self.assertIn("tool=list_tools", text)
        self.assertIn("TOOL_RESULT", text)
        self.assertRegex(text, r"\d{2}:\d{2}:\d{2}\s+#\d{4}")

    def test_replay_ordered_and_complete(self):
        rows = self._run([
            _tool_call("list_tools"),
            {"role": "assistant", "content": "完成"},
        ])
        seqs = [r.seq for r in rows]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_no_reasoning_in_persisted_events(self):
        """整条链路都不该把思维链写进事件文件。"""
        self._run([
            _tool_call("list_tools"),
            {"role": "assistant", "content": "完成", "reasoning_content": "内部思维链XYZ"},
        ])
        raw = Path(self.log._path(self.tid)).read_text(encoding="utf-8")
        self.assertNotIn("内部思维链XYZ", raw)

    # ---------------- 与 audit.log 的分工 ----------------

    def test_tool_events_no_longer_go_to_audit_log(self):
        """v1.4 分工：工具事件归 events，安全事件归 audit。

        断言的是**调用**而不是文件行数：audit.log 由 RotatingFileHandler 缓冲
        落盘，且 ANT_HOME 是共享目录，数行数会因写入缓冲和用例顺序而抖动。
        """
        calls = _capture_audit()
        try:
            self._run([
                _tool_call("list_tools"),
                {"role": "assistant", "content": "完成"},
            ])
        finally:
            calls.stop()
        methods = [c[0] for c in calls.calls]
        self.assertNotIn("log_tool_call", methods, "工具事件仍在写 audit.log（双写没消掉）")
        self.assertNotIn("log_tool_result", methods, "工具事件仍在写 audit.log（双写没消掉）")

    def test_security_events_still_reach_audit(self):
        calls = _capture_audit()
        try:
            self._run([
                _tool_call("write_file", {
                    "path": str(ROOT / "antnest_config.py"), "content": "x",
                }),
                {"role": "assistant", "content": "等确认"},
            ])
        finally:
            calls.stop()
        security = [c for c in calls.calls if c[0] == "log_security_event"]
        self.assertTrue(security, "权限事件没有落审计，安全决策不可追责")
        # log_security_event(event_type, detail) → detail 里应含工具名
        self.assertTrue(
            any("write_file" in str(c[-1]) for c in security),
            f"审计详情里没有工具名：{security}",
        )

    def test_security_event_is_not_also_a_tool_event(self):
        """权限拦截不应再产生 log_tool_call（那是工具事件，不是安全事件）。"""
        calls = _capture_audit()
        try:
            self._run([
                _tool_call("write_file", {
                    "path": str(ROOT / "antnest_config.py"), "content": "x",
                }),
                {"role": "assistant", "content": "等确认"},
            ])
        finally:
            calls.stop()
        self.assertNotIn("log_tool_call", [c[0] for c in calls.calls])


class _AuditSpy:
    """记录 AuditLogger 上被调用的方法，用于断言事件分工。"""

    def __init__(self, target):
        self.calls: list[tuple] = []
        self._target = target
        self._saved: dict = {}

    def _wrap(self, name: str):
        original = getattr(self._target, name)

        def _inner(*args, **kwargs):
            self.calls.append((name, *args))
            return original(*args, **kwargs)

        return _inner

    def start(self):
        for name in ("log_tool_call", "log_tool_result", "log_security_event"):
            if hasattr(self._target, name):
                self._saved[name] = getattr(self._target, name)
                setattr(self._target, name, self._wrap(name))
        return self

    def stop(self):
        for name, fn in self._saved.items():
            setattr(self._target, name, fn)
        self._saved.clear()


def _capture_audit() -> _AuditSpy:
    import antnest_log

    return _AuditSpy(antnest_log.get_audit()).start()


class WorkerWidBindingTest(unittest.TestCase):
    """wrapped_spawn 必须把 wid 写回结果，供 Plan 绑定节点↔工蚁。"""

    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import antnest_bridge as bridge

            cls.bridge = importlib.reload(bridge)

    def test_result_carries_wid(self):
        core = self.bridge.AntNestCore()
        core.ready = True
        core.mod = type("M", (), {})()
        core.mod.tool_executors = {"spawn_clone": lambda **kw: json.dumps(
            {"status": "ok", "task_id": "abc123", "output": "hi"}
        )}
        core.mod._ui_patched = False
        core.mod.UI_STREAM_CB = None
        core._patch_tools()
        out = json.loads(core.mod.tool_executors["spawn_clone"](
            command="echo hi", timeout=5, label="扫描"
        ))
        self.assertEqual(out["task_id"], "abc123")
        self.assertIn("wid", out, "结果里没有 wid，Plan 无从绑定节点与工蚁")
        self.assertIsInstance(out["wid"], int)


if __name__ == "__main__":
    unittest.main()
