# -*- coding: utf-8 -*-
"""从 ui_assets/ 加载 CSS / JS，供 prototype_antnest 使用。"""
from __future__ import annotations

import os

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_assets")


def load_asset(name: str) -> str:
    path = os.path.join(_ASSETS_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_css() -> str:
    return load_asset("app.css")


def load_js() -> str:
    return load_asset("app.js")
