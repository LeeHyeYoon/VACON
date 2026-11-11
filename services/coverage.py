"""Vacation coverage planning for approved version work of the four unit-verification staff."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from data import db
from .internal_projects import (is_internal_version, is_legacy_sheet_version, _subsystem_experience,
                                _subsystem_rotation_history, _split_units, _split_units_capped,
                                recommend_distribution)
from .risk import workdays
from .worklog import recommend_daily_count, recommend_daily_median

UNIT_NAMES = {"김재강", "주해원", "황지원", "이혜윤"}
PM_MEMBER_NAME = "주해원"


def _phase_totals(project_id: int, phase: str, path=None) -> dict:
    workloads = db.list_version_workloads(project_id, phase, path)
    logs = db.list_version_worklogs(project_id, phase, path)
    if phase == "spec":
        total = sum(int(row.get("total_tasks") or 0) for row in workloads)
        done = sum(int(row.get("completed_tasks") or 0) for row in logs)
        return {"tasks": (total, done)}
    return {
        "requirements": (sum(int(row.get("total_requirements") or 0) for row in workloads),
                         sum(int(row.get("completed_requirements") or 0) for row in logs)),
        "functions": (sum(int(row.get("total_functions") or 0) for row in workloads),
                      sum(int(row.get("completed_functions") or 0) for row in logs)),
    }


def workflow_stage(project: dict, path=None) -> dict:
    """Derive the active phase; PM spec review completion gates dynamic verification."""
    project_id = int(project["id"])
    spec = _phase_totals(project_id, "spec", path)["tasks"]
    spec_complete = spec[0] > 0 and spec[1] >= spec[0]
    verify = _phase_totals(project_id, "verify", path)
    verify_complete = all(total <= 0 or done >= total for total, done in verify.values()) and any(total > 0 for total, _ in verify.values())
    if spec[0] <= 0:
        return {"stage": "spec_plan", "phase": None, "spec": spec, "verify": verify,
                "message": "요구사양서 작성 계획을 먼저 등록하세요."}
    if not spec_complete:
        return {"stage": "spec_work", "phase": "spec", "spec": spec, "verify": verify,
                "message": f"요구사양서 작성 단계 · 계획 {spec[0]}, 완료 {spec[1]}, 잔여 {max(0, spec[0]-spec[1])}"}
    if project.get("spec_review_status") != "완료":
        return {"stage": "spec_review", "phase": None, "spec": spec, "verify": verify,
                "message": "요구사양서 작성 완료 · PM 검토 완료 후 동적 검증이 시작됩니다."}
    if verify_complete:
        return {"stage": "done", "phase": None, "spec": spec, "verify": verify,
                "message": "등록된 동적 검증 목표를 모두 완료했습니다."}
    if not any(total > 0 for total, _ in verify.values()):
        return {"stage": "verify_plan", "phase": None, "spec": spec, "verify": verify,
                "message": "요구사양서 작성 완료 · 동적 검증 서브시스템별 목표를 등록하세요."}
    return {"stage": "verify_work", "phase": "verify", "spec": spec, "verify": verify,
            "message": "요구사양서 작성 완료 · 동적 검증 실적을 기록할 수 있습니다."}


def _normalized_record(row: dict, phase: str) -> dict:
    if phase == "spec":
        return {"date": row.get("date"), "member_id": int(row["member_id"]),
                "subsystem": str(row.get("subsystem") or "").strip(), "version": str(row.get("version") or ""),
                "tasks": float(row.get("completed_tasks", row.get("issue", 0)) or 0)}
    return {"date": row.get("date"), "member_id": int(row["member_id"]),
            "subsystem": str(row.get("subsystem") or "").strip(), "version": str(row.get("version") or ""),
            "requirements": float(row.get("completed_requirements", row.get("req", 0)) or 0),
            "functions": float(row.get("completed_functions", row.get("func", 0)) or 0),
            "executed": float(row.get("tc_executed", row.get("tc", 0)) or 0),
            "created": float(row.get("tc_created", row.get("tc_created_done", 0)) or 0),
            "modified": float(row.get("tc_modified", row.get("tc_modified_done", 0)) or 0),
            "deleted": float(row.get("tc_deleted", row.get("tc_deleted_done", 0)) or 0),
            "defects": float(row.get("defects", row.get("defect", row.get("defect_done", 0))) or 0)}


def _history_rows(team, phase: str, path=None) -> list[dict]:
    track = "req" if phase == "spec" else "dynamic"
    rows = []
    if len(team.logs):
        for row in team.logs.to_dict("records"):
            if row.get("track") != track or not is_legacy_sheet_version(row.get("version", "")):
                continue
            if str(row.get("source") or "").strip() == "샘플":
                continue
            rows.append(_normalized_record(row, phase))
    for row in db.list_all_internal_worklogs(phase, path):
        if is_internal_version(row.get("version", "")):
            rows.append(_normalized_record(row, phase))
    return rows


def _rates(team, phase: str, as_of: date, holidays: set[str], path=None) -> dict:
    """Person/subsystem pace from all available actual history, including legacy Sheets versions."""
    rows = [r for r in _history_rows(team, phase, path)
            if r["date"] and r["date"] <= as_of and r["subsystem"]]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["member_id"], row["subsystem"].casefold())].append(row)
        # Use same-phase person-wide history when this person has no record for the
        # requested subsystem. This remains grounded in recorded requirement/function counts.
        grouped[(row["member_id"], "*")].append(row)
    metrics = ("tasks",) if phase == "spec" else ("requirements", "functions", "executed", "created", "modified", "deleted", "defects")
    result = {}
    for (member_id, subsystem), records in grouped.items():
        begin = min(row["date"] for row in records)
        available_days = 0.0
        for day in workdays(begin, as_of):
            if day.isoformat() in holidays:
                continue
            leave = [r for r in team.leave_records() if int(r["member_id"]) == member_id and r["start"] <= day <= r["end"]]
            if any(r["type"] != "반차" for r in leave):
                continue
            available_days += 0.5 if leave else 1.0
        totals = {metric: sum(row.get(metric, 0.0) for row in records) for metric in metrics}
        rates = {metric: (amount / available_days if available_days > 0 and amount > 0 else None)
                 for metric, amount in totals.items()}
        versions = {row["version"].casefold() for row in records if row["version"]}
        active_days = {row["date"] for row in records if any(row.get(m, 0) > 0 for m in metrics)}
        record_count = len(records)
        breadth = sum(any(row.get(metric, 0) > 0 for row in records)
                      for metric in metrics if metric != "defects")
        if record_count < 3 or len(versions) < 2:
            level = "평가 데이터 부족"
        elif record_count >= 8 and len(versions) >= 3 and breadth >= 2:
            level = "반복·다양 경험"
        else:
            level = "업무 경험 확인"
        result[(member_id, subsystem)] = {"rates": rates, "versions": len(versions), "active_days": len(active_days),
            "records": record_count, "available_days": available_days, "metric_breadth": breadth, "level": level,
            "from": begin}
    return result


def _pace(phase_rates: dict, member_id: int, subsystem: str, metric: str):
    """Return (rate, history, used_person_wide_fallback), preferring exact subsystem data."""
    exact = phase_rates.get((int(member_id), str(subsystem or "").casefold()), {})
    rate = exact.get("rates", {}).get(metric)
    if rate and rate > 0:
        return rate, exact, False
    overall = phase_rates.get((int(member_id), "*"), {})
    rate = overall.get("rates", {}).get(metric)
    if rate and rate > 0:
        return rate, overall, True
    return None, exact or overall, bool(overall)


def _capacity_days(team, member_id: int, start: date, end: date, holidays: set[str]) -> float:
    capacity = 0.0
    for day in workdays(start, end):
        if day.isoformat() in holidays:
            continue
        leave = [row for row in team.leave_records() if int(row["member_id"]) == int(member_id)
                 and row["start"] <= day <= row["end"]]
        if any(row["type"] != "반차" for row in leave):
            continue
        capacity += 0.5 if leave else 1.0
    return capacity


def _completion_date(team, member_id: int, start: date, due: date, effort_days: float, holidays: set[str]) -> date | None:
    if effort_days < 0 or due < start:
        return None
    remaining = effort_days
    for day in workdays(start, due):
        if day.isoformat() in holidays:
            continue
        leave = [row for row in team.leave_records() if int(row["member_id"]) == int(member_id)
                 and row["start"] <= day <= row["end"]]
        if any(row["type"] != "반차" for row in leave):
            continue
        remaining -= 0.5 if leave else 1.0
        if remaining <= 0:
            return day
    return None


def _log_totals(phase: str, path=None) -> dict:
    result = defaultdict(lambda: defaultdict(float))
    for row in db.list_all_internal_worklogs(phase, path):
        if not is_internal_version(row.get("version", "")):
            continue
        key = (int(row["project_id"]), str(row["subsystem"]).casefold(), int(row["member_id"]))
        if phase == "spec":
            result[key]["tasks"] += float(row.get("completed_tasks") or 0)
        else:
            result[key]["requirements"] += float(row.get("completed_requirements") or 0)
            result[key]["functions"] += float(row.get("completed_functions") or 0)
    return result


def _commitments(phase: str, path=None) -> list[dict]:
    """Outstanding approved primary and already approved substitute workloads across active versions."""
    logs = _log_totals(phase, path)
    rows = []
    for project in db.list_version_projects(path):
        if not is_internal_version(project["version"]) or project["status"] == "완료":
            continue
        pid = int(project["id"])
        backups = db.list_version_backup_assignments(pid, phase, path)
        transferred = defaultdict(lambda: defaultdict(int))
        for row in backups:
            key = (str(row["subsystem"]).casefold(), int(row["absent_member_id"]))
            for metric in ("tasks", "functions", "requirements"):
                col = {"tasks": "allocated_tasks", "functions": "allocated_functions", "requirements": "allocated_requirements"}[metric]
                transferred[key][metric] += int(row.get(col) or 0)
        for assignment in db.list_version_assignments(pid, phase, path):
            subsystem = str(assignment["subsystem"] or "").casefold()
            member = int(assignment["member_id"])
            key = (pid, subsystem, member)
            if phase == "spec":
                amounts = {"tasks": int(assignment.get("allocated_tasks") or 0)}
            else:
                amounts = {"requirements": int(assignment.get("allocated_requirements") or 0),
                           "functions": int(assignment.get("allocated_functions") or 0)}
            completed = logs[key]
            moved = transferred[(subsystem, member)]
            remaining = {metric: max(0, amount - int(completed.get(metric, 0)) - int(moved.get(metric, 0)))
                         for metric, amount in amounts.items()}
            if any(remaining.values()):
                rows.append({"project_id": pid, "version": project["version"], "subsystem": assignment["subsystem"],
                             "member_id": member, "phase": phase, **remaining,
                             "difficulty": float(assignment.get("effort_score") or 0)})
        # Approved substitute work counts against the receiving person's capacity.
        for backup in backups:
            member = int(backup["backup_member_id"])
            subsystem = str(backup["subsystem"] or "").casefold()
            key = (pid, subsystem, member)
            if phase == "spec":
                amounts = {"tasks": int(backup.get("allocated_tasks") or 0)}
            else:
                amounts = {"requirements": int(backup.get("allocated_requirements") or 0),
                           "functions": int(backup.get("allocated_functions") or 0)}
            remaining = {metric: max(0, amount - int(logs[key].get(metric, 0))) for metric, amount in amounts.items()}
            if any(remaining.values()):
                rows.append({"project_id": pid, "version": project["version"], "subsystem": backup["subsystem"],
                             "member_id": member, "phase": phase, **remaining,
                             "difficulty": 0.0, "is_substitute": True})
    return rows


def version_leave_options(project: dict, team, path=None, as_of: date | None = None) -> list[dict]:
    """Actual leave periods for currently approved owners in the workflow's active phase."""
    as_of = as_of or date.today()
    context = workflow_stage(project, path)
    phase = context.get("phase")
    if not phase:
        return []
    owner_ids = {int(row["member_id"]) for row in db.list_version_assignments(project["id"], phase, path)}
    allowed_ids = team.unit_pool()
    options = []
    for leave in team.leave_records():
        member_id = int(leave["member_id"])
        if member_id not in owner_ids or member_id not in allowed_ids or leave["end"] < as_of:
            continue
        options.append({**leave, "member_id": member_id, "member_name": team.name(member_id), "phase": phase,
                        "label": f"{team.name(member_id)} · {leave['start']}~{leave['end']} · {leave['type']}"})
    return sorted(options, key=lambda row: (row["start"], row["member_name"].casefold()))


