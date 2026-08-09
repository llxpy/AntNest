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

# ====================== 路径解析 ======================
_resolved = Path(__file__).resolve()
THIS_FILE = str(_resolved)
THIS_DIR = str(_resolved.parent)

# ====================== Clone 模式早期入口 ======================
# 工蚁不需要 LLM，在导入阶段就拦截，避免不必要的 API 检查和模块加载
if os.environ.get("AN_CLONE_MODE") == "1":
    _clone_command = os.environ.get("AN_CLONE_COMMAND", "")
    _clone_timeout = int(os.environ.get("AN_CLONE_TIMEOUT", "300"))
    _result_file = os.environ.get("AN_RESULT_FILE", "")
    _clone_dir = os.environ.get("AN_CLONE_DIR", os.getcwd())

    # 如果通过文件传递命令，从文件读取
    _cmd_file = os.environ.get("AN_CLONE_COMMAND_FILE", "")
    if _cmd_file and os.path.exists(_cmd_file):
        _clone_command = Path(_cmd_file).read_text(encoding="utf-8")

    _shell = "powershell" if os.name == "nt" else "bash"
    _shell_flag = "-Command" if os.name == "nt" else "-c"

    print(f"[工蚁] 开始执行: {_clone_command[:100]}...")

    # ====== 安全过滤：拒绝明显危险的系统级命令 ======
    _cmd_lower = _clone_command.lower()
    _dangerous_patterns = [
        (r"\bformat\s+[a-z]:", "格式化磁盘"),
        (r"\bdel\s+/[fs]\s+[a-z]:\\", "强制删除系统文件"),
        (r"\brm\s+-rf\s+/", "删除根目录"),
        (r"\brmdir\s+/[sq]\s+[a-z]:\\", "删除系统目录"),
        (r"\bshutdown\s+/[rp]\b", "关机/重启"),
        (r"\bdeltree\b", "删除目录树"),
    ]
    for pattern, desc in _dangerous_patterns:
        if re.search(pattern, _cmd_lower):
            _output = {"status": "blocked", "error": f"工蚁拒绝执行危险命令（{desc}）"}
            if _result_file:
                with open(_result_file, "w", encoding="utf-8") as _f:
                    json.dump(_output, _f, ensure_ascii=False, indent=2)
            print(f"[工蚁] 拦截：{desc}")
            sys.exit(0)

    try:
        _result = subprocess.run(
            [_shell, _shell_flag, _clone_command],
            capture_output=True, text=True, errors="replace",
            timeout=_clone_timeout, shell=False, cwd=_clone_dir,
        )
        _output = {
            "status": "ok" if _result.returncode == 0 else "error",
            "exit_code": _result.returncode,
            "stdout": _result.stdout,
            "stderr": _result.stderr if _result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        _output = {"status": "timeout", "error": f"命令执行超时（{_clone_timeout}秒）"}
    except Exception as _e:
        _output = {"status": "error", "error": str(_e)}

    if _result_file:
        with open(_result_file, "w", encoding="utf-8") as _f:
            json.dump(_output, _f, ensure_ascii=False, indent=2)

    print(f"[工蚁] 完成，exit_code={_output.get('exit_code', 'N/A')}")
    sys.exit(0)

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
- spawn_clone：生成工蚁执行命令。工蚁在隔离目录中运行，执行完毕后销毁
- run_cli：仅在 clone 模式（depth>0）下可用，直接在本地执行命令
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

    # 复制自身到隔离目录
    clone_script = os.path.join(clone_dir, "AntNest.py")
    shutil.copy2(THIS_FILE, clone_script)

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

        try:
            stdout, stderr = proc.communicate(timeout=timeout + 60)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            return json.dumps({
                "status": "timeout",
                "error": f"工蚁执行超时（{timeout}秒）",
                "clone_dir": clone_dir,
            }, ensure_ascii=False)

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
                nest_md=nest_md or "无", hints=hints or "无", env_info=ENV_INFO
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
                    sys.stdout.write("\033[2m💭 ")
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
                    sys.stdout.write("\033[0m\n")
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
    "run_cli": run_cli,
    "leave_memory_hints": leave_memory_hints,
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
        r'\{\s*"name"\s*:\s*"(?:spawn_clone|run_cli|leave_memory_hints)"\s*,\s*"arguments"',
        content_lower,
    ):
        return True
    return False


def agent_single_loop():
    global COMPACT_PANIC, LAST_USAGE
    break_loop = False
    while not break_loop:
        try:
            sys.stdout.write("\n[*] ANTNEST: ")
            sys.stdout.flush()
            tools = (
                [spawn_clone_schema, memory_hints_schema]
                if COMPACT_PANIC
                else [spawn_clone_schema]
            )
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
    dir_hash = re.sub(r"[\\/:]", "_", PROJECT_DIR)
    return os.path.join(SESSION_DIR, f"{dir_hash}.json")


def acquire_lock():
    lock_file = get_session_file().replace(".json", ".lock")
    if os.path.exists(lock_file):
        try:
            pid = int(Path(lock_file).read_text().strip())
            if IS_WINDOWS:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                )
                alive = str(pid) in result.stdout
            else:
                alive = os.path.exists(f"/proc/{pid}")
            if alive:
                print(f"错误：该目录已有 AntNest 实例运行（PID: {pid}），不允许重复启动。")
                print(f"如需强制启动，请先删除锁文件：{lock_file}")
                sys.exit(1)
        except Exception:
            pass
    Path(lock_file).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_file).write_text(str(os.getpid()))


def release_lock():
    try:
        os.remove(get_session_file().replace(".json", ".lock"))
    except Exception:
        pass


def save_session(messages):
    os.makedirs(SESSION_DIR, exist_ok=True)
    session_file = get_session_file()
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    print(f"\n> 会话已保存到：{session_file}")


def load_session():
    session_file = get_session_file()
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            messages = json.load(f)

        messages[0] = {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                nest_md=nest_md or "无", hints=hints or "无", env_info=ENV_INFO
            ),
        }

        last_msg = messages[-1]
        if last_msg["role"] == "assistant":
            if "tool_calls" in last_msg:
                del last_msg["tool_calls"]
            if not last_msg["content"]:
                del messages[-1]
        size_KB = (os.path.getsize(session_file) + 999) // 1000
        print(f"\n> 会话已从文件加载：{session_file} ({format(size_KB, ',')} KB)")
        return messages
    except Exception:
        return None


def list_sessions():
    session_file = get_session_file()
    session_name = os.path.basename(session_file)
    print(f"目录: {SESSION_DIR}\n")
    if not os.path.exists(SESSION_DIR):
        print("> 没有找到任何会话记录。")
        return

    files = [f for f in os.listdir(SESSION_DIR) if f.endswith(".json")]
    if not files:
        print("> 没有找到任何会话记录。")
        return

    print(f"> 共找到 {len(files)} 个会话:")
    print("-" * 60)
    for i, f in enumerate(sorted(files), start=1):
        path = os.path.join(SESSION_DIR, f)
        size_KB = (os.path.getsize(path) + 999) // 1000
        marker = "    <=== 当前目录" if f == session_name else ""
        print(f"  {i}. {f} ({format(size_KB, ',')} KB){marker}")
    print("-" * 60)


def clear_session():
    session_file = get_session_file()
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
            print(f"> 已清除会话：{session_file}")
        except Exception as e:
            print(f"> 清除会话失败：{e}")
    else:
        print(f"> 会话不存在：{session_file}")


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
