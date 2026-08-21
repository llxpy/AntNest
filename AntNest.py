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
import memory_retrieval
from api_compat import effective_temperature, resolve_api_profile, sanitize_messages_for_api

# ====================== 路径解析 ======================
_resolved = Path(__file__).resolve()
THIS_FILE = str(_resolved)
THIS_DIR = str(_resolved.parent)

# 工蚁模式：在 LLM 加载前执行并退出
antnest_clone_worker.run_if_clone_mode()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
# 让没有 reconfigure 的旧解释器/子进程也尽量输出 UTF-8
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

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

# 启动时校验 Base URL（空或不含协议的 URL 会在 detect_model_len 时崩溃）
_ANT_BASE_STRIPPED = (ANT_BASE_URL or "").strip().rstrip("/")
if _ANT_BASE_STRIPPED and "://" not in _ANT_BASE_STRIPPED:
    print(f"警告：API Base URL 格式无效（{ANT_BASE_URL!r}），缺少协议前缀（https://）")
    print(f"配置文件位置：{_config_path}")
    print(f"将使用默认值 https://api.deepseek.com/v1")
    ANT_BASE_URL = "https://api.deepseek.com/v1"
elif not _ANT_BASE_STRIPPED:
    print(f"警告：API Base URL 为空，将使用默认值 https://api.deepseek.com/v1")
    print(f"配置文件位置：{_config_path}")
    ANT_BASE_URL = "https://api.deepseek.com/v1"


def _load_secret_key() -> str:
    """从独立密钥文件（config.json.key，权限 0600）读取 API Key，避免明文落在 config.json。"""
    try:
        kf = _config_path + ".key"
        if os.path.exists(kf):
            with open(kf, "r", encoding="utf-8") as _kf:
                return _kf.read().strip()
    except Exception:
        pass
    return ""


ANT_API_KEY = os.environ.get("ANT_API_KEY") or _load_secret_key() or _config_api.get("api_key", "")

if not ANT_API_KEY:
    print(f"错误：未配置 API Key。请在 config.json 中设置 api_key 或设置环境变量 ANT_API_KEY")
    print(f"配置文件位置：{_config_path}")
    sys.exit(1)

COMMON_HEADER = {"User-Agent": "AntNest", "Authorization": f"Bearer {ANT_API_KEY}"}


def _config_bool(val, default=False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


DEFAULT_TOKEN_CAP = int(_config_api.get("default_token_cap") or 128000)
SKIP_MODEL_CHECK = _config_bool(
    os.environ.get("ANT_SKIP_MODEL_CHECK") or _config_api.get("skip_model_check")
)
THINKING_MODE = (
    os.environ.get("ANT_THINKING_MODE") or _config_api.get("thinking_mode") or "auto"
).strip().lower()


def _thinking_requested(thinking: bool, profile: str) -> bool:
    mode = (THINKING_MODE or "auto").lower()
    if mode in ("off", "false", "0", "disabled", "none"):
        return False
    if mode in ("on", "true", "1", "enabled"):
        return True
    # auto：仅 DeepSeek 等明确支持的提供商尝试 thinking
    return bool(thinking and profile == "deepseek")


def _retry_after_seconds(err: urllib.error.HTTPError, attempt: int) -> float:
    ra = err.headers.get("Retry-After") if err.headers else None
    if ra:
        try:
            return max(float(ra), 1.0)
        except ValueError:
            pass
    return min(2 ** (attempt + 1), 30)


def _http_error_with_body(err: urllib.error.HTTPError, body: str) -> urllib.error.HTTPError:
    import io

    return urllib.error.HTTPError(
        err.url,
        err.code,
        err.reason,
        err.headers,
        io.BytesIO(body.encode("utf-8", errors="replace")),
    )


def _urlopen_with_retry(req: urllib.request.Request, max_retries: int = 3):
    """对 429/502/503 做有限次退避重试。"""
    retryable = {429, 502, 503}
    last_err = None
    last_raw = ""
    for attempt in range(max_retries + 1):
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            last_err = e
            last_raw = e.read().decode("utf-8", errors="replace")
            if e.code not in retryable or attempt >= max_retries:
                raise _http_error_with_body(e, last_raw) from e
            wait = _retry_after_seconds(e, attempt)
            hint = "服务繁忙" if e.code == 429 else "网关异常"
            sys.stdout.write(
                f"\n[!] {hint} (HTTP {e.code})，{wait:.0f}s 后重试 ({attempt + 1}/{max_retries})…\n"
            )
            sys.stdout.flush()
            time.sleep(wait)
    if last_err:
        raise _http_error_with_body(last_err, last_raw)
    raise RuntimeError("LLM 请求失败")


def detect_model_len():
    if SKIP_MODEL_CHECK:
        print(f"> 已跳过 /models 检测，使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}")
        return DEFAULT_TOKEN_CAP

    base = (ANT_BASE_URL or "").strip().rstrip("/")
    if not base or "://" not in base:
        print(f"警告：API Base URL 无效（{ANT_BASE_URL!r}），使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}")
        return DEFAULT_TOKEN_CAP

    url = f"{base}/models"
    req = urllib.request.Request(url, headers=COMMON_HEADER)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"警告：无法连接 {ANT_BASE_URL} 的 /models（{e}），使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}")
        return DEFAULT_TOKEN_CAP

    if status == 401:
        print("错误：API Key 无效或未授权，请检查 ANT_API_KEY 配置。")
        sys.exit(1)
    if status != 200:
        print(
            f"警告：/models 不可用（HTTP {status}）：{body[:200]}。"
            f"使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}。"
            f"可在 config.json 设置 skip_model_check=true 跳过此检测。"
        )
        return DEFAULT_TOKEN_CAP

    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        print(f"警告：/models 返回非法 JSON，使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}")
        return DEFAULT_TOKEN_CAP

    data = out.get("data") if isinstance(out, dict) else None
    if not isinstance(data, list):
        print(f"警告：/models 响应无 data 列表，使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}")
        return DEFAULT_TOKEN_CAP

    for d in data:
        if isinstance(d, dict) and d.get("id") == ANT_MODEL_NAME:
            raw = d.get("max_model_len") or d.get("context_length") or d.get("max_tokens")
            if raw:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    pass
            return {"deepseek-v4-flash": 1_000_000, "deepseek-v4-pro": 1_000_000}.get(
                ANT_MODEL_NAME, DEFAULT_TOKEN_CAP
            )

    ids = [d.get("id", "") for d in data if isinstance(d, dict)]
    preview = ", ".join(ids[:8]) or "（空）"
    print(
        f"警告：在 {ANT_BASE_URL} 未找到模型 '{ANT_MODEL_NAME}'。"
        f"可用示例：{preview}。使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}"
    )
    return DEFAULT_TOKEN_CAP

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
_ACTIVE_CLONE_PROCS = []  # 并行工蚁进程列表（原单值 _ACTIVE_CLONE_PROC 已废弃）

