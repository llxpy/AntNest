# -*- coding: utf-8 -*-
"""AntNest — 蚁后/工蚁架构的 AI Agent（聚合壳）

本文件从单文件巨石重构为「壳 + 子模块」结构，职责如下：

- antnest_config.py  启动序列/配置/共享状态/能力探测（import 期执行）
- antnest_schemas.py 12 个工具 JSON Schema（build_tool_schemas(shell)）
- antnest_queen.py   执行工具：spawn_clone 工蚁生命周期 / 文件 / CLI / Python / Web / MCP
- antnest_memory.py  记忆：记忆树 RAG 检索注入、压缩线索保留
- antnest_llm.py     LLM 调用：请求构造 / 流式解析 / 用量展示 / 重复检测
- antnest_loop.py    Agent 循环：单轮工具循环、人类主循环、任务状态查询

本壳只做三件事：
1. 按序导入子模块（保证 antnest_config 的启动序列最先执行）；
2. 把子模块的公共符号 re-export 到 AntNest 命名空间（保持 bridge / tests / prototype
   对 AntNest.xxx 的引用契约不变，_A().NAME 访问与壳一致）；
3. 提供 tool_executors 注册表、session 管理封装与 CLI 主入口。

子模块内部使用 `_A()`（延迟 import 本壳）读取共享状态，
这样任何对 AntNest.messages / AntNest.MCP_HUB 等的外部赋值都能被子模块读到。
"""
from __future__ import annotations

__version__ = "1.3.1"

import os
import sys
import re
import json
import time
import uuid
import shutil
import signal
import tempfile
import subprocess
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date

# ====================== 启动序列（由 antnest_config 执行） ======================
import antnest_config as _cfg
import antnest_schemas as _schemas
import antnest_queen as _queen
import antnest_memory as _memory
import antnest_llm as _llm
import antnest_loop as _loop
import antnest_toolforge as _toolforge

# session 模块（壳内 session 封装使用）
import antnest_session as session_mod

# 克隆工蚁运行依赖（clone worker 直接 import 本壳）
import antnest_clone_worker

# ====================== 共享状态与工具（re-export 自子模块） ======================
# 从 antnest_config 引入全部状态与启动常量
from antnest_config import *
from antnest_schemas import *
# from antnest_schemas import *（通过 build_tool_schemas 生成——见下方 _TOOLS_CATALOG）
from antnest_queen import *
from antnest_memory import *
from antnest_llm import *
from antnest_loop import *

# 新模块下划线符号（import * 不含下划线，显式补）
from antnest_config import (
    _config, _config_api, _config_agent, _load_secret_key, _config_bool,
    _thinking_requested, _retry_after_seconds, _http_error_with_body,
    _urlopen_with_retry, _CAP_SUMMARY, _ACTIVE_STREAM_RESP,
    _ACTIVE_CLONE_PROCS, _SELF_SOURCE_NAMES, _is_self_source_path,
    _command_self_source_target, _self_modification_gate,
    _runtime_prompt_context, _PROMPT_FILE, _FALLBACK_SYSTEM_PROMPT,
    _stream_emit, _kill_process_tree, _ensure_model_cap, _admin_info,
)
from antnest_queen import (
    _ARTIFACT_EXCLUDES, _ARTIFACTS_MAX_KEEP, _CLONE_POOL_MAX_KEEP,
    _RUN_PYTHON_OUTPUT_CAP, _MEMORY_TOP_K, _DUP_CALL_LIMIT,
    _DUP_RECENT_MAX, _DANGER_CLI_PATTERNS, _check_danger_command,
    _resolve_path, _worker_py_cmd, _unwrap_worker_json, _verify_clone_result,
    collect_clone_artifacts,
)
from antnest_memory import _with_retrieved_memory, _trim_tool_content
from antnest_llm import _build_request_data, display_usage
from antnest_loop import _detect_malformed_tool_call, agent_single_loop, human_loop
from antnest_toolforge import register_tool, list_tools, get_tool_source, remove_tool

# ====================== 工具 Schema 与注册表 ======================
# 工具 Schema 直接来自 antnest_schemas（import * 已引入 12 个 schema 变量）

# 工具执行器注册表（桥接 dispatch 用；函数本体来自子模块）
tool_executors = {
    "spawn_clone": spawn_clone,
    "get_task_status": get_task_status,
    "view_file": view_file,
    "list_dir": list_dir,
    "grep_files": grep_files,
    "write_file": write_file,
    "search_replace": search_replace,
    "run_cli": run_cli,
    "run_python": run_python,
    "web_fetch": web_fetch,
    "leave_memory_hints": leave_memory_hints,
    "mcp_call": mcp_call,
    "register_tool": register_tool,
    "list_tools": list_tools,
    "get_tool_source": get_tool_source,
    "mcp_list_tools": mcp_list_tools,
}

# ====================== Session 管理封装 ======================
def get_session_file():
    return session_mod.get_session_file(PROJECT_DIR, SESSION_DIR)

def acquire_lock():
    session_mod.acquire_lock(PROJECT_DIR, SESSION_DIR, IS_WINDOWS)

def release_lock():
    session_mod.release_lock(PROJECT_DIR, SESSION_DIR)

