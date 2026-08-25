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

# import code_tools as ct  # (已迁移至子模块)
import antnest_clone_worker
# import antnest_session as session_mod  # (已迁移至子模块)
# import memory_retrieval  # (已迁移至子模块)
# from api_compat import effective_temperature, resolve_api_profile, sanitize_messages_for_api  # (已迁移至子模块)
import admin_utils
import antnest_runtime_state as runtime_state
# import memory_tree  # (已迁移至子模块)
import model_capabilities

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

# ====================== 管理员模式初始化 ======================
# 检测管理员权限，修复输入法，显示警告
_admin_info = admin_utils.get_admin_status()
if _admin_info["is_admin"]:
    print(f"\n{'='*60}")
    print(f"⚠️  AntNest 正在以管理员权限运行")
    print(f"{'='*60}")
    print(f"管理员模式下可执行系统级操作")
    print(f"危险操作将需要用户确认后才会执行")
    print(f"{'='*60}\n")
    # 修复输入法问题
    admin_utils.fix_ime_for_admin()


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

    cap = model_capabilities.detect_capability(
        ANT_BASE_URL, ANT_MODEL_NAME, ANT_API_KEY, default_cap=DEFAULT_TOKEN_CAP
    )
    # 只在“确实命中 /models 里的当前模型”时采用实测值；失败/未命中一律用配置默认，
    # 避免内置表把 TOKEN_CAP 悄悄放大。401 不再 sys.exit：降级为默认上限。
    if cap.get("detected") and cap.get("context_length"):
        return int(cap["context_length"])
    if cap.get("msg"):
        print(f"警告：{cap['msg']}，使用默认 token 上限 {DEFAULT_TOKEN_CAP:,}。")
    return DEFAULT_TOKEN_CAP


def format_model_capability_section(cap) -> str:
    """把能力探测结果转成系统提示词里的「当前模型能力」分节文本。"""
    cap = cap or {}
    model = cap.get("model") or ANT_MODEL_NAME
    try:
        ctx = int(cap.get("context_length") or 0)
    except (TypeError, ValueError):
        ctx = 0
    src = "按 /models 实测" if cap.get("detected") else "按名称推定"
    vision = "支持图片输入" if cap.get("vision") else "不支持图片输入"
    kind = cap.get("model_kind") or "chat"
    summary = cap.get("summary") or model_capabilities.summarize_capability(cap)
    return (
        f"模型：{model}（类型 {kind}，上下文上限 {ctx:,} tokens，{vision}；{src}）\n"
        f"{summary}"
    )


def model_capability_summary() -> str:
    """当前能力画像，用于填充系统提示词 {model_capability} 占位符。"""
    if not isinstance(_CAP_SUMMARY, dict):
        return _CAP_SUMMARY
    return format_model_capability_section(_CAP_SUMMARY)


def _ensure_model_cap():
    """首次会话前同步校准模型能力：更新 TOKEN_CAP 并把画像注入 SYSTEM_PROMPT。

    - 复用 model_capabilities 的探测缓存：import 期 detect_model_len 已探测时零额外网络；
    - 工蚁（clone）进程跳过，避免每只工蚁重复联网；
    - 失败不致命：画像降级为按名称推定，TOKEN_CAP 保持默认；
    - 开关：config.json → api.skip_model_check（或环境变量 ANT_SKIP_MODEL_CHECK）。
    """
    global TOKEN_CAP, SYSTEM_PROMPT, _CAP_SUMMARY
    if SKIP_MODEL_CHECK or os.environ.get("AN_CLONE_MODE"):
        return
    if isinstance(_CAP_SUMMARY, dict):
        return
    cap = model_capabilities.detect_capability(
        ANT_BASE_URL, ANT_MODEL_NAME, ANT_API_KEY,
        default_cap=TOKEN_CAP, timeout=6,
    )
    if cap.get("detected") and cap.get("context_length"):
        try:
            TOKEN_CAP = int(cap["context_length"])
        except (TypeError, ValueError):
            pass
    _CAP_SUMMARY = cap
    SYSTEM_PROMPT = _fill_env_fields(_system_prompt_template()).replace(
        "{model_capability}", format_model_capability_section(cap)
    )

