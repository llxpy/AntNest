# -*- coding: utf-8 -*-
"""模型能力探测（共享、惰性、非致命）。

统一从 /models 拉取当前模型信息：上下文长度、是否支持图片（vision）、模型类型
（chat / reasoning / vision / embedding）。桥接层（UI 展示、图片按钮开关）与
Agent 运行时（系统提示词）共用同一份探测与关键词兜底，避免两套实现判断不一致。

关键约定：
- 永不抛异常、永不退出进程：任何网络 / 解析失败都回退默认值并标记 detected=False。
- 探测结果按 (base, model) 缓存，TTL 600 秒。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from api_compat import resolve_api_profile

_CACHE: dict = {}  # (base, model) -> {"ts": float, "cap": dict}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 600.0

# OpenAI 兼容 /models 通常不返回 capabilities，靠关键词兜底。
_VISION_KEYWORDS = (
    "vision", "gpt-4o", "gpt-4-turbo", "gpt-4.5",
    "claude-3", "qwen-vl", "gemini-pro-vision", "gemini-1.5", "gemini-2",
    "llava", "internvl", "deepseek-vl", "glm-4v", "yi-vl",
    "minimax", "abab", "vl-", "-vl",
)

_REASONING_KEYWORDS = (
    "reasoner", "thinking", "-think", "o1", "o3", "r1",
    "deepseek-reasoner", "deepseek-r1",
)

_EMBEDDING_KEYWORDS = ("embedding", "text-embedding", "embed")

# 已知模型的上下文兜底（/models 不可用时使用）。
_DEFAULT_CAPS = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
}


def supports_vision(model_name: str, models_data: list | None = None) -> bool:
    """模型是否支持图片输入。优先信 /models 的 capabilities 字段，否则关键词兜底。"""
    m = (model_name or "").lower()
    if models_data:
        for d in models_data:
            if not isinstance(d, dict):
                continue
            if d.get("id") != model_name:
                continue
            caps = d.get("capabilities") or {}
            if isinstance(caps, dict):
                if caps.get("vision") or caps.get("image") or caps.get("multimodal"):
                    return True
            break
    return any(k.lower() in m for k in _VISION_KEYWORDS)


def infer_model_kind(model_name: str, models_data: list | None = None) -> str:
    """模型类型：vision / reasoning / embedding / chat（未知归 chat）。

    优先级：接口 capabilities > 视觉关键词 > 推理关键词 > 嵌入关键词。
    """
    m = (model_name or "").lower()
    if models_data:
        for d in models_data:
            if not isinstance(d, dict):
                continue
            if d.get("id") != model_name:
                continue
            caps = d.get("capabilities") or {}
            if isinstance(caps, dict):
                if caps.get("vision") or caps.get("image"):
                    return "vision"
                if caps.get("reasoning"):
                    return "reasoning"
            break
    if supports_vision(model_name, models_data):
        return "vision"
    if any(k in m for k in _REASONING_KEYWORDS):
        return "reasoning"
    if any(k in m for k in _EMBEDDING_KEYWORDS):
        return "embedding"
    return "chat"


def infer_context_length(
    model_name: str, models_data: list | None = None, default: int = 128000
) -> int:
    """上下文长度：/models 的 max_model_len / context_length / max_tokens 优先，其次内置表，最后默认。"""
    m = (model_name or "").lower()
    if models_data:
        for d in models_data:
            if not isinstance(d, dict):
                continue
            if d.get("id") != model_name:
                continue
            raw = d.get("max_model_len") or d.get("context_length") or d.get("max_tokens")
            if raw:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    pass
            break
    return _DEFAULT_CAPS.get(m, default)


def fetch_models(base_url: str, api_key: str, timeout: float = 12) -> dict:
    """拉取 /models。返回 {ok, msg, data（带 id 的字典列表）, raw（原始 data）}，绝不抛异常。"""
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base or "://" not in base:
        return {"ok": False, "msg": "Base URL 为空或格式无效", "data": [], "raw": []}
    if not key:
        return {"ok": False, "msg": "API Key 为空", "data": [], "raw": []}

    url = f"{base}/models"
    req = urllib.request.Request(
        url, headers={"User-Agent": "AntNest", "Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
    except Exception as e:
        return {"ok": False, "msg": f"无法连接到 {base}：{e}", "data": [], "raw": []}

    if status == 401:
        return {"ok": False, "msg": "API Key 无效或未授权（HTTP 401）", "data": [], "raw": []}
    if status != 200:
        return {
            "ok": False,
            "msg": f"获取模型列表失败（HTTP {status}）：{body[:120]}",
            "data": [],
            "raw": [],
        }
    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "msg": f"返回了非法 JSON：{body[:120]}", "data": [], "raw": []}

    raw = [d for d in out.get("data", []) if isinstance(d, dict)]
    data = [d for d in raw if d.get("id")]
    return {"ok": True, "msg": f"共检测到 {len(data)} 个模型 @ {base}", "data": data, "raw": raw}


def default_capability(model_name: str, base_url: str = "", default_cap: int = 128000) -> dict:
    """未联网时的兜底能力（detected=False），按名称关键词推定。"""
    return {
        "model": (model_name or "").strip(),
        "base": (base_url or "").strip().rstrip("/"),
        "provider": resolve_api_profile(base_url),
        "context_length": infer_context_length(model_name, None, default_cap),
        "vision": supports_vision(model_name),
        "model_kind": infer_model_kind(model_name),
        "detected": False,
        "msg": "未探测，按名称推定",
        "summary": summarize_capability({"model": (model_name or "").strip()}),
    }


def detect_capability(
    base_url: str, model_name: str, api_key: str,
    default_cap: int = 128000, timeout: float = 12,
) -> dict:
    """探测当前模型能力（带缓存）。任何失败都回退兜底，绝不抛异常。"""
    base = (base_url or "").strip().rstrip("/")
    model = (model_name or "").strip()
    cache_key = (base, model)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(cache_key)
        if hit and now - hit["ts"] < _CACHE_TTL:
            return dict(hit["cap"])

    result = fetch_models(base, api_key, timeout)
    data = result.get("data") or []
    found = bool(data) and any(d.get("id") == model for d in data)
    cap = {
        "model": model,
        "base": base,
        "provider": resolve_api_profile(base),
        "context_length": infer_context_length(model, data, default_cap),
        "vision": supports_vision(model, data),
        "model_kind": infer_model_kind(model, data),
        "detected": found,
        "msg": result.get("msg", ""),
        "summary": summarize_capability({"model": model}),
    }
    with _CACHE_LOCK:
        _CACHE[cache_key] = {"ts": now, "cap": dict(cap)}
    return cap


def describe_capability(cap: dict) -> str:
    """把能力字典变成一行摘要，注入系统提示词。"""
    cap = cap or {}
    vision = "支持图片输入" if cap.get("vision") else "不支持图片输入"
    kind = cap.get("model_kind") or "chat"
    ctx = int(cap.get("context_length") or 128000)
    provider = cap.get("provider") or "openai_compat"
    src = "已从 /models 探测" if cap.get("detected") else "按名称/默认值推定"
    return (
        f"当前模型：{cap.get('model') or '未知'}（提供商 {provider}，类型 {kind}，"
        f"上下文约 {ctx:,} tokens，{vision}；{src}）"
    )


# —— 能力画像（按模型名推定的分项摘要，供系统提示词注入） ——
_KNOWN_SUMMARIES = {
    "deepseek-v4-flash": (
        "- 数学：中上（常规推导可完成，复杂证明能力有限）\n"
        "- 代码：强（工程级代码生成与重构、调试）\n"
        "- 写作：良好（结构清晰、风格稳定）\n"
        "- 其他：长上下文 / 高吞吐优先"
    ),
    "deepseek-v4-pro": (
        "- 数学：强（符号推理、证明、复杂计算）\n"
        "- 代码：强（架构、并发、调试、重构）\n"
        "- 写作：强（结构严谨、风格稳定）\n"
        "- 其他：深度思考优先，支持长任务分解"
    ),
}
_DEFAULT_SUMMARY = (
    "- 数学：中等\n"
    "- 代码：良好（通用编码与脚本）\n"
    "- 写作：良好\n"
    "- 其他：默认画像（未按名称校准）"
)


def summarize_capability(cap) -> str:
    """按模型名返回分项能力摘要（数学/代码/写作/其他），未知模型用默认画像。"""
    model = ((cap or {}).get("model") or "").strip().lower()
    return _KNOWN_SUMMARIES.get(model, _DEFAULT_SUMMARY)


def capability_cached(base_url: str, model_name: str) -> bool:
    """当前 (base, model) 是否已有未过期的探测缓存（供调用方避免重复探测）。"""
    base = (base_url or "").strip().rstrip("/")
    model = (model_name or "").strip()
    if not base or not model:
        return False
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get((base, model))
        return bool(hit and now - hit["ts"] < _CACHE_TTL)