"""인수인계 문서 생성. 템플릿(데이터가 없으면 '미입력')과, 그 템플릿·추천·위험 근거로 LLM이 작성하는 문서."""
from __future__ import annotations

from datetime import date

from data import db
from . import llm
from .internal_projects import is_internal_version
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
    if member_id in team.unit_pool():
        return _generate_unit_handover(team, member, start, end, leave_type, today)
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


def _current_unit_project(ref: date) -> tuple[dict | None, dict | None]:
    """Pick the newest version whose stored plans/logs indicate that work is still active."""
    from .coverage import workflow_stage

    candidates = []
    for project in db.list_version_projects():
        if not is_internal_version(project.get("version", "")):
            continue
        stage = workflow_stage(project)
        if stage["stage"] == "done":
            continue
        status_active = project.get("status") in ("사양서 작성 중", "검증 진행 중", "지연")
        phase = stage.get("phase")
        phase_start = project.get("spec_start" if phase == "spec" else "verify_start") if phase else None
        phase_due = project.get("spec_due" if phase == "spec" else "verify_due") if phase else None
        in_window = bool(phase_start and phase_start <= ref and (not phase_due or ref <= phase_due))
        has_work = stage["stage"] in ("spec_work", "verify_work", "verify_plan")
        if status_active or in_window or has_work:
            candidates.append((project, stage))
    # list_version_projects already orders versions newest first.
    return candidates[0] if candidates else (None, None)


def _current_version_progress(project: dict, member_id: int, phase: str, ref: date) -> list[dict]:
    workloads = db.list_version_workloads(project["id"], phase)
    logs = [row for row in db.list_version_worklogs(project["id"], phase)
            if row.get("date") and row["date"] <= ref]
    personal = [row for row in logs if int(row.get("member_id") or -1) == int(member_id)]
    by_subsystem = {}
    for row in workloads:
        name = str(row.get("subsystem") or "").strip()
        if not name:
            continue
        if phase == "spec":
            by_subsystem[name] = {"total_tasks": int(row.get("total_tasks") or 0), "team_tasks": 0, "own_tasks": 0,
                                  "team_rows": 0, "own_rows": 0, "own_last": None}
        else:
            by_subsystem[name] = {"total_requirements": int(row.get("total_requirements") or 0),
                                  "total_functions": int(row.get("total_functions") or 0),
                                  "team_requirements": 0, "team_functions": 0,
                                  "own_requirements": 0, "own_functions": 0,
                                  "team_rows": 0, "own_rows": 0, "own_last": None}
    for row in logs:
        name = str(row.get("subsystem") or "").strip()
        item = by_subsystem.get(name)
        if not item:
            continue
        own = int(row.get("member_id") or -1) == int(member_id)
        item["team_rows"] += 1
        if own:
            item["own_rows"] += 1
            if item["own_last"] is None or row["date"] > item["own_last"]:
                item["own_last"] = row["date"]
        if phase == "spec":
            value = int(row.get("completed_tasks") or 0)
            item["team_tasks"] += value
            if own:
                item["own_tasks"] += value
        else:
            reqs = int(row.get("completed_requirements") or 0)
            funcs = int(row.get("completed_functions") or 0)
            item["team_requirements"] += reqs
            item["team_functions"] += funcs
            if own:
                item["own_requirements"] += reqs
                item["own_functions"] += funcs
    return [{"subsystem": name, **values} for name, values in sorted(by_subsystem.items())]


def _approved_leave_redistribution(project_id: int, absent_member_id: int,
                                  start: date, end: date) -> list[dict]:
    rows = [row for row in db.list_version_backup_assignments(project_id)
            if int(row.get("absent_member_id") or -1) == int(absent_member_id)
            and row.get("leave_start") and row.get("leave_end")
            and row["leave_start"] <= end and start <= row["leave_end"]]
    exact = [row for row in rows if row["leave_start"] == start and row["leave_end"] == end]
    return exact or rows