def _recommend_version_remaining_rebalance(team, project: dict, phase: str, absent_member_id: int,
                                            leave_start: date, leave_end: date, as_of: date,
                                            context: dict, path=None) -> dict:
    """Redistribute all version remainder when the absent person's own scope has no residual."""
    workloads = db.list_version_workloads(project["id"], phase, path)
    logs = db.list_version_worklogs(project["id"], phase, path)
    done = defaultdict(lambda: defaultdict(int))
    for log in logs:
        key = str(log["subsystem"]).strip().casefold()
        if phase == "spec":
            done[key]["tasks"] += int(log.get("completed_tasks") or 0)
        else:
            done[key]["requirements"] += int(log.get("completed_requirements") or 0)
            done[key]["functions"] += int(log.get("completed_functions") or 0)

    adjusted, demands = [], []
    overreported = []
    for workload in workloads:
        subsystem = str(workload["subsystem"])
        key = subsystem.casefold()
        row = {"subsystem": subsystem, "difficulty": int(workload.get("difficulty") or 1)}
        demand = {"subsystem": subsystem}
        metrics = (("tasks", "total_tasks"),) if phase == "spec" else (
            ("requirements", "total_requirements"), ("functions", "total_functions"))
        for metric, total_field in metrics:
            target = int(workload.get(total_field) or 0)
            completed = int(done[key][metric])
            if completed > target:
                overreported.append(f"{subsystem} {metric}: {completed}/{target}")
            remaining = max(0, target - completed)
            row[total_field] = remaining
            demand[metric] = remaining
        if any(value > 0 for metric, value in demand.items() if metric != "subsystem"):
            adjusted.append(row)
            demands.append(demand)

    due = project.get("spec_due" if phase == "spec" else "verify_due")
    total_demand = sum(int(value or 0) for item in demands for key, value in item.items() if key != "subsystem")
    metric_keys = ("tasks",) if phase == "spec" else ("requirements", "functions")
    demand_by_metric = {metric: sum(int(item.get(metric) or 0) for item in demands) for metric in metric_keys}
    base = {"mode": "project_rebalance", "phase": phase, "stage": context["stage"],
            "absent_member_id": int(absent_member_id), "absent_member": team.name(absent_member_id),
            "leave_start": leave_start, "leave_end": leave_end, "due": due, "demands": demands,
            "total_demand": total_demand, "demand_by_metric": demand_by_metric}
    if overreported:
        return {**base, "rows": [], "assignments": [], "project_assignments": [], "total_allocated": 0,
                "allocated_by_metric": {metric: 0 for metric in metric_keys},
                "remaining_by_metric": demand_by_metric,
                "remaining_unallocated": total_demand, "summary": "추가 인력 또는 업무 재분배 필요",
                "confidence": "평가 불가", "message": "실적이 계획량보다 큰 항목이 있어 업무량을 확인하세요: " + "; ".join(overreported)}
    if not adjusted:
        return {**base, "rows": [], "assignments": [], "project_assignments": [], "total_allocated": 0,
                "allocated_by_metric": {metric: 0 for metric in metric_keys},
                "remaining_by_metric": {metric: 0 for metric in metric_keys},
                "remaining_unallocated": 0, "summary": "마감일 내 완료 가능", "confidence": "높음",
                "message": "버전 업무 계획에 미완료 잔여량이 없습니다."}

    unavailable_during_leave = {int(row["member_id"]) for row in team.leave_records()
                                if row["start"] <= leave_end and leave_start <= row["end"]}
    available_member_ids = team.unit_pool() - {int(absent_member_id)} - unavailable_during_leave
    metric_names = ("tasks",) if phase == "spec" else ("requirements", "functions")
    median_weights = {metric: {} for metric in metric_names}
    for member_id in available_member_ids:
        for metric in metric_names:
            historical = recommend_daily_median(team, member_id, phase, metric, as_of)
            if historical and historical.get("median", 0) > 0:
                median_weights[metric][member_id] = float(historical["median"])
    recommendation = recommend_distribution(team, project, phase, adjusted, as_of, path,
        exclude_member_ids={int(absent_member_id)} | unavailable_during_leave,
        metric_weight_overrides=median_weights)
    rec_rows = recommendation["rows"]
    added = defaultdict(lambda: defaultdict(int))
    rationale = {}
    for row in rec_rows:
        member_id, subsystem = int(row["member_id"]), str(row["subsystem"])
        rationale[(member_id, subsystem.casefold())] = row
        for metric in ("tasks", "requirements", "functions"):
            added[(member_id, subsystem.casefold())][metric] += int(row.get(metric) or 0)

    # The approved primary assignment must still add up to the full plan. Preserve completed log ownership,
    # then allocate only the remaining work to people other than the vacationer.
    project_totals = defaultdict(lambda: defaultdict(int))
    for workload in workloads:
        key = str(workload["subsystem"]).casefold()
        project_totals[key]["subsystem"] = str(workload["subsystem"])
        for metric, total_field in (("tasks", "total_tasks"), ("requirements", "total_requirements"),
                                    ("functions", "total_functions")):
            project_totals[key][metric] = int(workload.get(total_field) or 0)
    completed_by_owner = defaultdict(lambda: defaultdict(int))
    for log in logs:
        member_id, subsystem = int(log["member_id"]), str(log["subsystem"]).casefold()
        if phase == "spec":
            completed_by_owner[(member_id, subsystem)]["tasks"] += int(log.get("completed_tasks") or 0)
        else:
            completed_by_owner[(member_id, subsystem)]["requirements"] += int(log.get("completed_requirements") or 0)
            completed_by_owner[(member_id, subsystem)]["functions"] += int(log.get("completed_functions") or 0)
    combined = defaultdict(lambda: defaultdict(int))
    for (member_id, subsystem), metrics in completed_by_owner.items():
        combined[(member_id, subsystem)].update(metrics)
    for (member_id, subsystem), metrics in added.items():
        for metric, amount in metrics.items():
            combined[(member_id, subsystem)][metric] += amount

    names = {int(person["id"]): person["name"] for person in team.unit_members()}
    project_assignments = []
    for (member_id, subsystem), metrics in sorted(combined.items(), key=lambda item: (item[0][1], item[0][0])):
        if member_id not in names:
            overreported.append(f"{subsystem}: 완료 담당자 ID {member_id}가 지정 담당자 명단 밖입니다")
            continue
        project_assignments.append({"member_id": member_id, "subsystem": project_totals[subsystem]["subsystem"],
                                    **{metric: int(metrics.get(metric, 0)) for metric in ("tasks", "functions", "requirements")}})
    replacement_assignments = [
        {"member_id": member_id, "subsystem": project_totals[subsystem]["subsystem"], **dict(metrics)}
        for (member_id, subsystem), metrics in added.items()
        if any(int(metrics.get(metric, 0) or 0) for metric in ("tasks", "requirements", "functions"))
    ]
    rows = []
    rate_history = _rates(team, phase, as_of, set(str(v)[:10] for v in project.get("holidays", []) if v), path)
    commitments = _commitments(phase, path)
    for member in team.unit_members():
        member_id = int(member["id"])
        if member_id == int(absent_member_id):
            continue
        person_rows = [row for row in rec_rows if int(row["member_id"]) == member_id]
        if not person_rows:
            continue
        extra = {metric: sum(int(row.get(metric) or 0) for row in person_rows)
                 for metric in ("tasks", "requirements", "functions")}
        expected_days = 0.0
        missing_rate = False
        for row in person_rows:
            difficulty = max(1, int(next((w.get("difficulty", 1) for w in adjusted if w["subsystem"] == row["subsystem"]), 1)))
            for metric in (("tasks",) if phase == "spec" else ("requirements", "functions")):
                amount = int(row.get(metric) or 0)
                if amount <= 0:
                    continue
                rate, _, _ = _pace(rate_history, member_id, row["subsystem"], metric)
                if rate and rate > 0:
                    expected_days += amount * difficulty / rate
                else:
                    missing_rate = True
        prior_days = 0.0
        for work in commitments:
            if int(work["member_id"]) != member_id:
                continue
            for metric in ("tasks", "requirements", "functions"):
                amount = int(work.get(metric) or 0)
                rate, _, _ = _pace(rate_history, member_id, work["subsystem"], metric)
                if amount and rate and rate > 0:
                    prior_days += amount / rate
                elif amount:
                    missing_rate = True
        holidays = set(str(v)[:10] for v in project.get("holidays", []) if v)
        start = max(as_of, leave_start)
        end = _completion_date(team, member_id, start, due, expected_days + prior_days, holidays) if not missing_rate and due else None
        feasibility = "가능" if end and end <= due else "불가/평가 불가"
        row_reasons = "; ".join(dict.fromkeys(str(row.get("rationale") or "") for row in person_rows if row.get("rationale")))
        if member.get("name") == PM_MEMBER_NAME and extra["tasks"] + extra["requirements"] + extra["functions"]:
            row_reasons = "; ".join(filter(None, (row_reasons, "PM 업무 분담 요청을 반영해 추천 가중치 20% 상향")))
        rows.append({"member_id": member_id, "담당자": member["name"], "직무": member.get("role", ""),
            "기존 업무량": "버전별 담당량 미등록", "추가 배정 일감": extra["tasks"],
            "추가 배정 요구사양서": extra["requirements"], "추가 배정 함수": extra["functions"],
            "예상 완료일": end.isoformat() if end else "실적 속도 부족", "마감 준수 여부": feasibility,
            "추천 근거": row_reasons or "잔여 계획량을 휴가자를 제외한 인원의 가용일·역량·과거 이력으로 분배",
            "예측 신뢰도": "낮음 · 실제 처리 속도 부족" if missing_rate else "보통"})

    assigned_residual = sum(int(row.get("tasks", 0)) + int(row.get("requirements", 0)) + int(row.get("functions", 0))
                            for row in rec_rows)
    allocated_by_metric = {metric: sum(int(row.get(metric) or 0) for row in rec_rows) for metric in metric_keys}
    all_ready = bool(rows) and all(row["마감 준수 여부"] == "가능" and row["예측 신뢰도"] == "보통" for row in rows)
    if overreported:
        summary = "추가 인력 또는 업무 재분배 필요"
        saveable = False
    elif assigned_residual >= total_demand and all_ready:
        summary, saveable = "마감일 내 완료 가능", True
    elif assigned_residual > 0:
        summary, saveable = "추가 인력 또는 업무 재분배 필요", True
    else:
        summary, saveable = "현재 인력으로 마감일 내 완료 어려움", False
    project_assignments.sort(key=lambda row: (str(row["subsystem"]).casefold(), int(row["member_id"])))
    return {**base, "rows": rows, "assignments": replacement_assignments, "project_assignments": project_assignments,
            "total_allocated": assigned_residual, "remaining_unallocated": max(0, total_demand-assigned_residual),
            "allocated_by_metric": allocated_by_metric,
            "remaining_by_metric": {metric: max(0, demand_by_metric[metric] - allocated_by_metric[metric]) for metric in metric_keys},
            "summary": summary, "confidence": "보통" if all_ready else "낮음",
            "saveable": saveable, "distribution_warnings": recommendation.get("warnings", []),
            "message": (f"서브시스템별 개인 담당량이 없어 버전 잔여 작성 일감 {demand_by_metric.get('tasks', 0)}개를 휴가자를 제외해 재분배했습니다."
                        if phase == "spec" else
                        f"서브시스템별 개인 담당량이 없어 잔여 검증 요구사양서 {demand_by_metric.get('requirements', 0)}개와 "
                        f"함수 {demand_by_metric.get('functions', 0)}개를 휴가자를 제외해 각각 재분배했습니다.")}


