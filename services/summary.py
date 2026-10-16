"""대시보드 요약 지표."""
from __future__ import annotations

from datetime import date, timedelta

from .calendar import week_range
from .config import merge_rules
from .models import TeamData, isnull
from .risk import HIGH, MEDIUM, absent_map, assess_tasks


def dashboard_metrics(team: TeamData, today: date, rules: dict | None = None) -> dict:
    rules = merge_rules(rules)
    absent_today = absent_map(team, today, rules)
    ws, we = week_range(today)
    week_leaves = [lv for lv in team.leave_records() if lv["start"] <= we and ws <= lv["end"]]
    risks = assess_tasks(team, today, today + timedelta(days=rules["horizon_days"]), rules)
    active = [t for t in team.task_records() if t.get("status") != "완료"]
    return {
        "absent_today": [team.name(m) + f"({t})" for m, t in absent_today.items()],
        "week_leaves": week_leaves,
        "risk_count": int(risks["위험도"].isin([HIGH, MEDIUM]).sum()) if not risks.empty else 0,
        "high_count": int((risks["위험도"] == HIGH).sum()) if not risks.empty else 0,
        "insufficient_count": int((risks["위험도"] == "데이터 부족").sum()) if not risks.empty else 0,
        "no_backup": [t for t in active if isnull(t.get("backup_id"))],
        "risks": risks,
    }