def _generate_unit_handover(team: TeamData, member: dict, start: date, end: date,
                            leave_type: str | None, today: date | None) -> str:
    ref = today or date.today()
    member_id = int(member["id"])
    project, stage = _current_unit_project(ref)
    L = [f"# 단위 검증 업무 인수인계 — {member['name']}", "",
         "## 휴가 정보", f"- 휴가자: {member['name']}", f"- 기간: {start} ~ {end} ({_v(leave_type)})", ""]
    if not project:
        L += ["## 현재 진행 버전", "- 진행 중인 T0800 이후 버전 계획과 실적을 확인할 수 없습니다.",
              "",
              "## 인계 내용", "- 현재 버전이 확인되지 않아 개인 진행률·잔여 업무·휴가 중 일정을 작성하지 않았습니다.", "",
              f"_생성 기준일: {ref} · 현재 진행 버전 데이터 없음_"]
        return "\n".join(L)

    version = project["version"]
    L += ["## 현재 진행 버전", f"- 버전: {version}", f"- 현재 업무 단계: {stage['message']}",
          f"- 업무 상태(DB 등록값): {_v(project.get('status'))}"]
    if stage["phase"] == "spec":
        L.append(f"- 요구사양서 작성 검토 상태: {_v(project.get('spec_review_status'))}")
    elif stage["phase"] == "verify":
        L.append(f"- 동적 검증 결과 정리 상태: {_v(project.get('verify_summary_status'))}")

    L += ["", "## 본인 실제 진행 현황"]
    phases = ("spec", "verify")
    for phase in phases:
        title = "요구사양서 작성" if phase == "spec" else "동적 검증"
        data = _current_version_progress(project, member_id, phase, ref)
        if not data:
            L.append(f"- {title}: 계획된 서브시스템 물량이 없습니다.")
            continue
        L.append(f"### {title}")
        if phase == "spec":
            total = sum(item["total_tasks"] for item in data)
            team_done = sum(item["team_tasks"] for item in data)
            own_done = sum(item["own_tasks"] for item in data)
            own_rows = sum(item["own_rows"] for item in data)
            last_dates = [item["own_last"] for item in data if item["own_last"]]
            own_summary = f"본인 완료 {own_done}건" if own_rows else "본인 실적 기록 없음"
            latest = f" · 최근 기록 {max(last_dates)}" if last_dates else ""
            L.append(f"- {own_summary} · 팀 완료 {team_done}/{total}건 · 팀 잔여 {max(0, total-team_done)}건{latest}")
            continue
        for item in data:
            req_total, req_done, own_req = item["total_requirements"], item["team_requirements"], item["own_requirements"]
            func_total, func_done, own_func = item["total_functions"], item["team_functions"], item["own_functions"]
            L.append(f"- {item['subsystem']}: 본인 완료 요구사양서 {own_req}건 / 함수 {own_func}건 · "
                     f"버전 계획 요구사양서 {req_total}건, 함수 {func_total}건 · "
                     f"팀 완료 요구사양서 {req_done}건, 함수 {func_done}건 · "
                     f"버전 잔여 요구사양서 {max(0, req_total-req_done)}건, 함수 {max(0, func_total-func_done)}건 · "
                     f"본인 최근 기록 {_v(item['own_last'])}")

    L += ["", "## 휴가 중 승인된 대체 업무"]
    approved = _approved_leave_redistribution(project["id"], member_id, start, end)
    if approved:
        for row in approved:
            phase_label = "요구사양서 작성" if row["phase"] == "spec" else "동적 검증"
            amounts = (f"일감 {row.get('allocated_tasks', 0)}건" if row["phase"] == "spec" else
                       f"요구사양서 {row.get('allocated_requirements', 0)}건 · 함수 {row.get('allocated_functions', 0)}건")
            period = f"{row['leave_start']}~{row['leave_end']}"
            L.append(f"- {phase_label} · {row['subsystem']} → {row['backup_member_name']}: {amounts} · 승인 휴가기간 {period}")
    else:
        L.append("- 대체 담당자 페이지에서 저장한 승인 재분배안이 없습니다.")

    schedules = [row for row in db.list_version_task_schedules(project["id"])
                 if int(row.get("member_id") or -1) == member_id and start <= row["work_date"] <= end]
    L += ["", "## 휴가 기간 중 현재 버전 일정"]
    if schedules:
        for row in schedules:
            phase = "요구사양서 작성" if row["phase"] == "spec" else "동적 검증"
            L.append(f"- {row['work_date']} · {phase} · {row['task_detail']} (PM 등록 예정 일정)")
    else:
        L.append("- 휴가 기간에 해당 버전의 본인 일정이 등록되어 있지 않습니다.")

    spec_due, verify_due = project.get("spec_due"), project.get("verify_due")
    L += ["", "## 현재 버전 마감일"]
    if spec_due:
        L.append(f"- 요구사양서 작성: {spec_due}")
    if verify_due:
        L.append(f"- 동적 검증: {verify_due}")
    if not spec_due and not verify_due:
        L.append("- 이 버전에 등록된 마감일이 없습니다.")

    notes = []
    for phase in phases:
        for row in db.list_version_worklogs(project["id"], phase):
            if int(row.get("member_id") or -1) == member_id and row.get("date") and row["date"] <= ref and str(row.get("notes") or "").strip():
                notes.append((row["date"], "요구사양서 작성" if phase == "spec" else "동적 검증", row))
    L += ["", "## 최근 실적 특이사항"]
    if notes:
        for log_date, phase, row in sorted(notes, key=lambda item: item[0], reverse=True)[:8]:
            L.append(f"- {log_date} · {row.get('subsystem') or '서브시스템 미입력'} · {phase}: {row['notes']}")
    else:
        L.append("- 현재 버전에서 등록된 특이사항이 없습니다.")
    L += ["", "## 데이터 해석 기준",
         "- 이 문서는 현재 진행 중인 버전의 VACON 내부 계획·배정·실적만 사용합니다.",
         "- 본인 완료량은 개인 실적이며, 버전 잔여량은 팀 전체 계획 기준입니다. 개인 배정이 없으면 개인 잔여량을 추정하지 않습니다.",
         "- PM 일정은 예정이고 업무 로그는 실제 수행 실적입니다.", "",
         f"_생성 기준일: {ref} · 현재 진행 버전 {version}_"]
    return "\n".join(L)


