# -*- coding: utf-8 -*-
"""AntNest UI · 无副作用纯函数

从 prototype_antnest.py 抽取的纯函数集合，无模块级副作用：
  - import 本模块不会弹出 GUI 窗口、不会抢单实例锁、不会开线程
  - 所有函数均为纯函数（仅依赖入参与标准库）

设计约束（重要）：
  - 此模块禁止 import prototype_antnest（会触发 GUI 副作用）
  - 禁止 import ui_render（它与本模块相互依赖，会循环导入）
  - 禁止在模块顶层执行任何 I/O / 线程 / 单例检查
"""
from __future__ import annotations

import html as _html
import re


def _version_tuple(v: str | None) -> tuple:
    """把版本字符串解析为可比较的整数元组（忽略非数字段）。"""
    parts: list[int] = []
    for p in re.split(r"[.\-+]", str(v or "")):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def _version_gt(a: str | None, b: str | None) -> bool:
    """判断版本 a 是否大于版本 b。"""
    return _version_tuple(a) > _version_tuple(b)


def _classify_command(command: str | None) -> str:
    """从原始 shell 命令中提取人类可读的操作描述。"""
    cmd = (command or "").strip()
    if not cmd:
        return "执行命令"
    c = cmd.lower()

    # PowerShell 文件操作
    if re.search(r"Get-ChildItem", cmd, re.I):
        m = re.search(r"Get-ChildItem\s+['\"]?([^'\"\s]+)", cmd, re.I)
        return f"列出目录：{m.group(1)}" if m else "列出目录"
    if re.search(r"Get-Content", cmd, re.I):
        m = re.search(r"Get-Content\s+['\"]?([^'\"\s]+)", cmd, re.I)
        return f"读取文件：{m.group(1)}" if m else "读取文件"
    if re.search(r"Set-Content|Out-File|Add-Content", cmd, re.I):
        m = re.search(r"(?:Set-Content|Out-File|Add-Content)\s+['\"]?([^'\"\s]+)", cmd, re.I)
        return f"写入文件：{m.group(1)}" if m else "写入文件"

    # Python
    if re.search(r"python\s+", c) or re.search(r"python\.exe\s+", c):
        m = re.search(r"python(?:\.exe)?\s+(?:-\S+\s+)*['\"]?([^'\"\s]+\.py)", c)
        return f"执行 Python：{m.group(1)}" if m else "执行 Python 脚本"

    # Git
    if re.search(r"git\s+", c):
        m = re.search(r"git\s+(\w+)", c)
        if m:
            sub = m.group(1)
            desc_map = {
                "add": "暂存文件", "commit": "提交变更", "push": "推送代码",
                "pull": "拉取代码", "clone": "克隆仓库", "checkout": "切换分支",
                "merge": "合并分支", "status": "查看状态", "diff": "查看差异",
                "log": "查看日志", "branch": "管理分支", "fetch": "获取远程更新",
                "stash": "暂存工作", "reset": "重置变更",
            }
            return f"Git {desc_map.get(sub, sub)}"
        return "执行 Git 操作"

    # npm / node
    if re.search(r"npm\s+", c):
        m = re.search(r"npm\s+(\w+)", c)
        return f"npm {m.group(1)}" if m else "执行 npm"

    # docker
    if re.search(r"docker\s+", c):
        m = re.search(r"docker\s+(\w+)", c)
        return f"Docker {m.group(1)}" if m else "执行 Docker 操作"

    # 测试/构建
    if re.search(r"pytest|unittest|go test|cargo test", c):
        return "运行测试"
    if re.search(r"make|cmake|cargo build|go build|mvn|gradle", c):
        return "构建项目"

    # pip
    if re.search(r"pip\s+", c):
        m = re.search(r"pip\s+(\w+)\s+(\S+)", c)
        if m and m.group(1) == "install":
            return f"安装依赖：{m.group(2)}"
        return f"pip {m.group(1)}" if m else "执行 pip"

    # 通用 shell
    if re.search(r"Write-Output|echo\s+", cmd, re.I):
        return "输出内容"

    # 取前40字符作为兜底描述
    return cmd[:40].replace("\n", " ") + ("…" if len(cmd) > 40 else "")


def _esc(s: str) -> str:
    """HTML 转义（标准库实现，避免与 ui_render 循环导入）。"""
    return _html.escape(str(s))


