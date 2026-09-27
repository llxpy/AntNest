# -*- coding: utf-8 -*-
"""AntNest · 工具注册表（Tool Registry）

工具名历史上被手写了 4 遍，其中两处**已经腐烂**：

| 位置 | 形式 | 覆盖 | 状态 |
|------|------|------|------|
| `AntNest.py` tool_executors | dict 字面量 | 16 | 真源，但不含 level/exposed 元信息 |
| `antnest_queen.get_queen_tools` | schema 变量列表 | 11+2+2 | 手工同步 |
| `antnest_loop._detect_malformed_tool_call` | 正则 alternation | 13 | **已过期**（缺 3 个） |
| `prompts/queen_system.md` | 散文列表 | 13 | **已过期**（缺 3 个） |

任何 allowlist / 权限声明挂在这些地方，只会跟着一起腐烂。本模块把 `TOOL_SPECS`
变成唯一真源，其余全部派生。

导入方向（无环）
----------------
    antnest_permissions（仅标准库）
    antnest_config      → 提供 SHELL（给 run_cli/run_python 的 schema 文案）
    antnest_schemas     → 16 个 schema dict（本身由 antnest_config 构建）
    antnest_registry    → 只 import 上面两个；**不 import** antnest_queen
    antnest_queen       → import antnest_registry，经 _A() 在调用时解析 schema

`ToolSpec` 里存的是 schema / executor 的**属性名**而非对象本身，正是为了避开
「注册表 import 蚁后、蚁后再 import 注册表」的环。

不变性边界
----------
`TOOL_SPECS` 与 `visible_specs()` 的计算结果是不变的；但 `AntNest.tool_executors`
这个 dict 仍可被运行时改写——`antnest_bridge._patch_tools` 就在 import 之后把
`spawn_clone` 换成带 UI 事件的包装版本。这是有意的，包装器仍会调用原实现。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from antnest_permissions import PermLevel


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的声明式元数据。"""

    name: str
    level: PermLevel
    schema_attr: str
    executor_attr: str
    exposed: bool = True
    worker_only: bool = False
    panic_only: bool = False
    mutating: bool = False
    summary: str = ""

    @property
    def key(self) -> str:
        return self.name

    @property
    def queen_visible(self) -> bool:
        """常态下是否出现在蚁后的工具数组里。"""
        return self.exposed and not self.worker_only and not self.panic_only


# 压缩panic 状态下唯一允许的两个工具（与 antnest_config.COMPACT_PROMPT 一致：
# 只留「派工蚁」和「留记忆线索」，其余全部下架以腾出上下文）
_PANIC_TOOLS = ("spawn_clone", "leave_memory_hints")

# MCP 工具。刻意建模成**两个普通 spec**而不是「按服务器动态生成条目」——
# mcp_call 是单一派发器，tool_executors 里只有 "mcp_call" 一个键。若把每个 MCP
# 工具都做成独立 spec，会产生没有对应 executor 的 schema，而 fail-closed 权限
# 会把它们全部拒绝，MCP 就从「能用」变成「全瘫」。
_MCP_TOOLS = ("mcp_call", "mcp_list_tools")


