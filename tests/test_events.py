# -*- coding: utf-8 -*-
"""AntNest · 事件日志测试

三条不可让步的约束各有专门用例：

  1. **fail-open 但首次失败要出声**（硬约束 C4）。若写盘静默失败，事件系统
     看起来和「正常没记录」一模一样——那等于没有。
  2. **绝不落盘推理内容 / 凭据**。与 antnest_runtime_state 的既有约定一致。
  3. **事件与状态同临界区**。否则 UI 会观察到「有状态无事件」，时间线不可重放。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import antnest_events as ev  # noqa: E402


def _tmp_log() -> ev.EventLog:
    d = tempfile.mkdtemp(prefix="antnest_evtest_")
    log = ev.EventLog(d)
    log._test_tmp = d  # type: ignore[attr-defined]
    return log


def _cleanup(log: ev.EventLog) -> None:
    tmp = getattr(log, "_test_tmp", None)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


class EventRecordTest(unittest.TestCase):
    def setUp(self):
        self.log = _tmp_log()

    def tearDown(self):
        _cleanup(self.log)

    def test_seq_is_monotonic(self):
        a = self.log.emit(ev.Event.TASK_CREATED, task_id="t1", goal="x")
        b = self.log.emit(ev.Event.TASK_STARTED, task_id="t1")
        c = self.log.emit(ev.Event.TASK_COMPLETED, task_id="t1")
        self.assertEqual([a.seq, b.seq, c.seq], [1, 2, 3])

    def test_roundtrip_through_disk(self):
        self.log.emit(ev.Event.WORKER_STARTED, task_id="t1", worker_id=7, task="扫描")
        rows = self.log.records("t1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event, "WORKER_STARTED")
        self.assertEqual(rows[0].worker_id, 7)
        self.assertEqual(rows[0].data["task"], "扫描")

    def test_one_file_per_task(self):
        self.log.emit(ev.Event.TASK_CREATED, task_id="t_a", goal="A")
        self.log.emit(ev.Event.TASK_CREATED, task_id="t_b", goal="B")
        self.assertEqual(sorted(self.log.tasks()), ["t_a", "t_b"])
        self.assertEqual(len(self.log.records("t_a")), 1)
        self.assertEqual(len(self.log.records("t_b")), 1)

    def test_task_id_falls_back_to_adhoc(self):
        ev.clear_current_task_id()
        self.log.emit(ev.Event.TASK_CREATED, goal="无任务上下文")
        self.assertIn("adhoc", self.log.tasks())

    def test_task_id_isolation(self):
        ev.set_current_task_id("t_ctx")
        try:
            self.log.emit(ev.Event.LOOP_ROUND, round=1)
            self.assertEqual(self.log.records("t_ctx")[0].event, "LOOP_ROUND")
        finally:
            ev.clear_current_task_id()


class PrivacyTest(unittest.TestCase):
    """绝不落盘推理内容与凭据。"""

    def setUp(self):
        self.log = _tmp_log()

    def tearDown(self):
        _cleanup(self.log)

    def test_reasoning_content_is_redacted(self):
        self.log.emit(
            ev.Event.TOOL_CALL, task_id="t1", tool="spawn_clone",
            args={"command": "ls"},
            reasoning_content="模型的秘密思维链，不该落盘",
            thinking="另一个不该落盘的字段",
        )
        raw = Path(self.log._path("t1")).read_text(encoding="utf-8")
        self.assertNotIn("秘密思维链", raw)
        self.assertNotIn("不该落盘", raw)
        self.assertIn("已省略", raw)

    def test_credentials_are_redacted(self):
        self.log.emit(
            ev.Event.TASK_CREATED, task_id="t1",
            api_key="sk-should-never-persist", password="hunter2", secret="s3cr3t",
        )
        raw = Path(self.log._path("t1")).read_text(encoding="utf-8")
        for leak in ("sk-should-never-persist", "hunter2", "s3cr3t"):
            self.assertNotIn(leak, raw)

    def test_long_strings_are_truncated(self):
        self.log.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x", args={"c": "A" * 5000})
        row = self.log.records("t1")[0]
        self.assertLess(len(row.data["args"]["c"]), 600)
        self.assertIn("chars)", row.data["args"]["c"])

    def test_deep_structures_are_capped(self):
        self.log.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x",
                      args={"items": list(range(500))})
        row = self.log.records("t1")[0]
        self.assertLessEqual(len(row.data["args"]["items"]), 30)


class FailOpenTest(unittest.TestCase):
    def setUp(self):
        self.log = _tmp_log()

    def tearDown(self):
        _cleanup(self.log)

    def test_emit_never_raises_on_bad_dir(self):
        """写盘失败时 emit 仍应返回记录——事件本身发生了，
        只是没能持久化。返回 None 会让调用方误以为事件没产生。"""
        broken = ev.EventLog(os.path.join("Z:\\", "nonexistent", "\x00bad"))
        try:
            rec = broken.emit(ev.Event.TASK_CREATED, task_id="t1", goal="x")
        except Exception as e:  # pragma: no cover
            self.fail(f"事件系统抛异常了，会把 Agent 循环搞崩：{e}")
        else:
            self.assertIsNotNone(rec)
            self.assertTrue(broken.stats()["degraded"])

    def test_stats_never_raises_on_bad_dir(self):
        broken = ev.EventLog(os.path.join("Z:\\", "nonexistent", "\x00bad"))
        try:
            stats = broken.stats()
        except Exception as e:  # pragma: no cover
            self.fail(f"stats() 抛异常：{e}")
        else:
            self.assertEqual(stats["seq"], 0)

    def test_replay_never_raises_on_bad_dir(self):
        broken = ev.EventLog(os.path.join("Z:\\", "nonexistent", "\x00bad"))
        self.assertIsInstance(broken.replay("t1"), str)

    def test_first_write_failure_warns_once(self):
        broken = ev.EventLog(os.path.join("Z:\\", "nonexistent", "\x00bad"))
        with self.assertLogs("antnest.events", level="WARNING") as cap:
            broken.emit(ev.Event.TASK_CREATED, task_id="t1")
        self.assertTrue(any("事件日志写入失败" in m for m in cap.output))

    def test_second_failure_does_not_warn_again(self):
        """首次失败必须出声（否则静默丢弃不可见），但不能刷屏。"""
        broken = ev.EventLog(os.path.join("Z:\\", "nonexistent", "\x00bad"))
        with self.assertLogs("antnest.events", level="WARNING") as cap:
            for _ in range(5):
                broken.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x")
        warnings = [m for m in cap.output if "事件日志写入失败" in m]
        self.assertEqual(len(warnings), 1, f"应只警告一次，实际 {len(warnings)} 次")

    def test_degraded_flag_is_exposed(self):
        broken = ev.EventLog(os.path.join("Z:\\", "nonexistent", "\x00bad"))
        self.assertFalse(broken.stats()["degraded"])
        broken.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x")
        self.assertTrue(broken.stats()["degraded"])


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.log = _tmp_log()

    def tearDown(self):
        _cleanup(self.log)

    def test_rotates_when_file_too_large(self):
        path = self.log._path("t1")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * (ev.FILE_MAX_BYTES + 10))
        self.log.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x")
        self.assertTrue(os.path.exists(path + ".1"), "超限未轮转")
        self.assertLess(os.path.getsize(path), ev.FILE_MAX_BYTES)

    def test_prunes_old_task_files(self):
        for i in range(ev.TASKS_MAX + 5):
            self.log.emit(ev.Event.TASK_CREATED, task_id=f"t{i:03d}", goal="g")
        self.assertLessEqual(len(self.log.tasks()), ev.TASKS_MAX + 1)


class QueryReplayTest(unittest.TestCase):
    def setUp(self):
        self.log = _tmp_log()
        self.log.emit(ev.Event.TASK_CREATED, task_id="t1", goal="修复测试失败")
        self.log.emit(ev.Event.WORKER_STARTED, task_id="t1", worker_id=1, task="扫描项目")
        self.log.emit(ev.Event.TOOL_CALL, task_id="t1", tool="spawn_clone",
                      args={"command": "pytest -q"})
        self.log.emit(ev.Event.WORKER_COMPLETED, task_id="t1", worker_id=1, status="ok")
        self.log.emit(ev.Event.TASK_COMPLETED, task_id="t1")

    def tearDown(self):
        _cleanup(self.log)

    def test_records_ordered_by_seq(self):
        seqs = [r.seq for r in self.log.records("t1")]
        self.assertEqual(seqs, sorted(seqs))

    def test_query_by_event(self):
        rows = self.log.query(task_id="t1", event=ev.Event.WORKER_COMPLETED)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].data["status"], "ok")

    def test_query_by_since_seq(self):
        rows = self.log.query(task_id="t1", since_seq=3)
        self.assertEqual(len(rows), 2)

    def test_replay_is_human_readable(self):
        text = self.log.replay("t1")
        self.assertIn("TASK_CREATED", text)
        self.assertIn("WORKER_COMPLETED", text)
        self.assertIn("goal=修复测试失败", text)
        self.assertIn("worker=1", text)
        # 路线图 §5 要求的形态：时间 + 序号 + 事件
        self.assertRegex(text, r"\d{2}:\d{2}:\d{2}\s+#\d{4}")

    def test_replay_empty_task(self):
        self.assertIn("没有事件记录", self.log.replay("t_never_existed"))

    def test_replay_limit(self):
        self.assertEqual(len(self.log.replay("t1", limit=2).splitlines()), 2)

    def test_replay_never_leaks_reasoning(self):
        self.log.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x",
                      reasoning_content="不该出现在时间线里")
        self.assertNotIn("不该出现在时间线里", self.log.replay("t1"))


class ThreadSafetyTest(unittest.TestCase):
    """Agent 线程与 UI 线程并发写。"""

    def setUp(self):
        self.log = _tmp_log()

    def tearDown(self):
        _cleanup(self.log)

    def test_concurrent_emit_produces_unique_seq(self):
        errors: list[BaseException] = []

        def _work(n: int):
            try:
                for i in range(40):
                    self.log.emit(ev.Event.TOOL_CALL, task_id="t1",
                                  tool=f"tool{i % 5}", args={"n": n * 100 + i})
            except BaseException as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=_work, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [], "并发 emit 抛异常")
        rows = self.log.records("t1")
        seqs = [r.seq for r in rows]
        self.assertEqual(len(seqs), len(set(seqs)), "并发下 seq 出现重复")
        self.assertEqual(len(seqs), 240)

    def test_file_lines_match_records(self):
        for i in range(20):
            self.log.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x", args={"i": i})
        path = self.log._path("t1")
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertEqual(len(lines), 20)
        for ln in lines:
            json.loads(ln)  # 每行都必须是合法 JSON


class ListenerTest(unittest.TestCase):
    def setUp(self):
        self.log = _tmp_log()
        ev.reset(self.log.base_dir)

    def tearDown(self):
        _cleanup(self.log)
        ev.reset("")

    def test_subscribe_receives_events(self):
        got: list[ev.EventRecord] = []
        fn = got.append
        ev.subscribe(fn)
        try:
            ev.emit(ev.Event.TASK_CREATED, task_id="t9", goal="x")
        finally:
            ev.unsubscribe(fn)
        self.assertTrue(any(r.event == "TASK_CREATED" for r in got))

    def test_listener_exception_does_not_propagate(self):
        def bad(_rec):
            raise RuntimeError("UI 崩了")

        ev.subscribe(bad)
        try:
            rec = ev.emit(ev.Event.TASK_CREATED, task_id="t9")
            self.assertIsNotNone(rec, "监听器异常反过来搞崩了事件系统")
        finally:
            ev.unsubscribe(bad)

    def test_unsubscribe_stops_delivery(self):
        got: list[ev.EventRecord] = []
        fn = got.append
        ev.subscribe(fn)
        ev.emit(ev.Event.TASK_CREATED, task_id="t9")
        ev.unsubscribe(fn)
        before = len(got)
        ev.emit(ev.Event.TASK_STARTED, task_id="t9")
        self.assertEqual(len(got), before)


class HelperTest(unittest.TestCase):
    def test_worker_helper_does_not_duplicate_worker_id(self):
        """回归：worker() 曾把 worker_id 同时塞进 data 和 emit 的具名参数，
        导致 TypeError: got multiple values for keyword argument。"""
        log = _tmp_log()
        try:
            rec = log.emit(ev.Event.WORKER_COMPLETED, worker_id=3, task="扫描", status="ok")
            self.assertIsNotNone(rec)
            self.assertEqual(rec.worker_id, 3)
            self.assertNotIn("worker_id", rec.data)
        finally:
            _cleanup(log)

    def test_permission_helper(self):
        log = _tmp_log()
        try:
            ev.permission(ev.Event.PERMISSION_DENIED, tool="write_file",
                          action="deny", reason="越权", scope="self_source",
                          level="L5", log=log)
        finally:
            _cleanup(log)

    def test_event_categories(self):
        self.assertEqual(ev.Event.TASK_CREATED.category, "task")
        self.assertEqual(ev.Event.WORKER_STARTED.category, "worker")
        self.assertEqual(ev.Event.PERMISSION_DENIED.category, "permission")

    def test_all_events_unique(self):
        values = [e.value for e in ev.Event]
        self.assertEqual(len(values), len(set(values)))

    def test_new_task_id_shape(self):
        tid = ev.new_task_id()
        self.assertTrue(tid.startswith("t_"))
        self.assertEqual(len(tid), 10)
        self.assertNotEqual(tid, ev.new_task_id())


class NonOkStatusVocabularyTest(unittest.TestCase):
    """事件与工具结果的 status 词汇表必须一致，否则重放会说谎。"""

    def test_permission_status_matches_event_vocabulary(self):
        from antnest_permissions import (
            Action, PermLevel, decide,
        )

        ask = decide(PermLevel.SELF_MOD, level=PermLevel.READ,
                     ask_above_level=PermLevel.READ, explicit_confirm=True)
        deny = decide(PermLevel.SELF_MOD, level=PermLevel.READ,
                      ask_above_level=PermLevel.READ)
        self.assertEqual(ask.status, "approval_required")
        self.assertEqual(deny.status, "denied")
        self.assertEqual(Action.ALLOW.status if hasattr(Action.ALLOW, "status") else "ok", "ok")

    def test_loop_non_ok_includes_denied(self):
        from antnest_loop import NON_OK_STATUS

        for status in ("error", "blocked", "approval_required", "denied"):
            self.assertIn(status, NON_OK_STATUS)


if __name__ == "__main__":
    unittest.main()
