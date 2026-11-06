"""인수인계 문서 생성. 템플릿(데이터가 없으면 '미입력')과, 그 템플릿·추천·위험 근거로 LLM이 작성하는 문서."""
from __future__ import annotations

from datetime import date

from . import llm
from .models import TeamData, isnull
from .recommend import recommend_backups
from .risk import assess_tasks, format_reasons
from .worklog import METRIC_COLS, METRIC_LABELS, TRACKS, leave_impact, recent_summary

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

    if member_id in team.unit_pool():
        ref = today or date.today()
        L += ["## 2-1. 최근 업무 로그 (단위 테스트, 최근 14일)"]
        rs = recent_summary(team, member_id, ref, 14)
        if not rs["tracks"]:
            L.append(f"- 연동된 업무 로그가 없습니다({MISSING}).")
        for track, info in rs["tracks"].items():
            tot = ", ".join(f"{METRIC_LABELS[m]} {info['sum'][m]:.0f}" for m in METRIC_COLS[track])
            L.append(f"- {TRACKS[track]}: {tot} (최근 기록일 {info['last_date']})")
            for ver, vals in sorted(info["by_version"].items()):
                L.append(f"  - {ver}: " + ", ".join(f"{METRIC_LABELS[m]} {vals[m]:.0f}" for m in METRIC_COLS[track]))
        L += ["", "## 2-2. 휴가 중 예상 처리 공백 (최근 4주 일평균 기준 참고값)"]
        imp = leave_impact(team, member_id, start, end, ref)
        shown = False
        for it in imp["items"]:
            if it["gap"] is None:
                L.append(f"- {it['label']}: 최근 로그가 없어 예측 불가({MISSING})")
                continue
            shown = True
            share = ", ".join(f"{r['팀원']} {r['평소 대비']}" for r in it["rows"]) or "분담 가능 인원 없음"
            L.append(f"- {it['label']}: 휴가 중 약 {it['gap']:.0f}건 공백 예상 → 분담 시 평소 대비 {share}")
        if imp.get("pool_overlap_days"):
            L.append("- ⚠️ 단위 테스트 인원 다수가 함께 부재인 날: " + ", ".join(str(d) for d in imp["pool_overlap_days"]))
        if not imp["items"] or not shown and not any(it["gap"] is None for it in imp["items"]):
            L.append(f"- {MISSING}")
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


def handover_facts(team: TeamData, member_id: int, start: date, end: date, rules: dict | None = None) -> str:
    """LLM 작성용 추가 근거: 대체 담당자 미지정 업무의 추천 후보와 휴가 기간 업무 위험."""
    active = [t for t in team.task_records() if t.get("owner_id") == member_id and t.get("status") != "완료"]
    L = ["## 대체 담당자 미지정 업무의 추천 후보 (VACON 추천 로직, 미확정)"]
    unassigned = [t for t in active if isnull(t.get("backup_id"))]
    for t in unassigned:
        try:
            res = recommend_backups(team, int(t["id"]), start, end, rules, top_n=3)
        except ValueError as exc:
            L.append(f"- {t['name']}: 추천 불가({exc})")
            continue
        if not res["candidates"]:
            L.append(f"- {t['name']}: 추천 가능한 후보 없음 " + " ".join(res["warnings"]))
        for c in res["candidates"]:
            L.append(f"- {t['name']} → {c['이름']} (점수 {c['추천 점수']}, {c['예상 업무 부담']}); "
                     f"이유: {', '.join(c['추천 이유']) or '-'}; 주의: {', '.join(c['주의사항']) or '-'}")
    if not unassigned:
        L.append("- 해당 없음 (모든 미완료 업무에 대체 담당자 지정됨)")
    L.append("")
    L.append("## 휴가 기간 업무 위험 (VACON 위험 분석)")
    names = {t["name"] for t in active}
    risks = assess_tasks(team, start, end, rules)
    rows = [r for r in risks.to_dict("records") if r.get("업무") in names] if not risks.empty else []
    for r in rows:
        reasons = r.get("근거")
        actions = r.get("대응 방안")
        L.append(f"- {r['업무']}: {r.get('위험도')} (점수 {r.get('점수')}); "
                 f"근거: {format_reasons(reasons) if isinstance(reasons, list) else reasons}; "
                 f"대응: {' / '.join(actions) if isinstance(actions, list) else actions}")
    if not rows:
        L.append("- 해당 없음")
    return "\n".join(L)


def generate_ai_handover(team: TeamData, member_id: int, start: date, end: date, leave_type: str | None = None,
                         today: date | None = None, rules: dict | None = None) -> tuple[str, bool]:
    """LLM이 템플릿과 추천·위험 근거로 인수인계 문서를 작성한다. (문서, LLM 사용 여부). 실패 시 템플릿 문서."""
    draft = generate_handover(team, member_id, start, end, leave_type, today)
    if not llm.llm_available():
        return draft, False
    written = llm.write_handover(draft, handover_facts(team, member_id, start, end, rules))
    return (written, True) if written else (draft, False)
