# -*- coding: utf-8 -*-
"""
skills_loader — 加载 Hermes / Cursor 兼容的 SKILL.md（YAML frontmatter + Markdown 正文）
"""
from __future__ import annotations

import os
import re
from pathlib import Path


_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    raw_fm, body = m.group(1), text[m.end() :].strip()
    meta: dict = {}
    for line in raw_fm.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def find_skill_file(skills_root: str, name: str) -> str | None:
    """按 skill 名查找 SKILL.md（支持一级目录与递归子目录，兼容 Hermes 布局）。"""
    root = Path(os.path.expandvars(os.path.expanduser(skills_root or "")))
    if not name or not root.is_dir():
        return None
    nested = root / name.replace("/", os.sep) / "SKILL.md"
    if nested.is_file():
        return str(nested)
    direct = root / name / "SKILL.md"
    if direct.is_file():
        return str(direct)
    target = name.lower()
    for path in root.rglob("SKILL.md"):
        folder = path.parent.name.lower()
        if folder == target or path.parent.as_posix().lower().endswith("/" + target):
            return str(path)
    return None


def load_skill(skills_root: str, name: str) -> dict | None:
    src = find_skill_file(skills_root, name)
    if not src:
        return None
    try:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    meta, body = _parse_frontmatter(text)
    skill_name = meta.get("name") or Path(src).parent.name
    desc = meta.get("description") or ""
    if desc.startswith("|"):
        desc = desc.lstrip("|").strip()
    return {
        "name": skill_name,
        "description": desc,
        "path": str(Path(src).parent),
        "source": src,
        "meta": meta,
        "body": body,
    }


def format_skill_block(skill: dict, max_chars: int = 12000) -> str:
    body = (skill.get("body") or "").strip()
    if len(body) > max_chars:
        body = body[: max_chars // 2] + "\n\n…（Skill 正文过长，已截断）…\n\n" + body[-max_chars // 2 :]
    name = skill.get("name", "skill")
    desc = skill.get("description", "")
    header = f"# 已加载 Skill：{name}"
    if desc:
        header += f"\n> {desc}"
    return f"{header}\n\n{body}".strip()


def scan_skills(skills_dir: str) -> list[dict]:
    """递归扫描目录下所有 SKILL.md。"""
    root = Path(os.path.expandvars(os.path.expanduser(skills_dir or "")))
    if not root.is_dir():
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for path in sorted(root.rglob("SKILL.md")):
        try:
            rel = path.parent.relative_to(root)
            name = path.parent.name if str(rel) == "." else str(rel).replace("\\", "/")
        except ValueError:
            name = path.parent.name
        if name in seen:
            continue
        seen.add(name)
        loaded = load_skill(str(root), name)
        if not loaded:
            continue
        loaded["name"] = name if "/" in name else loaded.get("name", name)
        out.append(
            {
                "name": loaded["name"],
                "path": loaded["path"],
                "type": "md",
                "source": loaded["source"],
                "description": (loaded.get("description") or "（无描述）")[:200],
            }
        )
    return out