# 顺序即语义：蚁后可见的工具按 v1.3.1 手写列表的**原始顺序**排列
# （spawn_clone → get_task_status → view_file → list_dir → grep_files →
#   write_file → search_replace → web_fetch → register_tool → list_tools →
#   get_tool_source），MCP 工具追加在尾部——与旧 get_queen_tools 逐项一致。
# tools 数组的顺序会影响模型的注意力分布，无意改动等于悄悄改变模型行为；
# tests/test_registry.py 的 test_legacy_order_is_preserved_when_sorted_by_first_use
# 会把顺序钉死。
TOOL_SPECS: tuple[ToolSpec, ...] = (
    # ---- 蚁后可见（顺序与 v1.3.1 一致） ----
    ToolSpec("spawn_clone", PermLevel.EXECUTE, "spawn_clone_schema", "spawn_clone",
             mutating=True, summary="生成工蚁在隔离目录执行命令"),
    ToolSpec("get_task_status", PermLevel.READ, "get_task_status_schema", "get_task_status",
             summary="查询工蚁任务状态"),
    ToolSpec("view_file", PermLevel.READ, "view_file_schema", "view_file",
             summary="派工蚁只读查看文件"),
    ToolSpec("list_dir", PermLevel.READ, "list_dir_schema", "list_dir",
             summary="派工蚁只读列目录"),
    ToolSpec("grep_files", PermLevel.READ, "grep_files_schema", "grep_files",
             summary="派工蚁搜索文件内容"),
    ToolSpec("write_file", PermLevel.WRITE, "write_file_schema", "write_file",
             mutating=True, summary="派工蚁写入/覆盖文件"),
    ToolSpec("search_replace", PermLevel.WRITE, "search_replace_schema", "search_replace",
             mutating=True, summary="派工蚁精确替换文件片段"),
    ToolSpec("web_fetch", PermLevel.NETWORK, "web_fetch_schema", "web_fetch",
             summary="抓取网页转纯文本"),
    ToolSpec("register_tool", PermLevel.WRITE, "register_tool_schema", "register_tool",
             mutating=True, summary="登记手搓工具进工具库"),
    ToolSpec("list_tools", PermLevel.READ, "list_tools_schema", "list_tools",
             summary="列出工具库"),
    ToolSpec("get_tool_source", PermLevel.READ, "get_tool_source_schema", "get_tool_source",
             summary="取回工具源码"),

    # ---- 计划 ----
    # level=READ + mutating=False：update_plan 不碰任何文件、不执行任何命令，
    # 只登记意图。标成 WRITE 会让「只读审阅模式」下无法规划。
    # 位置：v1.3.1 那 11 个之后、MCP 之前。MCP 工具在 v1.3.1 里是 extend 追加到
    # 列表末尾的，保持这个位置关系便于与旧行为 diff。
    ToolSpec("update_plan", PermLevel.READ, "update_plan_schema", "update_plan",
             summary="登记本任务的执行计划（DAG），UI 可见"),

    # ---- MCP（启用时追加在尾部） ----
    ToolSpec("mcp_call", PermLevel.NETWORK, "mcp_call_schema", "mcp_call",
             summary="调用 MCP 服务器工具"),
    ToolSpec("mcp_list_tools", PermLevel.NETWORK, "mcp_list_tools_schema", "mcp_list_tools",
             summary="列出 MCP 服务器及其工具"),

    # ---- 仅工蚁模式 ----
    ToolSpec("run_cli", PermLevel.EXECUTE, "run_cli_schema", "run_cli",
             exposed=False, worker_only=True, mutating=True,
             summary="直接执行 shell 命令（仅工蚁模式）"),
    ToolSpec("run_python", PermLevel.EXECUTE, "run_python_schema", "run_python",
             exposed=False, worker_only=True, mutating=True,
             summary="直接解释执行 Python（仅工蚁模式）"),

    # ---- 仅压缩态 ----
    # panic_only：常态下不下架。leave_memory_hints 的实现要求 messages 里存在
    # COMPACT_PROMPT 标记（antnest_memory.leave_memory_hints:59-66），常态暴露
    # 只会让模型反复调用后拿到错误、并可能干扰正常的压缩流程。
    ToolSpec("leave_memory_hints", PermLevel.READ, "memory_hints_schema", "leave_memory_hints",
             panic_only=True, summary="压缩历史并留下记忆线索（仅压缩态可用）"),
)

_BY_NAME: dict[str, ToolSpec] = {s.name: s for s in TOOL_SPECS}


# ====================== 查询 ======================

def all_names() -> tuple[str, ...]:
    return tuple(s.name for s in TOOL_SPECS)


def get(name: str) -> ToolSpec | None:
    return _BY_NAME.get(str(name or ""))


def is_registered(name: str) -> bool:
    return str(name or "") in _BY_NAME


def exposed_specs(*, compact_panic: bool = False, mcp_on: bool = False) -> list[ToolSpec]:
    """当前回合暴露给蚁后 LLM 的工具（按注册顺序）。

    - compact_panic：只留 _PANIC_TOOLS
    - mcp_on：MCP 工具可用时才带上
    """
    if compact_panic:
        wanted = set(_PANIC_TOOLS)
        return [s for s in TOOL_SPECS if s.name in wanted]
    out = [s for s in TOOL_SPECS if s.queen_visible]
    if not mcp_on:
        out = [s for s in out if s.name not in _MCP_TOOLS]
    return out


# ====================== 派生：执行器表 ======================

def build_executors(ns: Any) -> dict[str, Callable[..., str]]:
    """从 TOOL_SPECS 构造 tool_executors。

    `ns` 是提供函数对象的命名空间（通常是 AntNest 壳模块）。缺失的执行器会被跳过
    并由 registry_invariants() 报告，不在这里抛异常——注册表在 import 期构建，
    抛异常会把整个蚁后打挂。
    """
    out: dict[str, Callable[..., str]] = {}
    for spec in TOOL_SPECS:
        fn = getattr(ns, spec.executor_attr, None)
        if callable(fn):
            out[spec.name] = fn
    return out


# ====================== 派生：schema 列表 ======================

def visible_schemas(ns: Any, *, compact_panic: bool = False, mcp_on: bool = False) -> list[dict]:
    """解析出给 LLM 的 schema 列表（替代 get_queen_tools 的手写列表）。"""
    out: list[dict] = []
    for spec in exposed_specs(compact_panic=compact_panic, mcp_on=mcp_on):
        schema = getattr(ns, spec.schema_attr, None)
        if isinstance(schema, dict):
            out.append(schema)
    return out


# ====================== 派生：畸形工具调用检测 ======================

