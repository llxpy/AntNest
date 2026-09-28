# -*- coding: utf-8 -*-
"""AntNest · Agent 循环：任务状态查询、工具循环、人类主循环。"""
from __future__ import annotations

import json
import os
import sys

import antnest_log
import antnest_registry
import antnest_permissions
import antnest_events
_log = antnest_log.get_logger("loop")

# 审计日志实例。
#
# 回归修复：v1.3.1 的模块拆分把审计调用写成了 `_audit()`，但 AntNest 壳
# 从未 re-export `get_audit`（它只是 antnest_log 的一个函数）。于是每一行
# `AntNest.get_audit` 都抛 AttributeError，被下面的宽 except 吞成
# 「工具执行异常：module 'AntNest' has no attribute 'get_audit'」——
# **所有工具调用全部失效**，Agent 一个动作也做不了。
#
# 该 bug 之所以长期没被发现：163 个测试里没有任何一个真正驱动过
# agent_single_loop 的工具派发路径（和 Step 0 的工蚁依赖清单同类问题）。
#
# 这里直接用模块级 antnest_log.get_audit()，不依赖壳的 re-export——
# 工具函数不该走那条脆弱的 _A() 路径。
_audit = antnest_log.get_audit

# 事件日志（v1.4）。直接持有模块引用而不是走 _A()——同 get_audit 的理由。
# 事件系统 fail-open，任何异常都不会影响循环。
_ev = antnest_events

# 工具结果里被视为「未成功」的 status 词汇。
# v1.3.1 只有三项；v1.4 权限闸门引入 denied（硬拒绝），若不登记会被记成 ok，
# 让审计与统计把「被拒绝」当成「成功」。
NON_OK_STATUS = ("error", "blocked", "approval_required", "denied")

# 统计本回合已发生的工具调用数，供检查点附带
_tool_call_counter = 0


def _save_checkpoint(reason: str, rounds: int = 0) -> None:
    """在静止点保存任务检查点。**永不抛异常。**

    快照必须在工具批次闭合后取（见调用点注释），否则 messages 是撕裂的。
    plan 走 antnest_plan.snapshot()，失败时留空——检查点是增强项，不该拖垮主流程。
    """
    global _tool_call_counter
    try:
        import antnest_checkpoint as _ck

        _plan: dict = {}
        try:
            import antnest_plan as _pl
            _plan = _pl.snapshot()
        except Exception:
            _plan = {}
        _ck.save(
            reason,
            goal=str(_plan.get("goal") or "")[:500],
            messages=list(_A().messages),
            plan=_plan,
            stats={"tool_calls": _tool_call_counter, "rounds": rounds},
        )
    except Exception as e:  # pragma: no cover - 检查点是增强项
        _log.debug(f"检查点保存跳过：{e}")


def _result_status(result: str) -> str:
    """从工具结果 JSON 里取 status；解析不出则视为 ok。"""
    try:
        data = json.loads(result)
    except Exception:
        return "ok"
    if isinstance(data, dict):
        status = data.get("status")
        if status in NON_OK_STATUS:
            return str(status)
    return "ok"


def check_permission(name: str, args: dict) -> "antnest_permissions.Decision | None":
    """派发前的权限闸门。返回 None 表示放行。

    这里是**唯一**的工具级闸门（antnest_loop.py:151 是全仓库唯一的工具派发点），
    queen 内部那几处自源码门禁作为第二道防线保留——两层都要在，因为
    view_file/list_dir/... 也会间接调 spawn_clone。

    fail-closed：未在注册表登记的工具一律 DENY。
    """
    spec = antnest_registry.get(name)
    try:
        engine = _A().PERMISSION_ENGINE
    except Exception:
        return None  # 引擎尚未就绪时不阻塞运行
    if spec is None:
        return antnest_permissions.decide(
            antnest_permissions.PermLevel.EXECUTE,
            level=engine.policy.level,
            ask_above_level=engine.policy.ask_above_level,
            unknown=True, tool=name, scope="unknown",
        )
    return engine.check_tool(name, args, required=spec.level, registered=True)


