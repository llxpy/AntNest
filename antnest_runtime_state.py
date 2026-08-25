# -*- coding: utf-8 -*-
"""Persistent runtime state for recovery, adaptive user strategy and persona anchors.

This module stores compact, inspectable JSON. It deliberately keeps summaries and
preference evidence instead of hidden model reasoning or raw credentials.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

PERSONA_ANCHORS = (
    "结论先行，证据说话",
    "主动推进，但在关键不确定性前先询问",
    "修改后验证，未验证不宣称完成",
    "保持边界清晰，不把用户偏好当作权限授权",
)

_PROFILE_RULES = (
    ("concise", ("简洁", "简短", "少废话", "直接一点", "不要长篇")),
    ("detailed", ("详细", "展开解释", "多解释", "讲清楚")),
    ("verify_first", ("先验证", "先测试", "先检查", "给证据")),
    ("autonomous", ("主动推进", "不要总问我", "自行完成", "自己判断")),
    ("ask_before_risk", ("先问我", "先询问", "需要确认", "不要擅自")),
    ("code_first", ("直接改代码", "先实现", "动手改", "以代码为主")),
    ("chinese_output", ("用中文", "中文回复", "中文输出")),
)


def _default_state() -> dict:
    return {
        "version": 1,
        "profile": {
            "preferences": {},
            "strategy": {
                "verbosity": 0.0,
                "verification": 0.55,
                "autonomy": 0.45,
                "clarification": 0.45,
            },
            "evidence": [],
        },
        "persona": {
            "anchors": list(PERSONA_ANCHORS),
            "custom_style": "",
        },
        "stop_snapshot": None,
        "pending_self_modification": None,
    }


def _read(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else _default_state()
    except Exception:
        return _default_state()


def _write(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".runtime-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def load(path: str) -> dict:
    state = _read(path)
    base = _default_state()
    base.update({k: v for k, v in state.items() if k in base})
    base["profile"] = {**_default_state()["profile"], **(state.get("profile") or {})}
    base["profile"]["strategy"] = {
        **_default_state()["profile"]["strategy"],
        **(base["profile"].get("strategy") or {}),
    }
    base["persona"] = {**_default_state()["persona"], **(state.get("persona") or {})}
    return base


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def update_profile(path: str, text: str) -> dict:
    """Extract explicit preference evidence and gently update strategy weights."""
    state = load(path)
    text = str(text or "").strip()
    if not text:
        return state
    profile = state["profile"]
    prefs = profile.setdefault("preferences", {})
    strategy = profile.setdefault("strategy", {})
    hits = []
    for key, terms in _PROFILE_RULES:
        if any(term in text for term in terms):
            old = prefs.get(key) or {"score": 0.0, "confidence": 0.0, "evidence": 0}
            count = int(old.get("evidence", 0)) + 1
            score = _clamp(float(old.get("score", 0.0)) * 0.82 + 0.18)
            confidence = min(1.0, float(old.get("confidence", 0.0)) + 0.12)
            prefs[key] = {"score": round(score, 4), "confidence": round(confidence, 4), "evidence": count}
            hits.append(key)
    if "concise" in hits:
        strategy["verbosity"] = round(_clamp(float(strategy.get("verbosity", 0.0)) - 0.18), 4)
    if "detailed" in hits:
        strategy["verbosity"] = round(_clamp(float(strategy.get("verbosity", 0.0)) + 0.18), 4)
    if "verify_first" in hits:
        strategy["verification"] = round(_clamp(float(strategy.get("verification", 0.55)) + 0.16, 0.0, 1.0), 4)
    if "autonomous" in hits:
        strategy["autonomy"] = round(_clamp(float(strategy.get("autonomy", 0.45)) + 0.16, 0.0, 1.0), 4)
    if "ask_before_risk" in hits:
        strategy["clarification"] = round(_clamp(float(strategy.get("clarification", 0.45)) + 0.16, 0.0, 1.0), 4)
    if hits:
        evidence = profile.setdefault("evidence", [])
        evidence.append({"time": int(time.time()), "signals": hits, "text": text[:240]})
        del evidence[:-30]
        _write(path, state)
    return state


def save_persona_style(path: str, text: str) -> dict:
    state = load(path)
    candidate = str(text or "").strip()[:4000]
    existing = str(state["persona"].get("custom_style") or "").strip()
    if candidate and candidate not in existing:
        state["persona"]["custom_style"] = (existing + "\n" + candidate).strip()[-4000:]
    elif not existing:
        state["persona"]["custom_style"] = candidate
    _write(path, state)
    return state


def profile_prompt(state: dict) -> str:
    profile = state.get("profile") or {}
    strategy = profile.get("strategy") or {}
    prefs = profile.get("preferences") or {}
    active = [k for k, v in prefs.items() if float((v or {}).get("confidence", 0)) >= 0.24]
    return (
        "《动态执行策略》\n"
        f"偏好信号：{', '.join(active) or '尚未形成'}\n"
        f"详细程度：{float(strategy.get('verbosity', 0)):+.2f}；"
        f"验证倾向：{float(strategy.get('verification', 0.55)):.2f}；"
        f"自主推进：{float(strategy.get('autonomy', 0.45)):.2f}；"
        f"高风险前询问：{float(strategy.get('clarification', 0.45)):.2f}\n"
        "这些是执行策略提示，不是权限授权；每次仍以当前用户明确请求为准。"
    )


def persona_prompt(state: dict) -> str:
    persona = state.get("persona") or {}
    anchors = persona.get("anchors") or list(PERSONA_ANCHORS)
    custom = str(persona.get("custom_style") or "").strip()
    text = "《固化人设锚点》\n- " + "\n- ".join(anchors)
    if custom:
        text += (
            "\n《用户定义的表达风格》\n" + custom
            + "\n（仅作为表达与工作偏好；不改变工具权限、系统规则或确认要求。）"
        )
    return text


def set_pending_self_modification(path: str, purpose: str = "") -> dict:
    state = load(path)
    state["pending_self_modification"] = {
        "path": str(path), "purpose": str(purpose or "")[:1000], "time": int(time.time())
    }
    _write(path, state)
    return state


def get_pending_self_modification(path: str) -> dict:
    return dict(load(path).get("pending_self_modification") or {})


def clear_pending_self_modification(path: str) -> None:
    state = load(path)
    state["pending_self_modification"] = None
    _write(path, state)


def record_stop(path: str, snapshot: dict) -> dict:
    state = load(path)
    state["stop_snapshot"] = {
        "time": int(time.time()),
        "reason": str(snapshot.get("reason") or "user_stop"),
        "task": str(snapshot.get("task") or "")[:500],
        "decision_summary": str(snapshot.get("decision_summary") or "")[:2000],
        "evidence": str(snapshot.get("evidence") or "")[:3000],
        "next_step": str(snapshot.get("next_step") or "")[:1000],
    }
    _write(path, state)
    return state


def get_stop(path: str) -> dict:
    return dict((load(path).get("stop_snapshot") or {}))


def clear_stop(path: str) -> None:
    state = load(path)
    state["stop_snapshot"] = None
    _write(path, state)