def save_session(messages, project_dir=None, session_dir=None, session_id="default"):
    pd = project_dir or PROJECT_DIR
    sd = session_dir or SESSION_DIR
    try:
        session_mod.save_session(messages, pd, sd, session_id)
    except TypeError:
        # 兼容旧版 antnest_session.py（仅 1 参数签名）：先按旧方式写入文件
        sf = session_mod.get_session_file(pd, sd, session_id)
        os.makedirs(os.path.dirname(sf), exist_ok=True)
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

def load_session(session_id="default"):
    try:
        nm = nest_md
    except NameError:
        nm = ""
    try:
        hn = hints
    except NameError:
        hn = ""
    try:
        return session_mod.load_session(
            None, SYSTEM_PROMPT, nm, hn, ENV_INFO,
            PROJECT_DIR, SESSION_DIR, session_id=session_id,
        )
    except TypeError:
        # 兼容旧版 antnest_session.py（无 session_id 参数）：直接读 JSON 文件
        sd = SESSION_DIR
        d_hash = re.sub(r"[\\/:]", "_", PROJECT_DIR)
        sf = os.path.join(sd, d_hash, f"{session_id}.json")
        if not os.path.exists(sf):
            legacy = os.path.join(sd, f"{d_hash}.json")
            sf = legacy if os.path.exists(sf) else None
        if not sf or not os.path.exists(sf):
            return None
        try:
            with open(sf, "r", encoding="utf-8") as f:
                msgs = json.load(f)
            if not isinstance(msgs, list) or len(msgs) < 2:
                return None
            msgs[0] = {"role": "system", "content": SYSTEM_PROMPT.format(
                nest_md=nm or "无", hints=hn or "无", env_info=ENV_INFO,
                model_capability=model_capability_summary(),
            )}
            return msgs
        except Exception:
            return None

def list_sessions():
    session_mod.list_sessions(PROJECT_DIR, SESSION_DIR)

def list_project_sessions():
    return session_mod.list_project_sessions(PROJECT_DIR, SESSION_DIR)

def delete_session(session_id):
    return session_mod.delete_session(PROJECT_DIR, SESSION_DIR, session_id)

def clear_session():
    session_mod.clear_session(PROJECT_DIR, SESSION_DIR)

# ====================== 系统终端信号处理 ======================
if IS_WINDOWS:
    os.environ["POWERSHELL_OUTPUT_ENCODING"] = "utf-8"
else:
    import readline

    readline.set_startup_hook()


def read_input(prompt=""):
    try:
        return input(prompt)
    except EOFError:
        return None

# ====================== CLI 主入口 ======================
def main():
    global ALLOW_ALL_CLI, messages, nest_md, hints

    parser = argparse.ArgumentParser(description="AntNest — 蚁后/工蚁架构的 AI Agent")
    parser.add_argument("-a", "--allow-all", action="store_true", help="跳过安全确认")
    parser.add_argument("-l", "--list-session", action="store_true", help="列出所有 session")
    parser.add_argument("-c", "--clear-session", action="store_true", help="清除当前目录 session")
    parser.add_argument("-u", "--user-ask", type=str, help="无交互模式，执行单条任务")
    parser.add_argument("-s", "--with-session", action="store_true", help="搭配 -u 使用，载入并保存 session")
    parser.add_argument("--until", type=str, help="搭配 -u 使用，任务达成条件（子串匹配）")
    args = parser.parse_args()

    ALLOW_ALL_CLI = args.allow_all

    if args.list_session:
        list_sessions()
        return
    elif args.clear_session:
        clear_session()
        return

    # 初始化目录
    os.makedirs(ANT_HOME, exist_ok=True)
    os.makedirs(PROJECT_ANT_DIR, exist_ok=True)
    os.makedirs(CLONE_POOL_DIR, exist_ok=True)

    # 首次任务前：按需校准模型能力并注入系统提示词（失败不致命）
    _ensure_model_cap()

    # 加载记忆
    nest_md = Path(NEST_FILE).read_text(encoding="utf-8") if Path(NEST_FILE).exists() else ""
    hints = Path(HINT_FILE).read_text(encoding="utf-8") if Path(HINT_FILE).exists() else ""
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                nest_md=nest_md or "无", hints=hints or "无", env_info=ENV_INFO,
                model_capability=model_capability_summary(),
            ),
        }
    ]

    if not args.user_ask or args.with_session:
        acquire_lock()

    depth = int(os.environ.get("AN_DEPTH", "0"))
    depth_tag = f"蚁后 (depth={depth})" if depth == 0 else f"工蚁 (depth={depth})"
    print("=" * 80)
    logo = f"AntNest ({ANT_MODEL_NAME}-{TOKEN_CAP//1000}k)"
    print(" " * ((78 - len(logo)) // 2), logo, "\n")
    print(f"> 模式: {depth_tag} | 最大深度: {MAX_DEPTH}")
    print(f"> 蚁后隔离执行 | 工蚁临时副本 + 执行后销毁")
    print("=" * 80)

    if not args.user_ask or args.with_session:
        loaded_messages = load_session()
        if loaded_messages is not None:
            messages = loaded_messages

    human_loop(args.user_ask, save_after=args.with_session, until=args.until)


if __name__ == "__main__":
    main()