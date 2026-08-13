"""
AntNest — 蚁巢架构的 LLM Agent
Author: LLXPY

本体（蚁后）不直接执行命令，而是生成工蚁（自身副本）去执行。
工蚁执行完毕后销毁，只留下结果文件。
"""
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

import code_tools as ct
import antnest_clone_worker
import antnest_session as session_mod

# ====================== 路径解析 ======================
_resolved = Path(__file__).resolve()
THIS_FILE = str(_resolved)
THIS_DIR = str(_resolved.parent)

# 工蚁模式：在 LLM 加载前执行并退出
antnest_clone_worker.run_if_clone_mode()

# ====================== LLM 配置区（仅蚁后模式到达此处） ======================
# 优先级：环境变量 > config.json > 默认值
# ANT_CONFIG_PATH 由安装版启动器设置（指向 %LOCALAPPDATA%/AntNest/config.json）；
# 未设置时回退到脚本同目录（dev 模式，保持仓库内 config.json 可读）。
_config_path = os.environ.get("ANT_CONFIG_PATH") or os.path.join(THIS_DIR, "config.json")
_config = {}
if os.path.exists(_config_path):
    try:
        with open(_config_path, "r", encoding="utf-8") as _f:
            _config = json.loads(_f.read())
    except Exception:
        pass

_config_api = _config.get("api", {})
ANT_BASE_URL = os.environ.get("ANT_BASE_URL") or _config_api.get("base_url", "https://api.deepseek.com/v1")
ANT_MODEL_NAME = os.environ.get("ANT_MODEL_NAME") or _config_api.get("model_name", "deepseek-v4-flash")
ANT_API_KEY = os.environ.get("ANT_API_KEY") or _config_api.get("api_key", "")

if not ANT_API_KEY:
    print(f"错误：未配置 API Key。请在 config.json 中设置 api_key 或设置环境变量 ANT_API_KEY")
    print(f"配置文件位置：{_config_path}")
    sys.exit(1)

COMMON_HEADER = {"User-Agent": "AntNest", "Authorization": f"Bearer {ANT_API_KEY}"}

def detect_model_len():
    url = f"{ANT_BASE_URL}/models"
    req = urllib.request.Request(url, headers=COMMON_HEADER)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"错误：无法连接到 {ANT_BASE_URL}，请检查 ANT_BASE_URL 配置。\n详情：{e}")
        sys.exit(1)

    if status == 401:
        print("错误：API Key 无效或未授权，请检查 ANT_API_KEY 配置。")
        sys.exit(1)
    if status != 200:
        print(f"错误：获取模型列表失败（HTTP {status}）：{body[:200]}")
        sys.exit(1)

    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        print(f"错误：获取模型列表返回了非法 JSON：{body[:200]}")
        sys.exit(1)

    for d in out["data"]:
        if d["id"] == ANT_MODEL_NAME:
            raw = d.get("max_model_len")
            if raw:  # None / 0 / 空串等 falsy 值都走兜底
                return raw
            return {"deepseek-v4-flash": 1_000_000, "deepseek-v4-pro": 1_000_000}.get(
                ANT_MODEL_NAME, 256_000
            )
    print(f"错误：在 {ANT_BASE_URL} 上未找到模型 '{ANT_MODEL_NAME}'，请检查 ANT_MODEL_NAME 配置。")
    print(f"可用模型：{[d['id'] for d in out.get('data', [])]}")
    sys.exit(1)

# ====================== AntNest 配置区 ======================
_config_agent = _config.get("agent", {})

TOKEN_CAP = detect_model_len()
COMPACT_THRESH = float(os.environ.get("ANT_COMPACT_THRESH") or _config_agent.get("compact_threshold", 0.85))
TOOL_RESULT_LEN = int(os.environ.get("ANT_TOOL_RESULT_LEN") or _config_agent.get("tool_result_max_len", min(8000, int(TOKEN_CAP / 20))))
ALLOW_ALL_CLI = False
COMPACT_PANIC = False
LAST_USAGE = None
AGENT_CANCEL = False
_ACTIVE_STREAM_RESP = None
_ACTIVE_CLONE_PROC = None

# UI 流式回调（由 antnest_bridge 注入）：callable(phase, text)
# phase: reasoning_start | reasoning | reasoning_end | content
UI_STREAM_CB = None

# MCP（由 antnest_bridge 在 ensure_loaded 时配置）
MCP_ENABLED = False
MCP_HUB = None


def agent_reset_cancel():
    global AGENT_CANCEL
    AGENT_CANCEL = False


