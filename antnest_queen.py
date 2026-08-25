# -*- coding: utf-8 -*-
"""AntNest · 蚁后执行域：spawn_clone 工蚁生命周期 + 文件/搜索工具 + CLI/Python/Web/MCP。

共享状态经 AntNest 壳读取（_A()）；本模块只含执行逻辑与私有常量。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import code_tools as ct
import antnest_clone_worker
import admin_utils


def _A():
    import AntNest as _m
    return _m


def get_queen_tools(compact_panic: bool = False) -> list:
    """蚁后可用工具列表（含可选 MCP）。"""
    if compact_panic:
        base = [_A().spawn_clone_schema, _A().memory_hints_schema]
    else:
        base = [
            _A().spawn_clone_schema,
            _A().get_task_status_schema,
            _A().view_file_schema,
            _A().list_dir_schema,
            _A().grep_files_schema,
            _A().write_file_schema,
            _A().search_replace_schema,
            _A().web_fetch_schema,
            _A().register_tool_schema,
            _A().list_tools_schema,
            _A().get_tool_source_schema,
        ]
    if _A().MCP_ENABLED and _A().MCP_HUB is not None:
        base.extend([_A().mcp_call_schema, _A().mcp_list_tools_schema])
    return base


def mcp_call(server: str, tool: str, arguments: str = "{}") -> str:
    if not _A().MCP_ENABLED or _A().MCP_HUB is None:
        return json.dumps({"status": "error", "error": "MCP 未启用或未加载"}, ensure_ascii=False)
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            return json.dumps({"status": "error", "error": "arguments 必须是 JSON 对象"}, ensure_ascii=False)
        return _A().MCP_HUB.call(server, tool, args)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def mcp_list_tools() -> str:
    if not _A().MCP_ENABLED or _A().MCP_HUB is None:
        return json.dumps({"status": "error", "error": "MCP 未启用或未加载"}, ensure_ascii=False)
    try:
        return json.dumps({"status": "ok", "tools": _A().MCP_HUB.list_catalog()}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

# ====================== 工蚁生命周期 ======================
# 资源上限常量（魔法数字统一命名，便于按需调整）
_ARTIFACTS_MAX_KEEP = 50       # artifacts 目录保留上限
_CLONE_POOL_MAX_KEEP = 20      # clone_pool 目录保留上限
_RUN_PYTHON_OUTPUT_CAP = 2 * 1024 * 1024  # _A().run_python 返回输出上限
_MEMORY_TOP_K = 4              # BM25 记忆检索注入段落数
_DUP_CALL_LIMIT = 3            # 相同工具+参数连续次数，超过即判定循环
_DUP_RECENT_MAX = 10           # 重复检测保留的最近调用记录数
_ARTIFACT_EXCLUDES = {
    "AntNest.py", "antnest_clone_worker.py", "code_tools.py",
    "antnest_session.py", "api_compat.py", "memory_retrieval.py",
    "antnest_runtime_state.py", "task_manager.py", "admin_utils.py",
    "memory_tree.py",
    "result.json", "clone.log", "command.txt", "ant_cmd.ps1", "ant_cmd.sh",
}


def collect_clone_artifacts(clone_dir: str, clone_id: str) -> list:
    """把工蚁隔离目录中的产出文件归档到 .antnest/artifacts/<clone_id>/。

    返回归档后的相对路径列表（相对项目目录，正斜杠）。只收用户文件，
    脚本/日志/结果等内部文件一律排除。被 _A().spawn_clone 在销毁目录前调用。
    """
    try:
        artifacts_root = os.path.join(_A().PROJECT_ANT_DIR, "artifacts", clone_id)
        os.makedirs(artifacts_root, exist_ok=True)
        # 保留上限：只留最近 50 个工蚁的产出，防止自检/长任务把磁盘撑满
        _art_root = os.path.join(_A().PROJECT_ANT_DIR, "artifacts")
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
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "prompts")]
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


def spawn_clone(command: str, timeout: int = 0, label: str = "", verify: bool = False) -> str:
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
    if timeout <= 0:
        timeout = _A().DEFAULT_WORKER_TIMEOUT
    # ====== 深度硬拦截：代码级兜底，不依赖 LLM 遵守 prompt ======
    current_depth = int(os.environ.get("AN_DEPTH", "0"))
    if current_depth > _A().MAX_DEPTH:
        return json.dumps({
            "status": "blocked",
            "error": (
                f"深度限制：当前 depth={current_depth} 已超过 MAX_DEPTH={_A().MAX_DEPTH}，"
                f"spawn_clone 已被硬拦截。请改用 run_cli 直接执行。"
            ),
        }, ensure_ascii=False)

    # 运行时门禁：即使模型绕过 write_file/search_replace，直接拼 shell/Python 写核心源码也必须询问。
    command_target = _A()._command_self_source_target(command)
    if command_target is not None:
        gate = _A()._self_modification_gate(command_target)
        if gate:
            return gate

    # ====== 管理员模式：危险命令确认 ======
    if _A()._admin_info["is_admin"]:
        analysis = admin_utils.analyze_command(command)
        if analysis["is_dangerous"] and analysis["confirmation_required"]:
            confirmed, user_input = admin_utils.get_user_confirmation(command)
            if not confirmed:
                return json.dumps({
                    "status": "cancelled",
                    "error": f"用户取消执行危险命令：{analysis['description']}",
                    "user_input": user_input
                }, ensure_ascii=False)

    # ====== 任务状态管理 ======
    from task_manager import get_task_manager, TaskStatus
    tm = get_task_manager()
    task_id = uuid.uuid4().hex[:8]
    task = tm.create_task(task_id, command, label)
    task.mark_running()

    clone_id = uuid.uuid4().hex[:8]
    clone_dir = os.path.join(_A().CLONE_POOL_DIR, f"clone_{clone_id}")
    os.makedirs(clone_dir, exist_ok=True)

    # 清理历史克隆目录：只保留最近 20 个，防止进程被强杀（finally 不执行）后残留堆积
    try:
        _dirs = [
            os.path.join(_A().CLONE_POOL_DIR, d) for d in os.listdir(_A().CLONE_POOL_DIR)
            if d.startswith("clone_") and os.path.isdir(os.path.join(_A().CLONE_POOL_DIR, d))
        ]
        if len(_dirs) > _CLONE_POOL_MAX_KEEP:
            _dirs.sort(key=os.path.getmtime)
            for _old in _dirs[: len(_dirs) - _CLONE_POOL_MAX_KEEP]:
                shutil.rmtree(_old, ignore_errors=True)
    except Exception:
        pass

    # 复制蚁后脚本及工蚁模式依赖（工蚁在隔离目录 import，必须随包带上）
    clone_script = os.path.join(clone_dir, "AntNest.py")
    shutil.copy2(_A().THIS_FILE, clone_script)
    for _dep in ("antnest_clone_worker.py", "code_tools.py", "antnest_session.py", "api_compat.py", "memory_retrieval.py", "antnest_runtime_state.py", "task_manager.py", "admin_utils.py", "memory_tree.py", "model_capabilities.py", "antnest_config.py", "antnest_schemas.py", "antnest_queen.py", "antnest_memory.py", "antnest_llm.py", "antnest_loop.py"):
        _src = os.path.join(_A().THIS_DIR, _dep)
        if os.path.isfile(_src):
            shutil.copy2(_src, os.path.join(clone_dir, _dep))
    # 工蚁若在隔离目录 import AntNest（懒加载主题文件）需要 prompts 目录
    _prompts_src = os.path.join(_A().THIS_DIR, "prompts")
    if os.path.isdir(_prompts_src):
        shutil.copytree(_prompts_src, os.path.join(clone_dir, "prompts"), dirs_exist_ok=True)

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
        _A()._ACTIVE_CLONE_PROCS.append(proc)

        try:
            deadline = time.time() + timeout + 60
            while proc.poll() is None:
                if _A().AGENT_CANCEL:
                    _A()._kill_process_tree(proc)
                    proc.wait(timeout=5)
                    return json.dumps({
                        "status": "cancelled",
                        "error": "用户强行停止，工蚁已终止",
                    }, ensure_ascii=False)
                if time.time() > deadline:
                    _A()._kill_process_tree(proc)
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
            _A()._kill_process_tree(proc)
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
                _A()._ACTIVE_CLONE_PROCS.remove(proc)
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
    if current_depth >= _A().MAX_DEPTH:
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
    return ct.resolve_path(path, _A().PROJECT_DIR)


def _worker_py_cmd(py_code: str) -> str:
    return ct.worker_py_cmd(py_code, is_windows=_A().IS_WINDOWS, python_exe=sys.executable)


def _unwrap_worker_json(spawn_result: str) -> str:
    return ct.unwrap_worker_json(spawn_result)


def view_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """派工蚁只读查看文件（蚁后自己不读）。"""
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    py = ct.build_view_file_script(p, start_line, end_line)
    raw = _A().spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"查看·{p.name}")
    return _unwrap_worker_json(raw)


def list_dir(path: str = ".", max_entries: int = 100) -> str:
    """派工蚁只读列目录（蚁后自己不列）。"""
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    py = ct.build_list_dir_script(p, max_entries)
    raw = _A().spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"列目录·{p.name}")
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
    raw = _A().spawn_clone(command=_worker_py_cmd(py), timeout=120, label=f"搜索·{pattern[:24]}")
    return _unwrap_worker_json(raw)


def write_file(path: str, content: str) -> str:
    """派工蚁写入文件（蚁后自己不写）。"""
    try:
        p = _resolve_path(path)
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    gate = _A()._self_modification_gate(p)
    if gate:
        return gate
    py = ct.build_write_file_script(p, content or "")
    raw = _A().spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"写入·{p.name}")
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
    gate = _A()._self_modification_gate(p)
    if gate:
        return gate
    py = ct.build_search_replace_script(p, old_string, new_string, replace_all)
    raw = _A().spawn_clone(command=_worker_py_cmd(py), timeout=60, label=f"替换·{p.name}")
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
    command_target = _A()._command_self_source_target(command)
    if command_target is not None:
        gate = _A()._self_modification_gate(command_target)
        if gate:
            return gate
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
    与 _A().run_cli 的区别：不经过 PowerShell/bash，中文与引号完整保留。
    """
    code_target = _A()._command_self_source_target(code)
    if code_target is not None:
        gate = _A()._self_modification_gate(code_target)
        if gate:
            return gate
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
            cwd=_A().PROJECT_DIR,
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
