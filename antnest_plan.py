# -*- coding: utf-8 -*-
"""AntNest · 计划 DAG（Plan / DAG）

v1.3.1 的「计划」只以自由文本存在于 `reasoning_content` 与聊天气泡里。UI 上的
`SUBTASKS` 面板是**自动派生**的——一次 spawn_clone 对应一条，与蚁后实际想做什么
无关。这正是路线图 §3 要解决的问题：让「蚁后负责规划、工蚁负责执行」在产品层
真的可见。

数据来源：双通道
----------------
1. **LLM 显式规划** —— `update_plan` 工具。level=READ，mutating=False：
   它不改任何文件，只记录意图。
2. **自动派生（兜底，必须有）** —— `spawn_clone` 被调用时，若当前没有 RUNNING
   节点且 LLM 从未调过 `update_plan`，自动开一个节点并认领该工蚁。

第 2 条不是可选的。flash 类模型经常不按格式调工具；只依赖 `update_plan` 会让
Plan 面板在多数真实任务里是空的，整个特性等于没做。派生逻辑保证「任何 spawn
都能落到某个节点上」。

环检测
------
`update_plan` 与 `ready_nodes()` 都要检环。有环则拒绝该次更新并发 PLAN_REJECTED，
**绝不让计划进入坏状态**——一个有环的 DAG 会让 ready_nodes() 永远返回空，
计划直接死锁。

并发
----
Plan 被 agent 线程写、UI 线程读。`PlanNode` 是多字段读取（status +
result_summary + finished_at），无锁会渲染出「✓ completed 但结果为空」这种撕裂
状态。因此所有变更都在 `antnest_events.STATE_LOCK` 内完成——那个锁同时保护
event_seq，保证「状态变更」与「对应事件」落在同一个临界区。

只依赖标准库 + antnest_events（为了共用 STATE_LOCK 与事件发射）。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import antnest_events as ev
from antnest_events import STATE_LOCK


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"

    @property
    def done(self) -> bool:
        """终态。COMPLETED 与 SKIPPED 都算完成（依赖可以解除）。"""
        return self in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)

    @property
    def glyph(self) -> str:
        return {
            NodeStatus.PENDING: "○",
            NodeStatus.RUNNING: "●",
            NodeStatus.COMPLETED: "✓",
            NodeStatus.FAILED: "✗",
            NodeStatus.SKIPPED: "−",
            NodeStatus.BLOCKED: "⊘",
        }[self]


@dataclass
class PlanNode:
    id: str
    title: str
    detail: str = ""
    status: NodeStatus = NodeStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    worker_ids: list[int] = field(default_factory=list)
    attempts: int = 0
    error: str = ""
    result_summary: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "status": self.status.value,
            "depends_on": list(self.depends_on),
            "worker_ids": list(self.worker_ids),
            "attempts": self.attempts,
            "error": self.error,
            "result_summary": self.result_summary,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "PlanNode":
        try:
            status = NodeStatus(str(raw.get("status") or "pending"))
        except ValueError:
            status = NodeStatus.PENDING
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            detail=str(raw.get("detail") or ""),
            status=status,
            depends_on=[str(x) for x in (raw.get("depends_on") or [])],
            worker_ids=[int(x) for x in (raw.get("worker_ids") or []) if str(x).lstrip("-").isdigit()],
            attempts=int(raw.get("attempts") or 0),
            error=str(raw.get("error") or ""),
            result_summary=str(raw.get("result_summary") or ""),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
        )


class PlanError(Exception):
    """计划更新被拒（环、非法引用、id 冲突等）。"""


class Plan:
    """一个任务的有向无环计划。

    所有公开方法都在 STATE_LOCK 内取锁，调用方无需自己加锁。
    """

    MAX_NODES = 60

    def __init__(self, goal: str = "", plan_id: str = "") -> None:
        self.plan_id = plan_id or f"plan_{uuid.uuid4().hex[:8]}"
        self.goal = str(goal or "")
        self.nodes: list[PlanNode] = []
        self.created_at = time.time()
        self.updated_at = self.created_at
        # task_id → node_id。spawn_clone 返回的 task_id 是执行侧真源，
        # 工蚁归巢时用它反查节点并推进状态。
        self._by_task: dict[str, str] = {}
        # LLM 是否显式规划过。决定自动派生还要不要兜底。
        self.explicit = False

    # ---------------- 查询 ----------------

    def node(self, node_id: str) -> PlanNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def _index(self) -> dict[str, PlanNode]:
        return {n.id: n for n in self.nodes}

    def ready_nodes(self) -> list[PlanNode]:
        """依赖全部完成的节点。这些是「可以并行推进」的节点。

        注意：v1.4 **执行仍是顺序的**（agent_single_loop 顺序跑 tool_calls）。
        ready_nodes 表达的是依赖结构，不是并发承诺。
        """
        with STATE_LOCK:
            idx = self._index()
            out: list[PlanNode] = []
            for n in self.nodes:
                if n.status is not NodeStatus.PENDING:
                    continue
                if all(
                    (idx.get(dep) is not None and idx[dep].status.done)
                    for dep in n.depends_on
                ):
                    out.append(n)
            return out

    def has_cycle(self) -> list[str] | None:
        """有环则返回构成环的节点 id 列表，无环返回 None。"""
        with STATE_LOCK:
            idx = self._index()
            WHITE, GREY, BLACK = 0, 1, 2
            color = {nid: WHITE for nid in idx}
            stack: list[str] = []

            def _visit(nid: str) -> list[str] | None:
                color[nid] = GREY
                stack.append(nid)
                for dep in idx[nid].depends_on:
                    if dep not in idx:
                        continue
                    if color[dep] == GREY:
                        return stack[stack.index(dep):] + [dep]
                    if color[dep] == WHITE:
                        found = _visit(dep)
                        if found:
                            return found
                stack.pop()
                color[nid] = BLACK
                return None

            for nid in idx:
                if color[nid] == WHITE:
                    found = _visit(nid)
                    if found:
                        return found
            return None

    def progress(self) -> dict:
        with STATE_LOCK:
            total = len(self.nodes)
            done = sum(1 for n in self.nodes if n.status.done)
            failed = sum(1 for n in self.nodes if n.status is NodeStatus.FAILED)
            running = sum(1 for n in self.nodes if n.status is NodeStatus.RUNNING)
            pct = int(done * 100 / total) if total else 0
            return {
                "total": total,
                "completed": done,
                "failed": failed,
                "running": running,
                "pending": total - done - failed - running,
                "percent": pct,
            }

    def groups(self) -> list[list[PlanNode]]:
        """按依赖深度分层，用于 UI 缩进展示。"""
        with STATE_LOCK:
            idx = self._index()
            depth: dict[str, int] = {}
            for n in self.nodes:
                deps = [d for d in n.depends_on if d in idx]
                depth[n.id] = 1 + max((depth.get(d, 0) for d in deps), default=-1)
            layers: dict[int, list[PlanNode]] = {}
            for n in self.nodes:
                layers.setdefault(depth[n.id], []).append(n)
            return [layers[k] for k in sorted(layers)]

    def to_dict(self) -> dict:
        with STATE_LOCK:
            return {
                "plan_id": self.plan_id,
                "goal": self.goal,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "explicit": self.explicit,
                "nodes": [n.to_dict() for n in self.nodes],
                "progress": self.progress(),
            }

    def to_prompt(self) -> str:
        """注入系统提示的紧凑视图。"""
        with STATE_LOCK:
            if not self.nodes:
                return ""
            lines = [f"《当前计划》目标：{self.goal or '（未填写）'}"]
            for n in self.nodes:
                dep = f" ← {', '.join(n.depends_on)}" if n.depends_on else ""
                lines.append(f"  {n.status.glyph} [{n.id}] {n.title}{dep}")
            return "\n".join(lines)

    def summary(self) -> str:
        """人读摘要（事件 / 检查点用）。"""
        p = self.progress()
        return (
            f"{p['completed']}/{p['total']} 完成"
            + (f"，{p['failed']} 失败" if p["failed"] else "")
            + (f"，{p['running']} 进行中" if p["running"] else "")
        )

    # ---------------- 变更 ----------------

    def set_goal(self, goal: str) -> None:
        with STATE_LOCK:
            self.goal = str(goal or "")[:500]
            self.updated_at = time.time()

    def replace(self, goal: str, nodes: list[dict]) -> None:
        """整体替换节点列表（update_plan 的语义）。

        校验：id 唯一、依赖存在、无环、数量不超限。任一不满足则**整体拒绝**，
        保留原计划——半个新计划比旧计划更糟。
        """
        cleaned: list[PlanNode] = []
        seen: set[str] = set()
        for raw in nodes[: self.MAX_NODES]:
            if not isinstance(raw, dict):
                raise PlanError("节点必须是对象")
            nid = str(raw.get("id") or "").strip()
            title = str(raw.get("title") or "").strip()
            if not nid:
                raise PlanError("节点缺少 id")
            if not title:
                raise PlanError(f"节点 {nid} 缺少 title")
            if nid in seen:
                raise PlanError(f"节点 id 重复：{nid}")
            seen.add(nid)
            deps = [str(d) for d in (raw.get("depends_on") or []) if str(d).strip()]
            cleaned.append(PlanNode(
                id=nid, title=title[:200],
                detail=str(raw.get("detail") or "")[:1000],
                depends_on=deps,
            ))
        for n in cleaned:
            for dep in n.depends_on:
                if dep not in seen:
                    raise PlanError(f"节点 {n.id} 依赖了不存在的节点 {dep}")
        with STATE_LOCK:
            saved_nodes, saved_goal = self.nodes, self.goal
            # 旧索引必须在换掉 self.nodes 之前取
            old = self._index()
            self.nodes, self.goal = cleaned, str(goal or self.goal)[:500]
            cycle = self.has_cycle()
            if cycle:
                self.nodes, self.goal = saved_nodes, saved_goal
                raise PlanError(f"计划存在循环依赖：{' → '.join(cycle)}")
            self.explicit = True
            self.updated_at = time.time()
            # 保留仍然存在的节点的进度，避免 LLM 重建计划时抹掉已完成的工作。
            for n in self.nodes:
                prev = old.get(n.id)
                if prev is None or prev.status is NodeStatus.PENDING:
                    continue
                n.status = prev.status
                n.result_summary = prev.result_summary
                n.worker_ids = list(prev.worker_ids)
                n.error = prev.error
                n.attempts = prev.attempts
                n.started_at = prev.started_at
                n.finished_at = prev.finished_at

    def add_node(self, title: str, detail: str = "", depends_on: list[str] | None = None) -> PlanNode:
        with STATE_LOCK:
            if len(self.nodes) >= self.MAX_NODES:
                raise PlanError(f"节点数已达上限 {self.MAX_NODES}")
            nid = self._next_id()
            node = PlanNode(id=nid, title=str(title or nid)[:200],
                            detail=str(detail or "")[:1000],
                            depends_on=[d for d in (depends_on or []) if d])
            self.nodes.append(node)
            self.updated_at = time.time()
            return node

    def _next_id(self) -> str:
        used = {n.id for n in self.nodes}
        i = 1
        while f"P{i}" in used:
            i += 1
        return f"P{i}"

    def mark_running(self, node_id: str, worker_id: int | None = None) -> None:
        with STATE_LOCK:
            n = self.node(node_id)
            if n is None or n.status.done:
                return
            n.status = NodeStatus.RUNNING
            n.started_at = n.started_at or time.time()
            n.attempts += 1
            if worker_id is not None and worker_id not in n.worker_ids:
                n.worker_ids.append(worker_id)
            self.updated_at = time.time()

    def mark_completed(self, node_id: str, result: str = "") -> None:
        with STATE_LOCK:
            n = self.node(node_id)
            if n is None:
                return
            n.status = NodeStatus.COMPLETED
            n.finished_at = time.time()
            if result:
                n.result_summary = str(result)[:500]
            self.updated_at = time.time()

    def mark_failed(self, node_id: str, error: str = "") -> None:
        with STATE_LOCK:
            n = self.node(node_id)
            if n is None:
                return
            n.status = NodeStatus.FAILED
            n.finished_at = time.time()
            n.error = str(error)[:500]
            self.updated_at = time.time()

    def fail_running(self, error: str = "") -> list[str]:
        """把所有 RUNNING 节点标记失败（异常/取消/回合结束时调用）。"""
        with STATE_LOCK:
            ids = [n.id for n in self.nodes if n.status is NodeStatus.RUNNING]
            for nid in ids:
                n = self.node(nid)
                if n is not None:
                    n.status = NodeStatus.FAILED
                    n.finished_at = time.time()
                    n.error = str(error)[:500]
            if ids:
                self.updated_at = time.time()
            return ids

    # ---------------- 工蚁归属 ----------------

    def bind_worker(self, task_id: str, node_id: str) -> None:
        with STATE_LOCK:
            if task_id and node_id:
                self._by_task[str(task_id)] = str(node_id)

    def node_for_task(self, task_id: str) -> PlanNode | None:
        with STATE_LOCK:
            nid = self._by_task.get(str(task_id))
            return self.node(nid) if nid else None

    def bind_and_start(self, task_id: str, worker_id: int | None = None) -> PlanNode | None:
        """工蚁归巢时的便捷入口：按 task_id 找节点并推进状态。"""
        with STATE_LOCK:
            n = self.node_for_task(task_id)
            if n is None:
                return None
            self.mark_running(n.id, worker_id)
            return n


# ====================== 进程级当前计划 ======================

_current: Plan | None = None


def current() -> Plan:
    """当前计划。没有就建一个空的——UI 永远拿得到对象，不会 NPE。"""
    global _current
    with STATE_LOCK:
        if _current is None:
            _current = Plan()
        return _current


def reset(goal: str = "") -> Plan:
    """新回合开始时重置。"""
    global _current
    with STATE_LOCK:
        _current = Plan(goal=goal)
        ev.emit(ev.Event.PLAN_CREATED, plan_id=_current.plan_id, goal=goal)
        return _current


def ensure(goal: str = "") -> Plan:
    """取当前计划；没有或已换任务则新建。"""
    with STATE_LOCK:
        if _current is None:
            return reset(goal)
        return _current


def set_current(plan: Plan | None) -> None:
    global _current
    with STATE_LOCK:
        _current = plan


# ====================== 自动派生（兜底） ======================

def ensure_node_for_spawn(title: str, task_id: str = "", worker_id: int | None = None) -> PlanNode | None:
    """spawn_clone 前的兜底：保证任何工蚁都落在某个节点上。

    规则：
    - LLM 显式规划过 → 不派生，交给它自己管理节点
    - 已有 RUNNING 节点 → 复用（不并发，一次只推进一个）
    - 否则新开一个节点
    """
    with STATE_LOCK:
        plan = current()
        if plan.explicit and plan.nodes:
            running = [n for n in plan.nodes if n.status is NodeStatus.RUNNING]
            if running:
                node = running[0]
            else:
                ready = plan.ready_nodes()
                node = ready[0] if ready else plan.nodes[-1]
        else:
            running = [n for n in plan.nodes if n.status is NodeStatus.RUNNING]
            if running:
                node = running[0]
            else:
                try:
                    node = plan.add_node(title or "执行任务")
                except PlanError:
                    return None
                ev.emit(
                    ev.Event.PLAN_NODE_UPDATED, plan_id=plan.plan_id,
                    node=node.id, status=node.status.value, title=node.title,
                    derived=True,
                )
        plan.mark_running(node.id, worker_id)
        if task_id:
            plan.bind_worker(task_id, node.id)
        return node


def complete_node_for_spawn(task_id: str, status: str, result: str = "") -> PlanNode | None:
    """工蚁归巢时按 task_id 反查节点并推进。"""
    with STATE_LOCK:
        plan = current()
        node = plan.node_for_task(task_id)
        if node is None:
            return None
        if status in ("ok", "verified", "done"):
            plan.mark_completed(node.id, result)
        elif status in ("blocked", "denied"):
            plan.mark_failed(node.id, f"{status}: {result}"[:500])
        else:
            plan.mark_failed(node.id, result or status)
        ev.emit(
            ev.Event.PLAN_NODE_UPDATED, plan_id=plan.plan_id,
            node=node.id, status=node.status.value, title=node.title,
        )
        return node


# ====================== 工具入口 ======================

def update_plan(goal: str = "", nodes: str = "[]") -> str:
    """`update_plan` 工具实现。nodes 是 JSON 数组字符串。

    失败一律返回 approval_required 之外的 error，并把原因告诉模型，
    让它改一版再提交——而不是静默忽略。
    """
    try:
        parsed = json.loads(nodes or "[]")
    except ValueError as e:
        return json.dumps(
            {"status": "error", "error": f"nodes 不是合法 JSON：{e}"},
            ensure_ascii=False,
        )
    if not isinstance(parsed, list):
        return json.dumps(
            {"status": "error", "error": "nodes 必须是 JSON 数组"},
            ensure_ascii=False,
        )
    plan = ensure(goal)
    try:
        plan.replace(goal, parsed)
    except PlanError as e:
        ev.emit(
            ev.Event.PLAN_REJECTED, plan_id=plan.plan_id,
            error=str(e), count=len(parsed),
        )
        return json.dumps(
            {"status": "error", "error": f"计划被拒绝：{e}", "hint": "请修正后重新提交"},
            ensure_ascii=False,
        )
    ev.emit(
        ev.Event.PLAN_UPDATED, plan_id=plan.plan_id,
        goal=plan.goal, count=len(plan.nodes),
    )
    return json.dumps(
        {"status": "ok", "plan_id": plan.plan_id, "goal": plan.goal,
         "nodes": [n.id for n in plan.nodes], "progress": plan.progress()},
        ensure_ascii=False,
    )


def snapshot() -> dict:
    """当前计划的 dict（UI / 检查点用）。"""
    return current().to_dict()


def restore(data: dict) -> Plan:
    """从 dict 恢复计划（检查点恢复用）。"""
    global _current
    with STATE_LOCK:
        raw_nodes = data.get("nodes") if isinstance(data, dict) else None
        plan = Plan(
            goal=str((data or {}).get("goal") or ""),
            plan_id=str((data or {}).get("plan_id") or ""),
        )
        if isinstance(raw_nodes, list):
            for raw in raw_nodes:
                if not isinstance(raw, dict):
                    continue
                try:
                    node = PlanNode.from_dict(raw)
                except Exception:
                    continue
                # 缺 id 或缺 title 的节点直接丢弃：它们无法被依赖、无法展示，
                # 留在计划里只会变成永远 ready 不动的僵尸节点。
                if not node.id or not node.title:
                    continue
                plan.nodes.append(node)
        plan.explicit = bool((data or {}).get("explicit"))
        _current = plan
        ev.emit(
            ev.Event.PLAN_UPDATED, plan_id=plan.plan_id,
            goal=plan.goal, count=len(plan.nodes), restored=True,
        )
        return plan


def _self_check() -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import tempfile

    ev.reset(tempfile.mkdtemp(prefix="antnest_plan_"))
    p = Plan(goal="修复测试失败")
    p.replace("修复测试失败", [
        {"id": "P1", "title": "扫描项目"},
        {"id": "P2", "title": "执行测试", "depends_on": ["P1"]},
        {"id": "P3", "title": "修复", "depends_on": ["P2"]},
    ])
    print(p.to_prompt())
    print("ready:", [n.id for n in p.ready_nodes()])
    p.mark_completed("P1")
    print("after P1 ready:", [n.id for n in p.ready_nodes()])
    try:
        p.replace("坏计划", [
            {"id": "A", "title": "a", "depends_on": ["B"]},
            {"id": "B", "title": "b", "depends_on": ["A"]},
        ])
    except PlanError as e:
        print("环检测生效:", e)
    print("plan intact:", [n.id for n in p.nodes])
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
