# -*- coding: utf-8 -*-
"""AntNest · 模块清单（Module Inventory）

工蚁（clone worker）在隔离目录里以**独立进程**运行 `AntNest.py`，因此它拿不到
仓库根目录，只能依赖被显式复制过去的模块文件。这份清单历史上是手写元组
（antnest_queen.spawn_clone），已经与真实代码脱节：

    antnest_clone_worker.py:13  import antnest_log          ← 顶层 import
    antnest_config.py:25        import antnest_clone_worker  ← 先于 :33 的 antnest_log

也就是说 antnest_log.py / antnest_errors.py / antnest_config_schema.py 都是
工蚁启动的**硬依赖**，却都不在旧清单里。旧代码没炸只是因为开发环境是 editable
安装，site-packages 的 finder 把缺失模块从仓库根解析出来了——那是巧合，不是保证。
一旦换成 wheel 安装或干净的 venv，所有工蚁会在 import 期 ModuleNotFoundError，
而 view_file / list_dir / grep_files / write_file / search_replace **全部**经由
spawn_clone，等于整条工具链全瘫。

本模块把清单改为**从真实 import 图派生**：用 ast 静态扫描蚁后模式入口的传递
闭包，谁漏了谁进不来，而不是靠人记得同步。

同一份结果还用于校验 pyproject.toml 的 py-modules 与 installer/AntNest.iss，
那两处也是模块清单（此前同样漏了 antnest_log / antnest_errors /
antnest_config_schema / antnest_ui_pure）。

零外部依赖，仅标准库。本模块**不得** import antnest_config —— 它会在模块
顶层被 antnest_queen 导入，而后者由 antnest_config 在半初始化状态下拉起
（见 docs/v1.4-DESIGN.md 硬约束 C1）。
"""
from __future__ import annotations

import ast
import os
import sys

# 蚁后模式入口：工蚁进程从这里开始，逐层 import 出全部运行时依赖。
_QUEEN_ENTRY = "AntNest.py"
# 桌面 UI 入口。AntNest.py 不 import UI（UI 反过来依赖桥接层），
# 但打包清单必须覆盖它，所以校验打包清单时要把这条边也算进去。
_UI_ENTRY = "prototype_antnest.py"

# 仅这几个模块是「非 AntNest 运行时」——它们服务于 UI / 打包 / 启动器，
# 工蚁在隔离目录里不需要，也不该复制过去（复制了只是浪费磁盘和时间）。
_UI_ONLY = frozenset({
    "antnest_bridge.py",     # 桌面 UI 桥接层
    "prototype_antnest.py",  # GUI 定义
    "phtmlwin.py",           # webview 窗口封装
    "antnest_launcher.py",   # 打包入口
    "antnest_ui_pure.py",    # UI 纯函数
    "ui_render.py",          # UI 渲染
    "ui_assets_loader.py",   # UI 资源
    "skills_loader.py",      # Skills 面板
    "mcp_client.py",         # 仅蚁后侧加载（工蚁不再嵌套 MCP）
})

# 这些模块随安装包/源码分发，但不属于「运行时 import 闭包」，
# 由专门的复制路径处理（如 prompts/、ui_assets/），不参与本清单的完整性断言。
_NON_RUNTIME = frozenset({
    "r_seg.py",         # 本地调试脚本
    "antnest_launcher.py",  # 打包入口（[project.scripts] 指向它，无人 import 它）
})


def _module_name(path: str) -> str:
    base = os.path.basename(path)
    return base[:-3] if base.endswith(".py") else base


