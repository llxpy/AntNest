# -*- coding: utf-8 -*-
"""UI 渲染辅助（纯函数，无 webview 依赖）。"""
from __future__ import annotations

import html as _h


def esc(s) -> str:
    return _h.escape(str(s))


# 蚁后头像：与顶栏品牌 logo 同一只蚂蚁（三节身体 + 六腿 + 触角）
_QUEEN_AVATAR_SVG = (
    '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    '<circle cx="12" cy="5.5" r="2.6"/>'
    '<circle cx="12" cy="11" r="2"/>'
    '<ellipse cx="12" cy="18" rx="3.6" ry="4.4"/>'
    '<path d="M7.2 9.2l3.2 1.6M16.8 9.2l-3.2 1.6M7 14.5l3.2-1M17 14.5l-3.2-1M7.8 19.5l3-1M16.2 19.5l-3-1" '
    'stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/>'
    '</svg>'
)


def render_chat(chats) -> str:
    parts = []
    for item in chats:
        if isinstance(item, dict):
            role = item.get("role", "queen")
            text = item.get("text", "")
            reasoning = item.get("reasoning", "") or ""
        else:
            role, text = item[0], item[1]
            reasoning = ""
        body = ""
        if reasoning:
            # 思考过程不再折叠，直接内联展开，让用户随时看到蚁后在想什么
            body += (
                f'<div class="think-block">'
                f'<div class="think-title">深度思考</div>'
                f'<div class="think-body">{esc(reasoning)}</div>'
                f'</div>'
            )
        if text:
            body += f'<div class="bubble-text">{esc(text)}</div>'
        if body:
            if role == "user":
                avatar = '<span class="avatar user-avatar">你</span>'
            else:
                avatar = f'<span class="avatar queen-avatar">{_QUEEN_AVATAR_SVG}</span>'
            parts.append(
                f'<div class="bubble {role}">{avatar}<div class="bubble-main">{body}</div></div>'
            )
    return "\n".join(parts) or '<div class="muted" style="padding:8px">开始和蚁后对话吧</div>'


def render_log(logs) -> str:
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
