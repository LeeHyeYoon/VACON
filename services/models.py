"""공통 상수, 데이터 컨테이너. 화면/DB와 무관한 순수 Python."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import pandas as pd

LEAVE_TYPES = ["연차", "반차", "병가", "경조사", "교육", "출장"]
IMPORTANCE = ["상", "중", "하"]
STATUSES = ["대기", "진행중", "지연", "완료"]
SKILL_LEVELS = {1: "기초", 2: "중급", 3: "전문"}

MBTI_TYPES = [a + b + c + d for a in "EI" for b in "NS" for c in "TF" for d in "JP"]
MEMBER_COLS = ["id", "name", "role", "workload", "contact", "mbti"]
SKILL_COLS = ["member_id", "skill", "level"]
TASK_COLS = ["id", "name", "owner_id", "backup_id", "importance", "status", "progress",
             "due_date", "required_skill", "remaining", "notes", "load_pct"]
LEAVE_COLS = ["id", "member_id", "start", "end", "type", "note"]


def today() -> date:
    """오늘 날짜. 시연/테스트를 위해 VACON_TODAY(YYYY-MM-DD)로 고정 가능."""
    override = os.environ.get("VACON_TODAY")
    return date.fromisoformat(override) if override else date.today()


def isnull(v) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _clean_id(v):
    return None if isnull(v) else int(v)


@dataclass
class TeamData:
    members: pd.DataFrame
    skills: pd.DataFrame
    tasks: pd.DataFrame
    leaves: pd.DataFrame

    def __post_init__(self):
        for col in ("owner_id", "backup_id"):
            self.tasks[col] = pd.Series([_clean_id(v) for v in self.tasks[col]],
                                        index=self.tasks.index, dtype=object)

    def name(self, member_id) -> str:
        if isnull(member_id):
            return "미지정"
        row = self.members[self.members["id"] == member_id]
        return str(row.iloc[0]["name"]) if not row.empty else "알 수 없음"

    def member_ids(self) -> set:
        return {int(i) for i in self.members["id"]}

    def skill_level(self, member_id, skill) -> int:
        if not skill:
            return 0
        r = self.skills[(self.skills["member_id"] == member_id) & (self.skills["skill"] == skill)]
        return int(r.iloc[0]["level"]) if not r.empty else 0

    def task_records(self) -> list[dict]:
        return self.tasks.to_dict("records")

    def leave_records(self) -> list[dict]:
        return self.leaves.to_dict("records")

    def member_records(self) -> list[dict]:
        return self.members.to_dict("records")


def make_team(members=(), skills=(), tasks=(), leaves=()) -> TeamData:
    """dict 리스트로 TeamData 생성(테스트/DB 로드용). 누락 컬럼은 None."""
    def build(rows, cols):
        if not rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(list(rows), columns=cols).astype(object)
    return TeamData(build(members, MEMBER_COLS), build(skills, SKILL_COLS),
                    build(tasks, TASK_COLS), build(leaves, LEAVE_COLS))