# UI 流式回调（由 antnest_bridge 注入）：callable(phase, text)
# phase: reasoning_start | reasoning | reasoning_end | content
UI_STREAM_CB = None

# MCP（由 antnest_bridge 在 ensure_loaded 时配置）
MCP_ENABLED = False
MCP_HUB = None


def agent_reset_cancel():
    global AGENT_CANCEL
    AGENT_CANCEL = False


def _kill_process_tree(proc):
    """杀死进程及其整棵子进程树，防止工蚁的命令子进程变孤儿堆积。

    Windows 用 taskkill /T 按父子关系杀树；POSIX 用进程组（需 start_new_session）。
    """
    pid = getattr(proc, "pid", None)
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def agent_cancel():
    """用户强行停止：中断流式 LLM 与正在运行的所有工蚁。"""
    global AGENT_CANCEL
    AGENT_CANCEL = True
    resp = _ACTIVE_STREAM_RESP
    if resp is not None:
        try:
            resp.close()
        except Exception:
            pass
    for proc in list(_ACTIVE_CLONE_PROCS):
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc)


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
    scratch = None
    try:
        scratch = tempfile.mkdtemp(prefix="antnest_env_")
        for i, (label, cmd) in enumerate(zip(labels, shell_cmds)):
            try:
                argv = antnest_clone_worker.prepare_command_script(cmd, scratch)
                r = subprocess.run(
                    argv,
                    capture_output=True,
                    timeout=8,
                    shell=False,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
                output = antnest_clone_worker.decode_output(r.stdout).strip()
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
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

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
  - 工蚁执行环境已统一为 UTF-8（自动 chcp 65001 + 输出编码转换 + 自适应解码），命令输出中的中文不会再乱码，可以在命令中放心使用中文路径/内容
  - 超长命令（超过环境变量限制）会自动写入脚本文件执行，无需担心长度限制
  - 运行 Python 代码时：先用 write_file 写入 .py 脚本再执行 `python script.py`（中文与引号都会完整保留）；不要用 `python -c "..."` 内联方式——Windows PowerShell 5.1 会吞掉其中的引号导致语法错误，这是已知坑
  - **验证模式**：设 `verify=true` 时，执行完成后会自动派验证工蚁检查结果。验证工蚁会检查：结果格式是否正确、是否有错误标志、输出是否完整。返回结果中会包含 `verified`、`verify_result`、`task_status` 字段
  - **任务状态机**：每个工蚁任务都有状态（pending→running→done→verified/failed/timeout/cancelled），返回结果中会包含 `task_id` 和 `task_status`
- view_file：派工蚁只读查看文件（分段、带行号）
- list_dir：派工蚁只读列目录
- grep_files：派工蚁在目录/文件中搜索文本（写代码前定位用）
- write_file：派工蚁写入/覆盖文件（蚁后不亲自写盘）
- search_replace：派工蚁精确替换文件中的一段文本（改代码首选，比 write_file 整文件覆盖更安全）
- run_cli：仅在 clone 模式（depth>0）下可用
- run_python：已不再作为蚁后工具暴露（避免蚁后主进程直接执行任意代码）；需要跑 Python 请走 run_cli 经工蚁隔离执行
- web_fetch：抓取网页内容转为纯文本（查最新文档/资料用）
- leave_memory_hints：记忆压缩时保留关键线索

# 记忆检索
每次思考前会依据你的最近问题自动检索 NEST.md 与 hints.md 中最相关的段落注入上下文，无需手动查找。

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
                "verify": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否派验证工蚁确认执行结果。设为 true 时，执行完成后会自动派验证工蚁检查结果是否正确。",
                },
            },
            "required": ["command"],
        },
    },
}

