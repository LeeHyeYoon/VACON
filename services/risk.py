"""업무 공백 위험 분석. 모든 판단에 근거(reasons)와 점수 기여도를 함께 반환한다."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .config import merge_rules
from .models import IMPORTANCE, TeamData, isnull

HIGH, MEDIUM, LOW, INSUFFICIENT = "High", "Medium", "Low", "데이터 부족"
_ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2}
_SORT = {HIGH: 0, MEDIUM: 1, INSUFFICIENT: 2, LOW: 3}

ACTIONS = {
    "no_backup": "대체 담당자를 지정하세요(‘대체 담당자’ 메뉴에서 후보 확인).",
    "backup_absent": "지정된 대체 담당자도 부재입니다. 다른 대체 담당자로 재지정하세요.",
    "deadline_overlap": "마감일을 조정하거나 휴가 전에 선완료/중간 산출물을 공유하세요.",
    "no_available_skilled": "해당 역량 보유자가 없습니다. 교육·외부 지원 또는 업무 일정 조정을 검토하세요.",
    "low_team_availability": "같은 기간 휴가가 몰려 있습니다. 휴가 일정 분산을 협의하세요.",
    "owner_absent": "인수인계 문서를 생성하고 휴가 전 진행 상황을 공유하세요.",
}


def workdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def absent_map(team: TeamData, d: date, rules: dict) -> dict:
    """d일에 (종일) 부재인 member_id -> 휴가유형. 반차는 설정에 따라 제외."""
    res = {}
    for lv in team.leave_records():
        if lv["start"] <= d <= lv["end"]:
            if lv["type"] == "반차" and not rules["half_day_counts_as_absent"]:
                continue
            res[int(lv["member_id"])] = lv["type"]
    return res


def available_headcount(team: TeamData, d: date, rules: dict) -> float:
    """가용 인원(반차는 0.5명으로 계산)."""
    total = float(len(team.members))
    absent = absent_map(team, d, rules)
    half = sum(1 for lv in team.leave_records()
               if lv["type"] == "반차" and lv["start"] <= d <= lv["end"]
               and int(lv["member_id"]) not in absent)
    return total - len(absent) - 0.5 * half


def _level(score: float, rules: dict) -> str:
    t = rules["thresholds"]
    return HIGH if score >= t["high"] else MEDIUM if score >= t["medium"] else LOW


def assess_task_day(team: TeamData, task: dict, d: date, rules: dict | None = None,
                    absent: dict | None = None) -> dict:
    """한 업무의 하루 위험도. 반환: level, score, reasons[(code,text,points)]."""
    rules = merge_rules(rules)
    w = rules["weights"]
    absent = absent if absent is not None else absent_map(team, d, rules)

    gaps = []
    owner = task.get("owner_id")
    if isnull(owner) or owner not in team.member_ids():
        gaps.append("담당자 미지정")
    if task.get("importance") not in IMPORTANCE:
        gaps.append("업무 중요도 미입력")
    if gaps:
        return {"level": INSUFFICIENT, "score": None,
                "reasons": [("insufficient", "위험도를 계산할 수 없습니다: " + ", ".join(gaps), 0)]}
    if task.get("status") == "완료":
        return {"level": LOW, "score": 0, "reasons": [("done", "완료된 업무입니다.", 0)]}
    if owner not in absent:
        return {"level": LOW, "score": 0, "reasons": [("ok", "담당자가 정상 근무합니다.", 0)]}

    name = team.name(owner)
    reasons = [("owner_absent", f"담당자 {name}님이 {absent[owner]}로 부재", w["owner_absent"])]
    imp = task["importance"]
    if imp == "상":
        reasons.append(("importance_high", "업무 중요도 '상'", w["importance_high"]))
    elif imp == "중":
        reasons.append(("importance_mid", "업무 중요도 '중'", w["importance_mid"]))

    backup = task.get("backup_id")
    if isnull(backup):
        reasons.append(("no_backup", "대체 담당자 미지정", w["no_backup"]))
    elif backup in absent:
        reasons.append(("backup_absent", f"대체 담당자 {team.name(backup)}님도 같은 날 부재", w["backup_absent"]))

    due = task.get("due_date")
    if not isnull(due) and d <= due <= d + timedelta(days=rules["deadline_window_days"]):
        reasons.append(("deadline_overlap", f"마감일({due})이 부재 기간과 겹치거나 임박", w["deadline_overlap"]))

    req = task.get("required_skill")
    if req:
        skilled_present = [m for m in team.skills[team.skills["skill"] == req]["member_id"]
                           if m != owner and m not in absent]
        if not skilled_present:
            reasons.append(("no_available_skilled", f"'{req}' 역량 보유자 중 근무 가능 인력 없음",
                            w["no_available_skilled"]))

    ratio = available_headcount(team, d, rules) / max(len(team.members), 1)
    if ratio < rules["low_availability_ratio"]:
        reasons.append(("low_team_availability", f"팀 가용 인원 {ratio:.0%}로 낮음", w["low_team_availability"]))

    score = sum(p for _, _, p in reasons)
    return {"level": _level(score, rules), "score": score, "reasons": reasons}


def assess_tasks(team: TeamData, start: date, end: date, rules: dict | None = None) -> pd.DataFrame:
    """기간 내 업무별 최악의 날 기준 위험도. 근거와 대응 방안 포함."""
    rules = merge_rules(rules)
    days = workdays(start, end)
    rows = []
    for task in team.task_records():
        worst, absent_days = None, []
        for d in days:
            a = absent_map(team, d, rules)
            res = assess_task_day(team, task, d, rules, a)
            if res["level"] != INSUFFICIENT and task.get("owner_id") in a:
                absent_days.append(d)
            key = (-1 if res["level"] == INSUFFICIENT else _ORDER[res["level"]], res["score"] or 0)
            if worst is None or key > worst[0]:
                worst = (key, d, res)
        if worst is None:  # 근무일이 없는 기간: 안전하다고 단정하지 않음
            res, wd = {"level": INSUFFICIENT, "score": None,
                       "reasons": [("insufficient", "선택한 기간에 근무일이 없어 위험도를 계산할 수 없습니다.", 0)]}, start
        else:
            _, wd, res = worst
        codes = [c for c, _, _ in res["reasons"]]
        rows.append({
            "task_id": task["id"], "업무": task["name"], "담당자": team.name(task.get("owner_id")),
            "대체 담당자": team.name(task.get("backup_id")), "중요도": task.get("importance"),
            "마감일": task.get("due_date"), "위험도": res["level"], "점수": res["score"],
            "대표 날짜": wd, "부재일수": len(absent_days),
            "근거": res["reasons"], "대응 방안": [ACTIONS[c] for c in codes if c in ACTIONS],
        })
    cols = ["task_id", "업무", "담당자", "대체 담당자", "중요도", "마감일", "위험도", "점수",
            "대표 날짜", "부재일수", "근거", "대응 방안"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    df["_s"] = df["위험도"].map(_SORT)
    df = df.sort_values(["_s", "점수"], ascending=[True, False], na_position="last")
    return df.drop(columns="_s").reset_index(drop=True)


def daily_summary(team: TeamData, start: date, end: date, rules: dict | None = None) -> pd.DataFrame:
    """근무일별 가용 인원과 위험 업무 수. 위험도 계산 불가 업무가 있으면 '데이터 부족' 표시."""
    rules = merge_rules(rules)
    tasks = team.task_records()
    rows = []
    for d in workdays(start, end):
        a = absent_map(team, d, rules)
        counts = {HIGH: 0, MEDIUM: 0, INSUFFICIENT: 0}
        for t in tasks:
            lvl = assess_task_day(team, t, d, rules, a)["level"]
            if lvl in counts:
                counts[lvl] += 1
        level = HIGH if counts[HIGH] else MEDIUM if counts[MEDIUM] else \
            INSUFFICIENT if counts[INSUFFICIENT] else LOW
        rows.append({"날짜": d, "전체": len(team.members), "부재": len(a),
                     "가용 인원": available_headcount(team, d, rules),
                     "High": counts[HIGH], "Medium": counts[MEDIUM],
                     "데이터 부족 업무": counts[INSUFFICIENT], "위험도": level})
    return pd.DataFrame(rows, columns=["날짜", "전체", "부재", "가용 인원", "High", "Medium",
                                       "데이터 부족 업무", "위험도"])


def tasks_on_day(team: TeamData, d: date, rules: dict | None = None) -> pd.DataFrame:
    rules = merge_rules(rules)
    a = absent_map(team, d, rules)
    rows = []
    for t in team.task_records():
        res = assess_task_day(team, t, d, rules, a)
        rows.append({"업무": t["name"], "담당자": team.name(t.get("owner_id")), "위험도": res["level"],
                     "점수": res["score"], "근거": res["reasons"]})
    df = pd.DataFrame(rows, columns=["업무", "담당자", "위험도", "점수", "근거"])
    if df.empty:
        return df
    df["_s"] = df["위험도"].map(_SORT)
    return df.sort_values(["_s", "점수"], ascending=[True, False]).drop(columns="_s").reset_index(drop=True)


def format_reasons(reasons) -> str:
    return " / ".join(f"{t}(+{p})" if p else t for _, t, p in reasons)
