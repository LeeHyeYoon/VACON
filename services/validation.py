"""입력값 검증. 오류 메시지 리스트를 반환(비어 있으면 통과)."""
from __future__ import annotations

from datetime import date

from .models import IMPORTANCE, LEAVE_TYPES, STATUSES, TeamData


def validate_leave(team: TeamData, member_id, start, end, leave_type, exclude_id=None) -> list[str]:
    errors = []
    if member_id is None or member_id not in team.member_ids():
        errors.append("존재하지 않는 팀원입니다.")
    if not isinstance(start, date) or not isinstance(end, date):
        return errors + ["시작일과 종료일을 입력하세요."]
    if end < start:
        errors.append("종료일은 시작일보다 빠를 수 없습니다.")
    if leave_type not in LEAVE_TYPES:
        errors.append(f"휴가 유형은 {', '.join(LEAVE_TYPES)} 중 하나여야 합니다.")
    if leave_type == "반차" and start != end:
        errors.append("반차는 하루만 등록할 수 있습니다.")
    if not errors:
        for lv in team.leave_records():
            if lv["member_id"] == member_id and lv["id"] != exclude_id \
                    and lv["start"] <= end and start <= lv["end"]:
                errors.append(f"같은 팀원의 기존 휴가({lv['start']}~{lv['end']})와 기간이 겹칩니다.")
                break
    return errors


def validate_task(name, importance, status, progress, load_pct) -> list[str]:
    errors = []
    if not str(name or "").strip():
        errors.append("업무명을 입력하세요.")
    if importance not in IMPORTANCE:
        errors.append("중요도는 상/중/하 중 하나여야 합니다.")
    if status not in STATUSES:
        errors.append("진행 상태가 올바르지 않습니다.")
    if not (0 <= int(progress) <= 100):
        errors.append("진행률은 0~100 이어야 합니다.")
    if not (0 <= int(load_pct) <= 100):
        errors.append("예상 업무 부담은 0~100 이어야 합니다.")
    return errors


def validate_member(name, workload) -> list[str]:
    errors = []
    if not str(name or "").strip():
        errors.append("이름을 입력하세요.")
    if not (0 <= int(workload) <= 150):
        errors.append("업무량은 0~150(%) 이어야 합니다.")
    return errors