def agent_cancel():
    """用户强行停止：中断流式 LLM 与正在运行的工蚁。"""
    global AGENT_CANCEL
    AGENT_CANCEL = True
    resp = _ACTIVE_STREAM_RESP
    if resp is not None:
        try:
            resp.close()
        except Exception:
            pass
    proc = _ACTIVE_CLONE_PROC
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def _stream_emit(phase, text=""):
    cb = UI_STREAM_CB
    if not cb:
        return False
    try:
        cb(phase, text)
    except Exception:
        pass
    return True

ANT_HOME = os.environ.get("ANT_HOME") or os.path.join(THIS_DIR, ".antnest")
NEST_FILE = os.path.join(ANT_HOME, "NEST.md")
SESSION_DIR = os.path.join(ANT_HOME, "sessions")
CLONE_POOL_DIR = os.path.join(ANT_HOME, "clone_pool")

PROJECT_DIR = os.getcwd()
PROJECT_ANT_DIR = os.path.join(PROJECT_DIR, ".antnest")
HINT_FILE = os.path.join(PROJECT_ANT_DIR, "hints.md")

# 深度限制：蚁后=0，工蚁=1，子工蚁=2，≥3 禁止复制
MAX_DEPTH = int(os.environ.get("AN_MAX_DEPTH") or _config_agent.get("max_depth", 2))

# 各层最大并发工蚁数
_default_max_clones = {0: 10, 1: 5, 2: 3}
_cfg_max_clones = _config_agent.get("max_clones", {})
MAX_CLONES = {k: _cfg_max_clones.get(str(k), _default_max_clones[k]) if isinstance(_cfg_max_clones, dict) else _default_max_clones[k] for k in _default_max_clones}
# 蚁后最多派出 10 只工蚁，工蚁最多派出 5 只子工蚁，子工蚁最多派出 3 只子子工蚁

# ====================== 跨平台配置 ======================
IS_WINDOWS = os.name == "nt"
OS_NAME = "Windows" if IS_WINDOWS else "Linux"
SHELL = "powershell" if IS_WINDOWS else "bash"
SHELL_FLAG = "-Command" if IS_WINDOWS else "-c"

# ====================== 环境探针 ======================
def collect_env_info():
    cmds = {
        "Linux": [
            "uname -a",
            "for t in python3 python node npm git docker curl wget; do command -v $t >/dev/null 2>&1 && echo \"$t: $(${t} --version 2>&1 | head -1)\" || echo \"$t: 未安装\"; done",
            "ls -1A | grep -v '^\\.$' | grep -v '^\\..$' | while IFS= read -r f; do if [ -d \"$f\" ]; then echo \"[目录] $f\"; else echo \"[文件] $f\"; fi; done",
        ],
        "Windows": [
            "[System.Environment]::OSVersion.VersionString",
            "foreach ($t in @('python','node','git','docker','curl.exe')) { $cmd = Get-Command $t -ErrorAction SilentlyContinue; if ($cmd) { $v = & $t --version 2>&1 | Select-Object -First 1; $name = $t -replace '\\.exe$',''; Write-Output \"$name`: $v\" } else { $name = $t -replace '\\.exe$',''; Write-Output \"$name`: 未安装\" } }",
            "Get-ChildItem -Force | Where-Object { $_.Name -ne '.' -and $_.Name -ne '..' } | ForEach-Object { if ($_.PSIsContainer) { Write-Output \"[目录] $($_.Name)\" } else { Write-Output \"[文件] $($_.Name)\" } }",
        ],
    }
    labels = [
        "=== 系统 ===",
        "=== 已安装工具 ===",
        f"=== 当前目录 {PROJECT_DIR} 的目录或文件 ===",
    ]
    results = []
    shell_cmds = cmds["Windows"] if IS_WINDOWS else cmds["Linux"]
    for i, (label, cmd) in enumerate(zip(labels, shell_cmds)):
        try:
            r = subprocess.run(
                [SHELL, SHELL_FLAG, cmd],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            )
            output = r.stdout.strip()
            if not output:
                continue
            if i == 2:
                lines = output.splitlines()
                total = len(lines)
                kept, chars = [], 0
                for line in lines:
                    if len(kept) >= 100 or chars + len(line) + 1 > 2000:
                        break
                    kept.append(line)
                    chars += len(line) + 1
                output = "\n".join(kept)
                hidden = total - len(kept)
                if hidden > 0:
                    output += f"\n...还有 {hidden} 个文件未显示"
            results.append(f"{label}\n{output}")
        except Exception:
            pass

    today = date.today().strftime("%Y-%m-%d")
    return f"=== 今天日期 ===\n{today}\n\n" + ("\n\n".join(results) if results else "环境信息获取失败")


ENV_INFO = collect_env_info()

