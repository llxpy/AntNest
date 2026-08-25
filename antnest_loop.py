# -*- coding: utf-8 -*-
"""AntNest · Agent 循环：任务状态查询、工具循环、人类主循环。"""
from __future__ import annotations

import json
import os
import re
import sys


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
    避免自然语言中偶然提到关键词的误报。"""
    content_lower = content.lower()
    # XML 格式：tool_call / function / parameter 闭合标签
    if any(x in content_lower for x in ["</parameter>", "</function>", "</tool_call>"]):
        return True
    # JSON 格式：文本中以 {"name": 开头且有 "arguments" 键（模拟 function call）
    if re.search(
        r'\{\s*"name"\s*:\s*"(?:spawn_clone|get_task_status|view_file|list_dir|grep_files|write_file|search_replace|run_cli|run_python|web_fetch|leave_memory_hints|mcp_call|mcp_list_tools)"\s*,\s*"arguments"',
        content_lower,
    ):
        return True
    return False


def agent_single_loop():
    global COMPACT_PANIC, LAST_USAGE
    _A()._ensure_model_cap()
    break_loop = False
    _rounds = 0
    _recent_cmds = []  # 最近工具调用 (name, args) 记录，用于重复循环检测
    _max_rounds = int(os.environ.get("ANT_MAX_ROUNDS", "60"))
    while not break_loop:
        _rounds += 1
        if _rounds > _max_rounds:
            print(f"\n[!] 已达单任务最大轮次 {_max_rounds}，强制结束")
            _A().messages.append({
                "role": "user",
                "content": (
                    f"《系统提示》已达单任务最大轮次（{_max_rounds} 轮），"
                    "请立即总结当前进度并停止，不要再调用任何工具。"
                ),
            })
            break
        if _A().AGENT_CANCEL:
            print("\n\n[STOP] 用户强行停止")
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
                msg, usage = _A().llm_chat_stream(_A()._with_retrieved_memory(_A().messages), tools=tools)
            except _A().ThinkRepeatError:
                print("\n\n[!] 检测到 thinking 重复，自动中断")
                _A().messages.append({
                    "role": "user",
                    "content": "警告：你的 thinking 中出现了大量重复内容，已被擦除。请继续，不要陷入循环。",
                })
                continue

            _A().LAST_USAGE = usage
            _A().messages.append(msg)

            if _A().AGENT_CANCEL:
                break

            sys.stdout.write("\n\n")
            sys.stdout.flush()

            if not msg.get("tool_calls"):
                content = msg.get("content") or ""
                if not str(content).strip():
                    _A().messages.pop()
                    _A().messages.append({
                        "role": "user",
                        "content": "警告：回复为空，没有输出也没有调用工具，请重新回答。",
                    })
                    continue

                if _detect_malformed_tool_call(content):
                    _A().messages.append({
                        "role": "user",
                        "content": "警告：工具调用格式不正确，请以正确的格式调用 spawn_clone 工具。",
                    })
                    continue
                break

            _approval_request = None
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
                        print(f"\n[!] 检测到重复调用 {name}（连续 3 次相同参数），注入自我反思引导")
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

                    print(f"===> {name}")
                    for k, v in args.items():
                        print(f"  {k}: {v}")
                    print()

                    result = _A().tool_executors[name](**args)
                    # spawn_clone 失败自动重试一次（仅 error；timeout 不重试，
                    # 避免卡死命令翻倍耗时）
                    if name == "spawn_clone":
                        try:
                            d = json.loads(result)
                            if d.get("status") == "error":
                                print(f"[重试] spawn_clone 状态=error，自动重试一次")
                                result = _A().tool_executors[name](**args)
                        except Exception:
                            pass
                except KeyboardInterrupt:
                    print("\n工具调用已中断，回到用户 turn")
                    result = "用户中止该工具运行"
                    break_loop = True
                except Exception as e:
                    result = f"工具执行异常：{str(e)}"

                print("<=== 工具返回：")
                preview = (
                    f"{result[:6000]}\n... 后面内容省略"
                    if len(result) > 6000
                    else result
                )
                lines = preview.splitlines()
                print("\n".join(lines[:30]))
                if len(lines) > 30:
                    print("\n... 后面内容省略")
                print("\n")

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

                # 自身源码修改必须先得到用户明确确认：结束当前工具批次，
                # 避免模型在同一回合继续尝试其它写操作。
                _approval = False
                try:
                    _approval_data = json.loads(result)
                    _approval = _approval_data.get("status") == "approval_required"
                except Exception:
                    pass
                if _approval:
                    _approval_request = {"path": _approval_data.get("path", "")}
                    break_loop = True
                    break

                if _A().AGENT_CANCEL:
                    break_loop = True
                    break

                if (
                    not _A().COMPACT_PANIC
                    and usage["total_tokens"] >= _A().TOKEN_CAP * _A().COMPACT_THRESH
                ):
                    print("！！！紧急回合，触发记忆压缩")
                    _A().COMPACT_PANIC = True
                    for i, m in enumerate(_A().messages):
                        _A().messages[i] = _A()._trim_tool_content(m)
                    _A().messages.append({"role": "user", "content": _A().COMPACT_PROMPT})

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

            if _approval_request:
                _approval_path = _approval_request.get("path", "")
                _A().messages.append({
                    "role": "assistant",
                    "content": (
                        "我准备修改正在运行的 AntNest 核心源码："
                        f"`{_approval_path}`。修改目的、影响范围和验证计划需要先向你说明，"
                        "请明确回复‘同意修改自身源码’后我再继续。"
                    ),
                })

        except KeyboardInterrupt:
            print("\nagent_single_loop 已中断，回到用户 turn")
            break_loop = True
            break
        except Exception as e:
            print(f"LLM 调用异常：{e}")
            break


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
                        print(f"\n[!] 已达 until 最大轮数 ({until_max})，停止等待停止字符串。")
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
                print("\n输入结束")
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
                print("\n已中断" + ("，会话已保存" if (not user_ask or save_after) else ""))
                _A().release_lock()
            else:
                print("\n已中断")
            break
        except Exception as e:
            print(f"主循环异常：{e}")
            if not user_ask or save_after:
                _A().release_lock()
            break
