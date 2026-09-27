# -*- coding: utf-8 -*-
"""AntNest · 工蚁隔离目录的端到端冒烟 + 模块清单完整性

背景（docs/v1.4-DESIGN.md §1.1 / 评审 C1）：

    antnest_clone_worker.py:13  import antnest_log          ← 顶层 import
    antnest_config.py:25        import antnest_clone_worker  ← 先于 :33 的 antnest_log

antnest_log.py / antnest_errors.py / antnest_config_schema.py 都是工蚁启动的
硬依赖，但历史上都不在 spawn_clone 的复制清单里。旧代码没炸只是因为开发环境是
editable 安装，site-packages 的 finder 把缺失模块从仓库根解析了出来。

而 view_file / list_dir / grep_files / write_file / search_replace **全部**经由
spawn_clone，所以一旦换成 wheel 安装或干净 venv，就是整条工具链全瘫。

因此本文件的核心用例是 `test_worker_runs_without_repo_on_syspath`：把工蚁需要
的文件复制到临时目录，**清空 PYTHONPATH** 后以独立进程跑一次真实工蚁。
editable 安装的 finder 挂在 site-packages 上（不是 PYTHONPATH），所以还需要
`-I`（isolated）之外的 `-S` 之外的手段——见该用例内的说明。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import antnest_inventory  # noqa: E402


def _stage_worker_dir(dest: Path, modules=None) -> None:
    """按派生清单把工蚁所需文件复制到 dest（含 prompts/）。"""
    for mod in (antnest_inventory.worker_modules() if modules is None else modules):
        src = ROOT / mod
        if src.is_file():
            shutil.copy2(src, dest / mod)
    prompts = ROOT / "prompts"
    if prompts.is_dir():
        shutil.copytree(prompts, dest / "prompts", dirs_exist_ok=True)


def _run_worker(extra_env: dict, command: str = "echo antnest-e2e-ok", modules=None):
    """以独立进程跑一次工蚁，返回 (returncode, stdout+stderr, result_dict)。"""
    tmp = Path(tempfile.mkdtemp(prefix="antnest_e2e_"))
    try:
        _stage_worker_dir(tmp, modules)
        result_file = tmp / "result.json"
        env = {
            "AN_CLONE_MODE": "1",
            "AN_CLONE_COMMAND": command,
            "AN_RESULT_FILE": str(result_file),
            "AN_CLONE_DIR": str(tmp),
            "AN_CLONE_TIMEOUT": "30",
            "AN_DEPTH": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "ANT_API_KEY": "e2e-test-key",
            "ANT_SKIP_MODEL_CHECK": "1",
            # 工蚁把命令写进 .ps1/.sh 再执行，需要能定位到 powershell / bash，
            # 因此保留宿主机的 PATH 与 SystemRoot。
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
            "TEMP": os.environ.get("TEMP", str(tmp)),
            "TMP": os.environ.get("TMP", str(tmp)),
            # 关键：清空继承来的路径变量，避免仓库根被拼回 sys.path。
            # editable 安装用的是 site-packages 下的 .pth + finder，不受 PYTHONPATH
            # 影响，所以额外用 -E 忽略 PYTHON* 变量、并把 cwd 设为隔离目录。
            "PYTHONPATH": "",
        }
        env.update(extra_env)
        proc = subprocess.run(
            [sys.executable, "-E", "-B", "AntNest.py"],
            cwd=str(tmp),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        data = {}
        if result_file.is_file():
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
            except ValueError:
                data = {"status": "unparsable", "raw": result_file.read_text(encoding="utf-8")}
        return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class WorkerDependencyTest(unittest.TestCase):
    """工蚁依赖清单本身。"""

    def test_inventory_covers_every_hard_dependency(self):
        """清单必须包含所有被顶层 import 的仓库内模块。"""
        mods = set(antnest_inventory.worker_modules())
        # 这三个是顶层 import 的硬依赖，历史上都曾漏掉。
        for required in ("antnest_log.py", "antnest_errors.py", "antnest_config_schema.py"):
            self.assertIn(required, mods, f"工蚁依赖清单缺少硬依赖 {required}")

    def test_inventory_excludes_ui_modules(self):
        """UI 层不该被复制进工蚁隔离目录。"""
        mods = set(antnest_inventory.worker_modules())
        for ui_only in ("antnest_bridge.py", "prototype_antnest.py", "phtmlwin.py"):
            self.assertNotIn(ui_only, mods)

    def test_every_inventory_module_exists(self):
        for mod in antnest_inventory.worker_modules():
            self.assertTrue((ROOT / mod).is_file(), f"清单里的 {mod} 在仓库中不存在")

    def test_antnest_queen_uses_derived_inventory(self):
        """spawn_clone 必须用派生清单，不能再有手写元组。"""
        src = (ROOT / "antnest_queen.py").read_text(encoding="utf-8")
        self.assertIn("antnest_inventory.worker_modules", src)
        # 旧实现是 `for _dep in ("antnest_clone_worker.py", "code_tools.py", ...)`
        # 复制循环里不得再出现字面量模块名元组。
        self.assertNotRegex(src, r'for\s+_dep\s+in\s+\(\s*"')
        self.assertNotRegex(src, r'for\s+_dep\s+in\s+\[\s*"')


class WorkerLeafModuleTest(unittest.TestCase):
    """硬约束 C1：antnest_clone_worker 必须是叶子模块。

    antnest_config.py:25 先 import antnest_clone_worker，:33 才 import antnest_log，
    :47 才跑 run_if_clone_mode() 并 sys.exit(0)。也就是说 clone_worker 被 import
    时 antnest_config 处于**半初始化**状态（只绑定了 ≤25 行的名字）。
    若 clone_worker 传递 import 到 antnest_config，就会在第一次提交时炸掉
    整个工蚁群。
    """

    def test_worker_does_not_reach_antnest_config(self):
        closure = antnest_inventory.import_closure("antnest_clone_worker.py")
        self.assertIn("antnest_log.py", closure, "clone_worker 至少应能取到 antnest_log")
        self.assertNotIn(
            "antnest_config.py",
            closure,
            "antnest_clone_worker 传递依赖了 antnest_config——会在 import 期循环导入并杀死所有工蚁",
        )

    def test_worker_does_not_import_business_modules(self):
        forbidden = {
            "antnest_permissions", "antnest_registry", "antnest_plan",
            "antnest_events", "antnest_checkpoint", "antnest_queen",
        }
        closure = antnest_inventory.import_closure("antnest_clone_worker.py")
        for name in forbidden:
            self.assertNotIn(
                f"{name}.py", closure,
                f"antnest_clone_worker 不得依赖 {name}（工蚁侧策略只读环境变量）",
            )


class WorkerEndToEndTest(unittest.TestCase):
    """真实启动工蚁进程——仓库里此前没有任何测试做过这件事。"""

    def test_worker_runs_without_repo_on_syspath(self):
        code, output, data = _run_worker({})
        self.assertTrue(
            data.get("status") == "ok",
            "工蚁未能在纯净环境启动。\n"
            f"returncode={code}\nresult={data}\n输出：\n{output[:3000]}",
        )

    def test_worker_executes_command_and_returns_output(self):
        _, _, data = _run_worker({})
        blob = json.dumps(data, ensure_ascii=False)
        self.assertIn("antnest-e2e-ok", blob, "工蚁结果里应含命令输出")

    def test_worker_still_blocks_dangerous_command(self):
        """清单修复不能削弱工蚁侧的第二道防线。"""
        _, _, data = _run_worker({}, command="format c:")
        self.assertEqual(
            data.get("status"), "blocked",
            f"工蚁侧危险命令拦截失效：{data}",
        )


class LegacyDependencyListRegressionTest(unittest.TestCase):
    """锁定 v1.4 修复的那个缺陷（docs/v1.4-DESIGN.md §1.1）。

    这里故意用**修复前的手写清单**去搭工蚁目录，断言工蚁起不来。
    如果哪天有人把 antnest_inventory 换成「什么都往里塞」，本用例会先炸，
    提醒他这份清单不是越多越好——它必须恰好覆盖 import 闭包。
    """

    LEGACY = (
        "antnest_clone_worker.py", "code_tools.py", "antnest_session.py",
        "api_compat.py", "memory_retrieval.py", "antnest_runtime_state.py",
        "task_manager.py", "admin_utils.py", "memory_tree.py",
        "model_capabilities.py", "antnest_config.py", "antnest_schemas.py",
        "antnest_queen.py", "antnest_memory.py", "antnest_llm.py",
        "antnest_loop.py",
    )

    def test_legacy_list_is_insufficient(self):
        code, output, data = _run_worker({}, modules=self.LEGACY)
        self.assertNotEqual(
            data.get("status"), "ok",
            "旧清单竟然能跑起来？说明 antnest_log/errors/config_schema 已不再"
            "是硬依赖，本回归测试的前提需要重新评估。\n"
            f"result={data}\n输出：\n{output[:2000]}",
        )

    def test_derived_list_is_a_strict_superset(self):
        derived = set(antnest_inventory.worker_modules())
        self.assertTrue(
            set(self.LEGACY).issubset(derived),
            f"派生清单应覆盖旧清单全部条目，缺失：{sorted(set(self.LEGACY) - derived)}",
        )
        self.assertGreater(
            len(derived), len(self.LEGACY),
            "派生清单应当比旧清单更全（至少补上 antnest_log/errors/config_schema）",
        )


class PackagingManifestTest(unittest.TestCase):
    """打包清单漂移检测（pyproject.toml / installer/AntNest.iss）。"""

    def test_no_packaging_gaps(self):
        gaps = antnest_inventory.packaging_gaps()
        for label, (missing, _extra) in gaps.items():
            self.assertFalse(
                missing,
                f"{label} 缺少 import 图中必需的模块：{sorted(missing)}",
            )

    def test_iss_ships_core_modules(self):
        iss = ROOT / "installer" / "AntNest.iss"
        declared = antnest_inventory.declared_iss_modules(str(iss))
        for required in ("antnest_log.py", "antnest_errors.py", "antnest_config_schema.py"):
            self.assertIn(required, declared, f"安装包未包含 {required}")


if __name__ == "__main__":
    unittest.main()