# ====================== System Prompt ======================
SYSTEM_PROMPT = f"""
# 你是谁
你是 AntNest，一个采用蚁后/工蚁架构的 AI Agent。

# 架构说明
你当前运行在 **蚁后模式（depth=0）**。你的身体从不执行命令——
- 你需要执行操作时，使用 spawn_clone 生成 **工蚁**（自身的临时副本）
- 工蚁在隔离的临时目录中执行命令，完成后将结果写入文件
- 工蚁退出后本体读取结果，然后销毁工蚁的所有临时文件

# 复制规则（极其重要）
- 蚁后（depth=0）只能 spawn_clone，不能直接执行命令
- 工蚁（depth=1）可以 spawn_clone 拆分子任务，最多 {MAX_CLONES[1]} 只子工蚁
- 子工蚁（depth=2）最多 {MAX_CLONES[2]} 只，之后禁止继续复制，降级为直接执行
- depth>={MAX_DEPTH+1} 时禁止 spawn_clone，只能直接执行命令
- 并发任务无依赖时，一次性 spawn 多只工蚁并行加速

# 你在哪
- 操作系统：{OS_NAME}
- 项目空间：{PROJECT_DIR}
- 你的私人空间：{ANT_HOME}（存放 NEST.md 记忆、session、工蚁临时文件）
- 项目记忆空间：{PROJECT_ANT_DIR}
- 记忆容量：{TOKEN_CAP} 个 token

# 当前环境
{{env_info}}

# 你要做什么
1. 帮助人类完成任务，结果要可验证、可靠
2. 接收到任务时先检查 **固化的知识及规则** 和 **记忆线索** 中是否有相关技能
3. 任务未完成前持续调用工具直到完成
4. 任务完成后主动验证结果

# 工具调用说明
- spawn_clone：生成工蚁执行命令（改系统、跑构建、复杂 shell 必须用此工具）
- view_file：派工蚁只读查看文件（分段、带行号）
- list_dir：派工蚁只读列目录
- grep_files：派工蚁在目录/文件中搜索文本（写代码前定位用）
- write_file：派工蚁写入/覆盖文件（蚁后不亲自写盘）
- search_replace：派工蚁精确替换文件中的一段文本（改代码首选，比 write_file 整文件覆盖更安全）
- run_cli：仅在 clone 模式（depth>0）下可用
- leave_memory_hints：记忆压缩时保留关键线索

# 固化的知识及规则（读取自 {NEST_FILE}）
<knowledge_and_rules>
{{nest_md}}
</knowledge_and_rules>

# 记忆线索（读取自 {HINT_FILE}）
<memory_hints>
{{hints}}
</memory_hints>
"""

COMPACT_PROMPT = r"""《紧急危机》！！！记忆容量即将达到上限。

你需要紧急完成：
1. 保存记忆：将对话中的关键信息整理写入记忆文件
2. 保存技能和知识：将对未来有用的技能/知识写入 NEST.md。每条必须包含【触发条件】和【内容】
3. 调用 leave_memory_hints 留下记忆线索

过程中不要中断，直到 leave_memory_hints 被执行。"""

# ====================== 工具定义 ======================
spawn_clone_schema = {
    "type": "function",
    "function": {
        "name": "spawn_clone",
        "description": (
            f"生成一只工蚁（自身副本）去执行命令。工蚁在隔离的临时目录中运行，"
            f"执行完毕后将结果写入文件，然后退出。本体会读取结果并销毁工蚁文件。"
            f"当前为蚁后模式（depth=0），所有执行必须通过此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "工蚁要执行的完整命令",
                },
                "timeout": {
                    "type": "integer",
                    "default": 300,
                    "description": "超时时间（秒）",
                },
                "label": {
                    "type": "string",
                    "description": "工蚁标签，用于日志追踪，如 '统计repo-a'",
                },
            },
            "required": ["command"],
        },
    },
}

view_file_schema = {
    "type": "function",
    "function": {
        "name": "view_file",
        "description": (
            "派工蚁只读查看文件内容（蚁后自己不读文件）。"
            "相对路径基于项目目录；大文件请用 start_line/end_line 分段查看。"
            "返回带行号的文本。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                "start_line": {
                    "type": "integer",
                    "default": 1,
                    "description": "起始行号（从 1 开始，含）",
                },
                "end_line": {
                    "type": "integer",
                    "default": 0,
                    "description": "结束行号（含）；0 表示读到文件末尾",
                },
            },
            "required": ["path"],
        },
    },
}

list_dir_schema = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": (
            "派工蚁只读列出目录内容（蚁后自己不列目录）。"
            "相对路径基于项目目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "目录路径（相对或绝对）",
                },
                "max_entries": {
                    "type": "integer",
                    "default": 100,
                    "description": "最多返回条目数",
                },
            },
        },
    },
}

