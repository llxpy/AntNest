# -*- coding: utf-8 -*-
"""AntNest · 结构化事件日志（Event Log）

v1.3.1 有三份日志，各自的定位一直很模糊：

| 文件 | 格式 | 内容 | 问题 |
|------|------|------|------|
| `.antnest/antnest.log` | JSONL | 全部 `antnest.*` logger | 是 debug 日志，不是事件流 |
| `.antnest/audit.log` | JSONL | tool_call / tool_result | 混了「发生了什么」与「谁被拦了」 |
| `.antnest/ui_trace.log` | 纯文本 | UI 阶段流水 | 只有阶段号，无法重放出任务时间线 |

`AuditLogger` 里 `log_worker_spawn` / `log_worker_done` / `log_config_change` /
`log_security_event` 四个方法从 v1.3.1 起就写好了，但**零调用点**。

本模块确立分工：

    events/<task_id>.jsonl   任务生命周期  → 可重放（replay）
    audit.log                仅安全事件    → 可追责

于是 `antnest_loop` 不再把工具事件写进 audit.log，audit 只留安全决策。

设计约束
--------
1. **fail-open**：任何 I/O 异常都不得让 Agent 循环崩溃。但首次写失败必须打
   WARNING——否则「静默丢弃」和「正常没记录」从外面看一模一样（硬约束 C4）。
2. **绝不记录推理内容**：`data` 里出现 reasoning / thinking 一类键会被剥掉。
   与 `antnest_runtime_state` 模块 docstring 的既有约定一致——只记决策、
   工具名、参数摘要、状态、耗时。
3. **落 PROJECT_ANT_DIR，不落 ANT_HOME**：ANT_HOME 在安装版指向 Program Files
   （只读），记在那儿等于所有安装用户的记录被静默丢弃。
4. **线程安全**：Agent 线程与 UI 线程并发写。`STATE_LOCK` 同时保护
   `(plan, event_seq)`，让「状态变更」与「对应事件」落在同一个临界区，
   否则 UI 会观察到有状态无事件 / 有事件无状态，时间线就不再可重放。
5. **工蚁侧事件不做**：工蚁是独立进程，汇入需要 `AN_EVENT_FILE` 跨进程写入。
   v1.4 只从父进程（蚁后侧）发 worker 生命周期事件——蚁后本来就观察得到
   spawn 的开始与结束。推迟到 v1.5。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import antnest_log

_log = antnest_log.get_logger("events")

# 运行时共享状态锁：同时保护 plan、event_seq、当前 task_id。
# agent 线程写、UI 线程读；PlanNode 是多字段读取，无锁会渲染出
# 「✓ completed 但结果为空」这种撕裂状态。
STATE_LOCK = threading.RLock()


class Event(str, Enum):
    """任务事件。值即落盘字符串。"""

    # 任务
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_PAUSED = "TASK_PAUSED"
    TASK_RESUMED = "TASK_RESUMED"
    TASK_CANCELLED = "TASK_CANCELLED"

    # 计划
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_UPDATED = "PLAN_UPDATED"
    PLAN_NODE_UPDATED = "PLAN_NODE_UPDATED"
    PLAN_REJECTED = "PLAN_REJECTED"

    # 工蚁
    WORKER_CREATED = "WORKER_CREATED"
    WORKER_STARTED = "WORKER_STARTED"
    WORKER_COMPLETED = "WORKER_COMPLETED"
    WORKER_FAILED = "WORKER_FAILED"
    WORKER_RETRY = "WORKER_RETRY"
    WORKER_CANCELLED = "WORKER_CANCELLED"
    WORKER_TIMEOUT = "WORKER_TIMEOUT"

    # 工具
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"

    # 权限
    PERMISSION_REQUESTED = "PERMISSION_REQUESTED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # 检查点
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    CHECKPOINT_RESUMED = "CHECKPOINT_RESUMED"

    # 循环
    LOOP_ROUND = "LOOP_ROUND"
    LOOP_MAX_ROUNDS = "LOOP_MAX_ROUNDS"
    LOOP_DUP_CALL = "LOOP_DUP_CALL"
    LOOP_COMPACT = "LOOP_COMPACT"
    LOOP_CANCELLED = "LOOP_CANCELLED"

    @property
    def category(self) -> str:
        return self.value.split("_", 1)[0].lower()


# 绝不允许落盘的键。分三类：
#   1) 推理内容——键名里出现即 redact（reasoning_content 也要命中，不能只锚定结尾）
#   2) 凭据——精确名单
#   3) 凭据后缀——_key / _token / _password / _secret / _credential
#      但 total_tokens / prompt_tokens / completion_tokens 是评测要用的指标，白名单放行。
_REASONING_KEY = re.compile(
    r"(reasoning|thinking|thought|chain_of_thought|cot_chain|inner_monologue)", re.I
)
_CREDENTIAL_KEY = frozenset({
    "key", "apikey", "api_key", "secret", "password", "passwd", "pwd",
    "token", "access_token", "auth_token", "refresh_token", "bearer",
    "authorization", "credential", "credentials", "cookie", "session_id_key",
})
_CREDENTIAL_SUFFIX = ("_key", "_token", "_password", "_passwd", "_secret", "_credential")
_METRIC_KEYS = frozenset({
    "total_tokens", "prompt_tokens", "completion_tokens", "tokens",
})
_REDACTED = "[已省略]"


def _is_sensitive_key(key: str) -> bool:
    k = str(key).strip()
    if _REASONING_KEY.search(k):
        return True
    low = k.lower()
    if low in _CREDENTIAL_KEY or low in _METRIC_KEYS:
        return low in _CREDENTIAL_KEY
    return low.endswith(_CREDENTIAL_SUFFIX)

# 单值截断阈值
_VALUE_MAX = 500
_NESTED_MAX = 120
# 内存环形缓冲条数（供 UI 实时展示）
RING_MAX = 500
# 单文件上限 + 保留代数
FILE_MAX_BYTES = 8 * 1024 * 1024
FILE_BACKUPS = 3
# 最多保留多少个任务的事件文件
TASKS_MAX = 20


def _safe(value: Any, depth: int = 0) -> Any:
    """把任意值压成可安全落盘的形式：截断过长字符串、剥掉敏感键。"""
    if isinstance(value, str):
        return value if len(value) <= _VALUE_MAX else value[:_VALUE_MAX] + f"...({len(value)} chars)"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 3:
        return _clip(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[:30]:
            key = str(k)
            out[key] = _REDACTED if _is_sensitive_key(key) else _safe(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_safe(v, depth + 1) for v in list(value)[:30]]
    return _clip(value)


def _clip(value: Any) -> str:
    text = str(value)
    return text if len(text) <= _NESTED_MAX else text[:_NESTED_MAX] + "..."


@dataclass
class EventRecord:
    seq: int
    ts: float
    event: str
    task_id: str = ""
    worker_id: Optional[int] = None
    node_id: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "EventRecord":
        return cls(
            seq=int(raw.get("seq") or 0),
            ts=float(raw.get("ts") or 0.0),
            event=str(raw.get("event") or ""),
            task_id=str(raw.get("task_id") or ""),
            worker_id=raw.get("worker_id"),
            node_id=str(raw.get("node_id") or ""),
            data=raw.get("data") if isinstance(raw.get("data"), dict) else {},
        )

    def line(self) -> str:
        """人读一行（replay 用）。"""
        ts = time.strftime("%H:%M:%S", time.localtime(self.ts))
        bits = [f"{ts} #{self.seq:04d}", self.event]
        if self.worker_id is not None:
            bits.append(f"worker={self.worker_id}")
        if self.node_id:
            bits.append(f"node={self.node_id}")
        detail = _summarise(self.data)
        if detail:
            bits.append(detail)
        return "  ".join(bits)


_SUMMARY_KEYS = (
    "tool", "status", "action", "reason", "title", "goal", "node", "duration_ms",
    "label", "command", "error", "checkpoint_id", "scope", "level_label",
    "completed", "remaining", "count", "code", "scope_name",
)


def _summarise(data: dict) -> str:
    parts: list[str] = []
    for key in _SUMMARY_KEYS:
        if key not in data:
            continue
        val = data[key]
        if val is None or val == "" or val == []:
            continue
        parts.append(f"{key}={_clip(val)}")
    if not parts:
        # 没有已知摘要键时退而求其次：取前两个「安全」的键
        for k, v in list(data.items())[:2]:
            if _is_sensitive_key(k):
                continue
            parts.append(f"{k}={_clip(v)}")
    return " ".join(parts)


# ====================== 事件日志 ======================

class EventLog:
    """单个事件日志实例（按 base_dir 落盘）。"""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = str(base_dir or ".")
        self._events_dir = os.path.join(self.base_dir, "events")
        self._seq = 0
        self._ring: list[EventRecord] = []
        self._write_failed = False
        # 写盘单独一把锁。不能复用 STATE_LOCK：那个锁要覆盖 plan 的状态变更，
        # 在持锁期间做文件 I/O 会把 UI 线程的读取一起阻塞住。
        # 也不能不锁——多线程各自 open(path,"a") 在 Windows 上会丢行
        # （实测 6 线程 × 40 条只剩 237 条）。
        self._write_lock = threading.Lock()

    # ---------------- 写 ----------------

    def emit(
        self,
        event: "Event | str",
        *,
        task_id: str = "",
        worker_id: int | None = None,
        node_id: str = "",
        **data: Any,
    ) -> Optional[EventRecord]:
        """记录一个事件。**永不抛异常**。

        seq 在 STATE_LOCK 内分配，保证与 plan 的状态变更同临界区。
        """
        name = event.value if isinstance(event, Event) else str(event)
        try:
            with STATE_LOCK:
                self._seq += 1
                rec = EventRecord(
                    seq=self._seq,
                    ts=time.time(),
                    event=name,
                    task_id=str(task_id or current_task_id()),
                    worker_id=worker_id,
                    node_id=str(node_id or ""),
                    data=_safe(data),
                )
                self._ring.append(rec)
                if len(self._ring) > RING_MAX:
                    del self._ring[: len(self._ring) - RING_MAX]
        except Exception as e:  # pragma: no cover - 防御
            _log.debug(f"事件记录构造失败（忽略）：{e}")
            return None
        self._write(rec)
        _notify(rec)
        return rec

    def _write(self, rec: EventRecord) -> None:
        try:
            with self._write_lock:
                os.makedirs(self._events_dir, exist_ok=True)
                path = self._path(rec.task_id)
                self._rotate(path)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
                self._prune_tasks()
        except Exception as e:
            if not self._write_failed:
                self._write_failed = True
                _log.warning(
                    f"事件日志写入失败，后续事件将只在内存中保留（任务重放不可用）：{e}"
                )
            else:
                _log.debug(f"事件日志写入失败（已静默）：{e}")

    def _path(self, task_id: str) -> str:
        safe = re.sub(r"[^\w.-]", "_", str(task_id or "adhoc"))[:64] or "adhoc"
        return os.path.join(self._events_dir, f"{safe}.jsonl")

    def _rotate(self, path: str) -> None:
        try:
            if not os.path.exists(path) or os.path.getsize(path) < FILE_MAX_BYTES:
                return
        except OSError:
            return
        for i in range(FILE_BACKUPS - 1, 0, -1):
            src, dst = f"{path}.{i}", f"{path}.{i + 1}"
            try:
                if os.path.exists(src):
                    os.replace(src, dst)
            except OSError:
                pass
        try:
            os.replace(path, f"{path}.1")
        except OSError:
            pass

    def _prune_tasks(self) -> None:
        try:
            files = [
                os.path.join(self._events_dir, f)
                for f in os.listdir(self._events_dir)
                if f.endswith(".jsonl")
            ]
            if len(files) <= TASKS_MAX:
                return
            files.sort(key=os.path.getmtime)
            for old in files[: len(files) - TASKS_MAX]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        except OSError:
            pass

    # ---------------- 读 ----------------

    def tail(self, n: int = 50) -> list[EventRecord]:
        with STATE_LOCK:
            return list(self._ring[-n:]) if n > 0 else list(self._ring)

    def records(self, task_id: str = "", limit: int = 0) -> list[EventRecord]:
        """读回落盘的事件（内存环之外的完整历史）。"""
        try:
            tasks = [task_id] if task_id else self.tasks()
            out: list[EventRecord] = []
            for tid in tasks:
                out.extend(self._read_file(tid))
            out.sort(key=lambda r: (r.ts, r.seq))
        except Exception as e:  # pragma: no cover - 读侧 fail-open
            _log.debug(f"事件读取失败（忽略）：{e}")
            return []
        return out[-limit:] if limit > 0 else out

    def _read_file(self, task_id: str) -> list[EventRecord]:
        path = self._path(task_id)
        out: list[EventRecord] = []
        if not os.path.isfile(path):
            return out
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(EventRecord.from_dict(json.loads(line)))
                    except (ValueError, TypeError):
                        continue
        except OSError:
            return out
        return out

    def tasks(self) -> list[str]:
        try:
            names = sorted(
                f[:-6] for f in os.listdir(self._events_dir) if f.endswith(".jsonl")
            )
        except (OSError, ValueError):
            # ValueError：路径含空字符等非法字符。目录不可用时返回空列表，
            # 不能让 stats()/replay() 反过来抛出去。
            return []
        return names

    def query(
        self,
        task_id: str = "",
        event: str = "",
        since_seq: int = 0,
        limit: int = 0,
    ) -> list[EventRecord]:
        rows = self.records(task_id=task_id)
        if event:
            wanted = event.value if isinstance(event, Event) else str(event)
            rows = [r for r in rows if r.event == wanted]
        if since_seq:
            rows = [r for r in rows if r.seq > since_seq]
        return rows[-limit:] if limit > 0 else rows

    def replay(self, task_id: str = "", limit: int = 0) -> str:
        """人读时间线，正是路线图 §5 想要的那种输出。"""
        try:
            rows = self.records(task_id=task_id, limit=limit)
        except Exception as e:  # pragma: no cover - 读侧同样 fail-open
            _log.debug(f"事件重放失败（忽略）：{e}")
            return f"（事件重放不可用：{e}）"
        if not rows:
            return "（该任务没有事件记录）"
        return "\n".join(r.line() for r in rows)

    def stats(self) -> dict:
        with STATE_LOCK:
            seq = self._seq
            ring = len(self._ring)
        try:
            task_count = len(self.tasks())
        except Exception:  # pragma: no cover
            task_count = -1
        return {
            "seq": seq,
            "ring": ring,
            "tasks": task_count,
            "dir": self._events_dir,
            "degraded": self._write_failed,
        }


# ====================== 当前任务上下文 ======================

_current_task = ""
_task_lock = threading.RLock()


def set_current_task_id(task_id: str) -> None:
    """回合开始时设置。task_manager 的 id 是**工蚁级**的，不是回合级。"""
    global _current_task
    with _task_lock:
        _current_task = str(task_id or "")


def current_task_id() -> str:
    with _task_lock:
        return _current_task or "adhoc"


def clear_current_task_id() -> None:
    global _current_task
    with _task_lock:
        _current_task = ""


def new_task_id(prefix: str = "t") -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ====================== 进程级单例 ======================

_log_instance: Optional[EventLog] = None
_instance_lock = threading.Lock()


def get_log(base_dir: str = "") -> EventLog:
    """进程级事件日志。base_dir 通常是 PROJECT_ANT_DIR（见硬约束 C4）。"""
    global _log_instance
    with _instance_lock:
        if _log_instance is None or (base_dir and _log_instance.base_dir != base_dir):
            _log_instance = EventLog(base_dir or os.path.join(os.getcwd(), ".antnest"))
        return _log_instance


def reset(base_dir: str = "") -> EventLog:
    """强制重建实例（测试与「切换项目目录」用）。"""
    global _log_instance
    with _instance_lock:
        _log_instance = None
    return get_log(base_dir)


def emit(event: "Event | str", **kwargs: Any) -> Optional[EventRecord]:
    return get_log().emit(event, **kwargs)


# ====================== UI 订阅 ======================

_listeners: list[Callable[[EventRecord], None]] = []
_listener_lock = threading.Lock()


def subscribe(fn: Callable[[EventRecord], None]) -> None:
    """注册 UI 监听。监听器异常会被吞掉——事件系统不能反过来搞崩 Agent。"""
    with _listener_lock:
        _listeners.append(fn)


def unsubscribe(fn: Callable[[EventRecord], None]) -> None:
    with _listener_lock:
        if fn in _listeners:
            _listeners.remove(fn)


def _notify(rec: EventRecord) -> None:
    with _listener_lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(rec)
        except Exception as e:
            _log.debug(f"事件监听器异常（忽略）：{e}")


# ====================== 便捷封装 ======================

def tool_call(name: str, args: dict, task_id: str = "") -> None:
    emit(Event.TOOL_CALL, task_id=task_id, tool=name, args=args)


def tool_result(
    name: str, status: str, duration_ms: float = 0.0, task_id: str = ""
) -> None:
    emit(
        Event.TOOL_RESULT, task_id=task_id,
        tool=name, status=status, duration_ms=round(float(duration_ms), 1),
    )


def worker(
    event: "Event | str", worker_id: int, task: str = "", status: str = "",
    duration_ms: float = 0.0, node_id: str = "", **extra: Any,
) -> None:
    """工蚁生命周期事件。worker_id 走 emit 的具名参数，不进 data。"""
    payload: dict[str, Any] = {"task": task, **extra}
    if status:
        payload["status"] = status
    if duration_ms:
        payload["duration_ms"] = round(float(duration_ms), 1)
    emit(event, worker_id=worker_id, node_id=node_id, **payload)


def permission(
    event: "Event | str", tool: str, action: str, reason: str = "",
    scope: str = "", level: str = "", **extra: Any,
) -> None:
    emit(
        event, tool=tool, action=action, reason=reason,
        scope_name=scope or None, level_label=level or None, **extra,
    )


def _self_check() -> int:
    if hasattr(__import__("sys"), "stdout") and hasattr(
        __import__("sys").stdout, "reconfigure"
    ):
        try:
            __import__("sys").stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import sys
    import tempfile

    tmp = tempfile.mkdtemp(prefix="antnest_ev_")
    log = EventLog(tmp)
    log.emit(Event.TASK_CREATED, task_id="t_demo", goal="演示")
    log.emit(Event.WORKER_STARTED, task_id="t_demo", worker_id=1, task="扫描")
    log.emit(Event.TOOL_CALL, task_id="t_demo", tool="spawn_clone", args={"command": "ls"})
    log.emit(Event.WORKER_COMPLETED, task_id="t_demo", worker_id=1, status="ok")
    log.emit(Event.TASK_COMPLETED, task_id="t_demo")
    print(log.replay("t_demo"))
    print("stats:", log.stats())
    return 0 if len(log.records("t_demo")) == 5 else 1


if __name__ == "__main__":
    raise SystemExit(_self_check())