def scheduled_leave_targets(team, as_of: date | None = None, path=None) -> list[dict]:
    """Find roster vacations overlapping actionable version work windows; preserve uncertainty explicitly."""
    as_of = as_of or date.today()
    allowed_ids = team.unit_pool()
    projects = db.list_version_projects(path)
    targets = []
    for leave in team.leave_records():
        member_id = int(leave["member_id"])
        if member_id not in allowed_ids or leave["end"] < as_of:
            continue
        leave_start = max(as_of, leave["start"])
        leave_end = leave["end"]
        for project in projects:
            if not is_internal_version(project.get("version", "")):
                # T0020–T0700 have read-only Sheets history, not VACON schedule authority.
                continue
            project_id = int(project["id"])
            if project.get("status") == "완료":
                continue
            stage = workflow_stage(project, path)
            for phase, phase_start, phase_due, planned in (
                ("spec", project.get("spec_start"), project.get("spec_due"), stage["spec"][0]),
                ("verify", project.get("verify_start"), project.get("verify_due"),
                 sum(total for total, _ in stage["verify"].values())),
            ):
                if planned <= 0:
                    continue
                is_active = (stage["phase"] == phase)
                pm_review_pending = (stage["spec"][0] > 0 and stage["spec"][1] >= stage["spec"][0]
                                     and project.get("spec_review_status") != "완료")
                # Planned verify dates can overlap spec dates in legacy schedules.
                # The actual workflow is sequential: do not surface a dynamic
                # verification target until the PM has completed spec review.
                is_waiting_for_gate = phase == "verify" and pm_review_pending
                if not is_active and not is_waiting_for_gate:
                    continue
                phase_assignments = db.list_version_assignments(project_id, phase, path)
                phase_logs = db.list_version_worklogs(project_id, phase, path)
                owner_field = "spec_owner_ids" if phase == "spec" else "verify_owner_ids"
                owner_ids = {int(value) for value in project.get(owner_field, []) if value}
                owner_ids.update(int(row["member_id"]) for row in phase_assignments)
                owner_ids.update(int(row["member_id"]) for row in phase_logs)
                if member_id not in owner_ids:
                    continue
                # Version-level verification owners and its active schedule are sufficient to show leave coverage;
                # individual subsystem allocation is optional for recording work.
                if phase_start and phase_due:
                    overlap_start = max(leave_start, phase_start)
                    overlap_end = min(leave_end, phase_due)
                    if overlap_start > overlap_end:
                        continue
                    status = "확인 필요" if is_waiting_for_gate else "대상 업무"
                    if is_waiting_for_gate:
                        spec_done, spec_target = stage["spec"][1], stage["spec"][0]
                        verify_owner_count = len({int(row["member_id"]) for row in phase_assignments})
                        reason = (f"요구사양서 작성 {spec_done}/{spec_target}건 · PM 검토 상태 "
                                  f"'{project.get('spec_review_status', '대기')}'입니다. PM 검토 완료 후 동적 검증 대체 업무를 판단하세요. "
                                  f"(동적 검증 담당 배정 {verify_owner_count}명·실적 {len(phase_logs)}건)")
                    else:
                        reason = "등록된 버전별 업무 기간과 진행 단계가 휴가와 겹칩니다."
                else:
                    # An incomplete schedule is surfaced for PM confirmation, but cannot generate a proposal.
                    if phase_due and phase_due < leave_start:
                        continue
                    overlap_start, overlap_end = leave_start, leave_end
                    status = "확인 필요"
                    reason = "버전별 시작일 또는 마감일이 없어 휴가와 업무 일정이 겹치는지 판단할 수 없습니다. 누락된 일정을 등록하세요."
                phase_label = "일감 작성" if phase == "spec" else "동적 검증"
                targets.append({"id": f"{leave['id']}:{project_id}:{phase}", "leave_id": int(leave["id"]),
                    "member_id": member_id, "member_name": team.name(member_id), "leave_start": leave["start"],
                    "leave_end": leave["end"], "overlap_start": overlap_start, "overlap_end": overlap_end,
                    "leave_type": leave.get("type", ""), "project_id": project_id, "version": project["version"],
                    "phase": phase, "phase_label": phase_label, "status": status, "reason": reason,
                    "label": f"{team.name(member_id)} - {phase_label} ({project['version']}) · {overlap_start}~{overlap_end}"})
    targets.sort(key=lambda row: (row["leave_id"], row["overlap_start"], row["overlap_end"], row["version"], row["phase"]))
    # When registered version windows overlap, use the next version's scheduled start as the handover.
    # If two versions claim the same start point, surface the conflict for PM confirmation.
    by_leave = defaultdict(list)
    for target in targets:
        by_leave[target["leave_id"]].append(target)
    for rows in by_leave.values():
        for previous, following in zip(rows, rows[1:]):
            if previous["overlap_end"] < following["overlap_start"]:
                continue
            # Spec and dynamic verification windows for one version may overlap
            # on the calendar by design. The actual handoff is controlled by
            # spec completion and the PM review gate in workflow_stage().
            if int(previous["project_id"]) == int(following["project_id"]):
                continue
            project = next((p for p in projects if int(p["id"]) == following["project_id"]), {})
            next_phase_start = project.get("spec_start" if following["phase"] == "spec" else "verify_start")
            if next_phase_start and previous["overlap_start"] < next_phase_start:
                previous["overlap_end"] = next_phase_start - timedelta(days=1)
                previous["label"] = (f"{previous['member_name']} - {previous['phase_label']} ({previous['version']}) · "
                                      f"{previous['overlap_start']}~{previous['overlap_end']}")
            else:
                for row in (previous, following):
                    row["status"] = "확인 필요"
                    row["reason"] = "버전별 일정이 겹치거나 전환 시작일이 같아 실제 전환 시점을 PM이 확인해야 합니다."
    return sorted(targets, key=lambda row: (row["overlap_start"], row["version"], row["member_name"].casefold(), row["phase"]))