grep_files_schema = {
    "type": "function",
    "function": {
        "name": "grep_files",
        "description": (
            "派工蚁在目录或文件中搜索匹配行（正则）。"
            "用于定位符号、函数、配置项。相对路径基于项目目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "搜索根路径（文件或目录）",
                },
                "glob": {
                    "type": "string",
                    "default": "*",
                    "description": "文件名 glob，如 *.py",
                },
                "max_matches": {
                    "type": "integer",
                    "default": 50,
                    "description": "最多返回匹配数",
                },
            },
            "required": ["pattern"],
        },
    },
}

write_file_schema = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "派工蚁写入或覆盖文件（蚁后自己不写盘）。"
            "会自动创建父目录。相对路径基于项目目录。"
            "改已有文件时优先用 search_replace。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
    },
}

search_replace_schema = {
    "type": "function",
    "function": {
        "name": "search_replace",
        "description": (
            "派工蚁在文件中精确替换一段文本（蚁后自己不写盘）。"
            "默认只替换第一处；若 old_string 多处出现且未设 replace_all，会报错以防误改。"
            "修改已有代码时应优先于 write_file。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {
                    "type": "string",
                    "description": "要被替换的原文（需与文件内容完全一致，含缩进与换行）",
                },
                "new_string": {"type": "string", "description": "替换后的内容"},
                "replace_all": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=替换所有匹配；false=仅第一处，且多处匹配时报错",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

run_cli_schema = {
    "type": "function",
    "function": {
        "name": "run_cli",
        "description": (
            f"直接执行 {SHELL} 命令。仅在工蚁模式（depth>0）下可用。"
            f"蚁后模式必须使用 spawn_clone。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {
                    "type": "integer",
                    "default": 300,
                    "description": "超时时间（秒）",
                },
            },
            "required": ["command"],
        },
    },
}

memory_hints_schema = {
    "type": "function",
    "function": {
        "name": "leave_memory_hints",
        "description": (
            "对消息历史进行压缩并将记忆线索写入 hints.md。"
            "参数 hints 是要保存的记忆线索文本。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hints": {"type": "string", "description": "记忆线索文本"},
            },
            "required": ["hints"],
        },
    },
}

mcp_call_schema = {
    "type": "function",
    "function": {
        "name": "mcp_call",
        "description": (
            "调用已连接的 MCP 服务器工具（需先在设置中启用 MCP）。"
            "server 为 mcp.json 里配置的服务器名，tool 为 tools/list 返回的工具名，"
            "arguments 为 JSON 对象字符串。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP 服务器名称"},
                "tool": {"type": "string", "description": "工具名称"},
                "arguments": {
                    "type": "string",
                    "default": "{}",
                    "description": "JSON 格式的参数对象",
                },
            },
            "required": ["server", "tool"],
        },
    },
}

