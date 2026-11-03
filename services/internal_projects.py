"""VACON 자체 DB로 관리하는 내부 버전 업무 및 배정 추천 로직."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

from data import db
from .risk import workdays


def is_internal_version(version: str) -> bool:
    """T0800+ versions use VACON DB; lower numeric T versions use legacy Sheets."""
    match = re.fullmatch(r"T\s*(\d+)", str(version or "").strip(), re.IGNORECASE)
    if not match:
        return bool(str(version or "").strip())
    number = int(match.group(1))
    return number >= 800


def is_legacy_sheet_version(version: str) -> bool:
    match = re.fullmatch(r"T\s*(\d+)", str(version or "").strip(), re.IGNORECASE)
    if not match:
        return False
    number = int(match.group(1))
    return number < 800


def internal_projects(path=None) -> list[dict]:
    return [p for p in db.list_version_projects(path) if is_internal_version(p["version"])]


def dashboard(project: dict, phase: str, workloads: list[dict], logs: list[dict], today: date) -> dict:
    """저장된 업무량과 해당 업무 테이블 기록만으로 진척 지표를 계산한다."""
    if phase not in ("spec", "verify"):
        raise ValueError("phase는 spec 또는 verify여야 합니다.")
    due = project["spec_due" if phase == "spec" else "verify_due"]
    remaining_days = max(0, (due - today).days) if due else None
    by_subsystem = {}
    for workload in workloads:
        item = dict(workload)
        item.update({"completed_tasks": 0, "new_requirements": 0, "modified_requirements": 0,
                     "deleted_requirements": 0, "completed_functions": 0, "completed_requirements": 0,
                     "defects": 0, "tc_executed": 0, "tc_created": 0, "tc_modified": 0, "tc_deleted": 0})
        by_subsystem[int(item["subsystem_id"])] = item
    by_member = {}
    for record in logs:
        sid = int(record["subsystem_id"])
        if sid not in by_subsystem:
            continue
        for metric in (db.INTERNAL_LOG_FIELDS[phase]):
            value = int(record.get(metric) or 0)
            by_subsystem[sid][metric] += value
            key = (int(record["member_id"]), str(record["member_name"]))
            member = by_member.setdefault(key, {m: 0 for m in db.INTERNAL_LOG_FIELDS[phase]})
            member[metric] += value
    for item in by_subsystem.values():
        if phase == "spec":
            item["total"] = int(item["total_tasks"])
            item["done"] = item["completed_tasks"]
            item["remaining"] = max(0, item["total"] - item["done"])
            item["progress"] = min(1.0, item["done"] / item["total"]) if item["total"] else 0.0
        else:
            for metric, field in (("functions", "total_functions"), ("requirements", "total_requirements")):
                total, done = int(item[field]), int(item[f"completed_{metric}"])
                item[f"{metric}_remaining"] = max(0, total - done)
                item[f"{metric}_progress"] = min(1.0, done / total) if total else 0.0
    totals = {}
    for metric in db.INTERNAL_LOG_FIELDS[phase]:
        totals[metric] = sum(int(item.get(metric) or 0) for item in by_subsystem.values())
    if phase == "spec":
        total = sum(int(w["total_tasks"]) for w in workloads)
        done = totals["completed_tasks"]
        totals.update({"total_tasks": total, "remaining_tasks": max(0, total-done),
                       "progress": min(1.0, done/total) if total else 0.0})
    else:
        funcs = sum(int(w["total_functions"]) for w in workloads)
        reqs = sum(int(w["total_requirements"]) for w in workloads)
        done_f, done_r = totals["completed_functions"], totals["completed_requirements"]
        totals.update({"total_functions": funcs, "remaining_functions": max(0, funcs-done_f),
                       "function_progress": min(1.0, done_f/funcs) if funcs else 0.0,
                       "total_requirements": reqs, "remaining_requirements": max(0, reqs-done_r),
                       "requirement_progress": min(1.0, done_r/reqs) if reqs else 0.0})
    member_rows = [{"member_id": mid, "담당자": name, **values}
                   for (mid, name), values in sorted(by_member.items(), key=lambda item: item[0][1].casefold())]
    return {"phase": phase, "due": due, "remaining_days": remaining_days,
            "subsystems": sorted(by_subsystem.values(), key=lambda item: item["subsystem"].casefold()),
            "members": member_rows, "totals": totals}


def _effective_days(team, member_id: int, start: date, end: date) -> float:
    capacity = 0.0
    leaves = team.leave_records()
    for day in workdays(start, end):
        records = [lv for lv in leaves if int(lv["member_id"]) == member_id and lv["start"] <= day <= lv["end"]]
        if any(lv["type"] != "반차" for lv in records):
            continue
        capacity += 0.5 if records else 1.0
    return capacity


def _history_rates(phase: str, team, path=None) -> dict[int, dict[str, float]]:
    """현재 배분과 분리된 과거 시트와 T0800 이상 내부 이력을 참고한다."""
    records = db.list_all_internal_worklogs(phase, path)
    metrics = ("completed_tasks",) if phase == "spec" else ("completed_functions", "completed_requirements")
    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    active: dict[int, set[date]] = defaultdict(set)
    for row in records:
        if not is_internal_version(row.get("version", "")):
            continue
        mid = int(row["member_id"])
        if row.get("date"):
            active[mid].add(row["date"])
        for metric in metrics:
            totals[mid][metric] += float(row.get(metric) or 0)
    if len(team.logs):
        legacy_track = "req" if phase == "spec" else "dynamic"
        for row in team.logs.to_dict("records"):
            if row.get("track") != legacy_track or not is_legacy_sheet_version(row.get("version", "")):
                continue
            mid = int(row["member_id"])
            if row.get("date"):
                active[mid].add(row["date"])
            if phase == "spec":
                totals[mid]["completed_tasks"] += float(row.get("issue") or 0)
            else:
                totals[mid]["completed_functions"] += float(row.get("func") or 0)
                totals[mid]["completed_requirements"] += float(row.get("req") or 0)
    return {mid: {metric: totals[mid][metric] / len(days) for metric in metrics}
            for mid, days in active.items() if days}


def _subsystem_experience(phase: str, team, path=None) -> dict[tuple[int, str], dict[str, int]]:
    """Count actual dated/versioned records per person and subsystem; never infer skill from volume totals."""
    track = "req" if phase == "spec" else "dynamic"
    rows = []
    if len(team.logs):
        rows.extend(r for r in team.logs.to_dict("records")
                    if r.get("track") == track and is_legacy_sheet_version(r.get("version", "")))
    rows.extend(r for r in db.list_all_internal_worklogs(phase, path)
                if is_internal_version(r.get("version", "")))
    result: dict[tuple[int, str], dict] = defaultdict(lambda: {"versions": set(), "days": set(), "records": set(),
        "spec_done": 0.0, "verify_req_done": 0.0, "verify_func_done": 0.0})
    for index, row in enumerate(rows):
        subsystem = str(row.get("subsystem") or "").strip().casefold()
        if not subsystem:
            continue
        member_id = int(row["member_id"])
        key = (member_id, subsystem)
        version = str(row.get("version") or "").strip()
        if version:
            result[key]["versions"].add(version.casefold())
        if row.get("date"):
            result[key]["days"].add(row["date"])
        result[key]["records"].add(index)
        metrics = (("spec_done", ("issue", "completed_tasks")),) if phase == "spec" else (
            ("verify_req_done", ("req", "completed_requirements")),
            ("verify_func_done", ("func", "completed_functions")))
        for metric, names in metrics:
            raw = next((row.get(name) for name in names if row.get(name) is not None), 0)
            try:
                amount = float(raw)
                if amount == amount: result[key][metric] += amount
            except (TypeError, ValueError):
                pass
    return {key: {"versions": len(value["versions"]), "days": len(value["days"]), "records": len(value["records"]),
                  "spec_done": value["spec_done"], "verify_req_done": value["verify_req_done"],
                  "verify_func_done": value["verify_func_done"]}
            for key, value in result.items()}


def _subsystem_rotation_history(team, current_version: str, path=None) -> dict[tuple[int, str], dict]:
    """Version-ordered subsystem participation/approved assignment history, by work type."""
    versions_by_phase: dict[tuple[int, str, str], set[str]] = defaultdict(set)
    all_versions = set()

    def record(member_id, subsystem, version, phase):
        version = str(version or "").strip()
        subsystem = str(subsystem or "").strip().casefold()
        if not version or not subsystem or version.casefold() == str(current_version or "").strip().casefold():
            return
        all_versions.add(version)
        versions_by_phase[(int(member_id), subsystem, phase)].add(version)

    if len(team.logs):
        for row in team.logs.to_dict("records"):
            if row.get("track") not in ("req", "dynamic") or not is_legacy_sheet_version(row.get("version", "")):
                continue
            record(row.get("member_id"), row.get("subsystem"), row.get("version"),
                   "spec" if row.get("track") == "req" else "verify")
    for phase in ("spec", "verify"):
        for row in db.list_all_internal_worklogs(phase, path):
            if is_internal_version(row.get("version", "")):
                record(row.get("member_id"), row.get("subsystem"), row.get("version"), phase)

    # Approved work allocations are assignment evidence even before a work log exists.
    projects = db.list_version_projects(path)
    for project in projects:
        version = str(project.get("version") or "").strip()
        if not version or version.casefold() == str(current_version or "").strip().casefold():
            continue
        if not (is_legacy_sheet_version(version) or is_internal_version(version)):
            continue
        all_versions.add(version)
        for phase in ("spec", "verify"):
            for row in db.list_version_assignments(project["id"], phase, path):
                record(row.get("member_id"), row.get("subsystem"), version, phase)

    def order(version):
        match = re.fullmatch(r"T\s*(\d+)", version, re.IGNORECASE)
        return (0, int(match.group(1)), version.casefold()) if match else (1, version.casefold(), version.casefold())

    ordered_versions = sorted(all_versions, key=order)
    result = {}
    keys = {(member, subsystem) for member, subsystem, _ in versions_by_phase}
    for member, subsystem in keys:
        spec = versions_by_phase.get((member, subsystem, "spec"), set())
        verify = versions_by_phase.get((member, subsystem, "verify"), set())
        combined = spec | verify
        streak = 0
        for version in reversed(ordered_versions):
            if version in combined:
                streak += 1
            else:
                break
        # Count history relative to the latest observed prior version. Keep a
        # short recency window as evidence for the PM, independent of phase.
        recent_versions = ordered_versions[-3:]
        recent = [version for version in recent_versions if version in combined]
        result[(member, subsystem)] = {
            "versions": sorted(combined, key=order),
            "spec_versions": sorted(spec, key=order),
            "verify_versions": sorted(verify, key=order),
            "cross_phase_versions": sorted(spec & verify, key=order),
            "recent_versions": recent,
            "consecutive_versions": streak,
        }
    return result


def _recent_work_activity(team, today: date, path=None) -> dict[int, dict[str, int]]:
    """Observed recent work-log activity, kept separate from effort estimates and skill."""
    start = today - timedelta(days=29)
    counts: dict[int, dict[str, set]] = defaultdict(lambda: {"records": set(), "days": set(), "spec_records": set(), "verify_records": set()})
    index = 0
    if len(team.logs):
        for row in team.logs.to_dict("records"):
            if row.get("track") not in ("req", "dynamic") or not is_legacy_sheet_version(row.get("version", "")):
                continue
            logged = row.get("date")
            if logged and start <= logged <= today:
                member = int(row["member_id"])
                track_key = "spec_records" if row["track"] == "req" else "verify_records"
                counts[member]["records"].add(index); counts[member]["days"].add(logged); counts[member][track_key].add(index); index += 1
    for phase in ("spec", "verify"):
        for row in db.list_all_internal_worklogs(phase, path):
            if not is_internal_version(row.get("version", "")):
                continue
            logged = row.get("date")
            if logged and start <= logged <= today:
                member = int(row["member_id"])
                phase_key = "spec_records" if phase == "spec" else "verify_records"
                counts[member]["records"].add(index); counts[member]["days"].add(logged); counts[member][phase_key].add(index); index += 1
    return {mid: {"records": len(values["records"]), "days": len(values["days"]),
                  "spec_records": len(values["spec_records"]), "verify_records": len(values["verify_records"])}
            for mid, values in counts.items()}


def recommend_subsystem(project: dict, team, phase: str, member_id: int, work_date: date, path=None) -> dict:
    """Rank plan bottlenecks for a selected person using phase history, capacity, assignments and leave."""
    if phase not in ("spec", "verify"):
        raise ValueError("phase는 spec 또는 verify여야 합니다.")
    leave = next((row for row in team.leave_records()
                  if int(row["member_id"]) == int(member_id) and row["start"] <= work_date <= row["end"]), None)
    if leave and leave.get("type") != "반차":
        return {"subsystem": None, "reason": f"선택한 날짜는 {leave.get('type', '부재')} 일정입니다. 다른 날짜를 선택하세요.", "basis": []}
    workloads = db.list_version_workloads(project["id"], phase, path)
    if not workloads:
        return {"subsystem": None, "reason": "선택한 업무 유형의 버전 계획이 없어 서브시스템을 추천할 수 없습니다.", "basis": []}
    logs = db.list_version_worklogs(project["id"], phase, path)
    done_by_subsystem: dict[str, dict[str, int]] = defaultdict(lambda: {"tasks": 0, "functions": 0, "requirements": 0})
    for row in logs:
        key = str(row["subsystem"]).strip().casefold()
        done_by_subsystem[key]["tasks"] += int(row.get("completed_tasks") or 0)
        done_by_subsystem[key]["functions"] += int(row.get("completed_functions") or 0)
        done_by_subsystem[key]["requirements"] += int(row.get("completed_requirements") or 0)
    experience = _subsystem_experience(phase, team, path)
    assignments = db.list_version_assignments(project["id"], phase, path)
    assigned_effort: dict[int, float] = defaultdict(float)
    people_by_subsystem: dict[str, set[int]] = defaultdict(set)
    current_records_by_member: dict[int, int] = defaultdict(int)
    for row in assignments:
        assigned_effort[int(row["member_id"])] += float(row.get("effort_score") or 0)
        people_by_subsystem[str(row["subsystem"]).strip().casefold()].add(int(row["member_id"]))
    for row in logs:
        current_member = int(row["member_id"])
        people_by_subsystem[str(row["subsystem"]).strip().casefold()].add(current_member)
        current_records_by_member[current_member] += 1
    recent = _recent_work_activity(team, work_date, path)
    members = {int(row["id"]): row for row in team.unit_members()}
    if int(member_id) not in members:
        return {"subsystem": None, "reason": "단위 검증 담당자는 김재강·주해원·황지원·이혜윤 중에서 선택해야 합니다.", "basis": []}
    selected_member = members.get(int(member_id), {})
    own_phase_logs = [row for row in logs if int(row["member_id"]) == int(member_id)]
    own_phase_count = len(own_phase_logs)
    other_phase = "verify" if phase == "spec" else "spec"
    other_phase_logs = db.list_version_worklogs(project["id"], other_phase, path)
    rotation_history = _subsystem_rotation_history(team, project["version"], path)
    other_phase_assignments = db.list_version_assignments(project["id"], other_phase, path)
    counterpart_people: dict[str, set[int]] = defaultdict(set)
    for row in other_phase_assignments:
        counterpart_people[str(row["subsystem"]).strip().casefold()].add(int(row["member_id"]))
    for row in other_phase_logs:
        counterpart_people[str(row["subsystem"]).strip().casefold()].add(int(row["member_id"]))
    same_day_phase_count = sum(1 for row in own_phase_logs + other_phase_logs
                               if int(row["member_id"]) == int(member_id) and row.get("date") == work_date)
    own_phase_effort = assigned_effort.get(int(member_id), 0.0)
    other_version_efforts = _approved_assignment_loads(phase, int(project["id"]), path)
    cross_version_effort = other_version_efforts.get(int(member_id), 0.0)
    recent_rows = recent.get(int(member_id), {"records": 0, "days": 0, "spec_records": 0, "verify_records": 0})
    recent_counts = [recent.get(mid, {}).get("records", 0) for mid in members]
    mean_recent = sum(recent_counts) / len(recent_counts) if recent_counts else 0
    workload_pct = int(selected_member.get("workload") or 0)
    overloaded = workload_pct >= 90 or bool(mean_recent and recent_rows["records"] >= max(6, mean_recent * 1.8))
    due = project.get("spec_due" if phase == "spec" else "verify_due")
    start = project.get("spec_start" if phase == "spec" else "verify_start")
    start_source = "계획 시작일" if start else ""
    if not start:
        recorded_starts = [row["date"] for row in logs if row.get("date")]
        if recorded_starts:
            start = min(recorded_starts)
            start_source = "선택 업무 유형의 첫 실적 기록일"
    days_to_due = (due - work_date).days if due else None
    if start and due and due >= start:
        full_period = workdays(start, due)
        elapsed_period = workdays(start, min(max(work_date, start), due))
        expected_progress = len(elapsed_period) / len(full_period) if full_period else None
    else:
        expected_progress = None
    absent_ids = {int(row["member_id"]) for row in team.leave_records()
                  if row["start"] <= work_date <= row["end"] and row.get("type") != "반차"}
    candidates = []
    for workload in workloads:
        name = str(workload["subsystem"]).strip()
        key = name.casefold()
        done = done_by_subsystem[key]
        if phase == "spec":
            total, completed = int(workload.get("total_tasks") or 0), done["tasks"]
        else:
            # Requirement progress is the verification milestone; use planned
            # functions only when no requirement target was entered.
            total, completed = int(workload.get("total_requirements") or 0), done["requirements"]
            if total <= 0:
                total, completed = int(workload.get("total_functions") or 0), done["functions"]
        remaining = max(0, total - completed)
        if total <= 0 or remaining <= 0:
            continue
        history = experience.get((int(member_id), key), {"versions": 0, "days": 0, "records": 0,
            "spec_done": 0, "verify_req_done": 0, "verify_func_done": 0})
        rotation = rotation_history.get((int(member_id), key), {"versions": [], "spec_versions": [], "verify_versions": [],
            "cross_phase_versions": [], "recent_versions": [], "consecutive_versions": 0,
            "spec_consecutive_versions": 0, "verify_consecutive_versions": 0})
        progress = min(1.0, completed / total)
        progress_gap = max(0.0, expected_progress - progress) if expected_progress is not None else None
        near_deadline = max(0.0, 1.0 - max(0, days_to_due or 0) / 30) if days_to_due is not None else None
        staffing = len(people_by_subsystem[key])
        staffing_need = 1.0 if staffing == 0 else 0.45 if staffing == 1 else 0.0
        need_score = 0.42 * (progress_gap / 0.5 if progress_gap is not None else 0.0)
        need_score += 0.24 * (remaining / total)
        need_score += 0.20 * (near_deadline if near_deadline is not None else 0.0)
        need_score += 0.14 * staffing_need
        fit_score = min(0.16, history["versions"] * 0.04 + min(history["days"], 6) * 0.01)
        current_cross_phase = int(member_id) in counterpart_people[key]
        separation_needed = current_cross_phase or bool(rotation["cross_phase_versions"])
        rotation_recommended = rotation["consecutive_versions"] >= 2 or len(rotation["recent_versions"]) >= 2
        rotation_penalty = min(0.28, max(0, rotation["consecutive_versions"] - 1) * 0.09 + max(0, len(rotation["recent_versions"]) - 1) * 0.04)
        separation_penalty = 0.22 if current_cross_phase else min(0.12, len(rotation["cross_phase_versions"]) * 0.04)
        score = need_score + fit_score - rotation_penalty - separation_penalty
        status = ("지원 필요" if progress_gap is not None and progress_gap >= 0.2 and remaining / total >= 0.25 and (days_to_due is None or days_to_due <= 30)
                  else "인력 보강 검토" if staffing == 0 else "업무 분산 권장" if staffing <= 1 and remaining / total >= 0.3
                  else "진행 중")
        support_people = []
        phase_skill = "요구사양서 작성" if phase == "spec" else "단위테스트"
        for candidate_id, person in members.items():
            if candidate_id == int(member_id) or candidate_id in absent_ids:
                continue
            history_row = experience.get((candidate_id, key), {"versions": 0, "days": 0, "records": 0,
                "spec_done": 0, "verify_req_done": 0, "verify_func_done": 0})
            rotation_row = rotation_history.get((candidate_id, key), {"versions": [], "spec_versions": [], "verify_versions": [],
                "cross_phase_versions": [], "recent_versions": [], "consecutive_versions": 0})
            separation_conflict = candidate_id in counterpart_people[key]
            domain_skill = team.skill_level(candidate_id, name)
            task_skill = team.skill_level(candidate_id, phase_skill)
            if not history_row["versions"] and not domain_skill and not task_skill:
                continue
            recent_row = recent.get(candidate_id, {"records": 0, "spec_records": 0, "verify_records": 0})
            phase_recent = recent_row["spec_records" if phase == "spec" else "verify_records"]
            workload = max(0, int(person.get("workload") or 0))
            if workload >= 90 or (mean_recent and recent_row["records"] >= max(6, mean_recent * 1.8)):
                continue
            prior_effort = other_version_efforts.get(candidate_id, 0.0)
            current_count = current_records_by_member.get(candidate_id, 0)
            current_effort = assigned_effort.get(candidate_id, 0.0)
            fit = history_row["versions"] * 10 + min(history_row["days"], 5) * 2 + domain_skill * 8 + task_skill * 3
            burden = workload + phase_recent * 5 + prior_effort + current_effort + current_count * 3
            rotation_cost = min(24, max(0, rotation_row["consecutive_versions"] - 1) * 8)
            separation_cost = 30 if separation_conflict else min(12, len(rotation_row["cross_phase_versions"]) * 4)
            support_people.append({"member_id": candidate_id, "name": person["name"], "workload_pct": workload,
                "experience_versions": history_row["versions"], "experience_days": history_row["days"],
                "recent_subsystem_versions": rotation_row["recent_versions"],
                "consecutive_subsystem_versions": rotation_row["consecutive_versions"],
                "cross_phase_versions": rotation_row["cross_phase_versions"],
                "separation_conflict": separation_conflict,
                "recent_phase_records": phase_recent, "current_phase_records": current_count,
                "approved_effort": prior_effort + current_effort,
                "score": fit - burden * 0.25 - rotation_cost - separation_cost,
                "basis": "과거 서브시스템 기록" if history_row["versions"] else "등록 역량"})
        support_people.sort(key=lambda row: (row["separation_conflict"], -row["score"], row["name"].casefold()))
        if any(not row["separation_conflict"] for row in support_people):
            support_people = [row for row in support_people if not row["separation_conflict"]]
        eligible_separated_support = [row for row in support_people if not row["separation_conflict"]]
        separation_exception = separation_needed and not eligible_separated_support
        counterpart_names = [members[mid]["name"] for mid in sorted(counterpart_people[key]) if mid in members]
        candidates.append({"subsystem": name, "total": total, "completed": completed, "remaining": remaining,
            "progress": progress, "expected_progress": expected_progress, "progress_gap": progress_gap,
            "days_to_due": days_to_due, "staff_count": staffing, "status": status,
            "experience": history, "rotation": rotation, "rotation_recommended": rotation_recommended,
            "separation_needed": separation_needed, "current_cross_phase": current_cross_phase,
            "separation_exception": separation_exception, "counterpart_names": counterpart_names,
            "counterpart_member_ids": sorted(counterpart_people[key]), "rotation_penalty": rotation_penalty,
            "separation_penalty": separation_penalty, "score": score, "support_people": support_people})
    if not candidates:
        return {"subsystem": None, "reason": "선택한 업무 유형의 계획 잔량이 없거나 목표 수량이 입력되지 않았습니다.", "basis": []}
    candidates.sort(key=lambda item: (-item["score"], item["subsystem"].casefold()))
    best = candidates[0]
    for rank, row in enumerate(candidates, 1):
        row["priority"] = "우선" if rank == 1 else f"대안 {rank - 1}"
        row["workload_pct"] = workload_pct
        row["own_phase_records"] = own_phase_count
        row["same_day_records"] = same_day_phase_count
        row["own_phase_effort"] = own_phase_effort
        row["cross_version_effort"] = cross_version_effort
        row["recent_phase_records"] = recent_rows["spec_records" if phase == "spec" else "verify_records"]
        row["recent_all_records"] = recent_rows["records"]
        pieces = [f"현재 완료율 {row['progress']:.0%}, 잔여 {row['remaining']}/{row['total']}"]
        if row["expected_progress"] is None:
            pieces.append("시작/마감일 부족으로 일정 대비 지연은 평가 불가")
        elif row["progress_gap"] >= 0.2:
            pieces.append(f"계획 시점 진척률 {row['expected_progress']:.0%}보다 {row['progress_gap']:.0%}p 낮음")
        if row["expected_progress"] is not None and start_source:
            pieces.append(f"일정 기준: {start_source}")
        if row["days_to_due"] is not None:
            pieces.append(f"마감 {'경과 ' + str(abs(row['days_to_due'])) + '일' if row['days_to_due'] < 0 else str(row['days_to_due']) + '일 남음'}")
        pieces.append(f"현재 기록/승인 기준 담당자 {row['staff_count']}명")
        if row["experience"]["versions"]:
            pieces.append(f"선택 담당자 과거 경험 {row['experience']['versions']}개 버전")
        else:
            pieces.append("선택 담당자 과거 해당 유형 경험 데이터 없음")
        if row["rotation"]["versions"]:
            pieces.append(f"최근 서브시스템 배정 버전 {', '.join(row['rotation']['recent_versions']) or '최근 3개 버전 내 없음'}; 연속 {row['rotation']['consecutive_versions']}개 버전 담당")
        if row["rotation_recommended"]:
            pieces.append("연속 담당 이력이 있어 업무 순환 권장")
        if row["rotation"]["cross_phase_versions"]:
            pieces.append("과거 동일 서브시스템 작성·검증 겸임 이력: " + ", ".join(row["rotation"]["cross_phase_versions"]))
        if row["separation_needed"]:
            pieces.append("작성·검증 담당자 분리 필요" + (" (현재 버전에서 겸임 중)" if row["current_cross_phase"] else " (과거 겸임 이력)"))
        if row["separation_exception"]:
            pieces.append("분리 가능한 대안 담당자가 없어 PM 예외 검토 필요")
        row["reason"] = "; ".join(pieces)
    if best["experience"]["versions"]:
        reason = (f"과거 동일 업무의 {best['subsystem']} 경험이 {best['experience']['versions']}개 버전·{best['experience']['days']}활동일 확인됩니다. "
                  f"현재 진척률 {best['progress']:.0%}, 잔여 {best['remaining']}/{best['total']}이며 {best['status']} 상태입니다.")
    else:
        reason = (f"현재 진척률 {best['progress']:.0%}, 잔여 {best['remaining']}/{best['total']}로 {best['status']} 우선순위가 높습니다. "
                  "선택 담당자의 과거 해당 업무·서브시스템 경험은 확인되지 않았습니다.")
    if best["rotation_recommended"]:
        reason += f" 동일 서브시스템 최근 연속 {best['rotation']['consecutive_versions']}개 버전 담당 이력이 있어 업무 순환을 권장합니다."
    if best["separation_needed"]:
        reason += " 요구사양서 작성과 동적 검증은 담당자 분리를 우선 권장합니다."
        if best["separation_exception"]:
            reason += " 분리 가능한 적합 인력이 확인되지 않아 PM의 예외 검토가 필요합니다."
        elif best["counterpart_names"]:
            reason += " 현재 반대 업무 담당: " + ", ".join(best["counterpart_names"]) + "."
    if expected_progress is None:
        reason += " 계획 시작일 또는 마감일이 없어 일정 기준 지연 가능성은 평가하지 않았습니다."
    elif start_source and start_source != "계획 시작일":
        reason += f" 일정 기준은 {start_source}입니다."
    if overloaded:
        reason += " 선택 담당자의 현재 업무 부담이 높아 이 담당자에게 추가 업무를 권하지 않습니다."
        if best["support_people"]:
            reason += f" 같은 업무 적합성이 확인된 대안 담당자는 {best['support_people'][0]['name']}입니다."
    if leave and leave.get("type") == "반차":
        reason += " 선택 날짜에 반차가 등록되어 있습니다."
    return {"subsystem": best["subsystem"], "reason": reason, "basis": candidates, "overloaded": overloaded,
        "rotation": best["rotation"], "rotation_recommended": best["rotation_recommended"],
        "separation_needed": best["separation_needed"], "separation_exception": best["separation_exception"],
        "counterpart_names": best["counterpart_names"],
        "workload_pct": workload_pct, "own_phase_records": own_phase_count, "own_phase_effort": own_phase_effort,
        "same_day_records": same_day_phase_count,
        "cross_version_effort": cross_version_effort, "recent_phase_records": recent_rows["spec_records" if phase == "spec" else "verify_records"],
        "recent_spec_records": recent_rows["spec_records"], "recent_verify_records": recent_rows["verify_records"],
        "recent_all_records": recent_rows["records"], "days_to_due": days_to_due, "expected_progress": expected_progress}


def _approved_assignment_loads(phase: str, current_project_id: int, path=None) -> dict[int, float]:
    """Other active internal versions' PM-approved difficulty points by person."""
    loads: dict[int, float] = defaultdict(float)
    for project in internal_projects(path):
        if int(project["id"]) == int(current_project_id) or project["status"] == "완료":
            continue
        for row in db.list_version_assignments(project["id"], phase, path):
            loads[int(row["member_id"])] += float(row["effort_score"] or 0)
    return loads