def _A():
    import AntNest as _m
    return _m


def get_task_status(task_id: str) -> str:
    """查看工蚁任务的状态"""
    from task_manager import get_task_manager
    tm = get_task_manager()
    task = tm.get_task(task_id)
    if task is None:
        return json.dumps({
            "status": "error",
            "error": f"任务 {task_id} 不存在",
        }, ensure_ascii=False)
    return json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
def _detect_malformed_tool_call(content: str):
    """检测 LLM 是否将 function call 写成了纯文本（而非 stream 中的 tool_calls delta）。

    仅在匹配到明确的 JSON 格式工具调用文本或 XML 标签时才判定为格式错误，
    避免自然语言中偶然提到关键词的误报。

    工具名 alternation 由 antnest_registry 派生（覆盖 TOOL_SPECS 全集 16 个），
    取代原先手写的 13 个——那份已经漏了 register_tool / list_tools /
    get_tool_source。范围刻意宽于「本回合实际暴露」的集合：leave_memory_hints
    只在 COMPACT_PANIC 下暴露，若按 exposed 收窄，模型就能用纯文本伪造它的
    JSON 调用绕过压缩流程。
    """
    content_lower = content.lower()
    # XML 格式：tool_call / function / parameter 闭合标签
    if any(x in content_lower for x in ["</parameter>", "</function>", "</tool_call>"]):
        return True
    # JSON 格式：文本中以 {"name": 开头且有 "arguments" 键（模拟 function call）
    if antnest_registry.malformed_call_regex().search(content_lower):
        return True
    return False


class LoopResult:
    """一轮 agent 循环的结局摘要，供 UI 在没有文本回复时说清「为什么」。

    为什么必须显式返回而不是读模块全局：agent_single_loop 最多 60 轮，
    每轮都调一次模型，任何「记在模块里」的 finish_reason 都会被下一轮覆盖，
    末尾只读得到最后一轮的值——而正常多轮回合的最后一轮几乎必然是
    tool_calls 或 stop，于是「被截断」「被内容策略拦截」永远诊断不出来。
    模块全局在回合重叠时还会串写。
    """

    __slots__ = ("outcome", "rounds", "finish_reasons", "retry_notes", "error")

    def __init__(self):
        self.outcome = "replied"   # replied/empty/max_rounds/cancelled/denied/approval/error
        self.rounds = 0
        self.finish_reasons = []   # 逐轮，用于定位「哪一轮开始异常」
        self.retry_notes = []      # 空回复/畸形调用等重试告警原文
        self.error = None          # 异常对象（outcome == "error" 时）

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "rounds": self.rounds,
            "finish_reasons": list(self.finish_reasons),
            "retry_notes": list(self.retry_notes),
        }


