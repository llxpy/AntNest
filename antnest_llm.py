# -*- coding: utf-8 -*-
"""AntNest · LLM 调用层：请求构造 / 流式解析 / 用量展示 / 重复检测。"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from api_compat import effective_temperature, resolve_api_profile, sanitize_messages_for_api


def _A():
    import AntNest as _m
    return _m


def clean_input(text):
    if not isinstance(text, str):
        return str(text)
    text = re.sub(r"[\ud800-\udfff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


def _build_request_data(messages, tools=None, temperature=0.6, thinking=True, stream=False):
    profile = resolve_api_profile(_A().ANT_BASE_URL)
    use_thinking = _A()._thinking_requested(thinking, profile)
    temp = effective_temperature(temperature, profile, _A().ANT_MODEL_NAME)
    data = {
        "model": _A().ANT_MODEL_NAME,
        "messages": sanitize_messages_for_api(messages, profile),
        "temperature": temp,
    }

    if profile == "deepseek":
        data["top_p"] = 0.95
        data["thinking"] = {"type": "adaptive" if use_thinking else "disabled"}
    elif profile == "openai":
        data["top_p"] = 1.0
    else:
        # MiniMax / 硅基 / 智谱等：仅标准 OpenAI 字段，避免 400
        data["top_p"] = 0.95

    if tools:
        data["tools"] = tools
    if stream:
        data["stream"] = True
        if profile in ("deepseek", "openai", "openai_compat"):
            data["stream_options"] = {"include_usage": True}
    return data


def display_usage(usage, cap):
    t = usage.get("total_tokens", 0) if usage else 0
    if t == 0 or cap <= 0:
        return
    p = min(t / cap, 1.0)
    bar = "\u2588" * int(p * 20) + "\u2591" * (20 - int(p * 20))
    print(f"\033[2mCTX [{bar}] {p:.0%}  ({t//1000}k/{cap//1000}k)\033[0m")


class ThinkRepeatError(Exception):
    pass


class RepeatSuffixChecker:
    """Rabin-Karp 滚动哈希检测 thinking 重复循环"""

    def __init__(self, min_unit_len: int, base: int = 91138233, mod: int = 10**9 + 7):
        self.min_unit_len = min_unit_len
        self.base = base
        self.mod = mod
        self.prefix_hash = [0]
        self.pow_base = [1]

    def _get_hash(self, l: int, r: int) -> int:
        return (self.prefix_hash[r] - self.prefix_hash[l] * self.pow_base[r - l]) % self.mod

    def add_char(self, ch: str) -> bool:
        self.pow_base.append((self.pow_base[-1] * self.base) % self.mod)
        new_hash = (self.prefix_hash[-1] * self.base + ord(ch)) % self.mod
        self.prefix_hash.append(new_hash)

        n = len(self.prefix_hash) - 1
        if n < 2 * self.min_unit_len or n % self.min_unit_len != 0:
            return False

        for unit_len in range(n // 2, self.min_unit_len - 1, -1):
            if self._get_hash(n - 2 * unit_len, n - unit_len) == self._get_hash(
                n - unit_len, n
            ):
                return True
        return False


def llm_chat_stream(messages, tools=None, temperature=0.6, thinking=True):
    url = f"{_A().ANT_BASE_URL}/chat/completions"
    data = _build_request_data(messages, tools, temperature, thinking, stream=True)

    def _open(body_bytes: bytes):
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={**_A().COMMON_HEADER, "Content-Type": "application/json"},
        )
        return _A()._urlopen_with_retry(req)

    body = json.dumps(data).encode("utf-8")
    try:
        resp = _open(body)
        global _ACTIVE_STREAM_RESP
        _A()._ACTIVE_STREAM_RESP = resp
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        if e.code == 400:
            minimal = {
                "model": _A().ANT_MODEL_NAME,
                "messages": sanitize_messages_for_api(
                    messages, resolve_api_profile(_A().ANT_BASE_URL)
                ),
                "temperature": data["temperature"],
                "stream": True,
            }
            if tools:
                minimal["tools"] = tools
            try:
                resp = _open(json.dumps(minimal).encode("utf-8"))
                _A()._ACTIVE_STREAM_RESP = resp
            except urllib.error.HTTPError as e2:
                raw2 = e2.read().decode("utf-8", errors="replace")
                raise Exception(f"LLM调用失败，HTTP {e2.code}: {raw2[:500]}")
        elif e.code == 429:
            raise Exception(
                f"LLM调用失败，HTTP 429：API 服务端过载，已自动重试仍失败。"
                f"请稍后再试或换时段/换模型。详情：{raw[:300]}"
            )
        else:
            raise Exception(f"LLM调用失败，HTTP {e.code}: {raw[:500]}")

    content_parts = []
    reasoning_parts = []
    tool_calls_map = {}
    usage = None
    role = "assistant"
    is_thinking = False

    detector = RepeatSuffixChecker(min_unit_len=400)

    try:
        for raw_line in resp:
            if _A().AGENT_CANCEL:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break

            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if "usage" in chunk and chunk["usage"]:
                usage = chunk["usage"]

            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            if not delta:
                continue

            role = delta.get("role") or role

            reasoning_content = (
                delta.get("reasoning_content") or delta.get("reasoning") or ""
            )
            if reasoning_content:
                if not is_thinking:
                    is_thinking = True
                    if not _A()._stream_emit("reasoning_start"):
                        sys.stdout.write("\033[2m[think] ")
                if not _A()._stream_emit("reasoning", reasoning_content):
                    sys.stdout.write(reasoning_content)
                    sys.stdout.flush()
                reasoning_parts.append(reasoning_content)
                for c in reasoning_content:
                    if detector.add_char(c):
                        raise ThinkRepeatError()

            text = delta.get("content") or ""
            if text:
                if is_thinking:
                    is_thinking = False
                    if not _A()._stream_emit("reasoning_end"):
                        sys.stdout.write("\033[0m\n")
                if not _A()._stream_emit("content", text):
                    sys.stdout.write(text)
                    sys.stdout.flush()
                content_parts.append(text)

            if "tool_calls" in delta and delta["tool_calls"]:
                for tc_delta in delta["tool_calls"]:
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc_delta.get("id", ""),
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    tc_entry = tool_calls_map[idx]
                    if tc_delta.get("id"):
                        tc_entry["id"] = tc_delta["id"]
                    func_delta = tc_delta.get("function", {})
                    if func_delta.get("name"):
                        tc_entry["function"]["name"] += func_delta["name"]
                    if func_delta.get("arguments"):
                        tc_entry["function"]["arguments"] += func_delta["arguments"]

        if is_thinking:
            sys.stdout.write("\033[0m\n")
    finally:
        resp.close()
        _A()._ACTIVE_STREAM_RESP = None
        if is_thinking:
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()

    full_content = "".join(content_parts)
    message = {"role": role, "content": full_content if full_content else None}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls_map:
        message["tool_calls"] = [
            tool_calls_map[i] for i in sorted(tool_calls_map.keys())
        ]
        if not full_content:
            message["content"] = " "

    if usage is None:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    # token 用量实时上报（UI 顶栏显示）
    _A()._stream_emit("usage", json.dumps(usage, ensure_ascii=False))
    return message, usage
