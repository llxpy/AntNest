# -*- coding: utf-8 -*-
"""从 ui_assets/ 加载 CSS / JS，供 prototype_antnest 使用。"""
from __future__ import annotations

import os
import base64 as _b64

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_assets")


def load_asset(name: str) -> str:
    path = os.path.join(_ASSETS_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_image_data_uri(name: str) -> str:
    """读取 ui_assets 下的图片并返回 data: URI（PNG 透明底可直接嵌入 HTML/JS，无需文件路由）。"""
    path = os.path.join(_ASSETS_DIR, name)
    with open(path, "rb") as f:
        b64 = _b64.b64encode(f.read()).decode("ascii")
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "ico": "image/x-icon"}.get(ext, "image/png")
    return f"data:{mime};base64,{b64}"


def load_css() -> str:
    return load_asset("app.css")


def load_js() -> str:
    return load_asset("app.js")