def _local_imports(tree: ast.AST, this_module: str) -> set[str]:
    """取出本文件里 import 的**仓库内**顶层模块名。

    只认 `import x` 与 `from x import ...`（x 不含点）——`from . import x`
    这类相对导入在本项目里不存在，出现即视为异常，忽略即可。
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入，本项目不使用
                continue
            if node.module:
                found.add(node.module.split(".")[0])
    found.discard(this_module)
    return found


def _parse(path: str) -> ast.Module | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return None


def import_closure(entry: str = _QUEEN_ENTRY, this_dir: str | None = None) -> set[str]:
    """返回从 entry 出发的仓库内模块传递闭包（含 entry 本身）。

    广度优先；只跟随「同目录下确实存在 .py」的模块，标准库与第三方包自然被排除。
    解析失败的文件按「不贡献依赖」处理（fail-soft：宁可少算也不要让整个
    蚁后因为一个坏文件起不来）。
    """
    root = this_dir or os.path.dirname(os.path.abspath(__file__))
    seen: set[str] = set()
    queue: list[str] = [entry]
    while queue:
        name = queue.pop()
        mod = name if name.endswith(".py") else name + ".py"
        if mod in seen:
            continue
        path = os.path.join(root, mod)
        if not os.path.isfile(path):
            continue
        seen.add(mod)
        tree = _parse(path)
        if tree is None:
            continue
        for dep in _local_imports(tree, mod):
            if dep + ".py" not in seen:
                queue.append(dep + ".py")
    return seen


def worker_modules(this_dir: str | None = None) -> tuple[str, ...]:
    """工蚁隔离目录必须携带的模块（已排序）。

    = 蚁后 import 闭包 − UI/打包专用模块。
    """
    closure = import_closure(this_dir=this_dir)
    return tuple(sorted(m for m in closure if m not in _UI_ONLY and m not in _NON_RUNTIME))


def runtime_modules(this_dir: str | None = None) -> tuple[str, ...]:
    """完整运行时模块（核心 + UI 层），用于校验打包清单是否漂移。"""
    root = this_dir or os.path.dirname(os.path.abspath(__file__))
    closure = import_closure(this_dir=root) | import_closure(_UI_ENTRY, this_dir=root)
    return tuple(sorted(m for m in closure if m not in _NON_RUNTIME))


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def declared_pyproject_modules(pyproject: str) -> set[str]:
    """从 pyproject.toml 的 [tool.setuptools] py-modules 里抽出模块文件名。

    不引入 tomllib 之外的依赖：Python 3.11+ 标准库自带 tomllib，3.12 是本项目
    的 requires-python 下限，可直接用。清单里写的是裸模块名（无 .py），这里统一
    补齐后缀以便和 import 闭包比对。解析失败返回空集。
    """
    try:
        import tomllib
    except ImportError:  # pragma: no cover - requires-python >= 3.12
        return set()
    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        return set()
    mods = (data.get("tool", {}).get("setuptools", {}) or {}).get("py-modules")
    if not isinstance(mods, list):
        return set()
    out: set[str] = set()
    for item in mods:
        name = str(item).strip()
        if not name:
            continue
        out.add(name if name.endswith(".py") else name + ".py")
    return out


def declared_iss_modules(iss: str) -> set[str]:
    """从 Inno Setup 脚本的 Source 行里抽出模块名。"""
    out: set[str] = set()
    for line in _read_text(iss).splitlines():
        stripped = line.strip()
        if not stripped.startswith("Source:"):
            continue
        if '"' not in stripped:
            continue
        target = stripped.split('"')[1]
        base = os.path.basename(target.replace("\\", "/"))
        if base.endswith(".py"):
            out.add(base)
    return out


def packaging_gaps(this_dir: str | None = None) -> dict[str, tuple[set[str], set[str]]]:
    """比对打包清单与 import 闭包。

    返回 {"pyproject": (缺失, 多余), "iss": (缺失, 多余)}。
    缺失 = import 图里有、清单里没有（会在线上炸）；多余 = 清单里有、import 图里
    没有（无害但说明清单已漂移）。
    """
    root = this_dir or os.path.dirname(os.path.abspath(__file__))
    expected = runtime_modules(this_dir=root)
    report: dict[str, tuple[set[str], set[str]]] = {}
    for label, declared in (
        ("pyproject", declared_pyproject_modules(os.path.join(root, "pyproject.toml"))),
        ("iss", declared_iss_modules(os.path.join(root, "installer", "AntNest.iss"))),
    ):
        report[label] = (set(expected) - declared, declared - set(expected))
    return report


def describe(this_dir: str | None = None) -> str:
    """人读摘要（启动日志 / 诊断用）。"""
    mods = worker_modules(this_dir=this_dir)
    lines = [f"工蚁依赖模块 {len(mods)} 个：{', '.join(mods)}"]
    for label, (missing, extra) in packaging_gaps(this_dir=this_dir).items():
        if missing:
            lines.append(f"打包清单 {label} 缺失 {len(missing)} 个：{', '.join(sorted(missing))}")
        if extra:
            lines.append(f"打包清单 {label} 多出 {len(extra)} 个：{', '.join(sorted(extra))}")
    return "\n".join(lines)


def _self_check() -> int:
    """开发期自检：python -m antnest_inventory"""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(describe())
    return 1 if any(missing for missing, _ in packaging_gaps().values()) else 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
