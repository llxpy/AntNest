# -*- coding: utf-8 -*-
"""AntNest · v1.4 UI 接线与并发修复测试

三块内容：

1. **事件协议**：bridge 必须 emit `plan` / `task_id` / `event` / `perm`，
   prototype_antnest 必须有对应的 handler 分支与面板。
2. **并发修复**：`send()` 的 TOCTOU 窗口、`sys.stdout` 的比较交换恢复。
3. **渲染接线**：新面板元素确实出现在布局里，且 JS 函数存在。
"""
import ast
import importlib
import json
import os
import re
import sys
import tempfile
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


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ====================== 事件协议 ======================

class EventProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import antnest_bridge as bridge

            cls.bridge = importlib.reload(bridge)

    def _core(self):
        core = self.bridge.AntNestCore()
        got: list[tuple] = []
        core.subscribe(lambda k, p: got.append((k, p)))
        return core, got

    def test_task_id_event(self):
        core, got = self._core()
        core.emit("task_id", task_id="t_abc")
        payload = next(p for k, p in got if k == "task_id")
        self.assertEqual(payload["task_id"], "t_abc")

    def test_plan_event(self):
        core, got = self._core()
        core.emit("plan", plan_id="p1", goal="g", nodes=[{"id": "P1"}])
        payload = next(p for k, p in got if k == "plan")
        self.assertEqual(payload["goal"], "g")
        self.assertEqual(payload["nodes"][0]["id"], "P1")

    def test_perm_event(self):
        core, got = self._core()
        core.emit("perm", level=3)
        payload = next(p for k, p in got if k == "perm")
        self.assertEqual(payload["level"], 3)

    def test_route_timeline_truncates_data(self):
        """事件 data 可能含命令全文/文件内容，绝不能原样丢给 UI。"""
        import antnest_events as ev

        tmp = tempfile.mkdtemp()
        try:
            log = ev.EventLog(tmp)
            rec = log.emit(
                ev.Event.TOOL_CALL, task_id="t1", tool="spawn_clone",
                args={"command": "rm -rf / 机密内容"},
                secret_field="不该出现在 UI",
                reasoning_content="思维链",
            )
            core, got = self._core()
            core._route_timeline(rec)
            payload = next(p for k, p in got if k == "event")
            blob = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("机密内容", blob)
            self.assertNotIn("不该出现在 UI", blob)
            self.assertNotIn("思维链", blob)
            self.assertEqual(payload["data"]["tool"], "spawn_clone")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_route_timeline_accepts_missing_data(self):
        core, got = self._core()
        bare = type("R", (), {"event": "X", "ts": 1.0, "worker_id": None, "data": None})()
        core._route_timeline(bare)
        self.assertTrue(any(k == "event" for k, _ in got))

    def test_new_routes_exist(self):
        for name in ("route_replay_task", "route_checkpoints", "route_resume_task"):
            self.assertTrue(callable(getattr(self.bridge.AntNestCore, name)), f"缺 {name}")

    def test_route_replay_returns_text(self):
        import antnest_events as ev
        core, _ = self._core()
        ev.emit(ev.Event.TASK_CREATED, task_id="t_r", goal="演示")
        out = core.route_replay_task({"task_id": "t_r"})
        self.assertTrue(out["ok"])
        self.assertIn("TASK_CREATED", out["text"])

    def test_route_resume_missing_task(self):
        core, _ = self._core()
        out = core.route_resume_task({"task_id": "never"})
        self.assertFalse(out["ok"])


# ====================== 并发修复 ======================

class ConcurrencyFixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import antnest_bridge as bridge

            cls.bridge = importlib.reload(bridge)

    def test_turn_lock_exists(self):
        import threading

        core = self.bridge.AntNestCore()
        self.assertIsInstance(core._turn_lock, type(threading.Lock()))

    def test_send_is_rejected_while_turn_holds_lock(self):
        """核心修复：锁在**启动线程之前**抢，第二次 send 必须被拒。

        旧实现里 self.busy=True 是在新线程里设的，两次快速 send 都能通过
        busy 检查、起两个 _turn 线程。
        """
        import threading

        core = self.bridge.AntNestCore()
        core.busy = False
        started = threading.Event()
        release = threading.Event()

        def _fake_turn(*_a, **_k):
            started.set()
            release.wait(5)
            core.busy = False
            try:
                core._turn_lock.release()
            except RuntimeError:
                pass

        core._turn = _fake_turn

        ok1, _ = core.send("第一个任务")
        self.assertTrue(ok1)
        self.assertTrue(started.wait(5))
        ok2, err2 = core.send("第二个任务")
        self.assertFalse(ok2, "第二个任务被放行了——TOCTOU 未修复")
        self.assertIn("busy", err2)
        release.set()

    def test_real_turn_releases_lock_on_early_failure(self):
        """真实 _turn 在 ensure_loaded 失败时也必须释放锁。

        不能用假的 _turn 测这个——假的绕过了 finally，测不到东西。
        这里让 ensure_loaded 返回 False，走真实的 finally 分支。
        """
        import threading

        core = self.bridge.AntNestCore()
        core.busy = False
        core.mod = None
        core._ui_settings = {}
        core.ensure_loaded = lambda *a, **k: (False, "故意失败")

        core.send("x")
        for _ in range(60):
            if not core._turn_lock.locked():
                break
            threading.Event().wait(0.05)
        self.assertFalse(
            core._turn_lock.locked(),
            "_turn 提前失败后锁没释放，用户之后每条消息都会收到 busy",
        )

    def test_stdout_restore_uses_compare_and_swap(self):
        """恢复 sys.stdout 必须是比较交换，不能无条件赋值。

        无条件恢复在两个 turn 交错时会把别人的对象覆盖掉，
        而 old_out 可能本身就是一个已死的 StdoutTap。

        注意不能全局断言「src 里没有 sys.stdout = old_out」——ensure_loaded
        里那几处是正确且无关的。要断言的是 **_turn 的 finally 块**。
        """
        src = _source("antnest_bridge.py")
        self.assertIn("if sys.stdout is _my_tap:", src)
        # finally 块里，紧跟 CAS 之前不应出现无条件赋值
        cas = src.find("if sys.stdout is _my_tap:")
        window = src[max(0, cas - 400):cas]
        self.assertNotIn(
            "\n                sys.stdout = old_out",
            window,
            "_turn 的 finally 仍在无条件恢复 sys.stdout",
        )

    def test_flush_uses_our_tap_not_global(self):
        src = _source("antnest_bridge.py")
        self.assertIn("_my_tap.flush()", src)

    def test_route_line_regex_uses_group(self):
        """旧代码拿 ln.split 而不是 m.group(0)，容易取错。"""
        src = _source("antnest_bridge.py")
        self.assertNotIn('ln.split(" ", 1)[1].strip()[:60] if " " in ln else ""', src)


# ====================== UI 接线 ======================