def _split_units(total: int, weights: dict[int, float]) -> dict[int, int]:
    if total <= 0 or not weights:
        return {mid: 0 for mid in weights}
    positive = {mid: max(0.0, float(weight)) for mid, weight in weights.items()}
    if sum(positive.values()) <= 0:
        positive = {mid: 1.0 for mid in weights}
    denominator = sum(positive.values())
    raw = {mid: total * weight / denominator for mid, weight in positive.items()}
    out = {mid: int(value) for mid, value in raw.items()}
    for mid in sorted(raw, key=lambda key: (raw[key] - out[key], -key), reverse=True)[:total - sum(out.values())]:
        out[mid] += 1
    return out


def recommend_distribution(team, project: dict, phase: str, workloads: list[dict], today: date, path=None) -> dict:
    """휴가·기존 업무량·역량을 반영한 검토용 배분안. 저장은 PM 승인 후 별도 수행한다."""
    if phase not in ("spec", "verify"):
        raise ValueError("phase는 spec 또는 verify여야 합니다.")
    members = {int(row["id"]): row for row in team.unit_members()}
    if not members:
        return {"rows": [], "warnings": ["단위 검증 담당자(김재강·주해원·황지원·이혜윤)가 등록되어 있지 않아 배분안을 만들 수 없습니다."], "needs_more_staff": None}
    unit_pool = team.unit_pool()
    skill_names = {mid: [str(s["skill"]) for s in team.skills[team.skills["member_id"] == mid].to_dict("records")]
                   for mid in members}
    warnings = []
    if phase == "verify":
        candidate_ids = sorted(unit_pool)
        skill_basis = "지정 단위 검증 담당자"
    elif phase == "spec":
        skilled = {mid for mid, names in skill_names.items()
                   if any("요구사양서" in name or "사양서 작성" in name for name in names)}
        candidate_ids = sorted(set(members))
        skill_basis = "지정 단위 검증 담당자"
        if not skilled:
            warnings.append("지정 담당자의 요구사양서 작성 역량 정보가 없어 해당 네 명 안에서 업무량·이력을 기준으로 배분합니다.")
    else:
        candidate_ids = sorted(members)
        skill_basis = "지정 단위 검증 담당자"

    due = project["spec_due" if phase == "spec" else "verify_due"]
    deadline_days = (due - today).days if due else None
    work_start = max(today, project["spec_start" if phase == "spec" else "verify_start"] or today)
    if due and due < today:
        warnings.append("마감일이 지났습니다. 추천 배분만 제시하며 완료 가능 예측은 할 수 없습니다.")
    rates = _history_rates(phase, team, path)
    subsystem_history = _subsystem_experience(phase, team, path)
    recent_activity = _recent_work_activity(team, today, path)
    observed = [recent_activity.get(mid, {}).get("records", 0) for mid in candidate_ids]
    mean_activity = sum(observed) / len(observed) if observed else 0
    reserved_loads = _approved_assignment_loads(phase, project["id"], path)
    profile = {}
    for mid in candidate_ids:
        member = members[mid]
        days = _effective_days(team, mid, work_start, due) if due and due >= work_start else 0.0
        load = max(0, min(150, int(member.get("workload") or 0)))
        capacity = days * max(0.0, 1 - min(load, 100) / 100)
        profile[mid] = {"days": days, "load": load, "capacity": capacity,
                        "skills": skill_names.get(mid, []), "reserved_effort": reserved_loads.get(mid, 0.0),
                        "recent_records": recent_activity.get(mid, {}).get("records", 0),
                        "recent_days": recent_activity.get(mid, {}).get("days", 0)}
    if due:
        if not any(p["capacity"] > 0 for p in profile.values()):
            warnings.append("선택한 기간에 가용한 담당자가 없습니다. 마감일 또는 후보 인원을 확인하세요.")
    else:
        warnings.append("마감일이 없어 남은 업무 가능일을 계산할 수 없습니다.")

    assignment: dict[tuple[str, int], dict] = {}
    for item in workloads:
        subsystem = str(item["subsystem"])
        weights = {}
        for mid, person in profile.items():
            match = any(name.casefold() in subsystem.casefold() or subsystem.casefold() in name.casefold()
                        for name in person["skills"] if name not in ("단위테스트", "요구사양서 작성"))
            level = max([team.skill_level(mid, name) for name in person["skills"] if name], default=0)
            history = subsystem_history.get((mid, subsystem.casefold()), {"versions": 0, "days": 0, "records": 0})
            experience_bonus = min(0.3, history["versions"] * 0.06 + min(history["days"], 5) * 0.02)
            multiplier = 1 + (0.1 * level if match else 0) + experience_bonus
            existing_daily_effort = person["reserved_effort"] / max(1.0, person["days"])
            activity_factor = min(1.5, person["recent_records"] / mean_activity) if mean_activity else 0.0
            weights[mid] = person["capacity"] * multiplier / (1 + existing_daily_effort + 0.2 * activity_factor)
        if not any(weights.values()):
            weights = {mid: (p["days"] or 1.0) for mid, p in profile.items()}
        fields = ([("tasks", "total_tasks", "completed_tasks")] if phase == "spec" else
                  [("functions", "total_functions", "completed_functions"),
                   ("requirements", "total_requirements", "completed_requirements")])
        for label, total_key, metric in fields:
            pieces = _split_units(int(item.get(total_key) or 0), weights)
            for mid, amount in pieces.items():
                if amount <= 0:
                    continue
                key = (subsystem, mid)
                row = assignment.setdefault(key, {"subsystem": subsystem, "member_id": mid,
                    "member_name": members[mid]["name"], "tasks": 0, "functions": 0, "requirements": 0,
                    "effort_score": 0.0, "load": profile[mid]["load"], "available_days": profile[mid]["days"],
                    "reserved_effort": profile[mid]["reserved_effort"], "skill_basis": skill_basis,
                    "experience_versions": history["versions"], "experience_days": history["days"],
                    "recent_worklog_count": profile[mid]["recent_records"], "recent_worklog_days": profile[mid]["recent_days"],
                    "deadline_days": deadline_days})
                row[label] += amount
                row["effort_score"] += amount * int(item.get("difficulty") or 1)

    has_planned_work = any(int(w.get("total_tasks", 0) or 0) > 0 for w in workloads) if phase == "spec" else any(
        int(w.get("total_functions", 0) or 0) > 0 or int(w.get("total_requirements", 0) or 0) > 0 for w in workloads)
    needs_more = bool(due and has_planned_work and not any(p["capacity"] > 0 for p in profile.values()))
    for row in assignment.values():
        mid = row["member_id"]
        available = profile[mid]["capacity"]
        assigned_metrics = [("tasks", "completed_tasks")] if phase == "spec" else [
            ("functions", "completed_functions"), ("requirements", "completed_requirements")]
        unknown = []
        overloaded = []
        for assigned_key, metric in assigned_metrics:
            assigned = row[assigned_key] * max(1, int(next((w.get("difficulty", 1) for w in workloads
                                                              if w["subsystem"] == row["subsystem"]), 1)))
            historical_rate = rates.get(mid, {}).get(metric)
            if historical_rate is None:
                unknown.append(metric)
            elif available <= 0 or assigned > historical_rate * available:
                overloaded.append(metric)
        if due is None or not rates.get(mid):
            row["feasibility"] = "판단 자료 부족"
        elif overloaded:
            row["feasibility"] = "추가 인력/일정 검토"
            needs_more = True
        elif unknown:
            row["feasibility"] = "일부 처리량 이력 부족"
        else:
            row["feasibility"] = "과거 처리량 이력상 가능"
        row["estimated_daily_effort"] = row["effort_score"] / available if available > 0 else None
        exp = (f"해당 서브시스템 경험 {row['experience_versions']}개 버전·{row['experience_days']}일" if row["experience_versions"] or row["experience_days"]
               else "해당 서브시스템 과거 기록 없음")
        row["rationale"] = (f"{skill_basis}; {exp}; 최근 30일 실적 기록 {row['recent_worklog_count']}건·{row['recent_worklog_days']}활동일; "
                            f"등록 업무량 {row['load']}%; 기존 승인 업무 부담 {row['reserved_effort']:g}점; "
                            f"남은 가용일 {row['available_days']:g}일. "
                            + ("기존 시트/내부 기록의 일평균 처리량을 초과합니다." if overloaded else
                               "과거 기록이 없어 완료 가능 여부를 확정할 수 없습니다." if unknown else
                               "기존 시트/내부 기록의 일평균 처리량 범위입니다."))
    rows = sorted(assignment.values(), key=lambda row: (row["member_name"].casefold(), row["subsystem"].casefold()))
    if not rates:
        warnings.append("과거 시트/내부 처리 이력이 없어 마감 가능성은 참고값으로만 표시합니다.")
    return {"rows": rows, "warnings": warnings, "needs_more_staff": needs_more,
            "due": due, "skill_basis": skill_basis, "history_available": bool(rates)}
