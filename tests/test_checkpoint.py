# -*- coding: utf-8 -*-
"""AntNest · 检查点与恢复测试

最重要的用例是 RehydrateTest 里那几条：**朴素切片会产出被 API 拒绝的消息列表**。
这里逐条钉死，因为这是本模块最容易做错、且失败方式最隐蔽的地方
（ApiError 被 antnest_loop 的宽 except 吞掉 → 整轮无输出 → 静默失效）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import antnest_checkpoint as ck  # noqa: E402
import antnest_events as ev  # noqa: E402
from antnest_checkpoint import Checkpoint, CheckpointStore  # noqa: E402


SYSTEM = {"role": "system", "content": "当前有效的 system prompt"}


def _tc(tid, name="spawn_clone"):
    return {"id": tid, "type": "function",
            "function": {"name": name, "arguments": "{}"}}


def _tmp() -> str:
    d = tempfile.mkdtemp(prefix="antnest_ckpt_")
    ev.reset(d)
    ck.reset(d)
    return d


class StripCotTest(unittest.TestCase):
    def test_strips_reasoning_fields(self):
        msg = {
            "role": "assistant", "content": "答案",
            "reasoning_content": "秘密思维链",
            "thinking": "另一个",
            "tool_calls": [_tc("c1")],
        }
        out = ck.strip_cot(msg)
        self.assertNotIn("reasoning_content", out)
        self.assertNotIn("thinking", out)
        self.assertEqual(out["content"], "答案")
        self.assertIn("tool_calls", out)

    def test_strips_inside_multimodal_content(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图", "reasoning_content": "不该留"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        }
        out = ck.strip_cot(msg)
        self.assertNotIn("reasoning_content", out["content"][0])
        self.assertEqual(out["content"][0]["text"], "看图")

    def test_does_not_mutate_input(self):
        msg = {"role": "assistant", "content": "x", "reasoning_content": "y"}
        ck.strip_cot(msg)
        self.assertIn("reasoning_content", msg, "strip_cot 篡改了入参")

    def test_no_cot_on_disk(self):
        """端到端：思维链不得进检查点文件。"""
        d = _tmp()
        try:
            ck.save("x", task_id="t1", messages=[
                {"role": "system", "content": "s"},
                {"role": "assistant", "content": "a", "reasoning_content": "机密XYZ"},
            ])
            path = Path(ck.get_store()._path("t1", 1))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("机密XYZ", raw)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class RehydrateTest(unittest.TestCase):
    """rehydrate 而不是切片。"""

    def test_prepends_fresh_system(self):
        out = ck.rehydrate([{"role": "user", "content": "hi"}], SYSTEM)
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(out[0]["content"], "当前有效的 system prompt")

    def test_drops_stale_system_in_tail(self):
        """尾部里的旧 system 可能是几百轮前的版本，必须丢弃，
        否则会出现两条 system，且旧的那份带着过期的工具契约。"""
        out = ck.rehydrate(
            [{"role": "system", "content": "500 轮前的旧 prompt"},
             {"role": "user", "content": "hi"}],
            SYSTEM,
        )
        systems = [m for m in out if m.get("role") == "system"]
        self.assertEqual(len(systems), 1, "出现了多条 system 消息")
        self.assertEqual(systems[0]["content"], "当前有效的 system prompt")

    def test_drops_leading_orphan_tool_messages(self):
        """切点落在工具批次中间 → 列表以 role:tool 开头，父 assistant 已丢失。
        OpenAI 兼容端点会直接 400。"""
        tail = [
            {"role": "tool", "tool_call_id": "c1", "content": "结果"},
            {"role": "tool", "tool_call_id": "c2", "content": "结果"},
            {"role": "user", "content": "继续"},
        ]
        out = ck.rehydrate(tail, SYSTEM)
        self.assertEqual(out[1]["role"], "user", "开头的孤儿 tool 消息没有被丢弃")
        self.assertTrue(ck.looks_valid(out))

    def test_fills_missing_tool_responses(self):
        tail = [
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1"), _tc("c2")]},
        ]
        out = ck.rehydrate(tail, SYSTEM)
        tools = [m for m in out if m.get("role") == "tool"]
        self.assertEqual(len(tools), 2, "未响应的 tool_call 没有被补上")
        self.assertTrue(all("中断" in t["content"] for t in tools))
        self.assertTrue(ck.looks_valid(out))

    def test_fills_across_all_batches_not_just_last(self):
        """antnest_loop 的回填只扫最后一个 assistant 然后 break，
        恢复必须遍历全部批次。"""
        tail = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c2")]},
        ]
        out = ck.rehydrate(tail, SYSTEM)
        responded = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
        self.assertEqual(responded, {"c1", "c2"}, "只补了最后一个批次")
        self.assertTrue(ck.looks_valid(out))

    def test_does_not_duplicate_existing_responses(self):
        tail = [
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": "真实结果"},
        ]
        out = ck.rehydrate(tail, SYSTEM)
        tools = [m for m in out if m.get("role") == "tool"]
        self.assertEqual(len(tools), 1, "重复补了已有的响应")
        self.assertEqual(tools[0]["content"], "真实结果")

    def test_strips_cot_during_rehydrate(self):
        tail = [{"role": "assistant", "content": "a", "reasoning_content": "机密"}]
        out = ck.rehydrate(tail, SYSTEM)
        self.assertNotIn("机密", json.dumps(out, ensure_ascii=False))

    def test_handles_empty_tail(self):
        out = ck.rehydrate([], SYSTEM)
        self.assertEqual(len(out), 1)
        self.assertTrue(ck.looks_valid(out))

    def test_handles_garbage_entries(self):
        out = ck.rehydrate(["not a dict", None, 42, {"role": "user", "content": "x"}], SYSTEM)
        self.assertTrue(ck.looks_valid(out))
        self.assertEqual(out[-1]["role"], "user")

    def test_no_system_yet_still_valid(self):
        out = ck.rehydrate([{"role": "user", "content": "x"}], {})
        self.assertFalse(ck.looks_valid(out), "没有 system 时应判定为无效")

    def test_result_passes_sanitize(self):
        """过一遍 api_compat.sanitize_messages_for_api 兜底，确认不炸。"""
        tail = [
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
        ]
        out = ck.rehydrate(tail, SYSTEM)
        try:
            from api_compat import sanitize_messages_for_api
        except ImportError:
            self.skipTest("api_compat 不可用")
        for profile in ("deepseek", "kimi", "openai_compat"):
            cleaned = sanitize_messages_for_api(list(out), profile)
            self.assertIsInstance(cleaned, list)
            self.assertTrue(cleaned)


class LooksValidTest(unittest.TestCase):
    def test_rejects_empty(self):
        self.assertFalse(ck.looks_valid([]))
        self.assertFalse(ck.looks_valid(None))

    def test_requires_system_first(self):
        self.assertFalse(ck.looks_valid([{"role": "user", "content": "x"}]))

    def test_rejects_dangling_tool_call(self):
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
        ]
        self.assertFalse(ck.looks_valid(msgs))

    def test_accepts_complete(self):
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": "r"},
        ]
        self.assertTrue(ck.looks_valid(msgs))


class TailMessagesTest(unittest.TestCase):
    def test_respects_limit(self):
        msgs = [{"role": "user", "content": str(i)} for i in range(50)]
        self.assertLessEqual(len(ck.tail_messages(msgs, 10)), 10)

    def test_empty_input(self):
        self.assertEqual(ck.tail_messages([]), [])
        self.assertEqual(ck.tail_messages(None), [])

    def test_filters_non_dict(self):
        self.assertEqual(ck.tail_messages(["x", 1, None]), [])

    def test_strips_cot(self):
        out = ck.tail_messages([{"role": "assistant", "content": "a", "thinking": "t"}])
        self.assertNotIn("thinking", out[0])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = _tmp()
        self.store = ck.get_store()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _save(self, task="t1", n=1, **kw):
        for i in range(n):
            ck.save(kw.get("reason", "manual"), task_id=task, goal="g",
                    messages=[{"role": "user", "content": f"m{i}"}], **kw)

    def test_save_and_load(self):
        self._save()
        loaded = self.store.latest("t1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.goal, "g")
        self.assertEqual(loaded.task_id, "t1")

    def test_seq_increments(self):
        self._save(n=3)
        seqs = [c.seq for c in self.store.list("t1")]
        self.assertEqual(seqs, [1, 2, 3])

    def test_latest_is_highest_seq(self):
        self._save(n=3)
        self.assertEqual(self.store.latest("t1").seq, 3)

    def test_prunes_to_keep_limit(self):
        store = CheckpointStore(self.dir, keep=3)
        for i in range(6):
            seq = store.next_seq("t2")
            store.save(Checkpoint(
                checkpoint_id=f"c{i}", task_id="t2", seq=seq, created_at=0.0,
                reason="r", goal="g",
            ))
        self.assertLessEqual(len(store.list("t2")), 3)

    def test_isolation_between_tasks(self):
        self._save(task="tA")
        self._save(task="tB")
        self.assertEqual(len(self.store.list("tA")), 1)
        self.assertEqual(len(self.store.list("tB")), 1)
        self.assertIsNone(self.store.latest("tC"))

    def test_load_missing_returns_none(self):
        self.assertIsNone(self.store.load("nope", 1))

    def test_corrupt_file_returns_none(self):
        d = self.store._task_dir("tX")
        os.makedirs(d, exist_ok=True)
        Path(self.store._path("tX", 1)).write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.store.load("tX", 1))

    def test_clear(self):
        self._save()
        self.store.clear("t1")
        self.assertIsNone(self.store.latest("t1"))

    def test_tasks_listing(self):
        self._save(task="tA")
        self._save(task="tB")
        self.assertIn("tA", self.store.tasks())
        self.assertIn("tB", self.store.tasks())

    def test_write_is_atomic(self):
        """不留 .tmp 残file。"""
        self._save(n=2)
        d = self.store._task_dir("t1")
        leftovers = [f for f in os.listdir(d) if f.startswith(".ckpt-")]
        self.assertEqual(leftovers, [], "原子写留下了临时文件")


class ResumePromptTest(unittest.TestCase):
    def setUp(self):
        self.dir = _tmp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _ckpt(self, nodes):
        return Checkpoint(
            checkpoint_id="c1", task_id="t1", seq=1, created_at=0.0,
            reason="worker_done", goal="修复测试失败",
            plan={"goal": "修复测试失败", "nodes": nodes},
        )

    def test_answers_the_three_questions(self):
        """路线图 §6：已完成 / 进行中 / 剩余。"""
        text = self._ckpt([
            {"id": "P1", "title": "扫描项目", "status": "completed"},
            {"id": "P2", "title": "跑测试", "status": "running"},
            {"id": "P3", "title": "修复", "status": "pending"},
        ]).resume_prompt()
        self.assertIn("目标：修复测试失败", text)
        self.assertIn("已完成：扫描项目", text)
        self.assertIn("进行中：跑测试", text)
        self.assertIn("剩余：修复", text)

    def test_includes_result_summaries(self):
        text = self._ckpt([
            {"id": "P1", "title": "扫描", "status": "completed",
             "result_summary": "183 files"},
        ]).resume_prompt()
        self.assertIn("183 files", text)

    def test_includes_failed(self):
        text = self._ckpt([
            {"id": "P1", "title": "修复", "status": "failed"},
        ]).resume_prompt()
        self.assertIn("已失败：修复", text)

    def test_handles_no_nodes(self):
        text = self._ckpt([]).resume_prompt()
        self.assertIn("（无）", text)

    def test_skipped_counts_as_done(self):
        text = self._ckpt([
            {"id": "P1", "title": "跳过", "status": "skipped"},
        ]).resume_prompt()
        self.assertIn("已完成：跳过", text)

    def test_falls_back_to_id_when_title_missing(self):
        text = self._ckpt([{"id": "P9", "status": "pending"}]).resume_prompt()
        self.assertIn("P9", text)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.dir = _tmp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_resume_returns_valid_messages(self):
        ck.save("user_stop", task_id="t1", goal="g", messages=[
            {"role": "system", "content": "旧"},
            {"role": "user", "content": "任务"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
        ], plan={"goal": "g", "nodes": [{"id": "P1", "title": "a", "status": "completed"}]})
        messages, restored = ck.resume("t1", system_msg=SYSTEM)
        self.assertIsNotNone(restored)
        self.assertTrue(ck.looks_valid(messages), "恢复出的消息列表不能被 API 拒绝")
        self.assertEqual(messages[0]["content"], SYSTEM["content"])

    def test_resume_restores_plan(self):
        import antnest_plan as plan
        ck.save("x", task_id="t2", plan={
            "goal": "g", "nodes": [
                {"id": "P1", "title": "扫描", "status": "completed"},
                {"id": "P2", "title": "测试", "status": "pending", "depends_on": ["P1"]},
            ],
        })
        ck.resume("t2")
        self.assertEqual([n.id for n in plan.current().nodes], ["P1", "P2"])
        self.assertEqual(plan.current().node("P1").status.value, "completed")

    def test_resume_can_skip_plan(self):
        import antnest_plan as plan
        plan.reset("原有")
        ck.save("x", task_id="t3", plan={"goal": "新", "nodes": [{"id": "X", "title": "x"}]})
        ck.resume("t3", restore_plan=False)
        self.assertEqual(plan.current().goal, "原有", "restore_plan=False 仍覆盖了计划")

    def test_resume_missing_task(self):
        messages, restored = ck.resume("never_existed")
        self.assertEqual(messages, [])
        self.assertIsNone(restored)

    def test_resume_emits_event(self):
        ev.reset(self.dir)
        ck.save("x", task_id="t4")
        before = len(ev.get_log().tail(1000))
        ck.resume("t4")
        kinds = [r.event for r in ev.get_log().tail(1000)[before:]]
        self.assertIn("CHECKPOINT_RESUMED", kinds)


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.dir = _tmp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("ANT_CHECKPOINT", None)

    def test_disabled_by_env(self):
        os.environ["ANT_CHECKPOINT"] = "0"
        try:
            self.assertIsNone(ck.save("x", task_id="t1"))
        finally:
            os.environ.pop("ANT_CHECKPOINT", None)

    def test_never_raises_on_bad_base_dir(self):
        ck.reset(os.path.join("Z:\\", "nope", "\x00bad"))
        try:
            self.assertIsNone(ck.save("x", task_id="t1"))
        finally:
            ck.reset(self.dir)

    def test_emits_event(self):
        ck.save("worker_done", task_id="t1", goal="g")
        kinds = [r.event for r in ev.get_log().tail(1000)]
        self.assertIn("CHECKPOINT_SAVED", kinds)

    def test_records_event_watermark(self):
        ev.emit(ev.Event.TOOL_CALL, task_id="t1", tool="x")
        saved = ck.save("x", task_id="t1")
        self.assertIsNotNone(saved)
        self.assertGreater(saved.event_seq, 0, "未记录事件水位，恢复时无法对齐")

    def test_carries_stats(self):
        saved = ck.save("x", task_id="t1", stats={"tool_calls": 5, "workers": 2})
        self.assertEqual(saved.stats["tool_calls"], 5)
        self.assertIn("5", saved.one_line())

    def test_reason_is_truncated(self):
        saved = ck.save("x" * 200, task_id="t1")
        self.assertLessEqual(len(saved.reason), 40)


class CollectStatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = _tmp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_counts_tools_and_workers(self):
        ck.save("x", task_id="t1")
        ev.emit(ev.Event.TOOL_CALL, task_id="t1", tool="spawn_clone")
        ev.emit(ev.Event.WORKER_COMPLETED, task_id="t1", worker_id=1)
        ev.emit(ev.Event.WORKER_COMPLETED, task_id="t1", worker_id=2)
        stats = ck.collect_stats()
        self.assertGreaterEqual(stats["tool_calls"], 1)
        self.assertEqual(stats["workers"], 2)

    def test_never_raises(self):
        self.assertIsInstance(ck.collect_stats(), dict)


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.dir = _tmp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_empty(self):
        self.assertIn("没有检查点", ck.summary("nope"))

    def test_lists_entries(self):
        ck.save("worker_done", task_id="t1", stats={"tool_calls": 3, "workers": 1})
        text = ck.summary("t1")
        self.assertIn("#1", text)
        self.assertIn("worker_done", text)


if __name__ == "__main__":
    unittest.main()