mcp_list_tools_schema = {
    "type": "function",
    "function": {
        "name": "mcp_list_tools",
        "description": "列出所有已连接 MCP 服务器及其工具（启用 MCP 后可用）。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def get_queen_tools(compact_panic: bool = False) -> list:
    """蚁后可用工具列表（含可选 MCP）。"""
    if compact_panic:
        base = [spawn_clone_schema, memory_hints_schema]
    else:
        base = [
            spawn_clone_schema,
            view_file_schema,
            list_dir_schema,
            grep_files_schema,
            write_file_schema,
            search_replace_schema,
        ]
    if MCP_ENABLED and MCP_HUB is not None:
        base.extend([mcp_call_schema, mcp_list_tools_schema])
    return base


def mcp_call(server: str, tool: str, arguments: str = "{}") -> str:
    if not MCP_ENABLED or MCP_HUB is None:
        return json.dumps({"status": "error", "error": "MCP 未启用或未加载"}, ensure_ascii=False)
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            return json.dumps({"status": "error", "error": "arguments 必须是 JSON 对象"}, ensure_ascii=False)
        return MCP_HUB.call(server, tool, args)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def mcp_list_tools() -> str:
    if not MCP_ENABLED or MCP_HUB is None:
        return json.dumps({"status": "error", "error": "MCP 未启用或未加载"}, ensure_ascii=False)
    try:
        return json.dumps({"status": "ok", "tools": MCP_HUB.list_catalog()}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

# ====================== 工蚁生命周期 ======================
def spawn_clone(command: str, timeout: int = 300, label: str = "") -> str:
    """
    生成工蚁执行命令。
    1. 检查深度限制（硬拦截）
    2. 在 clone_pool 下创建隔离目录
    3. 命令通过文件传递（避免环境变量截断）
    4. 复制 AntNest.py 到隔离目录
    5. 以 clone 模式启动工蚁
    6. 等待工蚁执行完毕，读取结果
    7. 销毁工蚁目录
    """
    # ====== 深度硬拦截：代码级兜底，不依赖 LLM 遵守 prompt ======
    current_depth = int(os.environ.get("AN_DEPTH", "0"))
    if current_depth > MAX_DEPTH:
        return json.dumps({
            "status": "blocked",
            "error": (
                f"深度限制：当前 depth={current_depth} 已超过 MAX_DEPTH={MAX_DEPTH}，"
                f"spawn_clone 已被硬拦截。请改用 run_cli 直接执行。"
            ),
        }, ensure_ascii=False)

    clone_id = uuid.uuid4().hex[:8]
    clone_dir = os.path.join(CLONE_POOL_DIR, f"clone_{clone_id}")
    os.makedirs(clone_dir, exist_ok=True)

    # 复制蚁后脚本及工蚁模式依赖（工蚁在隔离目录 import，必须随包带上）
    clone_script = os.path.join(clone_dir, "AntNest.py")
    shutil.copy2(THIS_FILE, clone_script)
    for _dep in ("antnest_clone_worker.py", "code_tools.py", "antnest_session.py"):
        _src = os.path.join(THIS_DIR, _dep)
        if os.path.isfile(_src):
            shutil.copy2(_src, os.path.join(clone_dir, _dep))

    result_file = os.path.join(clone_dir, "result.json")
    log_file = os.path.join(clone_dir, "clone.log")

    # ====== 命令传递：超长命令写文件，避免环境变量截断 ======
    MAX_ENV_LEN = 8000  # Windows 环境变量限制约 32KB，留足余量
    if len(command) > MAX_ENV_LEN:
        cmd_file = os.path.join(clone_dir, "command.txt")
        with open(cmd_file, "w", encoding="utf-8") as f:
            f.write(command)
        use_cmd_file = True
        print(f"[警告] 命令长度 {len(command)} 字符，改用文件传递")
    else:
        use_cmd_file = False

    print(f"\n[工蚁] {label or clone_id} → {clone_dir}")

    try:
        env = os.environ.copy()
        env["AN_CLONE_MODE"] = "1"
        env["AN_CLONE_DIR"] = clone_dir
        env["AN_RESULT_FILE"] = result_file
        env["AN_CLONE_TIMEOUT"] = str(timeout)
        env["AN_DEPTH"] = str(current_depth + 1)

        if use_cmd_file:
            env["AN_CLONE_COMMAND_FILE"] = cmd_file
            env["AN_CLONE_COMMAND"] = ""
        else:
            env["AN_CLONE_COMMAND"] = command

        proc = subprocess.Popen(
            [sys.executable, clone_script],
            cwd=clone_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        global _ACTIVE_CLONE_PROC
        _ACTIVE_CLONE_PROC = proc

        try:
            deadline = time.time() + timeout + 60
            while proc.poll() is None:
                if AGENT_CANCEL:
                    proc.kill()
                    proc.wait(timeout=5)
                    return json.dumps({
                        "status": "cancelled",
                        "error": "用户强行停止，工蚁已终止",
                    }, ensure_ascii=False)
                if time.time() > deadline:
                    proc.kill()
                    proc.wait(timeout=5)
                    return json.dumps({
                        "status": "timeout",
                        "error": f"工蚁执行超时（{timeout}秒）",
                        "clone_dir": clone_dir,
                    }, ensure_ascii=False)
                time.sleep(0.15)
            stdout, stderr = proc.communicate()
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            return json.dumps({
                "status": "timeout",
                "error": f"工蚁执行超时（{timeout}秒）",
                "clone_dir": clone_dir,
            }, ensure_ascii=False)
        finally:
            _ACTIVE_CLONE_PROC = None

        # 读取工蚁结果
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                result = f.read()
        else:
            result = json.dumps({
                "status": "error",
                "error": "工蚁未生成结果文件",
                "stdout": stdout[:2000] if stdout else "",
                "stderr": stderr[:2000] if stderr else "",
            }, ensure_ascii=False)

        print(f"[工蚁] {label or clone_id} 完成 (exit={proc.returncode})")

        return result

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"工蚁管理异常：{str(e)}",
            "clone_dir": clone_dir,
        }, ensure_ascii=False)

    finally:
        # 销毁工蚁目录
        try:
            shutil.rmtree(clone_dir, ignore_errors=True)
        except Exception:
            pass


def _resolve_path(path: str) -> Path:
    return ct.resolve_path(path, PROJECT_DIR)


def _worker_py_cmd(py_code: str) -> str:
    return ct.worker_py_cmd(py_code, is_windows=IS_WINDOWS, python_exe=sys.executable)


def _unwrap_worker_json(spawn_result: str) -> str:
    return ct.unwrap_worker_json(spawn_result)


def view_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """派工蚁只读查看文件（蚁后自己不读）。"""
    p = _resolve_path(path)
    py = ct.build_view_file_script(p, start_line, end_line)
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"查看·{p.name}")
    return _unwrap_worker_json(raw)


def list_dir(path: str = ".", max_entries: int = 100) -> str:
    """派工蚁只读列目录（蚁后自己不列）。"""
    p = _resolve_path(path)
    py = ct.build_list_dir_script(p, max_entries)
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"列目录·{p.name}")
    return _unwrap_worker_json(raw)


