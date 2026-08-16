# -*- coding: utf-8 -*-
"""会话持久化与目录锁（从 AntNest.py 拆分）。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def get_session_file(project_dir: str, session_dir: str, session_id: str = "default") -> str:
    """会话文件路径：<session_dir>/<dir_hash>/<session_id>.json（一个项目多份会话）。"""
    dir_hash = re.sub(r"[\\/:]", "_", project_dir)
    sid = _sanitize_session_id(session_id)
    return os.path.join(session_dir, dir_hash, f"{sid}.json")


def _sanitize_session_id(sid) -> str:
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid or "default"))
    return sid or "default"


def _legacy_session_file(project_dir: str, session_dir: str) -> str:
    """旧版单会话文件路径（<dir_hash>.json），用于兼容迁移。"""
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


def save_session(messages, project_dir: str, session_dir: str, session_id: str = "default") -> None:
    os.makedirs(session_dir, exist_ok=True)
    session_file = get_session_file(project_dir, session_dir, session_id)
    os.makedirs(os.path.dirname(session_file), exist_ok=True)
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
    session_id: str = "default",
):
    session_file = get_session_file(project_dir, session_dir, session_id)
    if not os.path.exists(session_file):
        # 兼容旧版单会话文件（<dir_hash>.json）
        legacy = _legacy_session_file(project_dir, session_dir)
        if os.path.exists(legacy):
            session_file = legacy
        else:
            return None
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


def list_project_sessions(project_dir: str, session_dir: str) -> list:
    """返回当前项目的所有会话元信息（按时间倒序），供 UI 展示。"""
    dir_hash = re.sub(r"[\\/:]", "_", project_dir)
    folder = os.path.join(session_dir, dir_hash)
    out = []
    try:
        if os.path.isdir(folder):
            for fn in os.listdir(folder):
                if fn.endswith(".json"):
                    meta = _session_meta(os.path.join(folder, fn), os.path.splitext(fn)[0])
                    if meta:
                        out.append(meta)
    except Exception:
        pass
    legacy = _legacy_session_file(project_dir, session_dir)
    if os.path.exists(legacy):
        meta = _session_meta(legacy, "default")
        if meta:
            out.append(meta)
    out.sort(key=lambda x: x["time"], reverse=True)
    return out


def _session_meta(path, sid):
    try:
        msgs = json.load(open(path, "r", encoding="utf-8"))
        if not isinstance(msgs, list):
            return None
        preview, count = "", 0
        for m in msgs:
            if not isinstance(m, dict) or m.get("role") == "system":
                continue
            count += 1
            if not preview and m.get("role") == "user":
                preview = ((m.get("content") or "").strip().replace("\n", " "))[:60]
        if count == 0:
            return None
        return {"id": sid, "time": os.path.getmtime(path),
                "size_kb": (os.path.getsize(path) + 999) // 1000,
                "preview": preview or "(无文本消息)", "count": count}
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


def delete_session(project_dir: str, session_dir: str, session_id: str) -> bool:
    """删除指定会话文件，返回是否成功。"""
    session_file = get_session_file(project_dir, session_dir, session_id)
    try:
        if os.path.exists(session_file):
            os.remove(session_file)
            return True
    except Exception:
        pass
    return False
