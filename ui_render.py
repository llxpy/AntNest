# -*- coding: utf-8 -*-
"""UI 渲染辅助（纯函数，无 webview 依赖）。"""
from __future__ import annotations

import base64 as _b64
import html as _h
import os as _os

from antnest_ui_pure import render_markdown


def esc(s: object) -> str:
    return _h.escape(str(s))


# 蚁后二次元形象（透明底 PNG，随 ui_assets/queen-avatar.png 分发）：
# 优先用图片，加载失败时回退到原有的六边形 SVG 标志。
_QUEEN_AVATAR_IMG = None


def _queen_avatar_img() -> str:
    """返回蚁后头像的 data: URI；读取失败返回空串（调用方回退 SVG）。"""
    global _QUEEN_AVATAR_IMG
    if _QUEEN_AVATAR_IMG is not None:
        return _QUEEN_AVATAR_IMG
    _QUEEN_AVATAR_IMG = ""
    try:
        p = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "ui_assets", "queen-avatar.png"
        )
        with open(p, "rb") as f:
            _QUEEN_AVATAR_IMG = "data:image/png;base64," + _b64.b64encode(f.read()).decode("ascii")
    except Exception:
        pass
    return _QUEEN_AVATAR_IMG


# 蚁后头像：与顶栏品牌 logo 同一款「六边形蚁巢 + 几何蚂蚁」标志（图片不可用时的兜底）
_FALLBACK_QUEEN_AVATAR_SVG = (
    '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none">'
    '<path d="M12 2.4 L20.3 7.2 V16.8 L12 21.6 L3.7 16.8 V7.2 Z" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.45"/>'
    '<ellipse cx="12" cy="17" rx="3.5" ry="4.2" fill="currentColor"/>'
    '<ellipse cx="10.6" cy="15.9" rx="1.5" ry="2.1" fill="#ffffff" fill-opacity="0.18"/>'
    '<circle cx="12" cy="11.2" r="1.9" fill="currentColor"/>'
    '<circle cx="12" cy="6.9" r="2.4" fill="currentColor"/>'
    '<path d="M10.5 5.2 C9.3 3.5 7.9 2.9 6.6 3 M13.5 5.2 C14.7 3.5 16.1 2.9 17.4 3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>'
    '<path d="M7.5 9.3 L5.5 7.5 M7.7 11.6 L5.1 10.3 M7.4 14.1 L4.8 14.8 M16.5 9.3 L18.5 7.5 M16.3 11.6 L18.9 10.3 M16.6 14.1 L19.2 14.8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>'
    '</svg>'
)


def _queen_avatar_html() -> str:
    # 立绘仅用于设置面板/启动页展示；聊天气泡头像保持几何 SVG 标志
    return f'<span class="avatar queen-avatar">{_FALLBACK_QUEEN_AVATAR_SVG}</span>'


# ---------------------------------------------------------------------------
# Plan DAG 面板（v1.4）
# ---------------------------------------------------------------------------

_PLAN_GLYPH_CLASS = {
    "pending": "plan-pending",
    "running": "plan-running",
    "completed": "plan-done",
    "failed": "plan-failed",
    "skipped": "plan-skipped",
    "blocked": "plan-blocked",
}