get_task_status_schema = {
    "type": "function",
    "function": {
        "name": "get_task_status",
        "description": (
            "查看工蚁任务的状态。返回任务的状态机信息（pending/running/done/verified/failed/timeout/cancelled）。"
            "可用于检查任务是否完成、是否已验证。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（从 spawn_clone 返回结果中的 task_id 字段获取）",
                },
            },
            "required": ["task_id"],
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

run_python_schema = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "直接解释执行 Python 代码（不经过 shell，中文与引号完整保留）。"
            "适合数据处理、快速验证；复杂任务建议先用 write_file 写文件再执行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码"},
                "timeout": {
                    "type": "integer", "default": 120, "description": "超时时间（秒）",
                },
            },
            "required": ["code"],
        },
    },
}

web_fetch_schema = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "抓取网页内容并转为纯文本（查文档、查资料用）。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的完整 URL"},
                "max_bytes": {
                    "type": "integer", "default": 30000, "description": "最多返回的字符数",
                },
            },
            "required": ["url"],
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


def _with_retrieved_memory(msgs):
    """每轮 LLM 调用前，用最近的用户消息检索记忆，临时注入（不改原列表）。"""
    try:
        query = ""
        for m in reversed(msgs):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                query = m["content"][:200]
                break
        if not query:
            return msgs
        hit = memory_retrieval.retrieve(
            query, ((NEST_FILE, "NEST"), (HINT_FILE, "hints")), k=_MEMORY_TOP_K
        )
        if not hit:
            return msgs
        return msgs + [{
            "role": "system",
            "content": "《记忆检索》根据当前问题从记忆库检索到的相关内容（供参考）：\n" + hit,
        }]
    except Exception:
        return msgs