def grep_files(
    pattern: str,
    path: str = ".",
    glob: str = "*",
    max_matches: int = 50,
) -> str:
    """派工蚁搜索文件内容（蚁后自己不搜）。"""
    err = ct.validate_grep_pattern(pattern)
    if err:
        return json.dumps({"status": "error", "error": f"无效正则：{err}"}, ensure_ascii=False)
    p = _resolve_path(path)
    py = ct.build_grep_script(p, pattern, glob, max_matches)
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=120, label=f"搜索·{pattern[:24]}")
    return _unwrap_worker_json(raw)


def write_file(path: str, content: str) -> str:
    """派工蚁写入文件（蚁后自己不写）。"""
    p = _resolve_path(path)
    py = ct.build_write_file_script(p, content or "")
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"写入·{p.name}")
    return _unwrap_worker_json(raw)


def search_replace(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """派工蚁精确替换文件片段（蚁后自己不写）。"""
    p = _resolve_path(path)
    py = ct.build_search_replace_script(p, old_string, new_string, replace_all)
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"替换·{p.name}")
    return _unwrap_worker_json(raw)


def run_cli(command: str, timeout: int = 300) -> str:
    """工蚁模式下直接执行命令。蚁后模式不应调用此函数。"""
    try:
        result = subprocess.run(
            [SHELL, SHELL_FLAG, command],
            capture_output=True,
            text=True,
            errors="replace",
            cwd=os.getcwd(),
            timeout=timeout,
            shell=False,
        )
        output = f"Exit code: {result.returncode}\n{result.stdout}"
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"执行超时（{timeout}秒）"
    except Exception as e:
        return f"执行失败：{str(e)}"


# ====================== 记忆管理 ======================
def _trim_tool_content(msg):
    if msg.get("role") == "tool" and msg.get("content") and len(msg["content"]) > 400:
        c = msg["content"]
        msg = {**msg, "content": c[:200] + "\n…（中间内容已省略）…\n" + c[-200:]}
    return msg


def leave_memory_hints(hints):
    global messages, COMPACT_PANIC

    try:
        nest_text = Path(NEST_FILE).read_text(encoding="utf-8") if Path(NEST_FILE).exists() else ""
    except Exception:
        nest_text = globals().get("nest_md", "") or ""

    compact_i = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user" and messages[i]["content"] == COMPACT_PROMPT:
            compact_i = i
            break

    last_user_i = compact_i - 1
    for i in range(last_user_i, -1, -1):
        if messages[i]["role"] == "user":
            last_user_i = i
            break

    kept = []
    for m in messages[last_user_i:compact_i]:
        kept.append(_trim_tool_content(m))

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                nest_md=nest_text or "无", hints=hints or "无", env_info=ENV_INFO
            ),
        },
        {
            "role": "user",
            "content": (
                "《系统提示》记忆已压缩。你之前调用 leave_memory_hints 留下了关键线索"
                "（见系统提示中的记忆线索区块），请确认任务状态并继续。\n"
            ),
        },
    ] + kept

    COMPACT_PANIC = False

    os.makedirs(PROJECT_ANT_DIR, exist_ok=True)
    with open(HINT_FILE, "w", encoding="utf-8") as f:
        f.write(hints)
    return "已留下记忆线索，对话已压缩。"