def _recommend_version_leave_coverage_totals(team, project: dict, absent_member_id: int, leave_start: date,
                                            leave_end: date, as_of: date | None = None, path=None) -> dict:
    """Propose a non-persisted, deadline-aware reallocation of an approved vacationer's outstanding work."""
    as_of = as_of or date.today()
    context = workflow_stage(project, path)
    phase = context.get("phase")
    if phase is None:
        return {"phase": None, "stage": context["stage"], "message": context["message"], "rows": [],
                "summary": "추가 인력 또는 업무 재분배 필요", "confidence": "평가 불가"}
    if int(absent_member_id) not in team.unit_pool():
        return {"phase": phase, "stage": context["stage"], "message": "대상은 지정 단위 검증 담당자 네 명으로 제한됩니다.",
                "rows": [], "summary": "추가 인력 또는 업무 재분배 필요", "confidence": "평가 불가"}
    if leave_end < leave_start:
        return {"phase": phase, "stage": context["stage"], "message": "휴가 기간을 확인하세요.", "rows": [],
                "summary": "추가 인력 또는 업무 재분배 필요", "confidence": "평가 불가"}
    due = project.get("spec_due" if phase == "spec" else "verify_due")
    if due is None:
        return {"phase": phase, "stage": context["stage"], "message": "업무 마감일이 등록되지 않아 처리 가능성을 계산할 수 없습니다.",
                "rows": [], "summary": "추가 인력 또는 업무 재분배 필요", "confidence": "평가 불가"}
    if due < max(as_of, leave_start):
        return {"phase": phase, "stage": context["stage"], "message": "업무 마감일이 대체 작업 시작일보다 빠릅니다.",
                "rows": [], "summary": "현재 인력으로 마감일 내 완료 어려움", "confidence": "평가 불가"}
    assignments = db.list_version_assignments(project["id"], phase, path)
    target_assignments = [row for row in assignments if int(row["member_id"]) == int(absent_member_id)]
    if not target_assignments:
        # The UI's vacation target is based on the member's version-level ownership
        # and the phase schedule. A per-person assignment is optional, so its absence
        # must not hide the recommendation. Rebuild the phase allocation from the
        # remaining project plan, exclude the vacationer, and let the PM approve the
        # resulting full replacement plan.
        return _recommend_version_remaining_rebalance(team, project, phase, absent_member_id,
            leave_start, leave_end, as_of, context, path)

    holidays = set(str(value)[:10] for value in project.get("holidays", []) if value)
    start = max(as_of, leave_start)
    period_days = workdays(start, due)
    period_workdays = sum(1 for day in period_days if day.isoformat() not in holidays)
    if period_workdays <= 0:
        return {"phase": phase, "stage": context["stage"], "message": "휴가 시작일부터 마감일까지 주말·등록 휴일을 제외한 작업일이 없습니다.",
                "rows": [], "summary": "현재 인력으로 마감일 내 완료 어려움", "confidence": "평가 불가"}

    current_logs = _log_totals(phase, path)
    approved_backup = db.list_version_backup_assignments(project["id"], phase, path)
    phase_logs = db.list_version_worklogs(project["id"], phase, path)
    other_approved_allocated = defaultdict(lambda: defaultdict(int))
    current_window_completed = defaultdict(lambda: defaultdict(int))
    for row in approved_backup:
        if int(row["absent_member_id"]) != int(absent_member_id):
            continue
        subsystem = str(row["subsystem"]).strip()
        key = subsystem.casefold()
        backup_id = int(row["backup_member_id"])
        is_current_window = row["leave_start"] == leave_start and row["leave_end"] == leave_end
        worklog_start = row["leave_start"]
        worklog_end = min(row["leave_end"], as_of)
        logged = defaultdict(int)
        if worklog_start <= worklog_end:
            for log in phase_logs:
                if (int(log["member_id"]) != backup_id
                        or str(log["subsystem"]).strip().casefold() != key
                        or not worklog_start <= log["date"] <= worklog_end):
                    continue
                for metric, field in (("tasks", "completed_tasks"),
                                      ("requirements", "completed_requirements"),
                                      ("functions", "completed_functions")):
                    logged[metric] += int(log.get(field) or 0)
        for metric, column in (("tasks", "allocated_tasks"), ("functions", "allocated_functions"),
                               ("requirements", "allocated_requirements")):
            quantity = int(row.get(column) or 0)
            done = min(quantity, logged[metric])
            if is_current_window:
                current_window_completed[(backup_id, key)][metric] += done
            else:
                other_approved_allocated[key][metric] += quantity
    demands, total_demands = [], []
    for assignment in target_assignments:
        subsystem = str(assignment["subsystem"] or "").strip()
        key = (int(project["id"]), subsystem.casefold(), int(absent_member_id))
        total_demand = {"subsystem": subsystem}
        if phase == "spec":
            metric, field = "tasks", "allocated_tasks"
            original = max(0, int(assignment.get(field) or 0) - int(current_logs[key].get(metric, 0)))
            total_demand[metric] = max(0, original - other_approved_allocated[subsystem.casefold()][metric]
                                       - sum(values[metric] for (member, sub), values in current_window_completed.items()
                                             if sub == subsystem.casefold()))
        else:
            for metric, field in (("requirements", "allocated_requirements"),
                                  ("functions", "allocated_functions")):
                original = max(0, int(assignment.get(field) or 0)
                               - int(current_logs[key].get(metric, 0)))
                completed_here = sum(values[metric] for (member, sub), values in current_window_completed.items()
                                     if sub == subsystem.casefold())
                total_demand[metric] = max(0, original - other_approved_allocated[subsystem.casefold()][metric]
                                           - completed_here)
        if any(int(v or 0) for k, v in total_demand.items() if k != "subsystem"):
            total_demands.append(total_demand)
            demands.append(dict(total_demand))
    if not total_demands:
        # The vacationer's personal allocation may already be complete while
        # other work in the active version remains. In that case, redistribute
        # the version remainder across available staff instead of reporting a
        # false zero-work state.
        return _recommend_version_remaining_rebalance(team, project, phase, absent_member_id,
            leave_start, leave_end, as_of, context, path)

    phase_rates = _rates(team, phase, as_of, holidays, path)
    phase_experience = _subsystem_experience(phase, team, path)
    rotation = _subsystem_rotation_history(team, project["version"], path)
    existing = _commitments(phase, path)
    unavailable_during_leave = {int(row["member_id"]) for row in team.leave_records()
                                if row["start"] <= leave_end and leave_start <= row["end"]}
    candidate_ids = sorted(team.unit_pool() - {int(absent_member_id)} - unavailable_during_leave)
    if not candidate_ids:
        remaining_by_metric = {metric: sum(int(item.get(metric, 0) or 0) for item in demands)
                               for metric in (("tasks",) if phase == "spec" else ("requirements", "functions"))}
        return {"phase": phase, "stage": context["stage"], "message": "휴가 기간에 업무를 맡을 수 있는 대체 담당자가 없습니다.",
                "rows": [], "summary": "현재 인력으로 마감일 내 완료 어려움", "confidence": "평가 불가",
                "due": due, "demand_by_metric": remaining_by_metric, "remaining_by_metric": remaining_by_metric,
                "remaining_workdays": period_workdays}
    metric_names = ("tasks",) if phase == "spec" else ("requirements", "functions")
    daily_recommendations = {
        mid: {metric: recommend_daily_median(team, mid, phase, metric, as_of) for metric in metric_names}
        for mid in candidate_ids
    }
    metric_quota_remaining = {}
    for metric in metric_names:
        total = sum(int(item.get(metric, 0) or 0) for item in demands)
        weights = {mid: float(daily_recommendations[mid][metric]["median"])
                   for mid in candidate_ids if daily_recommendations[mid][metric]
                   and float(daily_recommendations[mid][metric].get("median", 0) or 0) > 0}
        metric_quota_remaining[metric] = (_split_units(total, weights)
                                          if total > 0 and weights else {mid: 0 for mid in candidate_ids})
    due_start = max(as_of, leave_start)
    base_rows = {}
    for mid in candidate_ids:
        member = next(row for row in team.unit_members() if int(row["id"]) == mid)
        available_days = _capacity_days(team, mid, due_start, due, holidays)
        existing_by_metric = defaultdict(int)
        effort_days = 0.0
        unknown_existing = []
        for work in existing:
            if int(work["member_id"]) != mid:
                continue
            for metric in ("tasks", "requirements", "functions"):
                amount = int(work.get(metric, 0) or 0)
                if not amount:
                    continue
                existing_by_metric[metric] += amount
                rate_metric = "tasks" if metric == "tasks" else metric
                rate, _, _ = _pace(phase_rates, mid, work["subsystem"], rate_metric)
                if rate and rate > 0:
                    effort_days += amount / rate
                else:
                    unknown_existing.append(f"{work['version']} {work['subsystem']} {metric}")
        base_rows[mid] = {"member_id": mid, "name": member["name"], "role": member.get("role", ""),
            "workload_pct": int(member.get("workload") or 0), "available_days": available_days,
            "existing": dict(existing_by_metric), "existing_days": effort_days,
            "unknown_existing": unknown_existing,
            "free_days": max(0.0, available_days * max(0.0, 1 - min(100, int(member.get("workload") or 0)) / 100) - effort_days)
                        if not unknown_existing else 0.0,
            "added": defaultdict(int), "reasons": [], "confidence": "높음"}

    allocation_details = defaultdict(lambda: defaultdict(int))
    for demand in demands:
        subsystem = demand["subsystem"]
        subsystem_key = subsystem.casefold()
        for metric in ("tasks", "requirements", "functions"):
            amount = int(demand.get(metric, 0) or 0)
            if not amount:
                continue
            weights = {}
            recommendation_rates = {}
            for mid in candidate_ids:
                row = base_rows[mid]
                daily_rec = daily_recommendations.get(mid, {}).get(metric)
                if not daily_rec or float(daily_rec.get("median", 0) or 0) <= 0:
                    continue
                rate = float(daily_rec["median"])
                recommendation_rates[mid] = rate
                exp = phase_experience.get((mid, subsystem_key), {"versions": 0, "records": 0, "days": 0})
                version_hist = rotation.get((mid, subsystem_key), {})
                repeats = int(version_hist.get("verify_consecutive_versions" if phase == "verify" else "spec_consecutive_versions", 0))
                weights[mid] = rate
                phase_name = "동적 검증" if phase == "verify" else "요구사양서 작성"
                row["reasons"].append(f"{subsystem} {phase_name} 이력 {exp.get('versions', 0)}개 버전, 최근 반복 담당 {repeats}개 버전")
                row["reasons"].append(f"과거 일일 처리량 중앙값 {rate:g}개를 배분 비율에 반영")
                if row["free_days"] <= 0:
                    row["reasons"].append("기존 업무 부담이 높아 마감 가능성을 별도로 확인할 필요")
                if row["unknown_existing"]:
                    row["reasons"].append("기존 업무 일부의 처리 이력이 없어 마감 예측 신뢰도가 낮음")
            allocated = _split_units_capped(amount, weights, metric_quota_remaining[metric])
            for mid, quantity in allocated.items():
                metric_quota_remaining[metric][mid] -= quantity
            for mid, quantity in allocated.items():
                if quantity <= 0:
                    continue
                rate = recommendation_rates.get(mid)
                base_rows[mid]["added"][metric] += quantity
                allocation_details[(mid, subsystem)][metric] += quantity
                recommendation_rate = recommendation_rates.get(mid, rate)
                if recommendation_rate and recommendation_rate > 0:
                    base_rows[mid]["free_days"] = max(
                        0.0, base_rows[mid]["free_days"] - quantity / recommendation_rate
                    )

    active = [row for row in base_rows.values()]
    output = []
    for row in active:
        total_existing = row["existing"]
        added = dict(row["added"])
        expected_days = row["existing_days"]
        missing = list(row["unknown_existing"])
        for (member_id, subsystem), metrics in allocation_details.items():
            if member_id != row["member_id"]:
                continue
            for metric in ("tasks", "requirements", "functions"):
                quantity = int(metrics.get(metric, 0) or 0)
                if not quantity:
                    continue
                rate_metric = "tasks" if phase == "spec" else metric
                rate, _, _ = _pace(phase_rates, member_id, subsystem, rate_metric)
                if rate and rate > 0:
                    expected_days += quantity / rate
                else:
                    missing.append(f"{subsystem} {metric} 처리 속도")
        due_date = _completion_date(team, row["member_id"], due_start, due, expected_days, holidays) if not missing else None
        histories = [_pace(phase_rates, row["member_id"], d["subsystem"],
                           "tasks" if phase == "spec" else "requirements")[1] for d in total_demands]
        histories = [h for h in histories if h]
        used_general_history = any(
            _pace(phase_rates, row["member_id"], d["subsystem"], metric)[2]
            for d in total_demands
            for metric in (("tasks",) if phase == "spec" else ("requirements", "functions"))
            if int(d.get(metric, 0) or 0)
        )
        proficiency = (histories[0]["level"] if histories else "평가 데이터 부족")
        if missing:
            confidence = "낮음 · 실제 처리 이력 부족"
        elif used_general_history and recommend_daily_count(team, row["member_id"], phase, None, as_of):
            confidence = "보통 · 개인 전체 실적 참고"
        elif used_general_history:
            confidence = "낮음 · 개인 전체 업무 처리량 참고"
        elif histories and any(h["level"] == "평가 데이터 부족" for h in histories):
            confidence = "낮음 · 이력 기준 미달"
        else:
            confidence = "보통" if histories else "낮음 · 해당 서브시스템 데이터 없음"
        final_tasks = int(total_existing.get("tasks", 0)) + int(added.get("tasks", 0))
        final_req = int(total_existing.get("requirements", 0)) + int(added.get("requirements", 0))
        final_func = int(total_existing.get("functions", 0)) + int(added.get("functions", 0))
        row_ready = due_date is not None and due_date <= due
        daily_rates = []
        if phase == "spec":
            rec = recommend_daily_count(team, row["member_id"], "spec", None, as_of)
            if rec:
                daily_rates.append(f"전체 실적 일감 {rec['value']}개/일 · {rec['basis']}")
        else:
            req_rec = recommend_daily_count(team, row["member_id"], "verify", None, as_of)
            if req_rec:
                daily_rates.append(f"전체 실적 요구사양서 {req_rec['value']}개/일 · {req_rec['basis']}")
            for demand in total_demands:
                label, metric = "함수", "functions"
                rate, history, used_person_wide = _pace(phase_rates, row["member_id"], demand["subsystem"], metric)
                if rate:
                    basis = f"(전체 실적·{history.get('versions', 0)}개 버전)" if used_person_wide else ""
                    daily_rates.append(f"{demand['subsystem']} {label} {rate:.2f}/일{basis}")
        output.append({"member_id": row["member_id"], "담당자": row["name"], "직무": row["role"],
            "기존 업무량": (f"요구사양서 {int(total_existing.get('requirements', 0))}개 · 함수 {int(total_existing.get('functions', 0))}개"
                          if phase == "verify" else f"일감 {int(total_existing.get('tasks', 0))}개"),
            "기존 요구사양서 잔여": int(total_existing.get("requirements", 0)),
            "기존 함수 잔여": int(total_existing.get("functions", 0)),
            "추가 배정 요구사양서": int(added.get("requirements", 0)), "추가 배정 함수": int(added.get("functions", 0)),
            "추가 배정 일감": int(added.get("tasks", 0)),
            "최종 업무량": f"요구사양서 {final_req}개 · 함수 {final_func}개" if phase == "verify" else f"일감 {final_tasks}개",
            "예상 일일 처리량": " · ".join(daily_rates) or "데이터 없음",
            "실제 작업 가능 일수": round(row["available_days"], 1),
            "예상 완료일": due_date.isoformat() if due_date else "계산 불가",
            "마감 준수 여부": "가능" if row_ready else "불가/평가 불가",
            ("동적 검증 숙련도" if phase == "verify" else "요구사양서 작성 숙련도"): proficiency,
            "현재 승인 업무 일수": round(row["existing_days"], 2),
            "예측 신뢰도": confidence, "추천 근거": "; ".join(dict.fromkeys(row["reasons"])) or "해당 서브시스템 실적 속도를 확인할 수 없어 추가 배정을 하지 않았습니다."})

    demanded = sum(int(d.get(k, 0) or 0) for d in total_demands for k in ("tasks", "requirements", "functions"))
    assigned = sum(int(r.get("추가 배정 일감", 0) or 0) + int(r.get("추가 배정 요구사양서", 0) or 0)
                   + int(r.get("추가 배정 함수", 0) or 0) for r in output)
    predictions = [r for r in output if int(r.get("추가 배정 일감", 0) or 0) + int(r.get("추가 배정 요구사양서", 0) or 0)
                   + int(r.get("추가 배정 함수", 0) or 0)]
    high_confidence = bool(predictions) and all(r["예측 신뢰도"] == "보통" and r["마감 준수 여부"] == "가능" for r in predictions)
    any_capacity = any(row["free_days"] > 0 for row in base_rows.values())
    if assigned >= demanded and high_confidence:
        summary = "마감일 내 완료 가능"
    elif any_capacity and assigned > 0:
        summary = "추가 인력 또는 업무 재분배 필요"
    else:
        summary = "현재 인력으로 마감일 내 완료 어려움"
    if phase == "spec":
        message = f"휴가자 미완료 작성 일감 {sum(int(d.get('tasks', 0) or 0) for d in total_demands)}개 중 {sum(int(r.get('추가 배정 일감', 0) or 0) for r in output)}개가 승인 배정되었거나 새로 분배됩니다."
    else:
        message = (f"휴가자 미완료 검증 요구사양서 {sum(int(d.get('requirements', 0) or 0) for d in total_demands)}개와 "
                   f"함수 {sum(int(d.get('functions', 0) or 0) for d in total_demands)}개 중 승인 배정량과 새 분배량을 표시합니다.")
    replacement_details = defaultdict(lambda: defaultdict(int))
    for (mid, subsystem_key), metrics in current_window_completed.items():
        subsystem = next((row["subsystem"] for row in approved_backup
                          if int(row["absent_member_id"]) == int(absent_member_id)
                          and int(row["backup_member_id"]) == mid
                          and row["leave_start"] == leave_start and row["leave_end"] == leave_end
                          and str(row["subsystem"]).casefold() == subsystem_key), subsystem_key)
        for metric, quantity in metrics.items():
            replacement_details[(mid, subsystem)][metric] += quantity
    for (mid, subsystem), metrics in allocation_details.items():
        for metric, quantity in metrics.items():
            replacement_details[(mid, subsystem)][metric] += quantity

    return {"phase": phase, "stage": context["stage"], "stage_message": context["message"],
            "absent_member_id": int(absent_member_id), "absent_member": team.name(absent_member_id),
            "leave_start": leave_start, "leave_end": leave_end, "due": due,
            "period_workdays": period_workdays, "demands": total_demands, "new_demands": demands, "rows": output,
            "summary": summary, "confidence": "보통" if high_confidence else "낮음",
            "total_demand": demanded, "total_allocated": assigned,
            "demand_by_metric": {metric: sum(int(d.get(metric, 0) or 0) for d in total_demands)
                                 for metric in (("tasks",) if phase == "spec" else ("requirements", "functions"))},
            "allocated_by_metric": {
                "tasks": sum(int(r.get("추가 배정 일감", 0) or 0) for r in output),
                "requirements": sum(int(r.get("추가 배정 요구사양서", 0) or 0) for r in output),
                "functions": sum(int(r.get("추가 배정 함수", 0) or 0) for r in output),
            },
            "assignments": [{"member_id": mid, "subsystem": subsystem, **dict(metrics)}
                            for (mid, subsystem), metrics in sorted(replacement_details.items(), key=lambda item: (item[0][1].casefold(), item[0][0]))],
            "coverage_assignments": [{"member_id": mid, "subsystem": subsystem, **dict(metrics)}
                                     for (mid, subsystem), metrics in sorted(allocation_details.items(), key=lambda item: (item[0][1].casefold(), item[0][0]))],
            "remaining_unallocated": max(0, demanded-assigned),
            "remaining_by_metric": {
                "tasks": max(0, sum(int(d.get("tasks", 0) or 0) for d in total_demands)
                               - sum(int(r.get("추가 배정 일감", 0) or 0) for r in output)),
                "requirements": max(0, sum(int(d.get("requirements", 0) or 0) for d in total_demands)
                                       - sum(int(r.get("추가 배정 요구사양서", 0) or 0) for r in output)),
                "functions": max(0, sum(int(d.get("functions", 0) or 0) for d in total_demands)
                                    - sum(int(r.get("추가 배정 함수", 0) or 0) for r in output)),
            },
            "message": "완료" if not total_demands else message}