# ====================== AntNest 配置区 ======================
_config_agent = _config.get("agent", {})

TOKEN_CAP = detect_model_len()
COMPACT_THRESH = float(os.environ.get("ANT_COMPACT_THRESH") or _config_agent.get("compact_threshold", 0.85))
TOOL_RESULT_LEN = int(os.environ.get("ANT_TOOL_RESULT_LEN") or _config_agent.get("tool_result_max_len", min(8000, int(TOKEN_CAP / 20))))
DEFAULT_WORKER_TIMEOUT = int(os.environ.get("ANT_WORKER_TIMEOUT") or _config_agent.get("worker_timeout", 300))
# 模型能力画像（会话开始时 _ensure_model_cap 校准；未校准时为占位说明）
_CAP_SUMMARY = "（模型能力将按 /models 校准…）"
ALLOW_ALL_CLI = False
COMPACT_PANIC = False
LAST_USAGE = None
AGENT_CANCEL = False
# 用户主动停止后的可恢复快照：只保存决策摘要、证据和下一步，不保存隐藏思维链。
LAST_STOP_SNAPSHOT = None
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


def set_stop_snapshot(snapshot):
    """记录最近一次用户主动停止的可见摘要，供下一轮恢复。"""
    global LAST_STOP_SNAPSHOT
    LAST_STOP_SNAPSHOT = dict(snapshot or {})


def get_stop_snapshot():
    return dict(LAST_STOP_SNAPSHOT or {})


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
RUNTIME_STATE_FILE = os.path.join(PROJECT_ANT_DIR, "runtime_state.json")
MEMORY_TREE_FILE = os.path.join(ANT_HOME, "memory_tree.json")

SELF_MODIFICATION_APPROVED = False


def approve_self_modification() -> None:
    global SELF_MODIFICATION_APPROVED
    SELF_MODIFICATION_APPROVED = True


_SELF_SOURCE_NAMES = {
    "AntNest.py", "antnest_bridge.py", "antnest_runtime_state.py",
    "antnest_session.py", "code_tools.py", "admin_utils.py",
    "prototype_antnest.py", "ui_render.py", "ui_assets_loader.py",
    "antnest_launcher.py", "phtmlwin.py",
    "ui_assets/app.js", "ui_assets/app.css", "ui_assets/index.html",
}


def _is_self_source_path(path: Path) -> bool:
    try:
        root, target = Path(THIS_DIR).resolve(), path.resolve()
        relative = os.path.normcase(os.path.normpath(os.path.relpath(target, root)))
        allowed = {
            os.path.normcase(os.path.normpath(item)) for item in _SELF_SOURCE_NAMES
        }
        return relative in allowed
    except Exception:
        return False


def _command_self_source_target(command: str) -> Path | None:
    """返回命令试图写入/删除的核心源码路径；只读命令不触发。"""
    text = str(command or "")
    lower = text.lower()
    write_signal = (
        re.search(r"\.write_(?:text|bytes)\s*\(", lower)
        or re.search(r"\bset-content\b|\bout-file\b|\badd-content\b", lower)
        or re.search(r"\b(?:sed|perl)\s+-i\b|\btee\b", lower)
        or re.search(r"\b(?:rm|del|remove-item|unlink|mv|move)\b", lower)
        or re.search(r"\b(?:os|pathlib)\.(?:remove|replace|rename|unlink)\s*\(", lower)
        or re.search(r"\bshutil\.(?:copy|copy2|move|rmtree)\s*\(", lower)
        or re.search(r"open\s*\([^\n)]*,\s*[\"'](?:w|a|x|r\\+|w\\+|a\\+)", lower)
        or re.search(r"(?:^|[^>])>{1,2}(?!=)", text)
    )
    if not write_signal:
        return None
    for name in _SELF_SOURCE_NAMES:
        if name.lower() in lower:
            return Path(THIS_DIR) / name
    return None


