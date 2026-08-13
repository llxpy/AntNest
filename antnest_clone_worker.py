# -*- coding: utf-8 -*-
"""工蚁 Clone 模式早期入口 — 在蚁后模式加载 LLM 之前执行并退出。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


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

    shell = "powershell" if os.name == "nt" else "bash"
    shell_flag = "-Command" if os.name == "nt" else "-c"

    print(f"[工蚁] 开始执行: {clone_command[:100]}...")

    cmd_lower = clone_command.lower()
    dangerous_patterns = [
        (r"\bformat\s+[a-z]:", "格式化磁盘"),
        (r"\bdel\s+/[fs]\s+[a-z]:\\", "强制删除系统文件"),
        (r"\brm\s+-rf\s+/", "删除根目录"),
        (r"\brmdir\s+/[sq]\s+[a-z]:\\", "删除系统目录"),
        (r"\bshutdown\s+/[rp]\b", "关机/重启"),
        (r"\bdeltree\b", "删除目录树"),
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
        result = subprocess.run(
            [shell, shell_flag, clone_command],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=clone_timeout,
            shell=False,
            cwd=clone_dir,
        )
        output = {
            "status": "ok" if result.returncode == 0 else "error",
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        output = {"status": "timeout", "error": f"命令执行超时（{clone_timeout}秒）"}
    except Exception as e:
        output = {"status": "error", "error": str(e)}

    if result_file:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[工蚁] 完成，exit_code={output.get('exit_code', 'N/A')}")
    sys.exit(0)
