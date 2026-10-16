"""규칙 기반 질의 처리(LLM 없이 동작). 실제 데이터에 근거해서만 답한다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .calendar import month_range, week_range
from .config import merge_rules
from .handover import generate_handover
from .models import TeamData, isnull
from .risk import HIGH, INSUFFICIENT, MEDIUM, absent_map, assess_tasks, daily_summary, format_reasons

SUPPORTED = [
    "다음 주에 업무 공백 위험이 높은 날짜는?",
    "휴가 중인 핵심 담당자가 누구야?",
    "대체 담당자가 지정되지 않은 업무를 알려줘.",
    "이번 주 휴가 인수인계 문서를 만들어줘.",
    "오늘 부재 인원은? / 위험도가 높은 업무 목록 보여줘.",
]


@dataclass
class Answer:
    text: str
    tables: list = field(default_factory=list)
    documents: list = field(default_factory=list)  # (파일명, markdown)
    supported: bool = True
    facts: str = ""  # LLM 보조 문장화에 쓰이는 근거 데이터


def parse_period(q: str, today: date, default_days: int | None = None):
    """질문에서 기간 추출 -> (start, end, label) 또는 None."""
    if "다음 주" in q or "다음주" in q:
        s, e = week_range(today + timedelta(days=7))
        return s, e, "다음 주"
    if "이번 주" in q or "이번주" in q:
        s, e = week_range(today)
        return s, e, "이번 주"
    if "다음 달" in q:
        s, e = month_range((today.replace(day=1) + timedelta(days=32)).replace(day=1))
        return s, e, "다음 달"
    if "이번 달" in q:
        s, e = month_range(today)
        return s, e, "이번 달"
    if "내일" in q:
        return today + timedelta(days=1), today + timedelta(days=1), "내일"
    if "오늘" in q:
        return today, today, "오늘"
    if default_days:
        return today, today + timedelta(days=default_days - 1), f"오늘부터 {default_days}일"
    return None


def answer_question(q: str, team: TeamData, today: date, rules: dict | None = None) -> Answer:
    rules = merge_rules(rules)
    q = q.strip()
    if not q:
        return Answer("질문을 입력해 주세요.", supported=False)
    if len(team.members) == 0:
        return Answer("등록된 팀원이 없어 답변할 수 있는 데이터가 없습니다. '데이터 관리'에서 먼저 입력해 주세요.", supported=False)

    if "인수인계" in q:
        return _handover(q, team, today, rules)
    if "대체" in q and re.search(r"지정|없|미|안\s?된", q):
        return _no_backup(team)
    if "핵심" in q and "담당" in q:
        return _key_owners(q, team, today, rules)
    if "위험" in q and re.search(r"날짜|언제|며칠|일자|요일", q):
        return _risky_dates(q, team, today, rules)
    if "위험" in q and re.search(r"업무|목록|항목", q):
        return _risky_tasks(q, team, today, rules)
    if re.search(r"부재|휴가", q) and re.search(r"누구|인원|현황|명", q):
        return _absentees(q, team, today, rules)
    return Answer("이 질문은 아직 지원하지 않습니다(데이터에 없는 내용을 추측해 답하지 않습니다).\n\n지원하는 질문 예시:\n"
                  + "\n".join(f"- {s}" for s in SUPPORTED), supported=False)


def _risky_dates(q, team, today, rules) -> Answer:
    s, e, label = parse_period(q, today, default_days=14)
    df = daily_summary(team, s, e, rules)
    if df.empty:
        return Answer(f"{label}({s}~{e})에는 근무일이 없습니다.")
    hit = df[df["위험도"] == HIGH]
    insuff = df[df["데이터 부족 업무"] > 0]
    note = f"\n\n※ 담당자 미지정 등 데이터 부족으로 위험도를 계산하지 못한 업무가 {int(insuff['데이터 부족 업무'].max())}건 있어, '안전'으로 단정할 수 없습니다." if not insuff.empty else ""
    if hit.empty:
        med = df[df["위험도"] == MEDIUM]
        msg = f"{label}({s}~{e}) 중 High 위험 날짜는 없습니다."
        if not med.empty:
            msg += " Medium 날짜: " + ", ".join(str(d) for d in med["날짜"])
        return Answer(msg + note, [df], facts=msg)
    dates = ", ".join(f"{d}({'월화수목금'[d.weekday()]})" for d in hit["날짜"])
    msg = f"{label}({s}~{e}) 중 High 위험 날짜: {dates}"
    return Answer(msg + note, [df[["날짜", "부재", "가용 인원", "High", "Medium", "위험도"]]], facts=msg)


def _key_owner_rows(team, s, e, rules) -> list[dict]:
    rows = []
    for t in team.task_records():
        if t.get("importance") != "상" or isnull(t.get("owner_id")) or t.get("status") == "완료":
            continue
        absent_days = [d for d in pd.date_range(s, e).date
                       if d.weekday() < 5 and t["owner_id"] in absent_map(team, d, rules)]
        if absent_days:
            rows.append({"담당자": team.name(t["owner_id"]), "업무(중요도 상)": t["name"],
                         "부재 시작일": absent_days[0], "대체 담당자": team.name(t.get("backup_id")),
                         "마감일": t.get("due_date")})
    return rows


def _key_owners(q, team, today, rules) -> Answer:
    period = parse_period(q, today)
    s, e, label = period or (today, today, "오늘")
    rows = _key_owner_rows(team, s, e, rules)
    if rows:
        names = ", ".join(sorted({r["담당자"] for r in rows}))
        msg = f"{label}({s}~{e}) 부재 중인 핵심 담당자(중요도 '상' 업무 보유): {names}"
        return Answer(msg, [pd.DataFrame(rows)], facts=msg)
    msg = f"{label}({s}~{e})에는 중요도 '상' 업무 담당자 중 부재자가 없습니다."
    if period is None:  # 기간 미지정이면 향후 기간까지 확인
        e2 = today + timedelta(days=rules["horizon_days"])
        rows = _key_owner_rows(team, today, e2, rules)
        if rows:
            msg += f" 다만 향후 {rules['horizon_days']}일 내 휴가 예정인 핵심 담당자는 {', '.join(sorted({r['담당자'] for r in rows}))}입니다."
            return Answer(msg, [pd.DataFrame(rows)], facts=msg)
    return Answer(msg + " (기준: 중요도 '상'인 미완료 업무의 담당자)", facts=msg)


def _no_backup(team) -> Answer:
    rows = [{"업무": t["name"], "담당자": team.name(t.get("owner_id")), "중요도": t.get("importance"),
             "마감일": t.get("due_date"), "상태": t.get("status")}
            for t in team.task_records() if isnull(t.get("backup_id")) and t.get("status") != "완료"]
    if not rows:
        return Answer("진행 중인 모든 업무에 대체 담당자가 지정되어 있습니다.")
    msg = f"대체 담당자가 지정되지 않은 업무는 {len(rows)}건입니다."
    return Answer(msg, [pd.DataFrame(rows)], facts=msg + " " + ", ".join(r["업무"] for r in rows))


def _risky_tasks(q, team, today, rules) -> Answer:
    s, e, label = parse_period(q, today, default_days=rules["horizon_days"])
    df = assess_tasks(team, s, e, rules)
    hit = df[df["위험도"].isin([HIGH])]
    if hit.empty:
        return Answer(f"{label}({s}~{e}) High 위험 업무가 없습니다.")
    view = hit[["업무", "담당자", "위험도", "점수", "대표 날짜"]].copy()
    view["근거"] = hit["근거"].map(format_reasons)
    return Answer(f"{label}({s}~{e}) High 위험 업무는 {len(hit)}건입니다.", [view], facts=f"High {len(hit)}건: " + ", ".join(hit["업무"]))


def _absentees(q, team, today, rules) -> Answer:
    s, e, label = parse_period(q, today) or (today, today, "오늘")
    rows = [{"팀원": team.name(lv["member_id"]), "유형": lv["type"], "시작": lv["start"], "종료": lv["end"]}
            for lv in team.leave_records() if lv["start"] <= e and s <= lv["end"]]
    if not rows:
        return Answer(f"{label}({s}~{e}) 휴가 예정자가 없습니다.")
    return Answer(f"{label}({s}~{e}) 휴가자는 {len(rows)}명입니다.", [pd.DataFrame(rows)], facts=f"{len(rows)}명")


def _handover(q, team, today, rules) -> Answer:
    s, e, label = parse_period(q, today, default_days=7)
    docs = []
    for lv in team.leave_records():
        if lv["start"] <= e and s <= lv["end"]:
            md = generate_handover(team, lv["member_id"], lv["start"], lv["end"], lv["type"], today)
            docs.append((f"handover_{team.name(lv['member_id'])}_{lv['start']}.md", md))
    if not docs:
        return Answer(f"{label}({s}~{e})에 해당하는 휴가가 없어 생성할 인수인계 문서가 없습니다.")
    return Answer(f"{label}({s}~{e}) 휴가자 {len(docs)}명의 인수인계 문서를 생성했습니다. 아래에서 확인·다운로드하세요.",
                  documents=docs, facts=f"{len(docs)}건 생성")