def _self_modification_gate(path: Path) -> str:
    if not _is_self_source_path(path) or SELF_MODIFICATION_APPROVED:
        return ""
    runtime_state.set_pending_self_modification(
        RUNTIME_STATE_FILE,
        "需要用户明确确认后才允许修改正在运行的 AntNest 核心源码",
    )
    return json.dumps({
        "status": "approval_required",
        "scope": "self_source",
        "path": str(path),
        "message": "这是 AntNest 正在运行的核心源码。请先向用户说明文件、修改目的和验证计划，等待用户明确同意后再重试。",
    }, ensure_ascii=False)


def _runtime_prompt_context():
    try:
        state = runtime_state.load(RUNTIME_STATE_FILE)
        text = runtime_state.profile_prompt(state) + "\n\n" + runtime_state.persona_prompt(state)
        stop = runtime_state.get_stop(RUNTIME_STATE_FILE)
        if stop:
            text += (
                "\n\n《用户主动停止后的恢复上下文》\n"
                "下一轮先理解可能原因，说明调整后再继续。\n"
                f"任务：{stop.get('task', '')}\n"
                f"决策摘要：{stop.get('decision_summary', '')}\n"
                f"已知证据：{stop.get('evidence', '')}\n"
                f"建议下一步：{stop.get('next_step', '')}"
            )
        return text
    except Exception:
        return ""

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

# ====================== System Prompt（主题文件解耦） ======================
# 引导文件在 prompts/queen_system.md，运行期懒加载，减少 AntNest.py 常驻内存；
# 文件缺失时用紧凑兜底模板，保证 CLI / 工蚁仍可启动。
_PROMPT_FILE = os.path.join(THIS_DIR, "prompts", "queen_system.md")
_FALLBACK_SYSTEM_PROMPT = (
    "# 你是谁\n你是 AntNest，一个采用蚁后/工蚁架构的 AI Agent。\n"
    "# 工具\n- spawn_clone：生成工蚁隔离执行命令\n"
    "- view_file / list_dir / grep_files：只读探索\n"
    "- write_file / search_replace：派工蚁读写文件\n"
    "- web_fetch：抓取网页\n"
    "- leave_memory_hints：记忆压缩时保留线索\n"
    "# 记忆\n每次检索 NEST.md 与 hints.md 的相关内容注入上下文后开始工作。\n"
    "# 当前模型能力\n{model_capability}\n"
)


def _system_prompt_template() -> str:
    """读取女王系统提示模板；文件缺失回退紧凑兜底。"""
    try:
        return Path(_PROMPT_FILE).read_text(encoding="utf-8")
    except Exception:
        return _FALLBACK_SYSTEM_PROMPT


def _fill_env_fields(template: str) -> str:
    """导入期只替换环境派生字段；nest_md / hints / env_info 会话级占位符保留给使用点填充。"""
    repl = {
        "{max_clones_1}": str(MAX_CLONES[1]),
        "{max_clones_2}": str(MAX_CLONES[2]),
        "{max_depth_plus1}": str(MAX_DEPTH + 1),
        "{os_name}": str(OS_NAME),
        "{project_dir}": str(PROJECT_DIR),
        "{ant_home}": str(ANT_HOME),
        "{project_ant_dir}": str(PROJECT_ANT_DIR),
        "{token_cap}": str(TOKEN_CAP),
        "{nest_file}": str(NEST_FILE),
        "{hint_file}": str(HINT_FILE),
    }
    for _k, _v in repl.items():
        template = template.replace(_k, _v)
    return template


SYSTEM_PROMPT = _fill_env_fields(_system_prompt_template())


COMPACT_PROMPT = r"""《紧急危机》！！！记忆容量即将达到上限。

你需要紧急完成：
1. 保存记忆：将对话中的关键信息整理写入记忆文件
2. 保存技能和知识：将对未来有用的技能/知识写入 NEST.md。每条必须包含【触发条件】和【内容】
3. 调用 leave_memory_hints 留下记忆线索

过程中不要中断，直到 leave_memory_hints 被执行。"""
