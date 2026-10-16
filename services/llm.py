"""선택적 LLM 연동. ANTHROPIC_API_KEY와 anthropic 패키지가 있을 때만 동작하며,
실패하면 항상 None을 반환해 규칙 기반 답변으로 대체된다. 키는 환경변수로만 읽는다."""
from __future__ import annotations

import os

MODEL = os.environ.get("VACON_LLM_MODEL", "claude-sonnet-4-6")


def llm_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def rephrase(question: str, rule_answer: str, facts: str) -> str | None:
    """규칙 기반 결과(사실)를 자연스러운 한국어로 다듬는다. 새 사실 추가 금지."""
    if not llm_available():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=500,
            system="너는 팀 휴가 관리 비서다. 주어진 '근거 데이터'에 있는 내용만 사용해 2~4문장으로 답하라. "
                   "근거에 없는 사실은 추가하거나 추측하지 마라.",
            messages=[{"role": "user", "content": f"질문: {question}\n근거 데이터: {rule_answer}\n{facts}"}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip() or None
    except Exception:
        return None


def summarize_digest(digest_md: str) -> str | None:
    """(선택·사용자 동의 시) 메일 요약 문서를 LLM으로 '복귀 브리핑'으로 다듬는다.
    주의: 메일 제목/요약이 외부 API로 전송되므로 UI에서 사용자가 명시적으로 켠 경우에만 호출한다."""
    if not llm_available():
        return None
    try:
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=700,
            system="너는 복귀하는 직원을 돕는 비서다. 주어진 메일 요약에 있는 내용만 사용해 "
                   "'가장 먼저 처리할 일 3가지'와 전체 흐름을 한국어로 간결히 정리하라. 없는 사실은 추가하지 마라.",
            messages=[{"role": "user", "content": digest_md}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip() or None
    except Exception:
        return None
