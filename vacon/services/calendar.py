"""휴가 캘린더 보조 함수(기간 계산, 부재 매트릭스, 중복 구간)."""
from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

import pandas as pd

from .models import TeamData
from .risk import workdays


def week_range(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def month_range(d: date) -> tuple[date, date]:
    return d.replace(day=1), d.replace(day=_cal.monthrange(d.year, d.month)[1])


def absence_matrix(team: TeamData, start: date, end: date) -> pd.DataFrame:
    """행=팀원, 열=근무일, 값=휴가 유형(없으면 빈 문자열)."""
    days = workdays(start, end)
    data = {}
    for m in team.member_records():
        row = {}
        for d in days:
            row[d] = ""
            for lv in team.leave_records():
                if lv["member_id"] == m["id"] and lv["start"] <= d <= lv["end"]:
                    row[d] = lv["type"]
                    break
        data[m["name"]] = row
    return pd.DataFrame.from_dict(data, orient="index", columns=days)


def overlap_periods(team: TeamData, start: date, end: date, min_people: int = 2) -> list[dict]:
    """동시에 min_people명 이상 부재(종일 기준, 반차 포함)인 연속 구간."""
    names = {m["id"]: m["name"] for m in team.member_records()}
    periods, cur = [], None
    for d in workdays(start, end):
        who = sorted({names.get(lv["member_id"], "?") for lv in team.leave_records()
                      if lv["start"] <= d <= lv["end"]})
        if len(who) >= min_people:
            if cur and cur["_last"] == d - timedelta(days=1 if d.weekday() else 3):
                cur["end"], cur["_last"] = d, d
                cur["max"] = max(cur["max"], len(who))
                cur["members"] |= set(who)
            else:
                if cur:
                    periods.append(cur)
                cur = {"start": d, "end": d, "_last": d, "max": len(who), "members": set(who)}
        elif cur:
            periods.append(cur)
            cur = None
    if cur:
        periods.append(cur)
    return [{"시작": p["start"], "종료": p["end"], "최대 동시 부재": p["max"],
             "해당 팀원": ", ".join(sorted(p["members"]))} for p in periods]
