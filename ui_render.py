# -*- coding: utf-8 -*-
"""UI 渲染辅助（纯函数，无 webview 依赖）。"""
from __future__ import annotations

import html as _h


def esc(s) -> str:
    return _h.escape(str(s))


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
            body += (
                f'<details class="think-block">'
                f'<summary>深度思考</summary>'
                f'<div class="think-body">{esc(reasoning)}</div>'
                f'</details>'
            )
        if text:
            body += f'<div class="bubble-text">{esc(text)}</div>'
        if body:
            parts.append(f'<div class="bubble {role}">{body}</div>')
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
