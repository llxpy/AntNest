# -*- coding: utf-8 -*-
"""AntNest · 任务检查点与恢复（Checkpoint / Resume）

v1.3.1 只有 `antnest_runtime_state.record_stop`：用户按停止时存一份「决策摘要 +
证据 + 下一步」的文本，恢复时靠正则从 `messages` 里找最后一次 user 消息当查询。
那不是检查点——它不知道做过什么、不知道计划走到哪、不知道哪些工蚁在跑。

本模块提供真正的检查点：计划快照 + 消息尾部 + 事件水位，恢复时能明确回答
路线图 §6 的三问：已完成什么、正在做什么、还剩什么。

rehydrate() 而不是切片 —— 这是本模块最容易做错的地方
--------------------------------------------------------
朴素地 `messages[-12:]` 会产出**被 API 拒绝**的消息列表，且既有回填逻辑救不回来：

* 切点落在工具批次中间 → 列表**以 `role:"tool"` 开头**，其父 assistant 已丢失。
  OpenAI 兼容端点直接 400。
* `antnest_loop.py` 的回填（`_responded` + 只扫最后一个 assistant + `break`）
  只补**缺失**响应、且 `break` 后不再往前扫，无法修复开头的孤儿 tool 消息。
* 12 条几乎不含 `messages[0]`（system prompt：NEST.md / hints / ENV_INFO /
  模型能力 / 深度规则 / 工具契约）。**安全与架构规则会全部丢失。**

失败模式最恶劣：`ApiError` 被 `antnest_loop` 的宽 `except` 吞掉后 `break`，
整轮没有任何 assistant 消息，UI 什么也收不到——「能恢复」静默失效。

所以恢复走 `rehydrate()`：前置**重新构建的** system 消息 → 丢弃开头的孤儿
tool 消息 → 为残留的 `assistant(tool_calls)` 在该批次内合成响应 → 剥离
`reasoning_content` → 过一遍 `sanitize_messages_for_api` 兜底。

与 stop_snapshot 的关系
----------------------
`record_stop` 保留不动（已被测试覆盖，`_runtime_prompt_context` 依赖它）。
checkpoint 是超集，「用户停止」这一个点两者同时写。这是**已知的一处双源**，
v1.5 应统一。本轮不碰已验证路径。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import antnest_events as ev
from antnest_events import STATE_LOCK
from antnest_log import get_logger

_log = get_logger("checkpoint")

# 落盘目录名（放在 PROJECT_ANT_DIR 下，不放 ANT_HOME——后者在安装版只读）
CKPT_DIRNAME = "checkpoints"
# 每个任务保留的检查点数
KEEP_PER_TASK = 20
# 保留最近多少个任务的检查点
KEEP_TASKS = 5
# 消息尾部条数上限。**不是**「取最后 N 条」——见模块 docstring 的 rehydrate 说明。
TAIL_MAX = 12
# 思维链剥离：这些键不进检查点
_COT_KEYS = ("reasoning_content", "reasoning", "thinking", "thought")


@dataclass
class Checkpoint:
    checkpoint_id: str
    task_id: str
    seq: int
    created_at: float
    reason: str
    goal: str = ""
    plan: dict = field(default_factory=dict)
    messages_tail: list = field(default_factory=list)
    event_seq: int = 0
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Checkpoint":
        return cls(
            checkpoint_id=str(raw.get("checkpoint_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            seq=int(raw.get("seq") or 0),
            created_at=float(raw.get("created_at") or 0.0),
            reason=str(raw.get("reason") or ""),
            goal=str(raw.get("goal") or ""),
            plan=raw.get("plan") if isinstance(raw.get("plan"), dict) else {},
            messages_tail=raw.get("messages_tail") if isinstance(raw.get("messages_tail"), list) else [],
            event_seq=int(raw.get("event_seq") or 0),
            stats=raw.get("stats") if isinstance(raw.get("stats"), dict) else {},
        )

    def resume_prompt(self) -> str:
        """注入上下文的恢复提示（路线图 §6 的三问）。"""
        nodes = [n for n in (self.plan.get("nodes") or []) if isinstance(n, dict)]
        done = [str(n.get("title") or n.get("id")) for n in nodes if n.get("status") in ("completed", "skipped")]
        running = [str(n.get("title") or n.get("id")) for n in nodes if n.get("status") == "running"]
        failed = [str(n.get("title") or n.get("id")) for n in nodes if n.get("status") == "failed"]
        remaining = [
            str(n.get("title") or n.get("id")) for n in nodes
            if n.get("status") in ("pending", "blocked")
        ]
        summaries = [
            f"  - {n.get('title') or n.get('id')}：{str(n.get('result_summary'))[:200]}"
            for n in nodes
            if n.get("result_summary")
        ]
        lines = ["《任务检查点恢复》"]
        if self.goal:
            lines.append(f"目标：{self.goal}")
        lines.append(f"已完成：{'、'.join(done) or '（无）'}")
        if running:
            lines.append(f"进行中：{'、'.join(running)}")
        if failed:
            lines.append(f"已失败：{'、'.join(failed)}")
        lines.append(f"剩余：{'、'.join(remaining) or '（无）'}")
        if summaries:
            lines.append("已知结果：")
            lines.extend(summaries[:10])
        return "\n".join(lines)

    def one_line(self) -> str:
        stats = self.stats or {}
        return (
            f"#{self.seq} {self.reason} "
            f"工具 {stats.get('tool_calls', '?')} 次 / 工蚁 {stats.get('workers', '?')} 只 / "
            f"{time.strftime('%H:%M:%S', time.localtime(self.created_at))}"
        )


# ====================== 消息卫生 ======================

def strip_cot(msg: dict) -> dict:
    """剥离思维链字段。

    llm_chat_stream 会给 assistant 消息挂 `reasoning_content`
    （antnest_llm.py:254-255）。检查点落盘即明文持久化思维链——这与
    antnest_runtime_state 模块 docstring 的既有约定（「只保存决策摘要、证据和
    下一步，不保存隐藏思维链」）直接冲突。
    """
    out = dict(msg)
    for key in _COT_KEYS:
        out.pop(key, None)
    content = out.get("content")
    if isinstance(content, list):
        out["content"] = [
            {k: v for k, v in part.items() if k not in _COT_KEYS}
            if isinstance(part, dict) else part
            for part in content
        ]
    return out


def tail_messages(messages: list, limit: int = TAIL_MAX) -> list:
    """取消息尾部（不做结构修复——那是 rehydrate 的职责）。

    从末尾往前收集 limit 条，但会额外带上一个可能的 user 消息作为上下文锚点。
    """
    if not messages:
        return []
    tail = list(messages[-limit:])
    return [strip_cot(m) for m in tail if isinstance(m, dict)]


def rehydrate(tail: list, system_msg: dict) -> list:
    """把消息尾部修复成 API 可接受的完整列表。

    四步（顺序即语义）：
    1. 前置**新构建的** system 消息——切片里的 system 可能是几百轮前的旧版本，
       里面是 NEST.md / hints / ENV_INFO / 模型能力 / 深度规则 / 工具契约。
    2. 丢弃开头的孤儿 `role:"tool"` 消息，直到遇到 user 或不带 tool_calls 的
       assistant。它们的父 assistant 已经不在尾部里了。
    3. 对残留的每个 `assistant(tool_calls)`，在该批次内为未响应的 tool_call_id
       合成响应。**遍历全部批次**，不像 loop 里的回填只扫最后一个然后 break。
    4. 剥离思维链字段。
    """
    out: list[dict] = [strip_cot(system_msg)] if system_msg else []

    body: list[dict] = []
    for msg in tail or []:
        if not isinstance(msg, dict):
            continue
        # 尾部里若还带着旧的 system 消息，丢弃：它可能是几百轮前的版本
        # （过期的 NEST.md / 模型能力 / 工具契约），而且会出现两条 system。
        # 前置的 system_msg 才是当前有效的。
        if msg.get("role") == "system":
            continue
        body.append(strip_cot(msg))

    # 2) 砍掉开头的孤儿 tool 消息
    start = 0
    while start < len(body):
        m = body[start]
        if m.get("role") == "tool":
            start += 1
            continue
        break
    body = body[start:]
    # 若砍完后仍以 tool 开头（不可能，但要保证不变量），继续砍
    while body and body[0].get("role") == "tool":
        body.pop(0)

    # 3) 为未响应的 tool_call 合成响应
    responded = {
        m.get("tool_call_id") for m in body
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    filled: list[dict] = []
    for m in body:
        filled.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"] or []:
                tid = (tc or {}).get("id")
                if tid and tid not in responded:
                    responded.add(tid)
                    filled.append({
                        "role": "tool",
                        "tool_call_id": tid,
                        "name": ((tc.get("function") or {}).get("name", "")),
                        "content": "（工具调用被中断，未执行）",
                    })
    out.extend(filled)
    return out


def looks_valid(messages: list) -> bool:
    """结构自检：能否安全发给 API。"""
    if not messages or not isinstance(messages, list):
        return False
    if messages[0].get("role") != "system":
        return False
    responded = {
        m.get("tool_call_id") for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"] or []:
                if (tc or {}).get("id") and tc["id"] not in responded:
                    return False
    return True


# ====================== 存储 ======================

class CheckpointStore:
    def __init__(self, base_dir: str, keep: int = KEEP_PER_TASK) -> None:
        self.base_dir = str(base_dir or ".")
        self.root = os.path.join(self.base_dir, CKPT_DIRNAME)
        self.keep = max(1, int(keep))
        self._lock = threading.RLock()
        self._seqs: dict[str, int] = {}

    def _task_dir(self, task_id: str) -> str:
        safe = re.sub(r"[^\w.-]", "_", str(task_id or "adhoc"))[:64] or "adhoc"
        return os.path.join(self.root, safe)

    def _path(self, task_id: str, seq: int) -> str:
        return os.path.join(self._task_dir(task_id), f"{seq:04d}.json")

    def next_seq(self, task_id: str) -> int:
        with self._lock:
            cur = self._seqs.get(task_id, 0)
            latest = self.latest_seq(task_id)
            nxt = max(cur, latest) + 1
            self._seqs[task_id] = nxt
            return nxt

    def latest_seq(self, task_id: str) -> int:
        try:
            files = [
                f for f in os.listdir(self._task_dir(task_id)) if f.endswith(".json")
            ]
        except OSError:
            return 0
        best = 0
        for f in files:
            try:
                best = max(best, int(f[:-5]))
            except ValueError:
                continue
        return best

    def save(self, ckpt: Checkpoint) -> bool:
        try:
            with self._lock:
                os.makedirs(self._task_dir(ckpt.task_id), exist_ok=True)
                path = self._path(ckpt.task_id, ckpt.seq)
                fd, tmp = tempfile.mkstemp(prefix=".ckpt-", suffix=".json",
                                          dir=self._task_dir(ckpt.task_id))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(ckpt.to_dict(), f, ensure_ascii=False, indent=2)
                    os.replace(tmp, path)
                finally:
                    try:
                        os.unlink(tmp)
                    except FileNotFoundError:
                        pass
                self._prune_task(ckpt.task_id)
                self._prune_tasks()
            return True
        except Exception as e:
            _log.warning(f"检查点写入失败（已忽略）：{e}")
            return False

    def _prune_task(self, task_id: str) -> None:
        try:
            d = self._task_dir(task_id)
            files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
            for old in files[: max(0, len(files) - self.keep)]:
                try:
                    os.remove(os.path.join(d, old))
                except OSError:
                    pass
        except OSError:
            pass

    def _prune_tasks(self) -> None:
        try:
            tasks = [
                (os.path.join(self.root, t), os.path.getmtime(os.path.join(self.root, t)))
                for t in os.listdir(self.root)
                if os.path.isdir(os.path.join(self.root, t))
            ]
            if len(tasks) <= KEEP_TASKS:
                return
            tasks.sort(key=lambda x: x[1])
            for path, _ in tasks[: len(tasks) - KEEP_TASKS]:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

    def load(self, task_id: str, seq: int) -> Optional[Checkpoint]:
        path = self._path(task_id, seq)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return Checkpoint.from_dict(json.load(f))
        except (OSError, ValueError, TypeError):
            return None

    def latest(self, task_id: str) -> Optional[Checkpoint]:
        seq = self.latest_seq(task_id)
        return self.load(task_id, seq) if seq else None

    def list(self, task_id: str, newest_first: bool = False) -> list[Checkpoint]:
        """列出某任务的检查点，按 seq 升序（newest_first=True 则倒序）。"""
        out: list[Checkpoint] = []
        try:
            names = [f for f in os.listdir(self._task_dir(task_id)) if f.endswith(".json")]
        except OSError:
            return out
        seqs: list[int] = []
        for name in names:
            try:
                seqs.append(int(name[:-5]))
            except ValueError:
                continue
        # 按数值排序，不靠文件名字符串序——seq 超过 999 时 "1000" < "999"，
        # 字符串排序会给出错误的时间顺序。
        for seq in sorted(seqs, reverse=newest_first):
            ckpt = self.load(task_id, seq)
            if ckpt is not None:
                out.append(ckpt)
        return out

    def tasks(self) -> list[str]:
        try:
            return sorted(os.listdir(self.root))
        except OSError:
            return []

    def clear(self, task_id: str) -> None:
        with self._lock:
            shutil.rmtree(self._task_dir(task_id), ignore_errors=True)
            self._seqs.pop(task_id, None)


def _system_message() -> dict:
    """当前有效的 system 消息。

    不能用切片里的旧 system——那可能是几百轮前的版本，里面的 NEST.md /
    模型能力 / 工具契约 / 深度规则全已过期。恢复时必须用**当前**的。
    拿不到就退回一个最小可用的（宁可信息少，也不能让 API 400）。
    """
    try:
        import AntNest as an

        msgs = getattr(an, "messages", None)
        if isinstance(msgs, list) and msgs:
            head = msgs[0]
            if isinstance(head, dict) and head.get("role") == "system":
                return dict(head)
        base = getattr(an, "SYSTEM_PROMPT", "") or ""
        if isinstance(base, str) and base.strip():
            return {"role": "system", "content": base}
    except Exception:
        pass
    return {"role": "system", "content": "你是一个本地 AI Agent 助手，请继续之前被中断的任务。"}


def collect_stats() -> dict:
    """从事件流统计本次任务的工具调用/工蚁数（检查点附带，便于 UI 展示）。"""
    try:
        log = ev.get_log()
        rows = log.tail(2000)
        tools = sum(1 for r in rows if r.event == "WORKER_STARTED" or r.event == "TOOL_CALL")
        workers = len({r.worker_id for r in rows if r.worker_id is not None and r.worker_id > 0})
        return {
            "tool_calls": tools,
            "workers": workers,
            "events": len(rows),
        }
    except Exception:
        return {"tool_calls": 0, "workers": 0, "events": 0}


# ====================== 进程级单例 ======================

_store: Optional[CheckpointStore] = None
_store_lock = threading.Lock()


def get_store(base_dir: str = "", keep: int = 0) -> CheckpointStore:
    global _store
    with _store_lock:
        if _store is None or (base_dir and _store.base_dir != base_dir):
            _store = CheckpointStore(base_dir or os.path.join(os.getcwd(), ".antnest"),
                                     keep=keep or KEEP_PER_TASK)
        elif keep and keep != _store.keep:
            _store.keep = keep
        return _store


def reset(base_dir: str = "", keep: int = 0) -> CheckpointStore:
    global _store
    with _store_lock:
        _store = None
    return get_store(base_dir, keep)


def enabled() -> bool:
    return bool(os.environ.get("ANT_CHECKPOINT", "1").lower() not in ("0", "false", "no", "off"))


# ====================== 保存 ======================

def save(
    reason: str,
    *,
    goal: str = "",
    messages: list | None = None,
    plan: dict | None = None,
    task_id: str = "",
    stats: dict | None = None,
    keep: int = 0,
) -> Optional[Checkpoint]:
    """保存一个检查点。**永不抛异常。**

    必须在工具批次已闭合的静止点调用（antnest_loop 的 while 顶部），
    不能从工具内部调——那时 messages 正在被追加，快照是撕裂的。
    """
    if not enabled():
        return None
    try:
        tid = task_id or ev.current_task_id()
        store = get_store(keep=keep)
        log = ev.get_log()
        ckpt = Checkpoint(
            checkpoint_id=f"ck_{uuid.uuid4().hex[:8]}",
            task_id=tid,
            seq=store.next_seq(tid),
            created_at=time.time(),
            reason=str(reason or "manual")[:40],
            goal=str(goal or "")[:500],
            plan=plan if isinstance(plan, dict) else {},
            messages_tail=tail_messages(list(messages or [])),
            event_seq=log.stats().get("seq", 0) if log else 0,
            stats=stats or {},
        )
        if store.save(ckpt):
            ev.emit(
                ev.Event.CHECKPOINT_SAVED, task_id=tid,
                checkpoint_id=ckpt.checkpoint_id, reason=ckpt.reason,
                seq=ckpt.seq, tool_calls=(stats or {}).get("tool_calls"),
            )
            return ckpt
        return None
    except Exception as e:
        _log.warning(f"检查点保存异常（已忽略）：{e}")
        return None


# ====================== 恢复 ======================

def resume(
    task_id: str = "",
    *,
    system_msg: dict | None = None,
    keep: int = 0,
    restore_plan: bool = True,
) -> tuple[list, Optional[Checkpoint]]:
    """恢复一个任务的检查点。返回 (messages, checkpoint)。

    messages 已经过 rehydrate()，可以直接交给 LLM。
    """
    store = get_store(keep=keep)
    ckpt = store.latest(task_id) if task_id else None
    if ckpt is None:
        return [], None
    messages = rehydrate(ckpt.messages_tail, system_msg or _system_message())
    if restore_plan and ckpt.plan:
        try:
            import antnest_plan

            antnest_plan.restore(ckpt.plan)
        except Exception as e:
            _log.debug(f"检查点恢复计划失败（忽略）：{e}")
    ev.emit(
        ev.Event.CHECKPOINT_RESUMED, task_id=ckpt.task_id,
        checkpoint_id=ckpt.checkpoint_id, seq=ckpt.seq, reason=ckpt.reason,
    )
    return messages, ckpt


def summary(task_id: str = "") -> str:
    """人读摘要（UI / CLI 用）。"""
    rows = get_store().list(task_id, newest_first=True) if task_id else []
    if not rows:
        return "（没有检查点）"
    return "\n".join(f"  {c.one_line()}" for c in rows)


def _self_check() -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import tempfile

    d = tempfile.mkdtemp(prefix="antnest_ckpt_")
    ev.reset(d)
    store = reset(d)
    plan = {
        "goal": "修复测试失败",
        "nodes": [
            {"id": "P1", "title": "扫描项目", "status": "completed",
             "result_summary": "183 files"},
            {"id": "P2", "title": "跑测试", "status": "running"},
            {"id": "P3", "title": "修复", "status": "pending"},
        ],
    }
    ck = save("worker_done", goal="修复测试失败", plan=plan, task_id="t_demo",
              messages=[
                  {"role": "system", "content": "旧 system"},
                  {"role": "user", "content": "跑测试"},
                  {"role": "assistant", "content": "", "tool_calls": [
                      {"id": "c1", "function": {"name": "spawn_clone"}}]},
              ],
              stats={"tool_calls": 7, "workers": 3})
    print("saved:", ck.one_line() if ck else "FAILED")
    print()
    print(ck.resume_prompt() if ck else "")
    print()
    msgs, restored = resume("t_demo", system_msg={"role": "system", "content": "新 system"})
    print("rehydrated:", [(m.get("role"), str(m.get("content"))[:24]) for m in msgs])
    print("valid:", looks_valid(msgs))
    shutil.rmtree(d, ignore_errors=True)
    return 0 if ck and looks_valid(msgs) else 1


if __name__ == "__main__":
    raise SystemExit(_self_check())