def get_queen_tools(compact_panic: bool = False) -> list:
    """蚁后可用工具列表（含可选 MCP）。"""
    if compact_panic:
        base = [spawn_clone_schema, memory_hints_schema]
    else:
        base = [
            spawn_clone_schema,
            get_task_status_schema,
            view_file_schema,
            list_dir_schema,
            grep_files_schema,
            write_file_schema,
            search_replace_schema,
            web_fetch_schema,
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
# 资源上限常量（魔法数字统一命名，便于按需调整）
_ARTIFACTS_MAX_KEEP = 50       # artifacts 目录保留上限
_CLONE_POOL_MAX_KEEP = 20      # clone_pool 目录保留上限
_RUN_PYTHON_OUTPUT_CAP = 2 * 1024 * 1024  # run_python 返回输出上限
_MEMORY_TOP_K = 4              # BM25 记忆检索注入段落数
_DUP_CALL_LIMIT = 3            # 相同工具+参数连续次数，超过即判定循环
_DUP_RECENT_MAX = 10           # 重复检测保留的最近调用记录数
_ARTIFACT_EXCLUDES = {
    "AntNest.py", "antnest_clone_worker.py", "code_tools.py",
    "antnest_session.py", "api_compat.py", "memory_retrieval.py",
    "result.json", "clone.log", "command.txt", "ant_cmd.ps1", "ant_cmd.sh",
}


def collect_clone_artifacts(clone_dir: str, clone_id: str) -> list:
    """把工蚁隔离目录中的产出文件归档到 .antnest/artifacts/<clone_id>/。

    返回归档后的相对路径列表（相对项目目录，正斜杠）。只收用户文件，
    脚本/日志/结果等内部文件一律排除。被 spawn_clone 在销毁目录前调用。
    """
    try:
        artifacts_root = os.path.join(PROJECT_ANT_DIR, "artifacts", clone_id)
        os.makedirs(artifacts_root, exist_ok=True)
        # 保留上限：只留最近 50 个工蚁的产出，防止自检/长任务把磁盘撑满
        _art_root = os.path.join(PROJECT_ANT_DIR, "artifacts")
        try:
            _dirs = [
                os.path.join(_art_root, d) for d in os.listdir(_art_root)
                if os.path.isdir(os.path.join(_art_root, d))
            ]
            if len(_dirs) > _ARTIFACTS_MAX_KEEP:
                _dirs.sort(key=os.path.getmtime)
                for _old in _dirs[: len(_dirs) - _ARTIFACTS_MAX_KEEP]:
                    shutil.rmtree(_old, ignore_errors=True)
        except Exception:
            pass
        rels = []
        for root, dirs, files in os.walk(clone_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn in _ARTIFACT_EXCLUDES:
                    continue
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, clone_dir)
                dst = os.path.join(artifacts_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                rels.append(os.path.join("artifacts", clone_id, rel).replace("\\", "/"))
        return rels
    except Exception:
        return []


def spawn_clone(command: str, timeout: int = 300, label: str = "", verify: bool = False) -> str:
    """
    生成工蚁执行命令。
    1. 检查深度限制（硬拦截）
    2. 创建任务状态跟踪
    3. 在 clone_pool 下创建隔离目录
    4. 命令通过文件传递（避免环境变量截断）
    5. 复制 AntNest.py 到隔离目录
    6. 以 clone 模式启动工蚁
    7. 等待工蚁执行完毕，读取结果
    8. 更新任务状态
    9. 销毁工蚁目录
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

    # ====== 任务状态管理 ======
    from task_manager import get_task_manager, TaskStatus
    tm = get_task_manager()
    task_id = uuid.uuid4().hex[:8]
    task = tm.create_task(task_id, command, label)
    task.mark_running()

    clone_id = uuid.uuid4().hex[:8]
    clone_dir = os.path.join(CLONE_POOL_DIR, f"clone_{clone_id}")
    os.makedirs(clone_dir, exist_ok=True)

    # 清理历史克隆目录：只保留最近 20 个，防止进程被强杀（finally 不执行）后残留堆积
    try:
        _dirs = [
            os.path.join(CLONE_POOL_DIR, d) for d in os.listdir(CLONE_POOL_DIR)
            if d.startswith("clone_") and os.path.isdir(os.path.join(CLONE_POOL_DIR, d))
        ]
        if len(_dirs) > _CLONE_POOL_MAX_KEEP:
            _dirs.sort(key=os.path.getmtime)
            for _old in _dirs[: len(_dirs) - _CLONE_POOL_MAX_KEEP]:
                shutil.rmtree(_old, ignore_errors=True)
    except Exception:
        pass

    # 复制蚁后脚本及工蚁模式依赖（工蚁在隔离目录 import，必须随包带上）
    clone_script = os.path.join(clone_dir, "AntNest.py")
    shutil.copy2(THIS_FILE, clone_script)
    for _dep in ("antnest_clone_worker.py", "code_tools.py", "antnest_session.py", "api_compat.py", "memory_retrieval.py"):
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
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
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
            encoding="utf-8",
            errors="replace",
            **(  {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                else {"start_new_session": True}  # POSIX：独立进程组，便于整树击杀
            ),
        )
        global _ACTIVE_CLONE_PROCS
        _ACTIVE_CLONE_PROCS.append(proc)

        try:
            deadline = time.time() + timeout + 60
            while proc.poll() is None:
                if AGENT_CANCEL:
                    _kill_process_tree(proc)
                    proc.wait(timeout=5)
                    return json.dumps({
                        "status": "cancelled",
                        "error": "用户强行停止，工蚁已终止",
                    }, ensure_ascii=False)
                if time.time() > deadline:
                    _kill_process_tree(proc)
                    proc.wait(timeout=5)
                    task.mark_timeout()
                    return json.dumps({
                        "status": "timeout",
                        "error": f"工蚁执行超时（{timeout}秒）",
                        "clone_dir": clone_dir,
                        "task_id": task_id,
                        "task_status": task.status.value,
                    }, ensure_ascii=False)
                time.sleep(0.15)
            stdout, stderr = proc.communicate()
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            stdout, stderr = proc.communicate()
            task.mark_timeout()
            return json.dumps({
                "status": "timeout",
                "error": f"工蚁执行超时（{timeout}秒）",
                "clone_dir": clone_dir,
                "task_id": task_id,
                "task_status": task.status.value,
            }, ensure_ascii=False)
        finally:
            try:
                _ACTIVE_CLONE_PROCS.remove(proc)
            except ValueError:
                pass

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

        # 产出回收：销毁目录前归档工蚁生成的用户文件
        artifacts = collect_clone_artifacts(clone_dir, clone_id)
        if artifacts:
            try:
                d = json.loads(result)
                if isinstance(d, dict):
                    d["artifacts"] = artifacts
                    result = json.dumps(d, ensure_ascii=False)
            except Exception:
                pass

        print(f"[工蚁] {label or clone_id} 完成 (exit={proc.returncode})")

        # ====== 任务状态更新 ======
        try:
            d = json.loads(result)
            if isinstance(d, dict) and d.get("status") in ("error", "timeout"):
                task.mark_failed(d.get("error", "未知错误"))
            else:
                task.mark_done(result)
        except Exception:
            task.mark_done(result)

        # 将 task_id 注入返回结果
        try:
            d = json.loads(result)
            if isinstance(d, dict):
                d["task_id"] = task_id
                d["task_status"] = task.status.value
                result = json.dumps(d, ensure_ascii=False)
        except Exception:
            pass

        # ====== 验证工蚁：如果 verify=True，派验证工蚁确认结果 ======
        if verify and task.status == TaskStatus.DONE:
            try:
                verify_result = _verify_clone_result(task_id, command, result, timeout=min(timeout, 120))
                task.mark_verified(verify_result)
                # 将验证结果注入返回
                try:
                    d = json.loads(result)
                    if isinstance(d, dict):
                        d["verified"] = True
                        d["verify_result"] = verify_result
                        d["task_status"] = task.status.value
                        result = json.dumps(d, ensure_ascii=False)
                except Exception:
                    pass
            except Exception as e:
                print(f"[验证] 验证工蚁异常：{e}")

        return result

    except Exception as e:
        task.mark_failed(str(e))
        return json.dumps({
            "status": "error",
            "error": f"工蚁管理异常：{str(e)}",
            "clone_dir": clone_dir,
            "task_id": task_id,
            "task_status": task.status.value,
        }, ensure_ascii=False)

    finally:
        # 销毁工蚁目录
        try:
            shutil.rmtree(clone_dir, ignore_errors=True)
        except Exception:
            pass


def _verify_clone_result(task_id: str, original_command: str, result: str, timeout: int = 120) -> str:
    """
    验证工蚁：派独立工蚁验证执行结果。
    
    验证逻辑：
    1. 检查结果格式是否正确
    2. 检查结果是否包含错误标志
    3. 如果是文件操作，验证文件是否存在
    4. 如果是代码执行，验证是否有语法错误
    """
    # 构造验证命令
    verify_prompt = f"""你是验证工蚁，负责验证执行工蚁的结果。

原始任务：{original_command[:500]}

执行结果：{result[:2000]}

请验证：
1. 结果是否表明任务成功完成？
2. 结果中是否有明显的错误或警告？
3. 如果任务涉及文件操作，相关文件是否应该已创建/修改？
4. 结果是否完整，没有截断？

请以 JSON 格式返回验证结果：
{{"verified": true/false, "reason": "验证原因", "issues": ["问题列表"]}}

只返回 JSON，不要其他内容。"""

    # 派验证工蚁（深度+1，避免无限嵌套）
    current_depth = int(os.environ.get("AN_DEPTH", "0"))
    if current_depth >= MAX_DEPTH:
        # 深度不足，跳过验证
        return json.dumps({
            "verified": True,
            "reason": "深度限制，跳过验证",
            "issues": []
        }, ensure_ascii=False)

    # 使用简单的 Python 脚本验证，不派完整工蚁
    try:
        import subprocess
        verify_script = f'''
import json
import sys

result_text = """{result[:3000]}"""

try:
    d = json.loads(result_text)
    if isinstance(d, dict):
        status = d.get("status", "unknown")
        if status in ("error", "timeout", "blocked"):
            print(json.dumps({{"verified": False, "reason": f"执行状态为 {{status}}", "issues": [d.get("error", "未知错误")]}}))
        elif status == "cancelled":
            print(json.dumps({{"verified": False, "reason": "任务被取消", "issues": ["用户取消"]}}))
        else:
            # 检查是否有 output 或 result 字段
            has_output = "output" in d or "result" in d or "stdout" in d
            print(json.dumps({{"verified": has_output, "reason": "有输出内容" if has_output else "无输出内容", "issues": [] if has_output else ["缺少输出字段"]}}))
    else:
        print(json.dumps({{"verified": True, "reason": "非标准格式但无错误", "issues": []}}))
except json.JSONDecodeError:
    # 非 JSON 结果，检查是否有错误关键词
    error_keywords = ["error", "Error", "ERROR", "exception", "Exception", "Traceback", "失败"]
    has_error = any(kw in result_text for kw in error_keywords)
    print(json.dumps({{"verified": not has_error, "reason": "包含错误关键词" if has_error else "无错误关键词", "issues": ["包含错误信息"] if has_error else []}}))
'''
        
        proc = subprocess.run(
            [sys.executable, "-c", verify_script],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace"
        )
        
        if proc.returncode == 0 and proc.stdout.strip():
            verify_result = proc.stdout.strip()
            # 解析验证结果
            try:
                vd = json.loads(verify_result)
                if isinstance(vd, dict):
                    return verify_result
            except Exception:
                pass
        
        # 验证脚本执行失败，默认通过
        return json.dumps({
            "verified": True,
            "reason": "验证脚本执行异常，默认通过",
            "issues": []
        }, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            "verified": True,
            "reason": f"验证异常：{str(e)}",
            "issues": []
        }, ensure_ascii=False)


def _resolve_path(path: str) -> Path:
    return ct.resolve_path(path, PROJECT_DIR)


def _worker_py_cmd(py_code: str) -> str:
    return ct.worker_py_cmd(py_code, is_windows=IS_WINDOWS, python_exe=sys.executable)


def _unwrap_worker_json(spawn_result: str) -> str:
    return ct.unwrap_worker_json(spawn_result)


def view_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """派工蚁只读查看文件（蚁后自己不读）。"""
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    py = ct.build_view_file_script(p, start_line, end_line)
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"查看·{p.name}")
    return _unwrap_worker_json(raw)


def list_dir(path: str = ".", max_entries: int = 100) -> str:
    """派工蚁只读列目录（蚁后自己不列）。"""
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
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
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    py = ct.build_grep_script(p, pattern, glob, max_matches)
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=120, label=f"搜索·{pattern[:24]}")
    return _unwrap_worker_json(raw)


def write_file(path: str, content: str) -> str:
    """派工蚁写入文件（蚁后自己不写）。"""
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
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
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    py = ct.build_search_replace_script(p, old_string, new_string, replace_all)
    raw = spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"替换·{p.name}")
    return _unwrap_worker_json(raw)


# 高危命令模式：命中即拦截，避免工蚁/蚁后手滑造成不可逆破坏。
# 覆盖范围：递归删除指向根/绝对路径/盘符/主目录/上级/当前/通配/HOME，以及格式化、关机、fork bomb。
# 设计意图（与旧版一致）：只拦系统级不可逆操作，项目内相对路径（如 rm -rf node_modules）仍放行。
_DANGER_CLI_PATTERNS = (
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+/(?!/)", "递归删除根/绝对路径"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+[a-zA-Z]:[\\/]", "递归删除整个盘符"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+~", "递归删除用户主目录"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+\.\.(?:[\\/]|\s|$)", "递归删除上级目录"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+\.(?:\s|$)", "递归删除当前目录"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+\*", "通配递归删除"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+[$]HOME", "递归删除 HOME"),
    (r"\bdel\s+/[sqf]+\s+[a-zA-Z]:[\\/]", "强制删除系统盘"),
    (r"\brd\s+/[sq]+\s+[a-zA-Z]:[\\/]", "删除系统盘目录"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bshutdown\b", "关机/重启"),
    (r"\bhalt\b", "关机"),
    (r"\bmkfs\b", "格式化文件系统"),
    (r"\bdd\s+if=/dev/zero\b", "危险磁盘写入"),
    (r"\bRemove-Item\b[^\n]*(?:-Recurse|-Force|-r\b)", "PowerShell 递归/强制删除"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};:", "fork bomb"),
)


def _check_danger_command(command: str):
    """返回 (是否拦截, 原因)。只拦指向系统根/盘符根/磁盘/关机的不可逆操作。"""
    for pat, why in _DANGER_CLI_PATTERNS:
        if re.search(pat, command or ""):
            return True, why
    return False, ""


def run_cli(command: str, timeout: int = 300) -> str:
    """工蚁模式下直接执行命令（UTF-8 环境 + 脚本文件传递）。蚁后模式不应调用此函数。"""
    danger, why = _check_danger_command(command)
    if danger:
        return (
            f"命令已被安全拦截（{why}）。如需删除文件请改用 write_file/search_replace，"
            f"或明确指定项目内的相对路径。"
        )
    scratch = None
    try:
        scratch = tempfile.mkdtemp(prefix="antnest_cli_")
        argv = antnest_clone_worker.prepare_command_script(command or "", scratch)
        run_env = os.environ.copy()
        run_env.setdefault("PYTHONIOENCODING", "utf-8")
        run_env.setdefault("PYTHONUTF8", "1")
        result = subprocess.run(
            argv,
            capture_output=True,
            cwd=os.getcwd(),
            timeout=timeout,
            shell=False,
            env=run_env,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        stdout = antnest_clone_worker.decode_output(result.stdout)
        stderr = antnest_clone_worker.decode_output(result.stderr)
        output = f"Exit code: {result.returncode}\n{stdout}"
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"执行超时（{timeout}秒）"
    except Exception as e:
        return f"执行失败：{str(e)}"
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def run_python(code: str, timeout: int = 120) -> str:
    """直接解释执行 Python 代码（不走 shell，免疫引号/编码问题）。

    代码写入临时 .py 文件后用当前解释器执行，stdout/stderr 按 UTF-8 捕获。
    与 run_cli 的区别：不经过 PowerShell/bash，中文与引号完整保留。
    """
    scratch = None
    try:
        scratch = tempfile.mkdtemp(prefix="antnest_py_")
        script = os.path.join(scratch, "run_py.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(code or "")
        run_env = os.environ.copy()
        run_env.setdefault("PYTHONIOENCODING", "utf-8")
        run_env.setdefault("PYTHONUTF8", "1")
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            cwd=PROJECT_DIR,
            timeout=timeout,
            env=run_env,
            **(  {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            ),
        )
        stdout = antnest_clone_worker.decode_output(result.stdout)
        stderr = antnest_clone_worker.decode_output(result.stderr)
        output = f"Exit code: {result.returncode}\n{stdout}"
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        if len(output) > _RUN_PYTHON_OUTPUT_CAP:
            output = output[: _RUN_PYTHON_OUTPUT_CAP] + "\n…（输出过长已截断）"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"执行超时（{timeout}秒）"
    except Exception as e:
        return f"执行失败：{str(e)}"
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def web_fetch(url: str, max_bytes: int = 30000) -> str:
    """抓取网页并转纯文本（蚁后查文档用）。仅允许 http/https，防 file:// 读本地与 SSRF。"""
    try:
        from urllib.parse import urlparse
        _scheme = (urlparse(url or "").scheme or "").lower()
        if _scheme not in ("http", "https"):
            return f"抓取被拒绝：仅支持 http/https 协议（收到 {_scheme or '无协议'}）"
        import html as _h, urllib.request as _u
        with _u.urlopen(_u.Request(url, headers={"User-Agent": "Mozilla/5.0 AntNest"}), timeout=20) as r:
            raw = r.read(max_bytes + 4096)
        t = raw.decode("utf-8", "replace")
        t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
        t = re.sub(r"(?s)<[^>]+>", " ", t)
        t = re.sub(r"[ \t]+", " ", _h.unescape(t)).strip()
        return t[:max_bytes] or "(无可读文本)"
    except Exception as e:
        return f"抓取失败：{str(e)}"


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

    if compact_i < 0:
        return "错误：未找到记忆压缩标记，无法执行 leave_memory_hints。"

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
    profile = resolve_api_profile(ANT_BASE_URL)
    use_thinking = _thinking_requested(thinking, profile)
    temp = effective_temperature(temperature, profile, ANT_MODEL_NAME)
    data = {
        "model": ANT_MODEL_NAME,
        "messages": sanitize_messages_for_api(messages, profile),
        "temperature": temp,
    }

    if profile == "deepseek":
        data["top_p"] = 0.95
        data["thinking"] = {"type": "adaptive" if use_thinking else "disabled"}
    elif profile == "openai":
        data["top_p"] = 1.0
    else:
        # MiniMax / 硅基 / 智谱等：仅标准 OpenAI 字段，避免 400
        data["top_p"] = 0.95

    if tools:
        data["tools"] = tools
    if stream:
        data["stream"] = True
        if profile in ("deepseek", "openai", "openai_compat"):
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

    def _open(body_bytes: bytes):
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={**COMMON_HEADER, "Content-Type": "application/json"},
        )
        return _urlopen_with_retry(req)

    body = json.dumps(data).encode("utf-8")
    try:
        resp = _open(body)
        global _ACTIVE_STREAM_RESP
        _ACTIVE_STREAM_RESP = resp
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        if e.code == 400:
            minimal = {
                "model": ANT_MODEL_NAME,
                "messages": sanitize_messages_for_api(
                    messages, resolve_api_profile(ANT_BASE_URL)
                ),
                "temperature": data["temperature"],
                "stream": True,
            }
            if tools:
                minimal["tools"] = tools
            try:
                resp = _open(json.dumps(minimal).encode("utf-8"))
                _ACTIVE_STREAM_RESP = resp
            except urllib.error.HTTPError as e2:
                raw2 = e2.read().decode("utf-8", errors="replace")
                raise Exception(f"LLM调用失败，HTTP {e2.code}: {raw2[:500]}")
        elif e.code == 429:
            raise Exception(
                f"LLM调用失败，HTTP 429：API 服务端过载，已自动重试仍失败。"
                f"请稍后再试或换时段/换模型。详情：{raw[:300]}"
            )
        else:
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
                        sys.stdout.write("\033[2m[think] ")
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
    message = {"role": role, "content": full_content if full_content else None}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls_map:
        message["tool_calls"] = [
            tool_calls_map[i] for i in sorted(tool_calls_map.keys())
        ]
        if not full_content:
            message["content"] = " "

    if usage is None:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    # token 用量实时上报（UI 顶栏显示）
    _stream_emit("usage", json.dumps(usage, ensure_ascii=False))
    return message, usage


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


# ====================== 工具执行器注册 ======================
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
        r'\{\s*"name"\s*:\s*"(?:spawn_clone|get_task_status|view_file|list_dir|grep_files|write_file|search_replace|run_cli|run_python|web_fetch|leave_memory_hints|mcp_call|mcp_list_tools)"\s*,\s*"arguments"',
        content_lower,
    ):
        return True
    return False


