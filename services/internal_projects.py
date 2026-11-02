"""VACON 자체 DB로 관리하는 T0080+ 버전 업무 및 배정 추천 로직."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

from data import db
from .risk import workdays


def is_internal_version(version: str) -> bool:
    """T0070 이하만 과거 Sheets 경로에 남기고 나머지는 VACON 내부 DB로 관리한다."""
    match = re.fullmatch(r"T\s*(\d+)", str(version or "").strip(), re.IGNORECASE)
    return int(match.group(1)) >= 80 if match else True


def is_legacy_sheet_version(version: str) -> bool:
    match = re.fullmatch(r"T\s*(\d+)", str(version or "").strip(), re.IGNORECASE)
    return bool(match and int(match.group(1)) <= 70)


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
    """현재 업무와 분리된 T0070 이하 시트 및 내부 버전 이력을 참고한다."""
    records = db.list_all_internal_worklogs(phase, path)
    metrics = ("completed_tasks",) if phase == "spec" else ("completed_functions", "completed_requirements")
    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    active: dict[int, set[date]] = defaultdict(set)
    for row in records:
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
    members = {int(row["id"]): row for row in team.member_records()}
    if not members:
        return {"rows": [], "warnings": ["등록된 팀원이 없어 배분안을 만들 수 없습니다."], "needs_more_staff": None}
    unit_pool = team.unit_pool()
    skill_names = {mid: [str(s["skill"]) for s in team.skills[team.skills["member_id"] == mid].to_dict("records")]
                   for mid in members}
    warnings = []
    if phase == "verify" and unit_pool:
        candidate_ids = sorted(unit_pool)
        skill_basis = "단위테스트 역량 보유자"
    elif phase == "spec":
        skilled = {mid for mid, names in skill_names.items()
                   if any("요구사양서" in name or "사양서 작성" in name for name in names)}
        candidate_ids = sorted(skilled or set(members))
        skill_basis = "요구사양서 작성 역량 보유자" if skilled else "전체 팀원(해당 역량 미등록)"
        if not skilled:
            warnings.append("요구사양서 작성 역량이 등록된 팀원이 없어 전체 팀원을 후보로 사용했습니다.")
    else:
        candidate_ids = sorted(members)
        skill_basis = "전체 팀원(단위테스트 역량 미등록)"
        warnings.append("단위테스트 역량이 등록되어 있지 않아 전체 팀원을 후보로 사용했습니다.")

    due = project["spec_due" if phase == "spec" else "verify_due"]
    work_start = max(today, project["spec_start" if phase == "spec" else "verify_start"] or today)
    if due and due < today:
        warnings.append("마감일이 지났습니다. 추천 배분만 제시하며 완료 가능 예측은 할 수 없습니다.")
    rates = _history_rates(phase, team, path)
    reserved_loads = _approved_assignment_loads(phase, project["id"], path)
    profile = {}
    for mid in candidate_ids:
        member = members[mid]
        days = _effective_days(team, mid, work_start, due) if due and due >= work_start else 0.0
        load = max(0, min(150, int(member.get("workload") or 0)))
        capacity = days * max(0.0, 1 - min(load, 100) / 100)
        profile[mid] = {"days": days, "load": load, "capacity": capacity,
                        "skills": skill_names.get(mid, []), "reserved_effort": reserved_loads.get(mid, 0.0)}
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
            multiplier = 1 + (0.1 * level if match else 0)
            existing_daily_effort = person["reserved_effort"] / max(1.0, person["days"])
            weights[mid] = person["capacity"] * multiplier / (1 + existing_daily_effort)
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
                    "reserved_effort": profile[mid]["reserved_effort"], "skill_basis": skill_basis})
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
        row["rationale"] = (f"{skill_basis}; 현재 업무량 {row['load']}%; 기존 승인 업무 부담 {row['reserved_effort']:g}점; "
                            f"남은 가용일 {row['available_days']:g}일. "
                            + ("기존 시트/내부 기록의 일평균 처리량을 초과합니다." if overloaded else
                               "과거 기록이 없어 완료 가능 여부를 확정할 수 없습니다." if unknown else
                               "기존 시트/내부 기록의 일평균 처리량 범위입니다."))
    rows = sorted(assignment.values(), key=lambda row: (row["member_name"].casefold(), row["subsystem"].casefold()))
    if not rates:
        warnings.append("과거 시트/내부 처리 이력이 없어 마감 가능성은 참고값으로만 표시합니다.")
    return {"rows": rows, "warnings": warnings, "needs_more_staff": needs_more,
            "due": due, "skill_basis": skill_basis, "history_available": bool(rates)}
