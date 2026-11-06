"""사내 LLM 추론 서버(OpenAI 호환 /v1/chat/completions) 호출. 설정은 환경변수로만 읽는다.

ALIRA_API_BASE  추론 서버 주소(예: http://10.10.10.200:19640/v1)
ALIRA_MODEL     모델 ID(예: Qwen/Qwen3.6-27B). 없으면 VACON_LLM_MODEL
ALIRA_API_KEY   (선택) Bearer 토큰
VACON_LLM_THINKING  1이면 Qwen thinking 모드 사용(기본 끔: 응답이 느리고 토큰 한도에서 잘릴 수 있음)
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

DEFAULT_TIMEOUT = 20


def api_base() -> str:
    return os.environ.get("ALIRA_API_BASE", "").strip().rstrip("/")


def model() -> str:
    return (os.environ.get("ALIRA_MODEL") or os.environ.get("VACON_LLM_MODEL") or "").strip()


def configured() -> bool:
    """서버 주소와 모델이 설정되었는지(네트워크 호출 없음)."""
    return bool(api_base() and model())


def thinking_enabled() -> bool:
    return os.environ.get("VACON_LLM_THINKING", "").strip().lower() in {"1", "true", "yes", "on"}


def chat(messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 1000,
         timeout: float = DEFAULT_TIMEOUT) -> dict:
    """chat/completions 1회 호출 후 choices[0].message(dict)를 반환한다. 실패 시 예외."""
    if not configured():
        raise RuntimeError("ALIRA_API_BASE와 ALIRA_MODEL이 설정되지 않았습니다.")
    body = {"model": model(), "messages": messages, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": thinking_enabled()}}
    if tools:
        body.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("ALIRA_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{api_base()}/chat/completions",
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise ValueError("LLM 응답에 message가 없습니다.")
    return choices[0]["message"]


def strip_reasoning(text: str | None) -> str:
    """content에 섞여 온 <think>…</think> 블록을 제거한다."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return re.sub(r"^.*?</think>", "", text, flags=re.S).strip()


def complete(system: str, user: str, *, max_tokens: int = 700, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """도구 없이 한 번 묻고 텍스트만 받는다. 실패하면 None."""
    try:
        msg = chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                   max_tokens=max_tokens, timeout=timeout)
        return strip_reasoning(msg.get("content")) or None
    except Exception:
        return None
