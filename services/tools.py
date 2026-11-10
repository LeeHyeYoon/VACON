"""조회 전용 AI Agent 도구. 각 도구는 기존 서비스/저장소 계층만 호출한다."""
from __future__ import annotations

from datetime import date

from data import db
from .handover import generate_handover
from .models import isnull
from .recommend import recommend_backups
from .risk import HIGH, MEDIUM, assess_tasks, daily_summary
from .version_projects import aggregate_version_logs, project_summary
from .internal_projects import dashboard as internal_dashboard, is_internal_version
from .worklog import classify_sheet_task


TOOL_DEFINITIONS = [
    {"name": "list_vacations", "description": "등록된 휴가 캘린더에서 지정 기간의 휴가를 조회한다. 이름이 지정되면 해당 팀원만 조회한다.",
     "input_schema": {"type": "object", "properties": {
         "start_date": {"type": "string", "description": "조회 시작일 YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "조회 종료일 YYYY-MM-DD"},
         "member_name": {"type": "string", "description": "팀원 이름(선택)"}}, "required": ["start_date", "end_date"]}},
    {"name": "get_version_status", "description": "등록된 버전의 일정, 담당자, 완료 수, 진행률, 목표 진행률을 조회한다. 버전명은 VACON 데이터와 정확히 일치해야 한다.",
     "input_schema": {"type": "object", "properties": {"version": {"type": "string", "description": "등록된 버전명"}}, "required": ["version"]}},
    {"name": "get_daily_workload", "description": "버전의 사양서 작성량과 동적검증량을 담당자 휴가와 주말에 따라 계산한다.",
     "input_schema": {"type": "object", "properties": {"version": {"type": "string", "description": "등록된 버전명"}}, "required": ["version"]}},
    {"name": "search_worklogs", "description": "검증 업무 로그를 직접 조회한다. 과거 Google Sheets 실적(T0800 미만), VACON 내부 DB 실적(T0800 이상), PM 예정 일정을 구분해서 반환한다. 사용자가 업무 로그, 실제 실적, 담당자별 수행 내역, 세부 업무, 서브시스템 이력을 물으면 반드시 이 도구를 먼저 호출한다. 가능한 필터만 전달하고 날짜가 없으면 전체 기간을 조회한다.",
     "input_schema": {"type": "object", "properties": {
         "version": {"type": "string", "description": "버전명(선택)"},
         "member_name": {"type": "string", "description": "담당자 이름(선택)"},
         "subsystem": {"type": "string", "description": "서브시스템명(선택)"},
         "work_type": {"type": "string", "description": "요구사양서 작성 또는 동적 검증(선택)"},
         "start_date": {"type": "string", "description": "조회 시작일 YYYY-MM-DD(선택)"},
         "end_date": {"type": "string", "description": "조회 종료일 YYYY-MM-DD(선택)"},
         "record_kind": {"type": "string", "enum": ["전체", "실적", "예정 일정"], "description": "기록 종류(선택, 기본 전체)"},
         "limit": {"type": "integer", "description": "반환할 최대 행 수(기본 30, 최대 100)"}}, "required": []}},
    {"name": "analyze_work_risks", "description": "지정 기간의 등록 업무 위험을 기존 위험 분석 로직으로 평가한다. 특정 버전이 있으면 해당 버전의 일정 위험도 포함한다.",
     "input_schema": {"type": "object", "properties": {
         "start_date": {"type": "string", "description": "분석 시작일 YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "분석 종료일 YYYY-MM-DD"},
         "version": {"type": "string", "description": "버전명(선택)"}}, "required": ["start_date", "end_date"]}},
    {"name": "recommend_backup", "description": "기존 대체 담당자 추천 기능으로 후보와 근거를 조회한다. 실제 담당자 변경은 하지 않는다.",
     "input_schema": {"type": "object", "properties": {
         "task_name": {"type": "string", "description": "VACON에 등록된 업무명"},
         "start_date": {"type": "string", "description": "대체 검토 시작일 YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "대체 검토 종료일 YYYY-MM-DD"}}, "required": ["task_name", "start_date", "end_date"]}},
    {"name": "draft_handover", "description": "기존 인수인계 기능으로 초안을 생성한다. 문서를 저장하거나 전송하지 않는다.",
     "input_schema": {"type": "object", "properties": {
         "member_name": {"type": "string", "description": "등록된 팀원 이름"},
         "start_date": {"type": "string", "description": "휴가 시작일 YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "휴가 종료일 YYYY-MM-DD"}}, "required": ["member_name", "start_date", "end_date"]}},
]

def openai_tools() -> list[dict]:
    """TOOL_DEFINITIONS를 OpenAI 호환 chat/completions의 tools 형식으로 변환한다."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in TOOL_DEFINITIONS]


_ARGUMENT_KEYS = {
    "list_vacations": {"start_date", "end_date", "member_name"},
    "get_version_status": {"version"},
    "get_daily_workload": {"version"},
    "search_worklogs": {"version", "member_name", "subsystem", "work_type", "start_date", "end_date", "record_kind", "limit"},
    "analyze_work_risks": {"start_date", "end_date", "version"},
    "recommend_backup": {"task_name", "start_date", "end_date"},
    "draft_handover": {"member_name", "start_date", "end_date"},
}


def _period(args):
    try:
        start = date.fromisoformat(args.get("start_date", ""))
        end = date.fromisoformat(args.get("end_date", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("시작일과 종료일을 YYYY-MM-DD 형식으로 입력하세요.") from exc
    if start.isoformat() != args["start_date"] or end.isoformat() != args["end_date"]:
        raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    if end < start:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
    if (end - start).days > 366:
        raise ValueError("한 번에 조회할 수 있는 기간은 최대 1년입니다.")
    return start, end


def _member(team, name):
    hits = [m for m in team.member_records() if m["name"].strip().casefold() == str(name or "").strip().casefold()]
    if len(hits) != 1:
        if not hits:
            raise ValueError(f"팀원 '{name}'을(를) 찾지 못했습니다.")
        raise ValueError("팀원 이름이 중복되어 대상을 특정할 수 없습니다.")
    return hits[0]


def _matches(value, requested):
    return not requested or str(value or "").strip().casefold() == str(requested).strip().casefold()


def _date_text(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _search_worklogs(args, team):
    start = end = None
    if args.get("start_date") or args.get("end_date"):
        if not args.get("start_date") or not args.get("end_date"):
            raise ValueError("기간 조회에는 시작일과 종료일을 모두 입력하세요.")
        start, end = _period(args)
    limit = max(1, min(100, int(args.get("limit", 30))))
    requested_type = str(args.get("work_type") or "").strip().casefold()
    type_aliases = {"요구사양서": "req", "요구사양서 작성": "req", "작성": "req", "req": "req",
                    "동적 검증": "dynamic", "동적검증": "dynamic", "검증": "dynamic", "dynamic": "dynamic"}
    track_filter = type_aliases.get(requested_type)
    if requested_type and not track_filter:
        raise ValueError("업무 유형은 요구사양서 작성 또는 동적 검증으로 지정하세요.")
    kind = str(args.get("record_kind") or "전체").strip()
    if kind not in {"전체", "실적", "예정 일정"}:
        raise ValueError("기록 종류는 전체, 실적, 예정 일정 중 하나여야 합니다.")
    entries = []

    # 과거 버전의 원본 실적은 Google Sheets 연동이 적재한 팀 로그에서 읽는다.
    for row in team.logs.to_dict("records") if len(team.logs) else []:
        if str(row.get("source") or "") == "샘플":
            continue
        track, detail = classify_sheet_task(row.get("category"), "req" if row.get("track") == "req" else "verify")
        work_type = "요구사양서 작성" if track == "req" else "동적 검증"
        entry = {"date": _date_text(row.get("date")), "version": str(row.get("version") or "").strip() or "(버전 미입력)",
                 "member": team.name(row.get("member_id")), "subsystem": str(row.get("subsystem") or "").strip() or "공통 업무",
                 "work_type": work_type, "detail": detail, "record_kind": "실적", "source": row.get("source") or "Google Sheets",
                 "completed_tasks": row.get("issue") if track == "req" else None,
                 "new_requirements": row.get("new_req") if track == "req" else None,
                 "modified_requirements": row.get("modified_req") if track == "req" else None,
                 "deleted_requirements": row.get("deleted_req") if track == "req" else None,
                 "completed_requirements": row.get("req") if track == "dynamic" else None,
                 "completed_functions": row.get("func") if track == "dynamic" else None,
                 "defects": row.get("defect") if track == "dynamic" else None,
                 "tc_executed": row.get("tc") if track == "dynamic" else None,
                 "tc_created": row.get("tc_created") if track == "dynamic" else None,
                 "tc_modified": row.get("tc_modified") if track == "dynamic" else None,
                 "tc_deleted": row.get("tc_deleted") if track == "dynamic" else None,
                 "notes": row.get("notes") or ""}
        entries.append(entry)

    # 신규 버전 실적은 내부 DB에서 업무 단계별로 읽는다.
    internal_fields = {
        "spec": ("completed_tasks", "new_requirements", "modified_requirements", "deleted_requirements"),
        "verify": ("completed_requirements", "completed_functions", "defects", "tc_executed", "tc_created", "tc_modified", "tc_deleted"),
    }
    for phase in ("spec", "verify"):
        for row in db.list_all_internal_worklogs(phase):
            if not is_internal_version(row.get("version", "")):
                continue
            work_type = "요구사양서 작성" if phase == "spec" else "동적 검증"
            entry = {"date": _date_text(row.get("date")), "version": row.get("version"), "member": row.get("member_name"),
                     "subsystem": row.get("subsystem") or "공통 업무", "work_type": work_type,
                     "detail": row.get("task_detail") or ("사양서 작성" if phase == "spec" else "동적 검증"),
                     "record_kind": "실적", "source": "VACON DB", "notes": row.get("notes") or ""}
            entry.update({field: row.get(field) for field in internal_fields[phase]})
            entries.append(entry)

    # PM 일정은 실적 집계와 혼합하지 않고 예정 일정으로 제공한다.
    if kind != "실적":
        for row in db.list_version_task_schedules():
            track = "req" if row.get("phase") == "spec" else "dynamic"
            work_type = "요구사양서 작성" if track == "req" else "동적 검증"
            entries.append({"date": _date_text(row.get("work_date")), "version": row.get("version"),
                            "member": row.get("member_name"), "subsystem": "공통 업무", "work_type": work_type,
                            "detail": row.get("task_detail"), "record_kind": "예정 일정", "source": "PM 일정"})

    filtered = []
    for entry in entries:
        if kind != "전체" and entry["record_kind"] != kind:
            continue
        if not _matches(entry.get("version"), args.get("version")):
            continue
        if not _matches(entry.get("member"), args.get("member_name")):
            continue
        if not _matches(entry.get("subsystem"), args.get("subsystem")):
            continue
        if track_filter and ("요구사양서 작성" if track_filter == "req" else "동적 검증") != entry["work_type"]:
            continue
        if start and end:
            try:
                entry_date = date.fromisoformat(entry["date"][:10])
            except (TypeError, ValueError):
                continue
            if not start <= entry_date <= end:
                continue
        filtered.append(entry)
    filtered.sort(key=lambda row: row.get("date") or "", reverse=True)
    return {"filters": {"version": args.get("version"), "member": args.get("member_name"),
                        "subsystem": args.get("subsystem"), "work_type": args.get("work_type"),
                        "period": [start.isoformat(), end.isoformat()] if start else None, "record_kind": kind},
            "matching_records": len(filtered), "returned_records": min(len(filtered), limit),
            "truncated": len(filtered) > limit, "records": filtered[:limit],
            "source_note": "과거 실적은 연동된 Google Sheets 로그, 신규 실적은 T0800 이상 VACON DB에서 조회했습니다. PM 일정은 예정 일정으로 따로 표시하며 실적 건수에 포함하지 않습니다."}


def _project(version):
    hits = [p for p in db.list_version_projects() if p["version"].casefold() == str(version or "").strip().casefold()]
    if len(hits) != 1:
        if not hits:
            known = ", ".join(p["version"] for p in db.list_version_projects()) or "없음"
            raise ValueError(f"버전 '{version}'이 등록되어 있지 않습니다. 등록된 버전: {known}")
        raise ValueError("버전명이 중복되어 대상을 특정할 수 없습니다.")
    return hits[0]


def _version_phases(team, project, today):
    if is_internal_version(project["version"]):
        reports = {}
        for phase in ("spec", "verify"):
            workloads = db.list_version_workloads(project["id"], phase)
            logs = db.list_version_worklogs(project["id"], phase)
            report = internal_dashboard(project, phase, workloads, logs, today)
            total_key, done_key = (("total_tasks", "completed_tasks") if phase == "spec" else
                                   ("total_functions", "completed_functions"))
            total, done = report["totals"][total_key], report["totals"][done_key]
            assignments = db.list_version_assignments(project["id"], phase)
            reports[phase] = {"internal": True, "version": project["version"], "phase": phase,
                "due_date": report["due"].isoformat() if report["due"] else None,
                "total": total, "completed": done, "remaining": max(0, total-done),
                "progress": report["totals"]["progress"] if phase == "spec" else report["totals"]["function_progress"],
                "total_requirements": report["totals"].get("total_requirements"),
                "completed_requirements": report["totals"].get("completed_requirements"),
                "requirement_progress": report["totals"].get("requirement_progress"),
                "remaining_days": report["remaining_days"],
                "approved_assignees": sorted({row["member_name"] for row in assignments}),
                "risk": "판단 자료 부족" if total and done < total else "완료" if total and done >= total else "물량 미등록",
                "warning": "내부 DB 실적 기준입니다. 완료 예측은 AI 배분 화면의 내부 처리 이력 근거를 확인하세요."}
        return reports
    totals = aggregate_version_logs(team.logs.to_dict("records") if len(team.logs) else [])
    log = totals.get(project["version"].strip().casefold(), {})
    return project_summary(team, project, today, {"spec": log.get("spec_done_req", 0),
                                                  "verify": log.get("verify_done_func", 0)})


def execute_tool(name: str, args: dict, *, team, rules: dict, today: date):
    """허용 목록 도구만 실행. 반환값은 JSON 호환 dict/list/string."""
    if not isinstance(args, dict):
        raise ValueError("도구 입력은 JSON 객체여야 합니다.")
    unknown = set(args) - _ARGUMENT_KEYS.get(name, set())
    if unknown:
        raise ValueError("지원하지 않는 도구 입력 항목: " + ", ".join(sorted(unknown)))
    if name == "search_worklogs":
        return _search_worklogs(args, team)
    if name == "list_vacations":
        start, end = _period(args)
        member = _member(team, args["member_name"]) if args.get("member_name") else None
        rows = [{"member": team.name(x["member_id"]), "type": x["type"], "start": x["start"].isoformat(),
                 "end": x["end"].isoformat(), "note": x["note"]}
                for x in team.leave_records() if x["start"] <= end and start <= x["end"]
                and (member is None or int(x["member_id"]) == int(member["id"]))]
        return {"period": [start.isoformat(), end.isoformat()], "vacations": rows}
    if name in ("get_version_status", "get_daily_workload"):
        p = _project(args.get("version"))
        phases = _version_phases(team, p, today)
        if is_internal_version(p["version"]):
            data = {"version": p["version"], "status": p["status"], "source": "VACON 내부 DB",
                    "spec": phases["spec"], "dynamic_verification": phases["verify"],
                    "source_info": p["source_info"], "notes": p["notes"]}
        else:
            data = {"version": p["version"], "status": p["status"],
                    "spec": _phase_payload(team, phases["spec"]), "dynamic_verification": _phase_payload(team, phases["verify"]),
                    "source_info": p["source_info"], "notes": p["notes"]}
        return data if name == "get_version_status" else {"version": p["version"], "spec": data["spec"], "dynamic_verification": data["dynamic_verification"]}
    if name == "analyze_work_risks":
        start, end = _period(args)
        tasks = assess_tasks(team, start, end, rules)
        high = tasks[tasks["위험도"].isin([HIGH, MEDIUM])] if not tasks.empty else tasks
        report = {"period": [start.isoformat(), end.isoformat()], "task_risks": high[[c for c in ("업무", "담당자", "마감일", "위험도", "점수", "대표 날짜", "부재일수", "근거", "대응 방안") if c in high]].to_dict("records")}
        if args.get("version"):
            p = _project(args["version"])
            report["version"] = p["version"]
            phases = _version_phases(team, p, today)
            report["version_schedule_risk"] = (phases if is_internal_version(p["version"])
                                                else {k: _phase_payload(team, v) for k, v in phases.items()})
        return report
    if name == "recommend_backup":
        start, end = _period(args)
        hits = [t for t in team.task_records() if t["name"].strip().casefold() == str(args.get("task_name", "")).strip().casefold()]
        if len(hits) != 1:
            if not hits:
                raise ValueError(f"업무 '{args.get('task_name')}'을(를) 찾지 못했습니다.")
            raise ValueError("업무명이 중복되어 대상을 특정할 수 없습니다.")
        res = recommend_backups(team, int(hits[0]["id"]), start, end, rules)
        return {"task": res["task"]["name"], "candidates": res["candidates"], "warnings": res["warnings"], "excluded": res["excluded"],
                "notice": "추천은 검토용이며 VACON 담당자 정보는 변경하지 않았습니다."}
    if name == "draft_handover":
        start, end = _period(args)
        member = _member(team, args.get("member_name"))
        leave = next((x for x in team.leave_records() if int(x["member_id"]) == int(member["id"])
                      and x["start"] <= end and start <= x["end"]), None)
        draft = generate_handover(team, int(member["id"]), start, end, leave["type"] if leave else None, today)
        return {"member": member["name"], "draft": draft[:10000], "saved_or_sent": False}
    raise ValueError(f"허용되지 않은 도구입니다: {name}")


def _phase_payload(team, phase):
    return {"start_date": phase["start"].isoformat() if phase["start"] else None,
            "due_date": phase["due"].isoformat() if phase["due"] else None,
            "total": phase["total"], "completed": phase["done"], "remaining": phase["remaining"],
            "progress": phase["progress"], "target_progress": phase["target_progress"],
            "assignees": [team.name(x) for x in phase["owner_ids"]],
            "available_workdays": phase["available_days"], "available_person_days": phase["person_days"],
            "vacation_capacity_lost_person_days": phase["lost_person_days"], "estimated_shortfall": phase["shortfall"],
            "daily_recommended": phase["daily"], "risk": phase["risk"], "warning": phase["warning"]}