def _dailyize_coverage_result(team, project: dict, result: dict, leave_start: date, as_of: date, path=None) -> dict:
    """Add deadline-based daily targets without mixing phase metrics in the UI."""
    phase = result.get("phase")
    due = result.get("due") or project.get("spec_due" if phase == "spec" else "verify_due")
    result["due"] = due
    start = max(as_of, leave_start)
    holidays = {str(value)[:10] for value in project.get("holidays", []) if value}
    calendar_days = [day for day in workdays(start, due)] if phase and due and due >= start else []
    team_days = sum(day.isoformat() not in holidays for day in calendar_days)
    metrics = ("tasks",) if phase == "spec" else (("requirements", "functions") if phase == "verify" else ())
    demand = result.get("demand_by_metric", {})
    result["remaining_workdays"] = team_days
    result["team_daily_required"] = {
        metric: (float(demand.get(metric, 0) or 0) / team_days if team_days else None)
        for metric in metrics
    }
    if not phase or not result.get("rows"):
        if phase and any(int(demand.get(metric, 0) or 0) for metric in metrics):
            if due is None:
                result["summary"] = "예측 불가"
                result["confidence"] = "예측 불가"
            elif team_days == 0:
                result["summary"] = "현재 인력으로 마감 내 완료 어려움"
                result["confidence"] = "보통"
            else:
                result["summary"] = "예측 불가"
                result["confidence"] = "예측 불가"
        return result

    # Use only the proposed replacement quantities for daily targets. In project
    # rebalance mode, project_assignments also contains already completed work so
    # it can be saved as a complete version plan; counting it here inflated goals.
    allocations = result.get("coverage_assignments", result.get("assignments", []))

    by_person = defaultdict(lambda: defaultdict(int))
    for allocation in allocations:
        mid = int(allocation["member_id"])
        for metric in metrics:
            by_person[mid][metric] += int(allocation.get(metric, 0) or 0)

    rate_history = _rates(team, phase, as_of, holidays, path) if phase else {}
    experience = _subsystem_experience(phase, team, path) if phase else {}
    subsystem_difficulty = {str(item["subsystem"]).casefold(): max(1, int(item.get("difficulty") or 1))
                            for item in db.list_version_workloads(project["id"], phase, path)} if phase else {}
    assignments_by_person = defaultdict(list)
    for row in allocations:
        assignments_by_person[int(row["member_id"])].append(row)
    member_by_id = {int(row["id"]): row for row in team.unit_members()}
    commitments = _commitments(phase, path) if phase else []
    capacity_shortfall = False
    allocated_by_metric = {metric: sum(int(values.get(metric, 0)) for values in by_person.values()) for metric in metrics}
    receiving_ids = {mid for mid, values in by_person.items() if any(int(values.get(metric, 0)) for metric in metrics)}
    pace_missing = False
    if not receiving_ids and any(int(demand.get(metric, 0) or 0) for metric in metrics):
        pace_missing = result.get("confidence") in ("낮음", "평가 불가", "예측 불가")
    for row in result["rows"]:
        mid = int(row["member_id"])
        member = member_by_id.get(mid, {})
        available_days = _capacity_days(team, mid, start, due, holidays) if due and due >= start else 0.0
        load_factor = max(0.0, 1.0 - min(100, max(0, int(member.get("workload") or 0))) / 100.0)
        scheduled_days = available_days * load_factor
        committed_effort = 0.0
        unknown_commitments = False
        for item in commitments:
            if int(item["member_id"]) != mid:
                continue
            for metric in metrics:
                amount = int(item.get(metric, 0) or 0)
                rate, _, _ = _pace(rate_history, mid, item["subsystem"], metric)
                if metric == ("tasks" if phase == "spec" else "requirements"):
                    rec = recommend_daily_count(team, mid, phase, None, as_of)
                    if rec:
                        rate = float(rec["value"])
                if amount and rate and rate > 0:
                    committed_effort += amount / rate
                elif amount:
                    unknown_commitments = True
        effective_days = max(0.0, scheduled_days - committed_effort)
        row["remaining_workdays"] = round(available_days, 1)
        row["effective_workdays"] = round(effective_days, 1) if not unknown_commitments else None
        main_metric = "tasks" if phase == "spec" else "requirements" if phase == "verify" else None
        historical_daily = recommend_daily_count(team, mid, phase, None, as_of) if main_metric else None
        assigned_main = int(by_person[mid].get(main_metric, 0) or 0) if main_metric else 0
        if historical_daily and effective_days > 0 and assigned_main > historical_daily["value"] * effective_days:
            capacity_shortfall = True
        daily = {}
        for metric in metrics:
            amount = int(by_person[mid].get(metric, 0))
            # Keep each person's displayed daily share on the same project-day
            # denominator so the team member targets add up to the team daily need.
            daily[metric] = (amount / team_days if team_days > 0 else None) if amount else 0.0
        row["daily_targets"] = daily
        if phase == "spec":
            task_recommendation = recommend_daily_count(team, mid, "spec", None, as_of)
            if task_recommendation:
                row["일감 추천 근거"] = (f"과거 처리량 중앙값 {task_recommendation['median']:g}개/일을 "
                                         "배분 가중치로 반영 · 표시 목표는 잔여 업무 배정량 기준")
            row["하루 작성 목표"] = daily.get("tasks")
        else:
            # Historical throughput informs allocation and feasibility, but the
            # displayed goal is each person's share of the team demand per workday.
            req_recommendation = recommend_daily_count(team, mid, "verify", None, as_of)
            if req_recommendation:
                row["검증 업무 기록과 같은 요구사양서 추천 기준"] = (
                    f"과거 처리량 중앙값 {req_recommendation['median']:g}개/일을 "
                    "배분 가중치로 반영 · 표시 목표는 잔여 업무 배정량 기준")
            row["하루 검증 요구사양서 목표"] = daily.get("requirements")
            row["하루 검증 함수 목표"] = daily.get("functions")

        required_effort = committed_effort
        has_personal_history = False
        for allocation in assignments_by_person.get(mid, []):
            subsystem = allocation["subsystem"]
            subsystem_key = str(subsystem).casefold()
            hist = rate_history.get((mid, subsystem_key), {}) or rate_history.get((mid, "*"), {})
            if hist:
                has_personal_history = True
            for metric in metrics:
                amount = int(allocation.get(metric, 0) or 0)
                if not amount:
                    continue
                rate, _, _ = _pace(rate_history, mid, subsystem, metric)
                if metric == main_metric and historical_daily:
                    rate = float(historical_daily["value"])
                if rate and rate > 0:
                    required_effort += amount * subsystem_difficulty.get(subsystem_key, 1) / rate
                else:
                    pace_missing = True
        if unknown_commitments or (any(by_person[mid].values()) and not has_personal_history and not historical_daily):
            pace_missing = True
        elif required_effort > scheduled_days + 1e-9:
            capacity_shortfall = True

    for metric, output_key in (("tasks", "하루 작성 목표"),
                               ("requirements", "하루 검증 요구사양서 목표"),
                               ("functions", "하루 검증 함수 목표")):
        if metric not in metrics:
            continue
        exact = [(row, row.get("daily_targets", {}).get(metric)) for row in result["rows"]]
        if team_days <= 0 and any(value for _, value in exact):
            for row, value in exact:
                row[output_key] = None if value else 0
            continue
        floors = {int(row["member_id"]): int(value or 0) for row, value in exact}
        target_total = int(sum(float(value or 0) for _, value in exact) + 0.5)
        remainder = max(0, target_total - sum(floors.values()))
        ordered = sorted(exact, key=lambda item: (
            -(float(item[1] or 0) - int(float(item[1] or 0))), int(item[0]["member_id"])))
        add_one = {int(row["member_id"]) for row, _ in ordered[:remainder]}
        for row, value in exact:
            row[output_key] = (floors[int(row["member_id"])] + (1 if int(row["member_id"]) in add_one else 0))

    result["allocated_by_metric"] = allocated_by_metric
    result["remaining_by_metric"] = {metric: max(0, int(demand.get(metric, 0) or 0) - allocated_by_metric[metric])
                                     for metric in metrics}
    result["team_daily_required"] = {
        metric: (float(demand.get(metric, 0) or 0) / team_days if team_days else None)
        for metric in metrics
    }
    if not any(int(demand.get(metric, 0) or 0) for metric in metrics):
        result["summary"] = "대체 분배할 잔여 업무 없음"
        result["confidence"] = "해당 없음"
    elif team_days <= 0:
        result["summary"] = "현재 인력으로 마감 내 완료 어려움"
        result["confidence"] = "보통"
    elif pace_missing:
        result["summary"] = "예측 불가"
        result["confidence"] = "예측 불가"
    elif any(result["remaining_by_metric"].values()) or capacity_shortfall:
        result["summary"] = "현재 인력으로 마감 내 완료 어려움"
        result["confidence"] = "보통"
    else:
        result["summary"] = "마감 내 완료 가능"
        result["confidence"] = "보통"
    return result


def recommend_version_leave_coverage(team, project: dict, absent_member_id: int, leave_start: date,
                                    leave_end: date, as_of: date | None = None, path=None) -> dict:
    """Return workload distribution as daily goals for the actual remaining workdays."""
    as_of = as_of or date.today()
    result = _recommend_version_leave_coverage_totals(team, project, absent_member_id,
        leave_start, leave_end, as_of, path)
    return _dailyize_coverage_result(team, project, result, leave_start, as_of, path)


def _split(total: int, weights: dict[int, float]) -> dict[int, int]:
    if total <= 0 or not weights:
        return {mid: 0 for mid in weights}
    denominator = sum(max(0.0, float(weight)) for weight in weights.values())
    if denominator <= 0:
        return {mid: 0 for mid in weights}
    raw = {mid: total * max(0.0, float(weight)) / denominator for mid, weight in weights.items()}
    output = {mid: int(value) for mid, value in raw.items()}
    for mid in sorted(raw, key=lambda x: (-(raw[x] - output[x]), x))[:total-sum(output.values())]:
        output[mid] += 1
    return output