def handover_facts(team: TeamData, member_id: int, start: date, end: date, rules: dict | None = None) -> str:
    """Provide domain-specific LLM instructions and evidence for the selected handover."""
    if member_id in team.unit_pool():
        return ("## 단위 검증 문서 작성 규칙\n"
                "- 입력 초안은 현재 진행 중인 버전 하나의 계획·배정·실적만 담고 있습니다. 과거 버전을 추가하지 않습니다.\n"
                "- 요구사양서 작성과 동적 검증의 수량 및 진척을 서로 합산하지 않습니다.\n"
                "- PM 등록 일정은 예정이고, 업무 로그만 실제 실적입니다.\n"
                "- 휴가 중 대체 업무는 대체 담당자 페이지에서 승인 저장된 재분배안만 사용합니다. 기본 PM 배정을 대체 배정으로 바꾸어 표현하지 않습니다.\n"
                "- 일정에 없는 담당자 배정, 로그에 없는 잔여량, 검토 완료 여부, 결함 원인 및 대응 방안을 만들지 않습니다.\n"
                "- 버전 전체 잔여량을 개인에게 배정된 잔여량으로 표현하지 않습니다.\n"
                "- 초안의 진행 단계, 날짜, 서브시스템, 본인 완료량, 팀 완료량 및 특이사항을 보존하고 다른 버전이나 일반 프로젝트 문구를 추가하지 않습니다.")
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
