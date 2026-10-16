"""대체 담당자 추천. 점수는 '검토용 참고값'이며 자동 확정하지 않는다."""
from __future__ import annotations

from datetime import date

from .config import merge_rules
from .models import SKILL_LEVELS, TeamData, isnull
from .risk import absent_map, workdays

DEFAULT_LOAD = 10


def recommend_backups(team: TeamData, task_id: int, start: date, end: date,
                      rules: dict | None = None, top_n: int = 5) -> dict:
    rules = merge_rules(rules)
    task = next((t for t in team.task_records() if t["id"] == task_id), None)
    if task is None:
        raise ValueError("존재하지 않는 업무입니다.")
    days = workdays(start, end)
    req = task.get("required_skill") or ""
    load = DEFAULT_LOAD if isnull(task.get("load_pct")) else int(task["load_pct"])
    warnings, candidates, excluded = [], [], []
    if not days:
        warnings.append("선택한 기간에 근무일이 없습니다.")
    if not req:
        warnings.append("필수 역량이 미입력이어서 역량 기준 평가는 제외했습니다(데이터 관리에서 입력).")
    if isnull(task.get("load_pct")):
        warnings.append(f"예상 업무 부담이 미입력이어서 기본값 {DEFAULT_LOAD}%p로 계산했습니다.")

    for m in team.member_records():
        if m["id"] == task.get("owner_id"):
            continue
        level = team.skill_level(m["id"], req)
        if req and level == 0:
            excluded.append({"이름": m["name"], "제외 사유": f"필수 역량 '{req}' 없음"})
            continue
        absent_days = [d for d in days if m["id"] in absent_map(team, d, rules)]
        if absent_days:
            excluded.append({"이름": m["name"],
                             "제외 사유": f"대체 기간 중 부재({absent_days[0]} 외 {len(absent_days) - 1}일)"
                             if len(absent_days) > 1 else f"대체 기간 중 부재({absent_days[0]})"})
            continue

        workload = 0 if isnull(m.get("workload")) else int(m["workload"])
        new_load = workload + load
        reasons, cautions = [], []
        score = (level * 15 if req else 22) + 25 + (100 - min(workload, 100)) * 0.3
        if req:
            reasons.append(f"'{req}' 역량 보유(Lv.{level} {SKILL_LEVELS.get(level, '')})")
        reasons.append("대체 기간 전체 근무 가능")
        reasons.append(f"현재 업무량 {workload}%")
        if task.get("backup_id") == m["id"]:
            score += 5
            reasons.append("이미 대체 담당자로 지정됨")
        if new_load > 100:
            score -= 15
            cautions.append(f"과부하 우려: 예상 업무량 {new_load}%")
        elif new_load > 90:
            cautions.append(f"여유가 적음: 예상 업무량 {new_load}%")
        own_due = [t["name"] for t in team.task_records()
                   if t.get("owner_id") == m["id"] and t.get("status") != "완료"
                   and not isnull(t.get("due_date")) and start <= t["due_date"] <= end]
        if own_due:
            score -= 10
            cautions.append("본인 업무 마감과 겹침: " + ", ".join(own_due))
        if any(lv["member_id"] == m["id"] and lv["type"] == "반차" and start <= lv["start"] <= end
               for lv in team.leave_records()):
            score -= 5
            cautions.append("대체 기간 중 반차 예정")
        due = task.get("due_date")
        if not isnull(due) and due > end and m["id"] in absent_map(team, due, rules):
            cautions.append(f"마감일({due})에 부재 예정")
        if level == 1:
            cautions.append("역량이 기초 수준이므로 인수인계 시 추가 지원 필요")
        candidates.append({
            "member_id": m["id"], "이름": m["name"], "직무": m["role"],
            "추천 점수": round(max(0, min(100, score))), "역량 수준": level,
            "추천 이유": reasons, "예상 업무 부담": f"현재 {workload}% → 예상 {new_load}% (+{load}%p)",
            "주의사항": cautions, "현재 지정": task.get("backup_id") == m["id"],
        })
    candidates.sort(key=lambda c: c["추천 점수"], reverse=True)
    return {"task": task, "candidates": candidates[:top_n], "excluded": excluded, "warnings": warnings}