def agent_single_loop():
    global COMPACT_PANIC, LAST_USAGE
    _A()._ensure_model_cap()
    COMPACT_PANIC = False
    break_loop = False
    _rounds = 0
    _result = LoopResult()
    _recent_cmds = []  # 最近工具调用 (name, args) 记录，用于重复循环检测
    _max_rounds = int(os.environ.get("ANT_MAX_ROUNDS", "60"))
    while not break_loop:
        _rounds += 1
        _result.rounds = _rounds
        if _rounds > _max_rounds:
            _log.warning(f"已达单任务最大轮次 {_max_rounds}，强制结束")
            _ev.emit(_ev.Event.LOOP_MAX_ROUNDS, rounds=_rounds, limit=_max_rounds)
            _result.outcome = "max_rounds"
            _A().messages.append({
                "role": "user",
                "content": (
                    f"《系统提示》已达单任务最大轮次（{_max_rounds} 轮），"
                    "请立即总结当前进度并停止，不要再调用任何工具。"
                ),
            })
            break
        # ====== 检查点：静止点快照 ======
        # 必须放在 while 顶部（工具批次已闭合），不能从工具内部调——
        # 那时 agent_single_loop 正在迭代 msg["tool_calls"]，下一步还会往
        # messages 追加，快照是撕裂的。
        if _rounds > 1:
            _save_checkpoint("round_boundary", _rounds)
        _ev.emit(_ev.Event.LOOP_ROUND, round=_rounds, limit=_max_rounds)
        if _A().AGENT_CANCEL:
            _log.info("用户强行停止")
            _ev.emit(_ev.Event.LOOP_CANCELLED, round=_rounds)
            _result.outcome = "cancelled"
            _A().messages.append({
                "role": "user",
                "content": "《系统提示》用户已强行停止当前操作。请简要确认已中断，并询问是否继续。",
            })
            break
        try:
            sys.stdout.write("\n[*] ANTNEST: ")
            sys.stdout.flush()
            tools = _A().get_queen_tools(_A().COMPACT_PANIC)
            try:
                msg, usage, _fr = _A().llm_chat_stream(
                    _A()._with_retrieved_memory(_A().messages), tools=tools
                )
                _result.finish_reasons.append(_fr or "")
            except _A().ThinkRepeatError:
                _log.warning("检测到 thinking 重复，自动中断")
                _A().messages.append({
                    "role": "user",
                    "content": "警告：你的 thinking 中出现了大量重复内容，已被擦除。请继续，不要陷入循环。",
                })
                continue

            _A().LAST_USAGE = usage
            _A().messages.append(msg)

            if _A().AGENT_CANCEL:
                _result.outcome = "cancelled"
                _result.outcome = "cancelled"

            sys.stdout.write("\n\n")
            sys.stdout.flush()

            if not msg.get("tool_calls"):
                content = msg.get("content") or ""
                if not str(content).strip():
                    _A().messages.pop()
                    # 这条告警就是模型自己算出来的诊断，原先只喂回给模型，
                    # 用户一个字都看不到。收进 LoopResult 供 UI 展示。
                    _note = "回复为空，没有输出也没有调用工具"
                    _result.retry_notes.append(_note)
                    _result.outcome = "empty"
                    _A().messages.append({
                        "role": "user",
                        "content": f"警告：{_note}，请重新回答。",
                    })
                    continue

                if _detect_malformed_tool_call(content):
                    _result.retry_notes.append("工具调用格式不正确")
                    _A().messages.append({
                        "role": "user",
                        "content": "警告：工具调用格式不正确，请以正确的格式调用 spawn_clone 工具。",
                    })
                    continue
                _result.outcome = "replied"
                break

            _approval_request = None
            _denied_request = None
            for tc in msg["tool_calls"]:
                func = tc["function"]
                name = func["name"]
                try:
                    args = json.loads(func["arguments"])

                    # 重复调用检测：连续 _DUP_CALL_LIMIT 次相同工具+相同参数 → 中断循环（防自检反复创建脚本/命令）
                    _key = (name, json.dumps(args, ensure_ascii=False)[:120])
                    _recent_cmds.append(_key)
                    if len(_recent_cmds) > _A()._DUP_RECENT_MAX:
                        del _recent_cmds[0]
                    if _recent_cmds.count(_key) >= _A()._DUP_CALL_LIMIT:
                        # 策略升级：不中断任务，改为「自我反思换方法」——
                        # 注入反思指令后继续循环，让 LLM 调整思路继续推进。
                        _log.warning(f"检测到重复调用 {name}（连续 3 次相同参数），注入自我反思引导")
                        _ev.emit(_ev.Event.LOOP_DUP_CALL, tool=name, count=_A()._DUP_CALL_LIMIT)
                        _A().messages.append({
                            "role": "user",
                            "content": (
                                "《自我反思》检测到你连续 3 次以相同参数调用同一工具，当前方法没有进展。\n"
                                "请立即停止当前做法，执行自我反思：\n"
                                "1. 重新分析任务目标与已掌握的信息；\n"
                                "2. 找出刚才方法失败或无效的原因；\n"
                                "3. 换一种可行的方法（不同工具、不同参数或不同思路）继续推进任务，不要重复刚才的调用。"
                            ),
                        })
                        _recent_cmds.clear()  # 反思轮不计入新一轮重复计数
                        continue

                    _log.debug(f"===> {name}")
                    for k, v in args.items():
                        _log.debug(f"  {k}: {v}")
                    _log.debug("")

                    _t0 = __import__("time").time()
                    global _tool_call_counter
                    _tool_call_counter += 1
                    _ev.tool_call(name, args)

                    # ====== 权限闸门（唯一工具级入口） ======
                    _denied_by_perm = False
                    try:
                        _dec = check_permission(name, args)
                    except Exception as _pe:
                        _dec = None
                        _log.warning(f"权限判定异常（按放行处理，请复查）：{_pe}")
                    if _dec is not None and not _dec.allowed:
                        _denied_by_perm = True
                        result = _dec.to_tool_result()
                        # 安全事件两边都记：audit.log 负责追责，events 负责重放
                        _audit().log_security_event(
                            _dec.action.value, f"{name}: {_dec.reason}"
                        )
                        _ev.permission(
                            _ev.Event.PERMISSION_DENIED
                            if _dec.blocked else _ev.Event.PERMISSION_REQUESTED,
                            tool=name, action=_dec.action.value, reason=_dec.reason,
                            scope=_dec.scope, level=_dec.level.label, code=_dec.code,
                        )
                        _log.warning(f"[权限] {name} → {_dec.action.value}：{_dec.reason}")
                    else:
                        result = _A().tool_executors[name](**args)

                    _dur_ms = (__import__("time").time() - _t0) * 1000
                    _status = _result_status(result)
                    _ev.tool_result(name, _status, _dur_ms)
                    # spawn_clone 失败自动重试一次（仅 error；timeout 不重试，
                    # 避免卡死命令翻倍耗时）。被权限拒绝的不重试——重试同一个
                    # 被禁的操作毫无意义。
                    if name == "spawn_clone" and not _denied_by_perm:
                        try:
                            d = json.loads(result)
                            if d.get("status") == "error":
                                _log.info(f"spawn_clone 状态=error，自动重试一次")
                                _ev.worker(_ev.Event.WORKER_RETRY, -1, task=str(args.get("label") or ""))
                                result = _A().tool_executors[name](**args)
                                _ev.tool_result(name, _result_status(result), _dur_ms)
                        except Exception:
                            pass
                except KeyboardInterrupt:
                    _log.info("工具调用已中断，回到用户 turn")
                    result = "用户中止该工具运行"
                    _result.outcome = "cancelled"
                    break_loop = True
                except Exception as e:
                    result = f"工具执行异常：{str(e)}"

                _log.debug("<=== 工具返回：")
                preview = (
                    f"{result[:6000]}\n... 后面内容省略"
                    if len(result) > 6000
                    else result
                )
                lines = preview.splitlines()
                _log.debug("\n".join(lines[:30]))
                if len(lines) > 30:
                    _log.debug("\n... 后面内容省略")
                _log.debug("")

                if name == "leave_memory_hints":
                    usage["total_tokens"] = 0
                if len(result) > _A().TOOL_RESULT_LEN:
                    half = _A().TOOL_RESULT_LEN // 2
                    result = (
                        result[:half]
                        + "\n...（中间内容已省略）...\n"
                        + result[-half:]
                    )
                _A().messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": name,
                    "content": _A().clean_input(result),
                })

                # 权限闸门的两种结局都需要停下来交给用户：
                #   approval_required → 询问（已批准/已授权后可继续）
                #   denied            → 硬拒绝，用户不介入就永远不该重试
                # 都要结束当前工具批次，避免模型在同一回合继续尝试其它写操作。
                _gate_status = ""
                _gate_data: dict = {}
                try:
                    _parsed = json.loads(result)
                    if isinstance(_parsed, dict):
                        _gate_status = str(_parsed.get("status") or "")
                        _gate_data = _parsed
                except Exception:
                    pass
                if _gate_status == "approval_required":
                    _approval_request = {
                        "path": _gate_data.get("path", ""),
                        "reason": _gate_data.get("reason", ""),
                        "scope": _gate_data.get("scope", ""),
                        "tool": name,
                    }
                    _result.outcome = "approval"
                    break_loop = True
                    break
                if _gate_status == "denied":
                    _denied_request = {
                        "reason": _gate_data.get("reason", ""),
                        "scope": _gate_data.get("scope", ""),
                        "tool": name,
                        "level": _gate_data.get("level_label", ""),
                    }
                    _result.outcome = "denied"
                    # 被硬拒绝的调用不计入重复检测：否则连续 3 次相同的被拒调用
                    # 会触发「自我反思换方法」，等于在教模型换一个方式去重试
                    # 一个刚被明确禁止的操作。
                    try:
                        _rk = (name, json.dumps(args, ensure_ascii=False)[:120])
                        while _rk in _recent_cmds:
                            _recent_cmds.remove(_rk)
                    except Exception:
                        pass
                    break_loop = True
                    break

                if _A().AGENT_CANCEL:
                    break_loop = True
                    break

                if (
                    not _A().COMPACT_PANIC
                    and usage["total_tokens"] >= _A().TOKEN_CAP * _A().COMPACT_THRESH
                ):
                    _log.warning("紧急回合，触发记忆压缩")
                    _ev.emit(_ev.Event.LOOP_COMPACT, total_tokens=usage["total_tokens"])
                    _A().COMPACT_PANIC = True
                    for i, m in enumerate(_A().messages):
                        _A().messages[i] = _A()._trim_tool_content(m)
                    _A().messages.append({"role": "user", "content": _A().COMPACT_PROMPT})

            if not _approval_request and not _denied_request:
                # 常规收尾也存一次，覆盖「工蚁归巢后 / 计划更新后」等进度节点
                _save_checkpoint("batch_closed", _rounds)

            # 兜底：确保每个 tool_call 都有 tool 响应，否则下一轮 LLM 调用会被
            # API 以「insufficient tool messages」400 拒绝（中断/重复检测提前 break 时会缺）
            for _i in range(len(_A().messages) - 1, -1, -1):
                _m = _A().messages[_i]
                if _m.get("role") != "assistant" or not _m.get("tool_calls"):
                    continue
                _responded = {
                    m.get("tool_call_id") for m in _A().messages
                    if m.get("role") == "tool" and m.get("tool_call_id")
                }
                for _tc in _m["tool_calls"]:
                    _tid = (_tc or {}).get("id")
                    if _tid and _tid not in _responded:
                        _A().messages.append({
                            "role": "tool",
                            "tool_call_id": _tid,
                            "name": ((_tc.get("function") or {}).get("name", "")),
                            "content": "（工具调用被中断，未执行）",
                        })
                break

            # 工具批次已闭合：这是保存检查点的安全静止点
            if _approval_request or _denied_request:
                _save_checkpoint(
                    "awaiting_approval" if _approval_request else "denied", _rounds
                )

            if _approval_request:
                _scope = _approval_request.get("scope") or "self_source"
                _target = _approval_request.get("path") or _approval_request.get("tool") or "该操作"
                if _scope == "danger_command":
                    _A().messages.append({
                        "role": "assistant",
                        "content": (
                            f"我准备执行一个被标记为高危的命令：{_target}。"
                            f"{_approval_request.get('reason', '')}\n"
                            "请说明你为什么需要它；如果确实必要，请明确回复"
                            "「同意执行该命令」后我再继续。"
                        ),
                    })
                elif _scope == "self_source":
                    _A().messages.append({
                        "role": "assistant",
                        "content": (
                            "我准备修改正在运行的 AntNest 核心源码："
                            f"`{_target}`。修改目的、影响范围和验证计划需要先向你说明，"
                            "请明确回复‘同意修改自身源码’后我再继续。"
                        ),
                    })
                else:
                    _A().messages.append({
                        "role": "assistant",
                        "content": (
                            f"我准备执行「{_target}」，但当前权限不足"
                            f"（{_approval_request.get('reason', '')}）。\n"
                            "请说明你为什么需要它；如果确实必要，请明确回复"
                            "「同意该操作」后我再继续。"
                        ),
                    })

            if _denied_request:
                _A().messages.append({
                    "role": "assistant",
                    "content": (
                        f"操作已被安全策略拒绝：{_denied_request.get('tool', '')}"
                        f"（{_denied_request.get('reason', '')}）。\n"
                        "这不会通过换参数或换工具绕过。请改用权限范围内的做法，"
                        "或向用户说明你需要提升权限。"
                    ),
                })

        except KeyboardInterrupt:
            _log.info("agent_single_loop 已中断，回到用户 turn")
            break_loop = True
            break
        except Exception as e:
            _result.error = e
            _result.outcome = "error"
            from antnest_errors import AntNestError
            if isinstance(e, AntNestError):
                _log.error(f"[{e.code}] LLM 调用异常：{e}")
            else:
                _log.error(f"LLM 调用异常：{e}")
            break

    return _result