class UiWiringSourceTest(unittest.TestCase):
    """prototype_antnest.py 太大且有 import 副作用，用源码断言代替 import。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _source("prototype_antnest.py")
        cls.js = _source("ui_assets/app.js")
        cls.css = _source("ui_assets/app.css")
        try:
            ast.parse(cls.src)
        except SyntaxError as e:  # pragma: no cover
            raise AssertionError(f"prototype_antnest.py 语法错误：{e}")

    def test_state_globals_exist(self):
        for name in ("PLAN", "EVENTS", "TASK_ID", "PERM_LEVEL", "_ui_state_lock"):
            self.assertIn(name, self.src, f"缺少 UI 状态 {name}")

    def test_event_handlers_exist(self):
        for kind in ("plan", "task_id", "event", "perm"):
            self.assertIn(f'elif kind == "{kind}":', self.src, f"缺 {kind} 事件分支")

    def test_flush_branches_exist(self):
        for part in ("plan", "timeline", "perm", "v14"):
            self.assertIn(f'if "{part}" in parts:', self.src, f"_flush 缺 {part} 分支")

    def test_render_functions_exist(self):
        for fn in ("_plan_panel", "_perm_panel", "_timeline_panel", "_control_buttons"):
            self.assertIn(f"def {fn}(", self.src, f"缺 {fn}")

    def test_plan_panel_in_layout(self):
        self.assertIn('ui.div(cls="card panel plan-card")[', self.src)
        self.assertIn('_plan_panel(),', self.src)
        self.assertIn('_perm_panel(),', self.src)

    def test_timeline_modal_in_layout(self):
        self.assertIn('id="timeline-modal"', self.src)
        self.assertIn('id="timeline-modal-list"', self.src)

    def test_flush_updates_timeline_modal(self):
        """展开模态也要跟着更新（面板只渲染 60 条，模态给完整列表）。"""
        self.assertIn('"#timeline-modal-list"', self.src)

    def test_routes_registered(self):
        for route in ("replay_task", "checkpoints", "resume_task"):
            self.assertIn(f'@app.route("{route}")', self.src, f"缺路由 {route}")

    def test_js_has_modal_functions(self):
        self.assertIn("function openTimelineModal(", self.js)
        self.assertIn("function closeTimelineModal(", self.js)

    def test_js_uses_dom_api_not_string_concat(self):
        """轨迹行用 textContent 渲染，天然免疫注入。"""
        body = self.js[
            self.js.find("function openTimelineModal("):self.js.find("function closeTimelineModal(")
        ]
        self.assertIn("textContent", body)
        self.assertNotIn("+esc(", body, "app.js 里没有 esc()，用了会直接抛 ReferenceError")

    def test_css_has_v14_styles(self):
        for cls in (".plan-panel", ".perm-panel", ".timeline", ".tl-item", ".plan-card"):
            self.assertIn(cls, self.css, f"app.css 缺少 {cls}")

    def test_turn_start_clears_v14_state_under_lock(self):
        i = self.src.find('elif kind == "turn":')
        block = self.src[i:i + 1200]
        self.assertIn("with _ui_state_lock:", block)
        for name in ("SUBTASKS.clear()", "WORKERS.clear()", "EVENTS.clear()", "PLAN = {}"):
            self.assertIn(name, block, f"turn start 未清理 {name}")

    def test_ui_state_lock_is_rlock(self):
        self.assertIn("_ui_state_lock = threading.RLock()", self.src)


class SelfSourceCoverageTest(unittest.TestCase):
    """本轮改动的文件都必须在自身源码门禁名单里。"""

    def test_v14_modules_are_self_source(self):
        import antnest_permissions
        from antnest_permissions import PermissionEngine, PermissionPolicy

        engine = PermissionEngine(policy=PermissionPolicy(
            self_source_names=frozenset({
                "antnest_registry.py", "antnest_events.py", "antnest_plan.py",
                "antnest_checkpoint.py", "antnest_permissions.py",
                "antnest_inventory.py", "ui_render.py",
            }),
            self_source_root=str(ROOT),
        ))
        for name in ("antnest_registry.py", "antnest_events.py", "antnest_plan.py",
                     "antnest_checkpoint.py", "ui_render.py"):
            d = engine.check_write_path(str(ROOT / name), tool="write_file")
            self.assertEqual(
                d.status, "approval_required",
                f"{name} 不在自身源码门禁内——Agent 可无确认改写它",
            )

    def test_actual_inventory_includes_v14_modules(self):
        import antnest_inventory

        mods = set(antnest_inventory.worker_modules())
        for name in ("antnest_registry.py", "antnest_events.py",
                     "antnest_plan.py", "antnest_checkpoint.py"):
            self.assertIn(name, mods, f"{name} 不在工蚁依赖清单里")


if __name__ == "__main__":
    unittest.main()