def malformed_call_pattern() -> str:
    """检测「模型把 function call 写成纯文本」用的工具名 alternation。

    覆盖 **TOOL_SPECS 全集**（16 个），而不是 exposed 子集。原因：现有手写正则是
    13 个，比 exposed 的 11 个还多——因为它顺带覆盖了 run_cli / run_python /
    leave_memory_hints / mcp_call / mcp_list_tools。

    其中 leave_memory_hints 尤其重要：它只在 COMPACT_PANIC 下暴露，若检测范围收窄
    到 11 个，模型就能用纯文本伪造它的 JSON 调用绕过压缩流程。所以这里宁可放宽。
    """
    return "|".join(re.escape(n) for n in sorted(all_names()))


def malformed_call_regex() -> re.Pattern:
    return re.compile(
        r'\{\s*"name"\s*:\s*"(?:' + malformed_call_pattern() + r')"\s*,\s*"arguments"',
        re.I,
    )


# ====================== 派生：prompt 工具目录 ======================

def prompt_catalog(*, compact_panic: bool = False, mcp_on: bool = False) -> str:
    """生成 prompt 里的工具清单文本（替代 queen_system.md 的手写散文）。

    刻意**不**做成 SYSTEM_PROMPT 的 {tool_catalog} 占位符：SYSTEM_PROMPT.format()
    在 6 处被调用（AntNest.py、antnest_bridge、antnest_memory、antnest_session、
    prototype_antnest），str.format 对未替换的 {...} 抛 KeyError；而工具可见性依赖
    运行时的 MCP_ENABLED / COMPACT_PANIC，import 期烘焙会 advertise 不存在的工具。

    真正消除漂移的手段是 prompt_catalog_lines() + tests 里的
    test_prompt_tool_list_matches_registry（CI 卡住不一致），而不是运行时注入。
    """
    specs = exposed_specs(compact_panic=compact_panic, mcp_on=mcp_on)
    if compact_panic:
        head = "（上下文紧急压缩中：仅保留以下工具）"
    else:
        head = "（下列工具由运行时注册表生成，与实际可调用集合一致）"
    lines = [head]
    for spec in specs:
        flags = []
        if spec.mutating:
            flags.append("会修改状态")
        flags.append(f"需 {spec.level.label}")
        lines.append(f"- {spec.name}：{spec.summary}（{'，'.join(flags)}）")
    return "\n".join(lines)


def prompt_catalog_lines() -> list[str]:
    """prompt 里应当出现的工具名（供一致性测试比对 queen_system.md）。"""
    return [s.name for s in exposed_specs(mcp_on=True)]


# ====================== 不变量自检 ======================

def registry_invariants(ns: Any) -> list[str]:
    """返回违反的不变量描述列表（空 = 全部通过）。供测试与启动自检使用。"""
    problems: list[str] = []
    executors = build_executors(ns)
    schemas = {s.schema_attr for s in TOOL_SPECS}

    # 1) 每个 spec 都要有执行器
    for spec in TOOL_SPECS:
        if spec.name not in executors:
            problems.append(f"工具 {spec.name} 没有执行器（executor_attr={spec.executor_attr}）")

    # 2) 反向不变量：暴露的工具必须都有执行器。
    #    这条能挡住「把动态条目塞进 exposed_specs」这类改动——它们有 schema 没执行器，
    #    在 fail-closed 权限下会被全部拒绝（例如曾经的 MCP 动态项设计）。
    for spec in exposed_specs(mcp_on=True):
        if spec.name not in executors:
            problems.append(f"暴露的工具 {spec.name} 没有对应执行器（会被 fail-closed 拒绝）")

    # 3) schema 属性必须能解析到 dict，且 name 与 spec 一致
    for spec in TOOL_SPECS:
        schema = getattr(ns, spec.schema_attr, None)
        if not isinstance(schema, dict):
            problems.append(f"工具 {spec.name} 的 schema 属性 {spec.schema_attr} 不是 dict")
            continue
        declared = ((schema.get("function") or {}).get("name"))
        if declared != spec.name:
            problems.append(
                f"schema {spec.schema_attr} 声明的 name={declared!r} 与 spec {spec.name!r} 不一致"
            )

    # 4) 名称唯一且合法
    seen: set[str] = set()
    for name in all_names():
        if name in seen:
            problems.append(f"工具名重复：{name}")
        seen.add(name)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            problems.append(f"工具名不合法（应全小写下划线）：{name}")

    # 5) schema 属性名不得重复占用
    if len(schemas) != len(TOOL_SPECS):
        problems.append("schema_attr 存在重复占用")

    return problems


def describe() -> str:
    lines = [f"工具注册表 {len(TOOL_SPECS)} 个，蚁后可见 {len(exposed_specs(mcp_on=True))} 个："]
    for spec in TOOL_SPECS:
        mark = (
            "可见" if spec.queen_visible
            else ("工蚁专用" if spec.worker_only else ("压缩态" if spec.panic_only else "隐藏"))
        )
        lines.append(f"  {spec.name:20s} L{spec.level} {mark:6s} {spec.summary}")
    return "\n".join(lines)


def _iter_specs(names: Iterable[str]) -> list[ToolSpec]:
    return [s for s in TOOL_SPECS if s.name in set(names)]


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(describe())
    raise SystemExit(0)
