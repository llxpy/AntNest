# -*- coding: utf-8 -*-
"""OpenAI 兼容 API 的提供商识别与模型参数推荐。"""


def resolve_api_profile(base_url: str) -> str:
    u = (base_url or "").lower()
    if "deepseek" in u:
        return "deepseek"
    if "minimax" in u or "minimaxi.com" in u:
        return "minimax"
    if "moonshot" in u or "kimi" in u:
        return "kimi"
    if "openai.com" in u:
        return "openai"
    return "openai_compat"


def effective_temperature(temperature: float, profile: str, model: str) -> float:
    m = (model or "").lower()
    if profile == "kimi" or m.startswith("kimi"):
        return 1.0
    return temperature


def recommend_model_settings(base_url: str, model_name: str) -> dict:
    """根据 Base URL 与模型名推荐 UI/配置参数。返回 thinking_mode、skip_model_check、hint。"""
    profile = resolve_api_profile(base_url)
    m = (model_name or "").lower()
    rec = {
        "thinking_mode": "auto",
        "skip_model_check": "false",
        "hint": "",
    }

    if profile == "kimi" or m.startswith("kimi"):
        rec["thinking_mode"] = "off"
        rec["hint"] = "Kimi：temperature 固定为 1，已关闭 thinking"
    elif profile == "minimax" or "minimax" in m or m.startswith("abab"):
        rec["thinking_mode"] = "off"
        rec["skip_model_check"] = "true"
        rec["hint"] = "MiniMax：已关闭 thinking，建议跳过 /models 检测"
    elif profile == "deepseek" or "deepseek" in m:
        rec["thinking_mode"] = "auto"
        rec["hint"] = "DeepSeek：thinking 按 auto 适配"
    elif m.startswith("o1") or m.startswith("o3") or "reasoner" in m:
        rec["thinking_mode"] = "off"
        rec["hint"] = "推理模型：已关闭 thinking 扩展字段"
    elif profile == "openai_compat" and not base_url:
        rec["hint"] = ""

    return rec


def apply_recommendations(settings: dict, base_url: str, model_name: str) -> dict:
    """将推荐参数写入 settings 副本并返回 (settings, rec)。"""
    out = dict(settings or {})
    rec = recommend_model_settings(base_url, model_name)
    if rec.get("thinking_mode"):
        out["thinking_mode"] = rec["thinking_mode"]
    if rec.get("skip_model_check"):
        out["skip_model_check"] = rec["skip_model_check"]
    return out, rec


_ASSISTANT_PLACEHOLDER = " "
_STRICT_MSG_KEYS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id"})


def _content_is_empty(content) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        return not content
    return False


def sanitize_messages_for_api(messages, profile: str) -> list:
    """发送前规范化 messages，避免 Kimi 等拒绝空 assistant content。"""
    keep_reasoning = profile == "deepseek"
    strict = profile in ("kimi", "minimax", "openai_compat")
    out = []
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        m = dict(raw)
        role = m.get("role")
        if role == "assistant":
            if _content_is_empty(m.get("content")):
                m["content"] = _ASSISTANT_PLACEHOLDER
            if not keep_reasoning:
                m.pop("reasoning_content", None)
        elif role == "tool" and _content_is_empty(m.get("content")):
            m["content"] = "(无返回)"
        elif not keep_reasoning:
            m.pop("reasoning_content", None)
        if strict:
            m = {k: v for k, v in m.items() if k in _STRICT_MSG_KEYS and v is not None}
        out.append(m)
    return out