# ====================== 主循环 ======================
def human_loop(user_ask=None, save_after=False, until: str = ""):
    global messages

    _A()._ensure_model_cap()

    if user_ask:
        if until:
            user_ask = (
                f"{user_ask}\n----注意，这是一个无人类参与的任务，"
                f"你需要自行判断任务是否完成。完成后输出字符串：{until}。"
                f"如果未输出，系统会提示你继续。----"
            )
        print(f"[-] You: {user_ask}\n")
        _A().messages.append({"role": "user", "content": _A().clean_input(user_ask)})

    while True:
        try:
            _A().display_usage(_A().LAST_USAGE, _A().TOKEN_CAP)
            if user_ask:
                until_rounds = 0
                until_max = int(os.environ.get("ANT_UNTIL_MAX_ROUNDS", "40"))
                while True:
                    agent_single_loop()
                    msg = _A().messages[-1]
                    if (
                        until
                        and msg.get("role") == "assistant"
                        and until in (msg.get("content") or "")
                    ):
                        break
                    until_rounds += 1
                    if until and until_rounds >= until_max:
                        _log.warning(f"已达 until 最大轮数 ({until_max})，停止等待停止字符串。")
                        break
                    _A().messages.append({
                        "role": "user",
                        "content": f"系统提示！未检测到停止字符串：{until}，请继续完成任务",
                    })

                if save_after:
                    _A().save_session(_A().messages)
                    _A().release_lock()
                break

            print("")
            user_input = _A().read_input("[-] You: ")
            if user_input is None:
                _log.info("输入结束")
                if not user_ask or save_after:
                    _A().save_session(_A().messages)
                    _A().release_lock()
                break
            user_input = user_input.strip()
            if not user_input:
                continue

            _A().messages.append({"role": "user", "content": _A().clean_input(user_input)})
            agent_single_loop()
        except KeyboardInterrupt:
            if not user_ask or save_after:
                _A().save_session(_A().messages)
                _log.info("已中断，会话已保存")
                _A().release_lock()
            else:
                _log.info("已中断")
            break
        except Exception as e:
            _log.error(f"主循环异常：{e}")
            if not user_ask or save_after:
                _A().release_lock()
            break
