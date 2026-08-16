# -*- coding: utf-8 -*-
"""工蚁 Clone 模式早期入口 — 在蚁后模式加载 LLM 之前执行并退出。"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path


def _pump_limited(stream, cap: int):
    """读管道直到 EOF，最多保留 cap 字节（超限丢弃后续，继续 drain 防子进程阻塞）。"""
    parts = []
    total = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        if total < cap:
            parts.append(chunk)
            total += len(chunk)
    return b"".join(parts)


_MAX_OUTPUT_CAP = 5 * 1024 * 1024  # 单管道输出收集上限 5MB


def decode_output(data: bytes) -> str:
    """自适应解码子进程输出：优先 UTF-8，失败回退 GBK，再失败用替换符。
    用于兼容中文 Windows 上部分原生工具仍按 GBK 输出的情况。"""
    if not data:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def prepare_command_script(command: str, scratch_dir: str) -> list:
    """把命令写入脚本文件并返回执行 argv，规避 Windows 32K 命令行限制与引号问题。

    Windows：写入带 BOM 的 UTF-8 .ps1（PowerShell 5.1 无 BOM 会按 ANSI 读取导致中文乱码），
    脚本头统一输出编码为 UTF-8 并 chcp 65001，使 PowerShell cmdlet 与原生工具的中文输出一致；
    脚本尾把最后一条命令的成功状态/原生退出码透传为进程退出码。
    POSIX：写入 UTF-8 .sh 由 bash 执行。
    """
    os.makedirs(scratch_dir, exist_ok=True)
    if os.name == "nt":
        script_path = os.path.join(scratch_dir, "ant_cmd.ps1")
        preamble = (
            "$ErrorActionPreference='Continue'\n"
            "$OutputEncoding=[System.Text.Encoding]::UTF8\n"
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
            "chcp 65001 | Out-Null\n"
        )
        epilogue = (
            "\n$global:ANT_NEST_OK=$?\n"
            "$global:ANT_NEST_LAST=$LASTEXITCODE\n"
            "if (-not $global:ANT_NEST_OK) {\n"
            "    if ($null -ne $global:ANT_NEST_LAST -and $global:ANT_NEST_LAST -ne 0) "
            "{ exit $global:ANT_NEST_LAST }\n"
            "    exit 1\n"
            "}\n"
            "if ($null -ne $global:ANT_NEST_LAST) { exit $global:ANT_NEST_LAST }\n"
            "exit 0\n"
        )
        with open(script_path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(preamble + command + epilogue)
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
        ]
    script_path = os.path.join(scratch_dir, "ant_cmd.sh")
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(command + "\n")
    return ["bash", script_path]


def run_if_clone_mode() -> None:
    if os.environ.get("AN_CLONE_MODE") != "1":
        return

    clone_command = os.environ.get("AN_CLONE_COMMAND", "")
    clone_timeout = int(os.environ.get("AN_CLONE_TIMEOUT", "300"))
    result_file = os.environ.get("AN_RESULT_FILE", "")
    clone_dir = os.environ.get("AN_CLONE_DIR", os.getcwd())

    cmd_file = os.environ.get("AN_CLONE_COMMAND_FILE", "")
    if cmd_file and os.path.exists(cmd_file):
        clone_command = Path(cmd_file).read_text(encoding="utf-8")

    run_env = os.environ.copy()
    run_env.setdefault("PYTHONIOENCODING", "utf-8")
    run_env.setdefault("PYTHONUTF8", "1")
    # 关键防递归：工蚁执行的命令子进程绝不能继承 AntNest 的 clone 控制变量。
    # 否则命令里一旦 `python AntNest.py` 或 `import AntNest`，就会带着 AN_CLONE_MODE /
    # AN_CLONE_COMMAND 无限自嵌套（每级一个新 python 进程），最终撑爆内存。
    for _k in ("AN_CLONE_MODE", "AN_CLONE_COMMAND", "AN_CLONE_COMMAND_FILE",
               "AN_CLONE_DIR", "AN_RESULT_FILE", "AN_CLONE_TIMEOUT", "AN_DEPTH"):
        run_env.pop(_k, None)

    print(f"[工蚁] 开始执行: {clone_command[:100]}...")

    cmd_lower = clone_command.lower()
    # 低严谨度命令已整体 lower，故模式用小写；覆盖范围与蚁后 _DANGER_CLI_PATTERNS 对齐：
    # 递归删除指向根/绝对路径/盘符/主目录/上级/当前/通配/HOME，以及格式化、关机、fork bomb。
    dangerous_patterns = [
        (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+/(?!/)", "递归删除根/绝对路径"),
        (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+[a-z]:[\\/]", "递归删除整个盘符"),
        (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+~", "递归删除用户主目录"),
        (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+\.\.(?:[\\/]|\s|$)", "递归删除上级目录"),
        (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+\.(?:\s|$)", "递归删除当前目录"),
        (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+\*", "通配递归删除"),
        (r"rm\s+-(?:rf|fr|r\s*-f|f\s*-r|r)\s+\$home", "递归删除 home"),
        (r"del\s+/[fs]\s+[a-z]:[\\/]", "强制删除系统文件"),
        (r"rmdir\s+/[sq]\s+[a-z]:[\\/]", "删除系统目录"),
        (r"format\s+[a-z]:", "格式化磁盘"),
        (r"shutdown\s+/[rp]\b", "关机/重启"),
        (r"deltree", "删除目录树"),
        (r"mkfs", "格式化文件系统"),
        (r"dd\s+if=/dev/zero", "危险磁盘写入"),
        (r"remove-item[^\n]*(?:-recurse|-force|-r\b)", "powershell 递归/强制删除"),
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};:", "fork bomb"),
    ]
    for pattern, desc in dangerous_patterns:
        if re.search(pattern, cmd_lower):
            output = {"status": "blocked", "error": f"工蚁拒绝执行危险命令（{desc}）"}
            if result_file:
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump(output, f, ensure_ascii=False, indent=2)
            print(f"[工蚁] 拦截：{desc}")
            sys.exit(0)

    try:
        argv = prepare_command_script(clone_command, clone_dir)
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            cwd=clone_dir,
            env=run_env,
            **(  {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                else {"start_new_session": True}
            ),
        )
        try:
            # 有上限收集输出（各 5MB），自检脚本刷屏也不会撑爆内存；
            # 双线程泵取避免 stdout/stderr 大输出时管道死锁
            import threading as _th
            _out_parts, _err_parts = [], []
            _t_out = _th.Thread(
                target=lambda: _out_parts.append(_pump_limited(proc.stdout, _MAX_OUTPUT_CAP)),
                daemon=True,
            )
            _t_err = _th.Thread(
                target=lambda: _err_parts.append(_pump_limited(proc.stderr, _MAX_OUTPUT_CAP)),
                daemon=True,
            )
            _t_out.start()
            _t_err.start()
            try:
                proc.wait(timeout=clone_timeout)
            except subprocess.TimeoutExpired:
                # 超时：连命令的子进程一起杀，避免 python.exe 等孤儿进程堆积
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            capture_output=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    else:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                proc.wait(timeout=5)
            _t_out.join(timeout=5)
            _t_err.join(timeout=5)
            out_b = b"".join(_out_parts)
            err_b = b"".join(_err_parts)
            if proc.returncode is None:
                output = {
                    "status": "timeout",
                    "error": f"命令执行超时（{clone_timeout}秒）",
                    "exit_code": -1,
                    "stdout": decode_output(out_b),
                    "stderr": decode_output(err_b),
                }
            else:
                output = {
                    "status": "ok" if proc.returncode == 0 else "error",
                    "exit_code": proc.returncode,
                    "stdout": decode_output(out_b),
                    "stderr": decode_output(err_b),
                }
        except Exception as e:
            output = {"status": "error", "error": str(e)}
    except Exception as e:
        output = {"status": "error", "error": str(e)}

    if result_file:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[工蚁] 完成，exit_code={output.get('exit_code', 'N/A')}")
    sys.exit(0)
