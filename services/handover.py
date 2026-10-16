"""템플릿 기반 인수인계 문서 생성. 데이터가 없으면 '미입력'으로 표시한다."""
from __future__ import annotations

from datetime import date

from .models import TeamData, isnull

MISSING = "미입력"


def _v(x) -> str:
    return MISSING if isnull(x) or str(x).strip() == "" else str(x)


def generate_handover(team: TeamData, member_id: int, start: date, end: date,
                      leave_type: str | None = None, today: date | None = None) -> str:
    member = next((m for m in team.member_records() if m["id"] == member_id), None)
    if member is None:
        raise ValueError("존재하지 않는 팀원입니다.")
    tasks = [t for t in team.task_records() if t.get("owner_id") == member_id]
    active = [t for t in tasks if t.get("status") != "완료"]
    days = (end - start).days + 1
    L = [f"# 업무 인수인계 문서 — {member['name']}", ""]
    L += ["## 1. 휴가 정보",
          f"- 휴가자: {member['name']} ({_v(member.get('role'))})",
          f"- 휴가 기간: {start} ~ {end} ({days}일, 유형: {_v(leave_type)})",
          f"- 비상 연락처: {_v(member.get('contact'))}", ""]

    L += ["## 2. 담당 업무와 현재 진행 상태"]
    if not tasks:
        L.append(f"- 등록된 담당 업무가 없습니다({MISSING}).")
    for t in tasks:
        L.append(f"- **{t['name']}** — 상태: {_v(t.get('status'))}, 진행률: {_v(t.get('progress'))}%, "
                 f"중요도: {_v(t.get('importance'))}")
    L.append("")

    L += ["## 3. 미완료 작업"]
    if not active:
        L.append("- 미완료 업무가 없습니다.")
    for t in active:
        L.append(f"- {t['name']}: {_v(t.get('remaining'))}")
    L.append("")

    L += ["## 4. 주요 마감일"]
    dated = sorted((t for t in active if not isnull(t.get("due_date"))), key=lambda t: t["due_date"])
    for t in dated:
        flag = " ⚠️ 휴가 기간 중/직후 마감" if start <= t["due_date"] <= end else ""
        L.append(f"- {t['due_date']} — {t['name']}{flag}")
    for t in (t for t in active if isnull(t.get("due_date"))):
        L.append(f"- {MISSING} — {t['name']}")
    if not active:
        L.append("- 해당 없음")
    L.append("")

    L += ["## 5. 대체 담당자"]
    for t in active:
        b = t.get("backup_id")
        L.append(f"- {t['name']}: {team.name(b) if not isnull(b) else '미지정 — 지정 필요'}")
    if not active:
        L.append("- 해당 없음")
    L.append("")

    L += ["## 6. 업무별 주의사항"]
    for t in active:
        L.append(f"- {t['name']}: {_v(t.get('notes'))}")
    if not active:
        L.append("- 해당 없음")
    L.append("")

    L += ["## 7. 긴급 상황 발생 시 확인할 사항",
          f"- 휴가자 비상 연락처: {_v(member.get('contact'))}"]
    for t in active:
        b = t.get("backup_id")
        L.append(f"- {t['name']}: 1차 연락 대상 {team.name(b) if not isnull(b) else MISSING}, "
                 f"참고 메모 {_v(t.get('notes'))}")
    L.append("")

    L += ["## 8. 휴가 전 완료 체크리스트"]
    for t in active:
        L.append(f"- [ ] {t['name']}: 진행 상황·산출물 위치 공유 (남은 작업: {_v(t.get('remaining'))})")
        if isnull(t.get("backup_id")):
            L.append(f"- [ ] {t['name']}: **대체 담당자 지정**")
        if not isnull(t.get("due_date")) and start <= t["due_date"] <= end:
            L.append(f"- [ ] {t['name']}: 마감({t['due_date']})이 휴가 기간 중 — 선완료 또는 마감 조정")
    L += ["- [ ] 팀 캘린더 및 메신저 부재 알림 설정", "- [ ] 대체 담당자와 인수인계 미팅 완료", ""]
    L.append(f"_생성일: {today or date.today()} · VACON 템플릿 기반 자동 생성. 내용은 휴가자와 대체 담당자가 검토 후 확정하세요._")
    return "\n".join(L)
