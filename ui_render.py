# -*- coding: utf-8 -*-
"""UI 渲染辅助（纯函数，无 webview 依赖）。"""
from __future__ import annotations

import base64 as _b64
import html as _h
import os as _os


def esc(s) -> str:
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
            body += f'<div class="bubble-text">{esc(text)}</div>'
        if body:
            if role == "user":
                avatar = '<span class="avatar user-avatar">你</span>'
            else:
                avatar = _queen_avatar_html()
            extra_class = " interrupt-note" if str(text).startswith("⏹ 已保存的中断摘要") else ""
            parts.append(
                f'<div class="bubble {role}{extra_class}">{avatar}<div class="bubble-main">{body}</div></div>'
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
