# -*- coding: utf-8 -*-
"""
code_tools — 经工蚁执行的代码/文件工具（纯函数：路径、脚本构建、结果解析）

蚁后不直接读写磁盘；AntNest.py 中的 view_file / list_dir / grep_files / write_file / search_replace
只负责拼脚本并调用 spawn_clone。
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path


def resolve_path(path: str, project_dir: str) -> Path:
    """解析为绝对路径并强制限制在 project_dir 内，越界即拒绝（防写项目外/../逃逸）。"""
    p = Path(path or ".")
    if not p.is_absolute():
        p = Path(project_dir) / p
    p = p.resolve()
    base = Path(project_dir).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise ValueError(f"路径越出项目目录，已拒绝：{path}")
    return p


def worker_py_cmd(py_code: str, *, is_windows: bool, python_exe: str) -> str:
    """把 Python 代码包进 shell -c。Windows PowerShell 不识别 JSON 的 \\\"，需用 base64。"""
    exe = json.dumps(python_exe)
    if is_windows:
        payload = base64.b64encode(py_code.encode("utf-8")).decode("ascii")
        return (
            f"& {exe} -c \"import base64;exec(base64.b64decode('{payload}').decode('utf-8'))\""
        )
    quoted = json.dumps(py_code, ensure_ascii=False)
    return f"{python_exe} -c {quoted}"


def unwrap_worker_json(spawn_result: str) -> str:
    """工蚁 stdout 里嵌 JSON 时，解析并返回内层 JSON 字符串。"""
    try:
        outer = json.loads(spawn_result)
    except Exception:
        return spawn_result
    if not isinstance(outer, dict):
        return spawn_result
    if outer.get("status") in ("cancelled", "timeout", "blocked", "error"):
        return spawn_result
    stdout = (outer.get("stdout") or "").strip()
    if stdout.startswith("{"):
        try:
            inner = json.loads(stdout)
            if isinstance(inner, dict) and inner.get("status"):
                return json.dumps(inner, ensure_ascii=False)
        except Exception:
            pass
    return spawn_result


def build_view_file_script(p: Path, start_line: int, end_line: int) -> str:
    s, e = int(start_line or 1), int(end_line or 0)
    return (
        "import json,pathlib,sys;"
        f"p=pathlib.Path({json.dumps(str(p))});"
        "lines=p.read_text(encoding='utf-8',errors='replace').splitlines() if p.is_file() else None;"
        "import sys as _s;"
        "(_s.exit(2) if lines is None else 0);"
        f"s={s};e={e} or len(lines);"
        "s=max(1,s);e=min(max(s,e),len(lines));"
        "c='\\n'.join(f'{i:5}| {lines[i-1]}' for i in range(s,e+1));"
        "print(json.dumps({'status':'ok','path':str(p),'total_lines':len(lines),"
        "'start_line':s,'end_line':e,'content':c},ensure_ascii=False))"
    )


def build_list_dir_script(p: Path, max_entries: int) -> str:
    m = max(1, int(max_entries or 100))
    return (
        "import json,pathlib,sys\n"
        f"p=pathlib.Path({json.dumps(str(p))})\n"
        f"m={m}\n"
        "if not p.is_dir():\n"
        "    sys.exit(2)\n"
        "items=[]\n"
        "all_items=sorted(p.iterdir())\n"
        "for x in all_items[:m]:\n"
        "    items.append(('[目录] ' if x.is_dir() else '[文件] ')+x.name)\n"
        "hidden=max(0,len(all_items)-m)\n"
        "print(json.dumps({'status':'ok','path':str(p),'entries':items,'hidden':hidden},ensure_ascii=False))\n"
    )

def build_grep_script(p: Path, pattern: str, glob_pattern: str, max_matches: int) -> str:
    m = max(1, min(int(max_matches or 50), 200))
    return (
        "import json,pathlib,re,sys\n"
        f"root=pathlib.Path({json.dumps(str(p))})\n"
        f"pat=re.compile({json.dumps(pattern)}, re.MULTILINE)\n"
        f"glob_pat={json.dumps(glob_pattern or '*')}\n"
        f"limit={m}\n"
        "if not root.exists():\n"
        "    sys.exit(2)\n"
        "matches=[]\n"
        "files=sorted(root.rglob(glob_pat)) if root.is_dir() else [root]\n"
        "for fp in files:\n"
        "    if not fp.is_file():\n"
        "        continue\n"
        "    try:\n"
        "        lines=fp.read_text(encoding='utf-8',errors='replace').splitlines()\n"
        "    except Exception:\n"
        "        continue\n"
        "    for i,line in enumerate(lines,1):\n"
        "        if pat.search(line):\n"
        "            matches.append({'file':str(fp),'line':i,'text':line[:300]})\n"
        "            if len(matches)>=limit:\n"
        "                break\n"
        "        if len(matches)>=limit:\n"
        "            break\n"
        "    if len(matches)>=limit:\n"
        "        break\n"
        "print(json.dumps({'status':'ok','pattern':pat.pattern,'root':str(root),'match_count':len(matches),'matches':matches,'truncated':len(matches)>=limit},ensure_ascii=False))\n"
    )

def build_write_file_script(p: Path, content: str) -> str:
    return (
        "import json,pathlib;"
        f"p=pathlib.Path({json.dumps(str(p))});"
        f"content={json.dumps(content)};"
        "p.parent.mkdir(parents=True, exist_ok=True);"
        "p.write_text(content, encoding='utf-8');"
        "print(json.dumps({'status':'ok','path':str(p),'bytes':len(content.encode('utf-8'))},ensure_ascii=False))"
    )


def build_search_replace_script(
    p: Path, old_string: str, new_string: str, replace_all: bool
) -> str:
    return (
        "import json,pathlib\n"
        f"p=pathlib.Path({json.dumps(str(p))})\n"
        f"old={json.dumps(old_string)}\n"
        f"new={json.dumps(new_string)}\n"
        f"replace_all={str(bool(replace_all))}\n"
        "result={'status':'error','error':'文件不存在','path':str(p)}\n"
        "if p.is_file():\n"
        "    text=p.read_text(encoding='utf-8',errors='replace')\n"
        "    cnt=text.count(old)\n"
        "    if cnt==0:\n"
        "        result={'status':'error','error':'未找到 old_string','path':str(p)}\n"
        "    elif not replace_all and cnt>1:\n"
        "        result={'status':'error','error':f'old_string 出现 {cnt} 次，请缩小范围或设 replace_all=true','path':str(p),'occurrences':cnt}\n"
        "    else:\n"
        "        n=cnt if replace_all else 1\n"
        "        new_text=text.replace(old,new,n) if replace_all else text.replace(old,new,1)\n"
        "        p.write_text(new_text,encoding='utf-8')\n"
        "        result={'status':'ok','path':str(p),'replacements':n,'occurrences_found':cnt}\n"
        "print(json.dumps(result,ensure_ascii=False))\n"
    )

def validate_grep_pattern(pattern: str) -> str | None:
    try:
        re.compile(pattern)
        return None
    except re.error as e:
        return str(e)