def agent_single_loop():
    global COMPACT_PANIC, LAST_USAGE
    break_loop = False
    _rounds = 0
    _recent_cmds = []  # 最近工具调用 (name, args) 记录，用于重复循环检测
    _max_rounds = int(os.environ.get("ANT_MAX_ROUNDS", "60"))
    while not break_loop:
        _rounds += 1
        if _rounds > _max_rounds:
            print(f"\n[!] 已达单任务最大轮次 {_max_rounds}，强制结束")
            messages.append({
                "role": "user",
                "content": (
                    f"《系统提示》已达单任务最大轮次（{_max_rounds} 轮），"
                    "请立即总结当前进度并停止，不要再调用任何工具。"
                ),
            })
            break
        if AGENT_CANCEL:
            print("\n\n[STOP] 用户强行停止")
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
                msg, usage = llm_chat_stream(_with_retrieved_memory(messages), tools=tools)
            except ThinkRepeatError:
                print("\n\n[!] 检测到 thinking 重复，自动中断")
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
                content = msg.get("content") or ""
                if not str(content).strip():
                    messages.pop()
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

                    # 重复调用检测：连续 _DUP_CALL_LIMIT 次相同工具+相同参数 → 中断循环（防自检反复创建脚本/命令）
                    _key = (name, json.dumps(args, ensure_ascii=False)[:120])
                    _recent_cmds.append(_key)
                    if len(_recent_cmds) > _DUP_RECENT_MAX:
                        del _recent_cmds[0]
                    if _recent_cmds.count(_key) >= _DUP_CALL_LIMIT:
                        print(f"\n[!] 检测到重复调用 {name}（连续 3 次相同参数），中断本轮循环")
                        messages.append({
                            "role": "user",
                            "content": (
                                "《系统提示》检测到你连续 3 次以相同参数调用同一工具，疑似陷入循环。"
                                "请停止重复尝试，换一种思路，或总结当前状态并报告。"
                            ),
                        })
                        break_loop = True
                        break

                    print(f"===> {name}")
                    for k, v in args.items():
                        print(f"  {k}: {v}")
                    print()

                    result = tool_executors[name](**args)
                    # spawn_clone 失败自动重试一次（仅 error；timeout 不重试，
                    # 避免卡死命令翻倍耗时）
                    if name == "spawn_clone":
                        try:
                            d = json.loads(result)
                            if d.get("status") == "error":
                                print(f"[重试] spawn_clone 状态=error，自动重试一次")
                                result = tool_executors[name](**args)
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

            # 兜底：确保每个 tool_call 都有 tool 响应，否则下一轮 LLM 调用会被
            # API 以「insufficient tool messages」400 拒绝（中断/重复检测提前 break 时会缺）
            for _i in range(len(messages) - 1, -1, -1):
                _m = messages[_i]
                if _m.get("role") != "assistant" or not _m.get("tool_calls"):
                    continue
                _responded = {
                    m.get("tool_call_id") for m in messages
                    if m.get("role") == "tool" and m.get("tool_call_id")
                }
                for _tc in _m["tool_calls"]:
                    _tid = (_tc or {}).get("id")
                    if _tid and _tid not in _responded:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": _tid,
                            "name": ((_tc.get("function") or {}).get("name", "")),
                            "content": "（工具调用被中断，未执行）",
                        })
                break

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
                until_rounds = 0
                until_max = int(os.environ.get("ANT_UNTIL_MAX_ROUNDS", "40"))
                while True:
                    agent_single_loop()
                    msg = messages[-1]
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
                    messages.append({
                        "role": "user",
                        "content": f"系统提示！未检测到停止字符串：{until}，请继续完成任务",
                    })

                if save_after:
                    save_session(messages)
                    release_lock()
                break

            print("")
            user_input = read_input("[-] You: ")
            if user_input is None:
                print("\n输入结束")
                if not user_ask or save_after:
                    save_session(messages)
                    release_lock()
                break
            user_input = user_input.strip()
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