# ====================== LLM 调用 ======================
def clean_input(text):
    if not isinstance(text, str):
        return str(text)
    text = re.sub(r"[\ud800-\udfff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


def _build_request_data(messages, tools=None, temperature=0.6, thinking=True, stream=False):
    data = {
        "model": ANT_MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "chat_template_kwargs": {"enable_thinking": thinking},
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if tools:
        data["tools"] = tools
    if stream:
        data["stream"] = True
        data["stream_options"] = {"include_usage": True}
    return data


def display_usage(usage, cap):
    t = usage.get("total_tokens", 0) if usage else 0
    if t == 0 or cap <= 0:
        return
    p = min(t / cap, 1.0)
    bar = "\u2588" * int(p * 20) + "\u2591" * (20 - int(p * 20))
    print(f"\033[2mCTX [{bar}] {p:.0%}  ({t//1000}k/{cap//1000}k)\033[0m")


class ThinkRepeatError(Exception):
    pass


class RepeatSuffixChecker:
    """Rabin-Karp 滚动哈希检测 thinking 重复循环"""

    def __init__(self, min_unit_len: int, base: int = 91138233, mod: int = 10**9 + 7):
        self.min_unit_len = min_unit_len
        self.base = base
        self.mod = mod
        self.prefix_hash = [0]
        self.pow_base = [1]

    def _get_hash(self, l: int, r: int) -> int:
        return (self.prefix_hash[r] - self.prefix_hash[l] * self.pow_base[r - l]) % self.mod

    def add_char(self, ch: str) -> bool:
        self.pow_base.append((self.pow_base[-1] * self.base) % self.mod)
        new_hash = (self.prefix_hash[-1] * self.base + ord(ch)) % self.mod
        self.prefix_hash.append(new_hash)

        n = len(self.prefix_hash) - 1
        if n < 2 * self.min_unit_len or n % self.min_unit_len != 0:
            return False

        for unit_len in range(n // 2, self.min_unit_len - 1, -1):
            if self._get_hash(n - 2 * unit_len, n - unit_len) == self._get_hash(
                n - unit_len, n
            ):
                return True
        return False


def llm_chat_stream(messages, tools=None, temperature=0.6, thinking=True):
    url = f"{ANT_BASE_URL}/chat/completions"
    data = _build_request_data(messages, tools, temperature, thinking, stream=True)
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={**COMMON_HEADER, "Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req)
        global _ACTIVE_STREAM_RESP
        _ACTIVE_STREAM_RESP = resp
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise Exception(f"LLM调用失败，HTTP {e.code}: {raw[:500]}")

    content_parts = []
    reasoning_parts = []
    tool_calls_map = {}
    usage = None
    role = "assistant"
    is_thinking = False

    detector = RepeatSuffixChecker(min_unit_len=400)

    try:
        for raw_line in resp:
            if AGENT_CANCEL:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break

            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if "usage" in chunk and chunk["usage"]:
                usage = chunk["usage"]

            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            if not delta:
                continue

            role = delta.get("role") or role

            reasoning_content = (
                delta.get("reasoning_content") or delta.get("reasoning") or ""
            )
            if reasoning_content:
                if not is_thinking:
                    is_thinking = True
                    if not _stream_emit("reasoning_start"):
                        sys.stdout.write("\033[2m💭 ")
                if not _stream_emit("reasoning", reasoning_content):
                    sys.stdout.write(reasoning_content)
                    sys.stdout.flush()
                reasoning_parts.append(reasoning_content)
                for c in reasoning_content:
                    if detector.add_char(c):
                        raise ThinkRepeatError()

            text = delta.get("content") or ""
            if text:
                if is_thinking:
                    is_thinking = False
                    if not _stream_emit("reasoning_end"):
                        sys.stdout.write("\033[0m\n")
                if not _stream_emit("content", text):
                    sys.stdout.write(text)
                    sys.stdout.flush()
                content_parts.append(text)

            if "tool_calls" in delta and delta["tool_calls"]:
                for tc_delta in delta["tool_calls"]:
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc_delta.get("id", ""),
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    tc_entry = tool_calls_map[idx]
                    if tc_delta.get("id"):
                        tc_entry["id"] = tc_delta["id"]
                    func_delta = tc_delta.get("function", {})
                    if func_delta.get("name"):
                        tc_entry["function"]["name"] += func_delta["name"]
                    if func_delta.get("arguments"):
                        tc_entry["function"]["arguments"] += func_delta["arguments"]

        if is_thinking:
            sys.stdout.write("\033[0m\n")
    finally:
        resp.close()
        _ACTIVE_STREAM_RESP = None
        if is_thinking:
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()

    full_content = "".join(content_parts)
    message = {"role": role, "content": full_content or None}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    else:
        message["reasoning_content"] = ""
    if tool_calls_map:
        message["tool_calls"] = [
            tool_calls_map[i] for i in sorted(tool_calls_map.keys())
        ]

    if usage is None:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return message, usage


# ====================== 工具执行器注册 ======================
tool_executors = {
    "spawn_clone": spawn_clone,
    "view_file": view_file,
    "list_dir": list_dir,
    "grep_files": grep_files,
    "write_file": write_file,
    "search_replace": search_replace,
    "run_cli": run_cli,
    "leave_memory_hints": leave_memory_hints,
    "mcp_call": mcp_call,
    "mcp_list_tools": mcp_list_tools,
}


# ====================== Agent Loop ======================
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
        r'\{\s*"name"\s*:\s*"(?:spawn_clone|view_file|list_dir|grep_files|write_file|search_replace|run_cli|leave_memory_hints|mcp_call|mcp_list_tools)"\s*,\s*"arguments"',
        content_lower,
    ):
        return True
    return False


def agent_single_loop():
    global COMPACT_PANIC, LAST_USAGE
    break_loop = False
    while not break_loop:
        if AGENT_CANCEL:
            print("\n\n⏹ 用户强行停止")
            messages.append({
                "role": "user",
                "content": "《系统提示》用户已强行停止当前操作。请简要确认已中断，并询问是否继续。",
            })
            break
        try:
            sys.stdout.write("\n[*] ANTNEST: ")
            sys.stdout.flush()
            tools = get_queen_tools(COMPACT_PANIC)
            try:
                msg, usage = llm_chat_stream(messages, tools=tools)
            except ThinkRepeatError:
                print("\n\n💥 检测到 thinking 重复，自动中断")
                messages.append({
                    "role": "user",
                    "content": "警告：你的 thinking 中出现了大量重复内容，已被擦除。请继续，不要陷入循环。",
                })
                continue

            LAST_USAGE = usage
            messages.append(msg)

            if AGENT_CANCEL:
                break

            sys.stdout.write("\n\n")
            sys.stdout.flush()

            if not msg.get("tool_calls"):
                content = msg.get("content", "")
                if not content:
                    messages.append({
                        "role": "user",
                        "content": "警告：回复为空，没有输出也没有调用工具，请重新回答。",
                    })
                    continue

                if _detect_malformed_tool_call(content):
                    messages.append({
                        "role": "user",
                        "content": "警告：工具调用格式不正确，请以正确的格式调用 spawn_clone 工具。",
                    })
                    continue
                break

            for tc in msg["tool_calls"]:
                func = tc["function"]
                name = func["name"]
                try:
                    args = json.loads(func["arguments"])

                    print(f"===> {name}")
                    for k, v in args.items():
                        print(f"  {k}: {v}")
                    print()

                    result = tool_executors[name](**args)
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
                else:
                    if len(result) > TOOL_RESULT_LEN:
                        half = TOOL_RESULT_LEN // 2
                        result = (
                            result[:half]
                            + "\n...（中间内容已省略）...\n"
                            + result[-half:]
                        )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": name,
                        "content": clean_input(result),
                    })

                if AGENT_CANCEL:
                    break_loop = True
                    break

                if (
                    not COMPACT_PANIC
                    and usage["total_tokens"] >= TOKEN_CAP * COMPACT_THRESH
                ):
                    print("！！！紧急回合，触发记忆压缩")
                    COMPACT_PANIC = True
                    for i, m in enumerate(messages):
                        messages[i] = _trim_tool_content(m)
                    messages.append({"role": "user", "content": COMPACT_PROMPT})

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

    if user_ask:
        if until:
            user_ask = (
                f"{user_ask}\n----注意，这是一个无人类参与的任务，"
                f"你需要自行判断任务是否完成。完成后输出字符串：{until}。"
                f"如果未输出，系统会提示你继续。----"
            )
        print(f"[-] You: {user_ask}\n")
        messages.append({"role": "user", "content": clean_input(user_ask)})

    while True:
        try:
            display_usage(LAST_USAGE, TOKEN_CAP)
            if user_ask:
                while True:
                    agent_single_loop()
                    msg = messages[-1]
                    if (
                        until
                        and msg.get("role") == "assistant"
                        and until in msg.get("content", "")
                    ):
                        break
                    messages.append({
                        "role": "user",
                        "content": f"系统提示！未检测到停止字符串：{until}，请继续完成任务",
                    })

                if save_after:
                    save_session(messages)
                    release_lock()
                break

            print("")
            user_input = read_input("[-] You: ").strip()
            if not user_input:
                continue

            messages.append({"role": "user", "content": clean_input(user_input)})
            agent_single_loop()
        except KeyboardInterrupt:
            if not user_ask or save_after:
                save_session(messages)
                print("\n已中断" + ("，会话已保存" if (not user_ask or save_after) else ""))
                release_lock()
            else:
                print("\n已中断")
            break
        except Exception as e:
            print(f"主循环异常：{e}")
            if not user_ask or save_after:
                release_lock()
            break


# ====================== Session 管理 ======================
def get_session_file():
    return session_mod.get_session_file(PROJECT_DIR, SESSION_DIR)


def acquire_lock():
    session_mod.acquire_lock(PROJECT_DIR, SESSION_DIR, IS_WINDOWS)


def release_lock():
    session_mod.release_lock(PROJECT_DIR, SESSION_DIR)


def save_session(messages):
    session_mod.save_session(messages, PROJECT_DIR, SESSION_DIR)


def load_session():
    return session_mod.load_session(
        None,
        SYSTEM_PROMPT,
        nest_md,
        hints,
        ENV_INFO,
        PROJECT_DIR,
        SESSION_DIR,
    )


def list_sessions():
    session_mod.list_sessions(PROJECT_DIR, SESSION_DIR)


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
        return ""


# ====================== 主入口 ======================
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

    # 加载记忆
    nest_md = Path(NEST_FILE).read_text(encoding="utf-8") if Path(NEST_FILE).exists() else ""
    hints = Path(HINT_FILE).read_text(encoding="utf-8") if Path(HINT_FILE).exists() else ""
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                nest_md=nest_md or "无", hints=hints or "无", env_info=ENV_INFO
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
