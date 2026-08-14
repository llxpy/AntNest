# -*- coding: utf-8 -*-
"""会话持久化与目录锁（从 AntNest.py 拆分）。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def get_session_file(project_dir: str, session_dir: str) -> str:
    dir_hash = re.sub(r"[\\/:]", "_", project_dir)
    return os.path.join(session_dir, f"{dir_hash}.json")


def acquire_lock(project_dir: str, session_dir: str, is_windows: bool) -> None:
    lock_file = get_session_file(project_dir, session_dir).replace(".json", ".lock")
    if os.path.exists(lock_file):
        try:
            pid = int(Path(lock_file).read_text().strip())
            if is_windows:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
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


def release_lock(project_dir: str, session_dir: str) -> None:
    try:
        os.remove(get_session_file(project_dir, session_dir).replace(".json", ".lock"))
    except Exception:
        pass


def save_session(messages, project_dir: str, session_dir: str) -> None:
    os.makedirs(session_dir, exist_ok=True)
    session_file = get_session_file(project_dir, session_dir)
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    print(f"\n> 会话已保存到：{session_file}")


def load_session(
    messages_template,
    system_prompt: str,
    nest_md: str,
    hints: str,
    env_info: str,
    project_dir: str,
    session_dir: str,
):
    session_file = get_session_file(project_dir, session_dir)
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            messages = json.load(f)

        messages[0] = {
            "role": "system",
            "content": system_prompt.format(
                nest_md=nest_md or "无", hints=hints or "无", env_info=env_info
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


def list_sessions(project_dir: str, session_dir: str) -> None:
    session_file = get_session_file(project_dir, session_dir)
    session_name = os.path.basename(session_file)
    print(f"目录: {session_dir}\n")
    if not os.path.exists(session_dir):
        print("> 没有找到任何会话记录。")
        return

    files = [f for f in os.listdir(session_dir) if f.endswith(".json")]
    if not files:
        print("> 没有找到任何会话记录。")
        return

    print(f"> 共找到 {len(files)} 个会话:")
    print("-" * 60)
    for i, f in enumerate(sorted(files), start=1):
        path = os.path.join(session_dir, f)
        size_KB = (os.path.getsize(path) + 999) // 1000
        marker = "    <=== 当前目录" if f == session_name else ""
        print(f"  {i}. {f} ({format(size_KB, ',')} KB){marker}")
    print("-" * 60)


def clear_session(project_dir: str, session_dir: str) -> None:
    session_file = get_session_file(project_dir, session_dir)
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
            print(f"> 已清除会话：{session_file}")
        except Exception as e:
            print(f"> 清除会话失败：{e}")
    else:
        print(f"> 会话不存在：{session_file}")
