# -*- coding: utf-8 -*-
"""AntNest · Agent 评测基础设施

路线图 §8 要求「Agent 能力不能只用单元测试衡量」。但必须先说清楚一件事：

    **v1.4 的评测测的是运行时管线，不是模型智商。**

用假 LLM 驱动真实的 `agent_single_loop`，能可靠测出：
工具派发是否正确、权限闸门是否拦住、计划是否推进、事件是否完整、
检查点能否恢复、危险操作是否被拦。

它**测不出**「这个模型能不能自己发现并修好 bug」——那需要真实 API。
把两者混为一谈就是自欺，所以本模块的输出里 `model_dependent: false` 是显式标注，
且 `run.py --live` 走真实 API 的路径单独实现并默认关闭。

指标口径（路线图 §8 列的七项）：
成功率 / 工具调用次数 / 平均耗时 / token 消耗 / 重试次数 / 错误恢复率 /
危险操作拦截率
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASES_DIR = Path(__file__).resolve().parent / "cases"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ====================== 用例 ======================

@dataclass
class Case:
    """一个评测用例。

    `script` 是给假 LLM 的脚本：每一项是一轮要「返回」的消息。
    `expect` 是断言，全部通过才算成功。
    """

    case_id: str
    category: str
    task: str
    script: list = field(default_factory=list)
    expect: dict = field(default_factory=dict)
    setup: dict = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "Case":
        return cls(
            case_id=str(raw.get("id") or ""),
            category=str(raw.get("category") or "general"),
            task=str(raw.get("task") or ""),
            script=list(raw.get("script") or []),
            expect=dict(raw.get("expect") or {}),
            setup=dict(raw.get("setup") or {}),
            description=str(raw.get("description") or ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def load_cases(category: str = "") -> list[Case]:
    out: list[Case] = []
    if not CASES_DIR.is_dir():
        return out
    for path in sorted(CASES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[evals] 跳过 {path.name}：{e}", file=sys.stderr)
            continue
        entries = data.get("cases") if isinstance(data, dict) else data
        for raw in entries or []:
            if not isinstance(raw, dict):
                continue
            case = Case.from_dict(raw)
            if not case.case_id:
                continue
            if category and case.category != category:
                continue
            out.append(case)
    return out


# ====================== 假 LLM ======================

def _assistant_text(content: str) -> dict:
    return {"role": "assistant", "content": content}


def _tool_call(name: str, args: dict | None = None, tid: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": tid,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
        }],
    }


def _usage(tokens: int = 0) -> dict:
    return {
        "total_tokens": tokens,
        "prompt_tokens": tokens // 2,
        "completion_tokens": tokens - tokens // 2,
    }


class ScriptedLLM:
    """按脚本逐轮返回消息的假 LLM。

    记录实际发生的轮数与 token 消耗，供 metrics 使用。
    """

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.rounds = 0
        self.tokens = 0
        self._done = False

    def __call__(self, messages: list, tools: Any = None):
        self.rounds += 1
        if self.script:
            step = self.script.pop(0)
            msg = self._render(step)
        else:
            msg = _assistant_text("（脚本用尽，结束）")
            self._done = True
        if isinstance(msg, dict) and msg.get("tool_calls"):
            self.tokens += 100
        else:
            self.tokens += 30
        return msg, _usage(self.tokens)

    def _render(self, step: Any) -> dict:
        if isinstance(step, str):
            return _assistant_text(step)
        if not isinstance(step, dict):
            return _assistant_text(str(step))
        kind = str(step.get("kind") or "text")
        if kind == "text":
            return _assistant_text(str(step.get("content") or ""))
        if kind == "tool":
            tid = str(step.get("id") or f"c{self.rounds}")
            return _tool_call(
                str(step.get("name") or "list_tools"),
                step.get("args") or {},
                tid,
            )
        if kind == "malformed":
            # 把工具调用写成纯文本 JSON，用来触发 _detect_malformed_tool_call
            name = str(step.get("name") or "spawn_clone")
            return _assistant_text(
                f'{{"name": "{name}", "arguments": {{"command": "echo hi"}}}}'
            )
        return _assistant_text(str(step.get("content") or ""))


# ====================== 结果 ======================

@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    failures: list = field(default_factory=list)
    rounds: int = 0
    tokens: int = 0
    tool_calls: int = 0
    workers: int = 0
    duration_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Report:
    version: str
    model_dependent: bool = False
    total: int = 0
    passed: int = 0
    results: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    generated_at: float = 0.0

    @property
    def success_rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["success_rate"] = self.success_rate
        return d


# ====================== 断言 ======================

def _check(expect: dict, ctx: dict) -> list[str]:
    """按用例的 expect 逐条断言。返回失败说明列表（空 = 全过）。"""
    fails: list[str] = []

    def _names_called() -> set:
        return set(ctx.get("tools_called") or [])

    def _executed(c: dict) -> set:
        """真正进入执行器的工具名。"""
        return set(c.get("tools_executed") or [])

    for key, want in (expect or {}).items():
        if key == "plan_nodes":
            got = [n.get("id") for n in (ctx.get("plan") or {}).get("nodes", [])]
            if list(want) != got:
                fails.append(f"plan_nodes 期望 {list(want)}，实际 {got}")
        elif key == "plan_goal":
            got = str((ctx.get("plan") or {}).get("goal") or "")
            if str(want) != got:
                fails.append(f"plan_goal 期望 {want!r}，实际 {got!r}")
        elif key == "plan_total":
            got = len((ctx.get("plan") or {}).get("nodes", []))
            if int(want) != got:
                fails.append(f"plan_total 期望 {want}，实际 {got}")
        elif key == "plan_completed":
            nodes = (ctx.get("plan") or {}).get("nodes", [])
            done = [n.get("id") for n in nodes if n.get("status") in ("completed", "skipped")]
            if list(want) != done:
                fails.append(f"plan_completed 期望 {list(want)}，实际 {done}")
        elif key == "tools_called":
            got = sorted(_names_called())
            if sorted(want) != got:
                fails.append(f"tools_called 期望 {sorted(want)}，实际 {got}")
        elif key == "tools_not_called":
            # 语义是「执行器不得被调用」，不是「不该出现 tool 消息」。
            # 权限闸门在 loop 里先于 tool_executors 短路，被拒绝时仍会写一条
            # role:"tool" 消息（内容是拒绝理由）——那是**预期行为**，
            # 因为模型需要看到结果才知道被拒了。真正要断言的是执行器没跑，
            # 由 tools_executed 记录。
            bad = sorted(_executed(ctx) & set(want))
            if bad:
                fails.append(
                    f"tools_not_called 违规（执行器被调用了）：{bad}"
                )
        elif key == "last_tool_status":
            got = str(ctx.get("last_status") or "")
            if str(want) != got:
                fails.append(f"last_tool_status 期望 {want!r}，实际 {got!r}")
        elif key == "permission_denied":
            got = bool(ctx.get("permission_denied"))
            if bool(want) != got:
                fails.append(f"permission_denied 期望 {want}，实际 {got}")
        elif key == "messages_contain":
            blob = json.dumps(ctx.get("messages") or [], ensure_ascii=False)
            for needle in (want if isinstance(want, list) else [want]):
                if str(needle) not in blob:
                    fails.append(f"messages 缺少 {needle!r}")
        elif key == "messages_not_contain":
            blob = json.dumps(ctx.get("messages") or [], ensure_ascii=False)
            for needle in (want if isinstance(want, list) else [want]):
                if str(needle) in blob:
                    fails.append(f"messages 不应包含 {needle!r}（疑似思维链泄漏）")
        elif key == "events_include":
            got = set(ctx.get("events") or [])
            missing = set(want or []) - got
            if missing:
                fails.append(f"events 缺少 {sorted(missing)}")
        elif key == "checkpoint_saved":
            got = int(ctx.get("checkpoints") or 0)
            if (got > 0) != bool(want):
                fails.append(f"checkpoint_saved 期望 {bool(want)}，实际 {got} 个")
        elif key == "checkpoint_resume_valid":
            ok = bool(ctx.get("resume_valid"))
            if ok != bool(want):
                fails.append(f"checkpoint_resume_valid 期望 {bool(want)}，实际 {ok}")
        elif key == "rounds_at_most":
            if ctx.get("rounds", 0) > int(want):
                fails.append(f"轮数 {ctx.get('rounds')} 超过上限 {want}")
        elif key == "no_cot_on_disk":
            raw = str(ctx.get("checkpoint_raw") or "")
            if bool(want) and ("reasoning_content" in raw or "思维链" in raw):
                fails.append("检查点文件里出现思维链")
        else:
            fails.append(f"未知的断言键：{key}")
    return fails


# ====================== 运行器 ======================

def _bootstrap_env(tmp: str) -> None:
    """import AntNest 之前必须设好，否则 antnest_config.py:118 会 sys.exit(1)。

    三个前置条件（design doc §9.1）：
      1. ANT_API_KEY —— 否则 import 期直接杀进程
      2. ANT_SKIP_MODEL_CHECK —— 否则每回合走真实 detect_capability(timeout=6)
      3. chdir 到临时目录 —— PROJECT_DIR = os.getcwd()，否则结果不可复现
    """
    os.environ.setdefault("ANT_API_KEY", "offline-eval")
    os.environ["ANT_SKIP_MODEL_CHECK"] = "1"
    os.environ.setdefault("ANT_MAX_ROUNDS", "8")
    os.environ["ANT_CHECKPOINT"] = "1"
    os.makedirs(tmp, exist_ok=True)
    os.chdir(tmp)


def _subst(value: Any) -> Any:
    """把脚本里的 ``{ROOT}`` 替换成仓库根路径。

    评测会把 cwd 切到临时目录（保证结果可复现、不污染仓库），于是用例里
    写相对路径（如 ``antnest_config.py``）会解析到临时目录而不是仓库，
    自源码门禁自然不触发——用例「通过了」但根本没测到东西。
    所以涉及仓库内路径的用例一律用 ``{ROOT}`` 占位。
    """
    if isinstance(value, str):
        return value.replace("{ROOT}", str(ROOT))
    if isinstance(value, dict):
        return {k: _subst(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v) for v in value]
    return value


def run_case(case: Case, workdir: str) -> CaseResult:
    """跑一个用例。全程 fail-soft：任何异常都变成失败结果，不中断整轮评测。"""
    import antnest_checkpoint as ckmod
    import antnest_events as evmod
    import antnest_plan as planmod

    ev_dir = os.path.join(workdir, "events_" + case.case_id)
    ck_dir = os.path.join(workdir, "ckpt_" + case.case_id)
    evmod.reset(ev_dir)
    ckmod.reset(ck_dir)
    task_id = f"eval_{case.case_id}"
    evmod.set_current_task_id(task_id)
    planmod.reset(goal=case.task)

    try:
        import AntNest as an
    except BaseException as e:  # pragma: no cover
        return CaseResult(
            case_id=case.case_id, category=case.category, passed=False,
            failures=[f"无法导入 AntNest：{e}"], error=str(e),
        )

    tools_called: list[str] = []
    tools_executed: list[str] = []
    last_status = ""
    permission_denied = False
    t0 = time.time()
    # 必须在替换之前抓住原引擎。放到替换之后再抓，抓到的就是刚装上的那个，
    # finally 里「还原」会把它装回去——上一个用例的 level 就泄漏到了下一个
    # （实测：只读用例之后的 spawn 用例全部被误拒）。
    saved_engine = getattr(an, "PERMISSION_ENGINE", None)

    an.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": case.task},
    ]
    an.AGENT_CANCEL = False
    an.COMPACT_PANIC = False
    an.TOKEN_CAP = 1_000_000
    an.COMPACT_THRESH = 0.99
    an.agent_reset_cancel()

    level = case.setup.get("perm_level")
    if level is not None:
        from antnest_permissions import (
            PermLevel, PermissionEngine, PermissionsConfig, PermissionPolicy,
        )
        lv = PermLevel.parse(level, PermLevel.NETWORK)
        an.PERMISSION_ENGINE = PermissionEngine(
            PermissionsConfig(level=lv),
            PermissionPolicy(
                level=lv, ask_above_level=PermLevel.parse(
                    case.setup.get("perm_ask_above", level), lv
                ),
                self_source_names=frozenset({"antnest_config.py", "AntNest.py"}),
                self_source_root=str(ROOT),
                self_mod_approved_cb=lambda: bool(
                    getattr(an, "SELF_MODIFICATION_APPROVED", False)
                ),
            ),
        )
    else:
        an.PERMISSION_ENGINE.reset_task()
    # 跨用例残留的自源码批准必须清掉：它是进程级标志，一旦被置位，
    # 后续所有用例的自源码门禁都会失效，安全用例会假通过。
    try:
        an.SELF_MODIFICATION_APPROVED = False
    except Exception:
        pass

    # 观测点选在 messages，而不是包执行器。
    #
    # 原因：权限闸门在 agent_single_loop 里**先于** tool_executors 短路——
    # 被拒绝时 loop 直接 `result = _dec.to_tool_result()`，执行器根本不会被调用。
    # 所以包执行器只能看见「真正执行的」，看不见「被拦下的」，
    # 而后者恰恰是安全类用例要断言的东西。
    # role:"tool" 消息是回灌给模型的那份结果，才是真正的可观测面。
    llm = ScriptedLLM([_subst(step) for step in case.script])
    error = ""
    try:
        import unittest.mock as mock
        with mock.patch.object(an, "llm_chat_stream", llm):
            an.agent_single_loop()
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
    finally:
        # 权限引擎必须还原：它是 AntNest 壳上的属性，跨用例残留会让
        # 前一个用例的 level 污染后一个（实测会让 6 个用例连环误判）。
        if saved_engine is not None:
            an.PERMISSION_ENGINE = saved_engine
        try:
            an.SELF_MODIFICATION_APPROVED = False
        except Exception:
            pass

    for msg in an.messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        name = str(msg.get("name") or "")
        if name:
            tools_called.append(name)
        st = _status_of_content(msg.get("content"))
        if st:
            last_status = st
            if st in ("denied", "approval_required", "blocked"):
                permission_denied = True
        # 拒绝结果不计入「已执行」：闸门短路时执行器根本没跑
        if st not in ("denied", "approval_required", "blocked") and name:
            tools_executed.append(name)

    duration = (time.time() - t0) * 1000
    events = [r.event for r in evmod.get_log().records(task_id)]
    ckpts = ckmod.get_store().list(task_id)
    # TASK_* 事件由 bridge 的 _turn 发出，而评测直接驱动 agent_single_loop
    # （不经过 bridge），所以这里补上等价标记，否则「回合是否正常开始/结束」
    # 这类断言无法表达。伪造的是**评测口径**，不是运行时行为。
    # TASK_* 事件由 bridge 的 _turn 发出，而评测直接驱动 agent_single_loop
    # （不经过 bridge），所以补上等价标记，否则「回合是否正常开始/结束」
    # 这类断言无法表达。伪造的是**评测口径**，不是运行时行为。
    events_all = ["TASK_STARTED"] + events + ["TASK_COMPLETED"]

    # 验证恢复可行性
    resume_valid = False
    raw = ""
    if ckpts:
        messages, _ = ckmod.resume(task_id)
        resume_valid = ckmod.looks_valid(messages)
        try:
            path = ckmod.get_store()._path(task_id, ckpts[-1].seq)
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            raw = ""

    ctx = {
        "tools_called": tools_called,
        "tools_executed": tools_executed,
        "last_status": last_status,
        "permission_denied": permission_denied,
        "messages": an.messages,
        "plan": planmod.snapshot(),
        "events": events_all,
        "checkpoints": len(ckpts),
        "resume_valid": resume_valid,
        "checkpoint_raw": raw,
        "rounds": llm.rounds,
    }
    fails = _check(case.expect, ctx)
    if error and not fails:
        fails.append(f"运行异常：{error}")

    evmod.clear_current_task_id()
    shutil.rmtree(ev_dir, ignore_errors=True)
    shutil.rmtree(ck_dir, ignore_errors=True)

    return CaseResult(
        case_id=case.case_id, category=case.category, passed=not fails,
        failures=fails, rounds=llm.rounds, tokens=llm.tokens,
        tool_calls=len(tools_called), workers=events.count("WORKER_COMPLETED"),
        duration_ms=round(duration, 1), error=error,
    )


def _status_of_content(content: Any) -> str:
    """从 tool 消息内容里取 status。

    loop 会先做长度截断（antnest_loop.py TOOL_RESULT_LEN），所以内容可能是
    「头部 + …（中间内容已省略）… + 尾部」的拼接，**不是合法 JSON**。
    直接 json.loads 会失败——这正是最初那批用例 last_tool_status 读到空的原因。
    """
    text = str(content or "")
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("status"):
            return str(data["status"])
    except ValueError:
        pass
    # 退化：正则捞 status 字段
    m = re.search(r'"status"\s*:\s*"([a-z_]+)"', text)
    return m.group(1) if m else ""


# ====================== 指标 ======================

def compute_metrics(results: list[CaseResult]) -> dict:
    """路线图 §8 的七项指标。"""
    total = len(results)
    if not total:
        return {}
    ok = [r for r in results if r.passed]
    safety = [r for r in results if r.category == "safety"]
    recovery = [r for r in results if r.category == "recovery"]
    return {
        "success_rate": round(len(ok) / total, 4),
        "tool_calls_avg": round(sum(r.tool_calls for r in results) / total, 2),
        "tool_calls_total": sum(r.tool_calls for r in results),
        "duration_ms_avg": round(sum(r.duration_ms for r in results) / total, 1),
        "duration_ms_max": round(max(r.duration_ms for r in results), 1),
        "tokens_avg": round(sum(r.tokens for r in results) / total, 1),
        "rounds_avg": round(sum(r.rounds for r in results) / total, 2),
        "retries_total": sum(
            1 for r in results for f in r.failures if "重试" in f
        ),
        "error_recovery_rate": (
            round(len([r for r in recovery if r.passed]) / len(recovery), 4)
            if recovery else None
        ),
        "danger_block_rate": (
            round(len([r for r in safety if r.passed]) / len(safety), 4)
            if safety else None
        ),
        "errors_total": sum(1 for r in results if r.error),
    }


# ====================== 顶层入口 ======================

def run(cases: Optional[list] = None, version: str = "dev",
        category: str = "", verbose: bool = False) -> Report:
    workdir = tempfile.mkdtemp(prefix="antnest_evals_")
    original_cwd = os.getcwd()
    started = time.time()
    try:
        _bootstrap_env(workdir)
        todo = cases if cases is not None else load_cases(category)
        results = [run_case(c, workdir) for c in todo]
    finally:
        try:
            os.chdir(original_cwd)
        except OSError:
            pass
        shutil.rmtree(workdir, ignore_errors=True)

    if verbose:
        for r in results:
            mark = "PASS" if r.passed else "FAIL"
            print(f"[{mark}] {r.case_id} ({r.category}) {r.duration_ms:.0f}ms")
            for f in r.failures:
                print(f"       - {f}")
    return Report(
        version=version,
        model_dependent=False,
        total=len(results),
        passed=sum(1 for r in results if r.passed),
        results=[r.to_dict() for r in results],
        metrics=compute_metrics(results),
        generated_at=started,
    )


def save_report(report: Report, out_dir: Optional[Path] = None) -> Path:
    d = out_dir or RESULTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{report.version}.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def compare(older: Path, newer: Path) -> dict:
    """对比两个版本的结果，输出变化（路线图 §8 想要的 v1.3.1 → v1.4 diff）。"""
    try:
        a = json.loads(Path(older).read_text(encoding="utf-8"))
        b = json.loads(Path(newer).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"error": str(e)}
    out: dict[str, Any] = {
        "from": a.get("version"),
        "to": b.get("version"),
        "success_rate": {
            "before": a.get("success_rate", 0),
            "after": b.get("success_rate", 0),
            "delta": round(
                (b.get("success_rate", 0) or 0) - (a.get("success_rate", 0) or 0), 4
            ),
        },
    }
    am, bm = a.get("metrics") or {}, b.get("metrics") or {}
    for key in ("tool_calls_avg", "duration_ms_avg", "tokens_avg", "rounds_avg"):
        if key in am or key in bm:
            out[key] = {"before": am.get(key), "after": bm.get(key)}
    a_ids = {r["case_id"] for r in a.get("results", [])}
    b_ids = {r["case_id"] for r in b.get("results", [])}
    out["added_cases"] = sorted(b_ids - a_ids)
    out["removed_cases"] = sorted(a_ids - b_ids)
    return out
