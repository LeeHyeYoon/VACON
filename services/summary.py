"""대시보드 요약 지표."""
from __future__ import annotations

from datetime import date, timedelta

from .calendar import week_range
from .config import merge_rules
from .models import TeamData
from .risk import absent_map, assess_tasks
from .coverage import scheduled_leave_targets
from data import db


def _dynamic_backup_gaps(team: TeamData, as_of: date) -> list[dict]:
    """Count actionable dynamic-verification allocations still lacking a substitute."""
    gaps = []
    for target in scheduled_leave_targets(team, as_of):
        if target.get("phase") != "verify" or target.get("status") != "대상 업무":
            continue
        project_id = int(target["project_id"])
        absent_id = int(target["member_id"])
        assignments = db.list_version_assignments(project_id, "verify")
        backup_rows = db.list_version_backup_assignments(project_id, "verify")
        owned = [row for row in assignments if int(row["member_id"]) == absent_id]
        if not assignments:
            leave_start = target["leave_start"].isoformat()
            leave_end = target["leave_end"].isoformat()
            covered = any(int(row["absent_member_id"]) == absent_id
                          and row["leave_start"] == leave_start and row["leave_end"] == leave_end
                          for row in backup_rows)
            if not covered:
                gaps.append({**target, "subsystem": "서브시스템 미배정"})
            continue
        logs = [row for row in db.list_version_worklogs(project_id, "verify")
                if int(row["member_id"]) == absent_id]
        for assignment in owned:
            subsystem = str(assignment["subsystem"] or "").casefold()
            completed_requirements = sum(int(row.get("completed_requirements") or 0) for row in logs
                                         if str(row["subsystem"] or "").casefold() == subsystem)
            completed_functions = sum(int(row.get("completed_functions") or 0) for row in logs
                                      if str(row["subsystem"] or "").casefold() == subsystem)
            transferred = [row for row in backup_rows if int(row["absent_member_id"]) == absent_id
                           and str(row["subsystem"] or "").casefold() == subsystem]
            requirements_left = max(0, int(assignment.get("allocated_requirements") or 0)
                                    - completed_requirements
                                    - sum(int(row.get("allocated_requirements") or 0) for row in transferred))
            functions_left = max(0, int(assignment.get("allocated_functions") or 0)
                                 - completed_functions
                                 - sum(int(row.get("allocated_functions") or 0) for row in transferred))
            if requirements_left or functions_left:
                gaps.append({**target, "subsystem": assignment["subsystem"]})
    return gaps


def dashboard_metrics(team: TeamData, today: date, rules: dict | None = None) -> dict:
    rules = merge_rules(rules)
    absent_today = absent_map(team, today, rules)
    ws, we = week_range(today)
    week_leaves = [lv for lv in team.leave_records() if lv["start"] <= we and ws <= lv["end"]]
    risks = assess_tasks(team, today, today + timedelta(days=rules["horizon_days"]), rules)
    dynamic_unassigned = _dynamic_backup_gaps(team, today)
    return {
        "absent_today": [team.name(m) + f"({t})" for m, t in absent_today.items()],
        "week_leaves": week_leaves,
        "insufficient_count": int((risks["위험도"] == "데이터 부족").sum()) if not risks.empty else 0,
        "dynamic_backup_unassigned": dynamic_unassigned,
        "risks": risks,
    }
