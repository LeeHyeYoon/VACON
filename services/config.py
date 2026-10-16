"""위험도 판단 규칙(설정 가능). 가중치 합산 점수 방식."""
from __future__ import annotations

import copy

DEFAULT_RULES = {
    "weights": {
        "owner_absent": 30,          # 담당자가 부재
        "importance_high": 20,       # 업무 중요도 '상'
        "importance_mid": 10,        # 업무 중요도 '중'
        "no_backup": 25,             # 대체 담당자 미지정
        "backup_absent": 25,         # 대체 담당자도 부재
        "deadline_overlap": 20,      # 마감일이 부재 기간과 겹침
        "no_available_skilled": 20,  # 해당 역량을 가진 근무 가능 인력이 없음
        "low_team_availability": 10, # 팀 가용 인원 비율이 낮음
    },
    "thresholds": {"high": 75, "medium": 40},  # 점수 >= 기준이면 해당 등급
    "deadline_window_days": 3,       # 부재일 기준 N일 이내 마감이면 '겹침'
    "low_availability_ratio": 0.5,   # 가용 인원 비율이 이 값 미만이면 가산
    "half_day_counts_as_absent": False,
    "horizon_days": 14,              # 대시보드 분석 기간
}


def merge_rules(custom: dict | None) -> dict:
    rules = copy.deepcopy(DEFAULT_RULES)
    for key, val in (custom or {}).items():
        if isinstance(val, dict) and isinstance(rules.get(key), dict):
            rules[key].update(val)
        else:
            rules[key] = val
    return rules


def validate_rules(rules: dict) -> list[str]:
    errors = []
    t = rules["thresholds"]
    if not (0 < t["medium"] < t["high"]):
        errors.append("임계값은 0 < Medium < High 이어야 합니다.")
    if any(v < 0 for v in rules["weights"].values()):
        errors.append("가중치는 0 이상이어야 합니다.")
    if rules["deadline_window_days"] < 0:
        errors.append("마감 인접 기준일은 0 이상이어야 합니다.")
    if not (0 <= rules["low_availability_ratio"] <= 1):
        errors.append("가용 인원 비율 기준은 0~1 사이여야 합니다.")
    if rules["horizon_days"] < 1:
        errors.append("분석 기간은 1일 이상이어야 합니다.")
    return errors