def render_plan(plan: dict) -> str:
    """渲染计划面板（路线图 §3 的树形 PLAN）。

    按依赖深度缩进，让「谁卡在谁后面」一眼可见。空计划返回空串——
    调用方据此决定是否显示整个面板。
    """
    if not isinstance(plan, dict):
        return ""
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ""

    def _depth(n: dict, seen: frozenset) -> int:
        deps = [str(d) for d in (n.get("depends_on") or [])]
        deps = [d for d in deps if d not in seen]
        if not deps:
            return 0
        idx = {str(x.get("id")): x for x in nodes if isinstance(x, dict)}
        best = 0
        for d in deps:
            parent = idx.get(d)
            if parent is not None:
                best = max(best, 1 + _depth(parent, seen | {str(n.get("id"))}))
        return best

    goal = str(plan.get("goal") or "").strip()
    progress = plan.get("progress") if isinstance(plan.get("progress"), dict) else {}
    pct = int(progress.get("percent") or 0)

    parts: list[str] = ['<div class="plan-panel">']
    if goal:
        parts.append(f'<div class="plan-goal">{esc(goal)}</div>')
    parts.append(
        f'<div class="plan-bar"><div class="plan-bar-fill" style="width:{pct}%"></div>'
        f'<span class="plan-pct">{pct}%</span></div>'
    )
    parts.append('<ul class="plan-list">')
    for node in nodes:
        if not isinstance(node, dict):
            continue
        status = str(node.get("status") or "pending")
        nid = str(node.get("id") or "")
        title = str(node.get("title") or "")
        glyph = {"pending": "○", "running": "●", "completed": "✓",
                 "failed": "✗", "skipped": "−", "blocked": "⊘"}.get(status, "○")
        indent = _depth(node, frozenset())
        workers = [w for w in (node.get("worker_ids") or [])]
        worker_html = ""
        if workers:
            tags = "".join(f'<span class="plan-worker">工蚁 {esc(w)}</span>' for w in workers)
            worker_html = f'<span class="plan-workers">{tags}</span>'
        summary = str(node.get("result_summary") or "").strip()
        summary_html = (
            f'<div class="plan-summary">{esc(summary[:200])}</div>' if summary else ""
        )
        err = str(node.get("error") or "").strip()
        err_html = f'<div class="plan-error">{esc(err[:200])}</div>' if err else ""
        title_attr = f' title="{esc(str(node.get("detail") or ""))}"' if node.get("detail") else ""
        parts.append(
            f'<li class="plan-item {_PLAN_GLYPH_CLASS.get(status, "plan-pending")}"'
            f' style="margin-left:{indent * 16}px"{title_attr}>'
            f'<span class="plan-glyph">{glyph}</span>'
            f'<span class="plan-id">{esc(nid)}</span>'
            f'<span class="plan-title">{esc(title)}</span>'
            f'{worker_html}{summary_html}{err_html}'
            f"</li>"
        )
    parts.append("</ul></div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 权限徽章（v1.4，路线图 §10）
# ---------------------------------------------------------------------------

def render_permissions(rows: list) -> str:
    """渲染权限徽章列表。rows 来自 antnest_permissions.describe_grants()。"""
    if not rows:
        return ""
    parts = ['<div class="perm-panel">']
    for row in rows:
        if not isinstance(row, dict):
            continue
        granted = bool(row.get("granted"))
        mark = "✓" if granted else "✗"
        cls = "perm-on" if granted else "perm-off"
        parts.append(
            f'<div class="perm-row {cls}">'
            f'<span class="perm-mark">{mark}</span>'
            f'<span class="perm-label">{esc(str(row.get("label") or ""))}</span>'
            f'<span class="perm-note">{esc(str(row.get("note") or ""))}</span>'
            f"</div>"
        )
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 事件时间线（v1.4，路线图 §5）
# ---------------------------------------------------------------------------

_EVENT_GLYPH = {
    "TASK": "◆", "PLAN": "▤", "WORKER": "🐜", "TOOL": "⚙",
    "PERMISSION": "🔒", "CHECKPOINT": "⎘", "LOOP": "↻",
}


def render_timeline(records: list) -> str:
    """渲染事件时间线。records 是 EventRecord 列表（或等价的 dict 列表）。"""
    if not records:
        return ""
    parts = ['<ol class="timeline">']
    for rec in records:
        if isinstance(rec, dict):
            name = str(rec.get("event") or "")
            ts = float(rec.get("ts") or 0)
            wid = rec.get("worker_id")
            data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
        else:
            name = str(getattr(rec, "event", "") or "")
            ts = float(getattr(rec, "ts", 0) or 0)
            wid = getattr(rec, "worker_id", None)
            data = getattr(rec, "data", {}) or {}
        category = name.split("_", 1)[0] if name else ""
        glyph = _EVENT_GLYPH.get(category, "·")
        import time as _t

        stamp = _t.strftime("%H:%M:%S", _t.localtime(ts)) if ts else "--:--:--"
        detail: list[str] = []
        for key in ("tool", "status", "action", "title", "goal", "node", "duration_ms", "reason"):
            val = data.get(key)
            if val not in (None, "", []):
                text = str(val)
                detail.append(f"{key}={text[:80]}")
        wid_html = f'<span class="tl-worker">#{esc(wid)}</span>' if wid is not None else ""
        parts.append(
            f'<li class="tl-item tl-{esc(category.lower())}">'
            f'<span class="tl-time">{stamp}</span>'
            f'<span class="tl-glyph">{glyph}</span>'
            f'<span class="tl-event">{esc(name)}</span>{wid_html}'
            f'<span class="tl-detail">{esc(" ".join(detail))}</span>'
            f"</li>"
        )
    parts.append("</ol>")
    return "".join(parts)


def render_chat(chats: list) -> str:
    parts = []
    for item in chats:
        if isinstance(item, dict):
            role = item.get("role", "queen")
            text = item.get("text", "")
            reasoning = item.get("reasoning", "") or ""
        else:
            role, text = item[0], item[1]
            reasoning = ""
        if role == "sys":
            # 系统事件（如工蚁派发/完工）：居中事件条，无头像
            if text:
                parts.append(
                    f'<div class="bubble sys"><div class="bubble-main">{render_markdown(text)}</div></div>'
                )
            continue
        body = ""
        if role == "queen":
            body += (
                '<div class="bubble-meta"><span class="bm-dot"></span>'
                '<span class="bm-label">蚁后 · Queen</span></div>'
            )
        if reasoning:
            # 思考过程不再折叠，直接内联展开，让用户随时看到蚁后在想什么
            body += (
                f'<div class="think-block">'
                f'<div class="think-title">深度思考</div>'
                f'<div class="think-body">{esc(reasoning)}</div>'
                f'</div>'
            )
        if text:
            body += f'<div class="bubble-text">{render_markdown(text)}</div>'
        if body:
            if role == "user":
                avatar = '<span class="avatar user-avatar">你</span>'
            else:
                avatar = _queen_avatar_html()
            extra_class = " interrupt-note" if str(text).startswith("⏹ 已保存的中断摘要") else ""
            copy_btn = (
                '<button type="button" class="msg-copy" title="复制内容">'
                '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" '
                'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                'stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/>'
                '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
                "</button>"
            )
            parts.append(
                f'<div class="bubble {role}{extra_class}">{avatar}<div class="bubble-main">{body}{copy_btn}</div></div>'
            )
    return "\n".join(parts) or '<div class="muted" style="padding:8px">开始和蚁后对话吧</div>'


def render_log(logs: list) -> str:
    lines = []
    for time, tag, text in logs:
        lines.append(
            f'<div class="log-line">'
            f'<div class="log-time">{time}</div>'
            f'<div class="log-tag {tag}">{tag.upper()}</div>'
            f'<div class="log-body">{esc(text)}</div>'
            f'</div>'
        )
    return "\n".join(lines)

# ================================================================
# 技能表（v1.4.1）
# ================================================================
# 蚁后「会学习」这件事此前完全不可见：自撰工具写进了 .antnest/tools/，
# 界面上毫无变化。本表把「蚁后现在能做什么」显式化，并让自撰工具的
# 数量成为一个**常驻**的可见信号（学习成果的量化）。
#
# 两点诚实性约束（AGENTS.md §3「文档与 UI 措辞不得夸大」）：
#   1. 等级色标**只画 payload 里真实存在的等级**。TOOL_SPECS 实际只用到
#      L0-L3；L4(SYSTEM)/L5(SELF_MOD) 在 PermLevel 里存在但没有任何工具行是
#      这个等级 —— L5 是权限闸门的参数敏感判定结果，不是工具的静态属性。
#      画一格 L5 进去是凭空捏造。
#   2. 标题写「蚁后可见」而不是「全部」。exposed_specs() 是 12，
#      TOOL_SPECS 是 17，工蚁专用的两条蚁后常态拿不到。

# 等级配色。键是 PermLevel 的整数值；缺失的等级不画（见上方约束 1）。
_SKILL_LEVELS = {
    0: ("L0", "只读审阅", "skill-lv lv0"),
    1: ("L1", "写入文件", "skill-lv lv1"),
    2: ("L2", "执行命令", "skill-lv lv2"),
    3: ("L3", "访问网络", "skill-lv lv3"),
}

# 名称长度上限。display_name 来自模型输出，等同不可信输入：
# 既要截断防布局被撑爆，也要在渲染层转义。
_SKILL_NAME_MAX = 18
_SKILL_DESC_MAX = 80


def _skill_name(s: object) -> str:
    """截断后转义。顺序不能反：先按字符截，再转义。"""
    t = str(s or "")
    if len(t) > _SKILL_NAME_MAX:
        t = t[:_SKILL_NAME_MAX - 1] + "…"
    return esc(t)


def _skill_desc(s: object) -> str:
    t = " ".join(str(s or "").split())
    if len(t) > _SKILL_DESC_MAX:
        t = t[:_SKILL_DESC_MAX - 1] + "…"
    return esc(t)


def render_skill_table(payload: dict) -> str:
    """渲染技能表。**纯函数** —— 不碰文件系统、不碰全局状态。

    payload 来自 antnest_toolforge.build_skill_payload()。
    """
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return '<div class="muted skill-empty">技能表暂不可用（核心尚未加载）</div>'

    builtin = payload.get("builtin") or []
    worker_only = payload.get("worker_only") or []
    learned = payload.get("learned") or []
    counts = payload.get("counts") or {}

    out = []

    # --- 图例：只列实际存在的等级 ---
    used = sorted({int(r.get("level", 0)) for r in builtin if isinstance(r, dict)})
    used = [lv for lv in used if lv in _SKILL_LEVELS]
    if used:
        leg = []
        for lv in used:
            tag, label, cls = _SKILL_LEVELS[lv]
            leg.append(
                f'<span class="skill-legend-item"><span class="{cls}">{tag}</span>'
                f'<span class="muted">{esc(label)}</span></span>'
            )
        out.append(f'<div class="skill-legend">{"".join(leg)}</div>')

    # --- 内置能力 ---
    nb = counts.get("builtin", len(builtin))
    if builtin:
        rows = []
        for r in builtin:
            lv = int(r.get("level", 0))
            tag, _label, cls = _SKILL_LEVELS.get(lv, ("L?", "", "skill-lv lvx"))
            rows.append(
                f'<div class="skill-row">'
                f'<span class="{cls}">{tag}</span>'
                f'<span class="skill-name">{_skill_name(r.get("display"))}</span>'
                f'<span class="muted skill-desc">{_skill_desc(r.get("summary"))}</span>'
                f"</div>"
            )
        out.append(
            f'<div class="skill-group"><div class="skill-group-h">'
            f'内置能力 <span class="skill-count">{nb}</span></div>'
            f'{"".join(rows)}</div>'
        )

    # --- 工蚁专用：蚁后常态拿不到，单独归组而非混进上面 ---
    nw = counts.get("worker_only", len(worker_only))
    if worker_only:
        rows = []
        for r in worker_only:
            lv = int(r.get("level", 0))
            tag, _label, cls = _SKILL_LEVELS.get(lv, ("L?", "", "skill-lv lvx"))
            rows.append(
                f'<div class="skill-row is-worker">'
                f'<span class="{cls}">{tag}</span>'
                f'<span class="skill-name">{_skill_name(r.get("display"))}</span>'
                f'<span class="muted skill-desc">{_skill_desc(r.get("summary"))}</span>'
                f"</div>"
            )
        out.append(
            f'<details class="skill-group skill-group-fold">'
            f'<summary class="skill-group-h">工蚁专用 '
            f'<span class="skill-count">{nw}</span></summary>'
            f'{"".join(rows)}</details>'
        )

    # --- 自撰工具：蚁后自己写的，是「它在变强」的可见证据 ---
    nl = counts.get("learned", len(learned))
    if learned:
        rows = []
        for r in learned:
            note = ""
            if r.get("broken"):
                note = '<span class="skill-broken">元数据损坏</span>'
            rows.append(
                f'<div class="skill-row is-learned">'
                f'<span class="skill-mark">◆</span>'
                f'<span class="skill-name">{_skill_name(r.get("display"))}</span>'
                f'<span class="muted skill-desc">{_skill_desc(r.get("description"))}</span>'
                f"{note}</div>"
            )
        out.append(
            f'<div class="skill-group"><div class="skill-group-h">'
            f'自撰工具 <span class="skill-count">{nl}</span></div>'
            f'{"".join(rows)}</div>'
        )
    else:
        # 零自撰工具是**常态**（刚装完就是这样），不是错误，所以给引导文案
        out.append(
            '<div class="skill-group"><div class="skill-group-h">自撰工具 '
            '<span class="skill-count">0</span></div>'
            '<div class="muted skill-empty">蚁后还没自撰过工具。'
            "遇到重复的活儿它会把脚本登记进来，下次直接复用。</div></div>"
        )

    return f'<div class="skill-table">{"".join(out)}</div>'
