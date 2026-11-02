"""버전별 사양서/동적검증 일정과 휴가 반영 업무량 계산."""
from __future__ import annotations

from datetime import date

from .risk import workdays


def aggregate_version_logs(log_records) -> dict[str, dict]:
    """누적 업무 로그를 버전별로 집계한다. 실적 입력값과 구분되는 활동량 지표다."""
    result = {}
    for row in log_records:
        raw_version = row.get("version")
        if raw_version is None or raw_version != raw_version:
            continue
        version = str(raw_version).strip()
        if not version:
            continue
        key = version.casefold()
        item = result.setdefault(key, {"version": version, "records": 0, "req_issue_logs": 0.0,
                                       "spec_done_req": 0.0, "dynamic_done_req": 0.0, "verify_done_func": 0.0,
                                       "verify_tc": 0.0})
        item["records"] += 1
        if row.get("track") == "req":
            completed = _number(row.get("issue"))
            item["req_issue_logs"] += completed
            # 요구사양서 진행률은 요구사양서 작성 시트 로그만 사용한다.
            item["spec_done_req"] += completed
        elif row.get("track") == "dynamic":
            item["verify_tc"] += _number(row.get("tc"))
            # 동적검증 시트의 I열 완료 Req는 검증 업무 내 활동량이다.
            # 요구사양서 작성 단계 진행률에 더하면 서로 다른 업무가 합쳐진다.
            item["dynamic_done_req"] += _number(row.get("req"))
            item["verify_done_func"] += _number(row.get("func"))
    return result


def _number(value):
    try:
        number = float(value)
        return number if number == number else 0.0  # NaN from legacy in-memory frames -> zero
    except (TypeError, ValueError):
        return 0.0


def _capacity_days(team, owner_ids: list[int], start: date | None, due: date | None,
                   today: date, holidays=()) -> tuple[float, int, int]:
    if not start or not due or due < today:
        return 0.0, 0, 0
    begin = max(today, start)
    # Company holidays are intentionally not part of planning; only weekends and
    # registered assignee leave affect the available capacity.
    days = workdays(begin, due)
    if not owner_ids:
        return float(len(days)), len(days), len(days)
    capacity = 0.0
    active_days = 0
    for d in days:
        day_capacity = 0.0
        for owner_id in owner_ids:
            leave = next((lv for lv in team.leave_records()
                          if int(lv["member_id"]) == int(owner_id) and lv["start"] <= d <= lv["end"]), None)
            day_capacity += 0.5 if leave and leave["type"] == "반차" else 0.0 if leave else 1.0
        capacity += day_capacity
        active_days += int(day_capacity > 0)
    return capacity, active_days, len(days)


def _period_capacity(team, owner_ids, days, holidays):
    capacity = 0.0
    for d in days:
        for owner_id in owner_ids:
            leave = next((lv for lv in team.leave_records()
                          if int(lv["member_id"]) == int(owner_id) and lv["start"] <= d <= lv["end"]), None)
            capacity += 0.5 if leave and leave["type"] == "반차" else 0.0 if leave else 1.0
    return capacity


def phase_summary(team, project: dict, phase: str, today: date) -> dict:
    spec = phase == "spec"
    start = project["spec_start" if spec else "verify_start"]
    due = project["spec_due" if spec else "verify_due"]
    raw_done = int(project["spec_done" if spec else "verify_done"] or 0)
    total = int(project.get("spec_total_items" if spec else "verify_total_items", project.get("total_items", 0)) or 0)
    done = min(raw_done, total) if total else 0
    remaining = max(0, total - done)
    owner_ids = project.get("spec_owner_ids" if spec else "verify_owner_ids") or []
    if not owner_ids:
        legacy_owner = project.get("spec_owner_id" if spec else "verify_owner_id")
        owner_ids = [legacy_owner] if legacy_owner else []
    registration_key = "spec_registered" if spec else "verify_registered"
    # Older in-memory project dictionaries may predate explicit phase flags.
    registered = bool(project.get(registration_key, start or due or owner_ids))
    holidays = project.get("holidays", [])
    capacity, available_days, scheduled_days = _capacity_days(team, owner_ids, start, due, today, holidays)
    remaining_days = max(0, (due - today).days) if due else None
    # Today is counted as an available workday when it is a weekday.
    configured = registered and total > 0
    if not configured:
        daily = None
    elif remaining == 0:
        daily = 0
    elif available_days <= 0:
        daily = None
    else:
        import math
        daily = math.ceil(remaining / available_days)
    elapsed = workdays(start, min(today, due)) if start and due and today >= start else []
    planned = workdays(start, due) if start and due else []
    elapsed_capacity = _period_capacity(team, owner_ids, elapsed, holidays)
    planned_capacity = _period_capacity(team, owner_ids, planned, holidays)
    target = elapsed_capacity / planned_capacity if planned_capacity else 0.0
    progress = done / total if total else 0.0
    nominal_capacity = scheduled_days * len(owner_ids) if owner_ids else scheduled_days
    # Verification throughput cap is intentionally not used; only spec entry retains a legacy optional cap.
    daily_cap = project.get("daily_spec_capacity") if spec else None
    shortfall = max(0, remaining - int(daily_cap) * available_days) if daily_cap and available_days else remaining if daily_cap and remaining else 0
    overrun = total > 0 and raw_done > total
    risk = ("미등록" if not registered else "대상 수 미입력" if total <= 0 else "로그 초과" if overrun else "완료" if remaining == 0 else
            "지연 위험" if not due or due < today or available_days <= 0 or shortfall > 0 else
            "주의" if daily and daily > 0 and capacity < nominal_capacity else "정상")
    return {"start": start, "due": due, "done": done, "total": total, "remaining": remaining,
            "progress": progress, "target_progress": target, "remaining_days": remaining_days,
            "available_days": available_days, "person_days": capacity, "weekdays": scheduled_days,
            "lost_person_days": max(0, nominal_capacity - capacity), "shortfall": shortfall, "daily": daily,
            "raw_done": raw_done, "registered": registered, "log_overrun": overrun,
            "owner_ids": owner_ids, "owner_id": owner_ids[0] if owner_ids else None, "risk": risk,
            "warning": ("이 단계는 아직 등록되지 않았습니다." if not registered else
                        "전체 대상 수를 입력해야 완료율과 일일 권장량을 계산할 수 있습니다." if total <= 0 else
                        f"업무 로그 누계({raw_done})가 전체 대상({total})을 초과했습니다. 버전 매핑이나 중복 로그를 확인하세요." if overrun else
                        "담당자가 지정되지 않아 팀 휴가를 반영하지 않은 참고값입니다." if not owner_ids and remaining else
                        "남은 업무 가능일이 없습니다. 마감일 조정 또는 추가 인력을 검토하세요." if remaining and available_days <= 0 else
                        f"현재 처리 가능량 기준 예상 부족 {shortfall}개입니다. 추가 인력·업무 재분배·일정 조정을 검토하세요." if shortfall else
                        "설정 처리량으로 마감일까지 완료하기 어렵습니다. 추가 인력·업무 재분배·일정 조정을 검토하세요." if risk == "지연 위험" else "")}


def project_summary(team, project: dict, today: date, completed: dict | None = None) -> dict:
    data = dict(project)
    if completed is not None:
        # Progress is driven by completed Req and Func log totals respectively.
        data["spec_done"] = completed.get("spec", data.get("spec_done", 0))
        data["verify_done"] = completed.get("verify", data.get("verify_done", 0))
    return {"spec": phase_summary(team, data, "spec", today),
            "verify": phase_summary(team, data, "verify", today)}