def _clip(s: str | None, n: int = 120) -> str:
    """将文本截断，过长时取前 n 字符并省略号，便于片段展示/发送。"""
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------------------
# 轻量 Markdown 渲染（安全白名单）
# ---------------------------------------------------------------------------
# 只渲染最常用的几种标记，全程先转义再套标签，杜绝注入：
#   - 代码块   ```lang\n…\n```
#   - 行内代码 `…`
#   - 加粗     **…**
#   - 斜体     *…*（不与加粗冲突：优先匹配 **）
#   - 标题     # …（消息语境统一用 h4，收敛视觉层级）
#   - 无序列表 - … / * …
#   - 链接     [text](url)，仅放行 http/https，补 target/rel
# 其余一律按纯文本转义输出，绝不放任何原始 HTML。

_RE_FENCE = re.compile(r"```(\w*)\s*\n(.*?)\n```", re.S)
_RE_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_RE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_RE_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_RE_HEADING = re.compile(r"^#{1,4}\s+(.*)$")
_RE_LIST = re.compile(r"^\s*[*\-+]\s+(.*)$")
_RE_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s\)]+)\)")
_LINK_REL = ' target="_blank" rel="noopener noreferrer"'


def _md_inline(s: str) -> str:
    """行内渲染：先处理行内代码（其内容不再套 markdown），再处理粗体/斜体/链接。"""
    parts: list[str] = []
    pos = 0
    for m in _RE_INLINE_CODE.finditer(s):
        parts.append(_md_link(segment=s[pos:m.start()]))
        parts.append("<code>" + _esc(m.group(1)) + "</code>")
        pos = m.end()
    parts.append(_md_link(segment=s[pos:]))
    return "".join(parts)


def _md_link(*, segment: str) -> str:
    """行内 markdown（链接/加粗/斜体）：先整体转义，再按白名单套标签。"""
    segment = _esc(segment)

    def _one(m: "re.Match[str]") -> str:
        label = _esc(m.group(1))
        href = _esc(m.group(2))
        return f'<a href="{href}"{_LINK_REL}>{label}</a>'

    segment = _RE_LINK.sub(_one, segment)
    segment = _RE_BOLD.sub(r"<strong>\1</strong>", segment)
    segment = _RE_ITALIC.sub(r"<em>\1</em>", segment)
    return segment


def render_markdown(text: str | None) -> str:
    """把可能含 Markdown 的文本渲染为安全的 HTML 片段。

    - 输入中的任何 HTML 一律先转义，绝不透传
    - 代码块整体转义并保留原样
    - 非 Markdown 文本结果与原 esc() 一致（保持既有测试语义）
    """
    text = str(text or "")
    if not text:
        return ""
    # 1) 代码块：整体优先提取，内部原样保留
    fences: list[str] = []

    def _fence_sub(m: "re.Match[str]") -> str:
        idx = len(fences)
        fences.append(m.group(2))
        return f"%%FENCE{idx}%%"

    pre = _RE_FENCE.sub(_fence_sub, text)
    # 2) 行级处理：标题 + 列表；其余段落保留
    lines_out: list[str] = []
    for line in pre.split("\n"):
        m = _RE_HEADING.match(line)
        if m:
            inner = _md_inline(m.group(1))
            lines_out.append(f"<h4>{inner}</h4>")
            continue
        m = _RE_LIST.match(line)
        if m:
            inner = _md_inline(m.group(1))
            lines_out.append(f"<li>{inner}</li>")
            continue
        if not line.strip():
            continue
        lines_out.append(_md_inline(line))
    # 3) 列表包裹
    joined: list[str] = []
    i = 0
    while i < len(lines_out):
        if lines_out[i].startswith("<li>"):
            group = [lines_out[i]]
            j = i + 1
            while j < len(lines_out) and lines_out[j].startswith("<li>"):
                group.append(lines_out[j])
                j += 1
            joined.append("<ul>" + "".join(group) + "</ul>")
            i = j
        else:
            joined.append(lines_out[i])
            i += 1
    # 4) 非空段落用 <br> 连接
    html = "<br>".join(joined)
    # 5) 还原代码块
    for idx, body in enumerate(fences):
        html = html.replace(f"%%FENCE{idx}%%", "<pre><code>" + _esc(body) + "</code></pre>")
    return html
