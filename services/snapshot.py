"""AI Agent 프롬프트에 항상 함께 넣는 VACON 기본 데이터 스냅샷.

팀원·역량·업무·휴가·버전 목록과 최근 업무 로그 요약을 짧은 텍스트로 만든다.
연락처 등 분석에 불필요한 개인 정보는 넣지 않고, 메모·비고는 잘라서 넣는다.
위험 점수·대체자 추천·작업량 계산·로그 상세 검색은 계속 도구가 담당한다.
"""
from __future__ import annotations

from datetime import date, timedelta

from .models import isnull
from .worklog import METRIC_COLS, METRIC_LABELS, TRACKS

DEFAULT_LIMIT = 24000      # 문자 수. 사내 모델 컨텍스트(약 10만 토큰)에서 질문·도구 결과 여유를 남긴다.
LEAVE_PAST_DAYS = 30
LOG_RECENT_DAYS = 28
NOTE_LIMIT = 120


def _s(value, default="-") -> str:
    if value is None or isnull(value) or str(value).strip() == "":
        return default
    return " ".join(str(value).split())


def _note(value) -> str:
    text = _s(value, "")
    return text if len(text) <= NOTE_LIMIT else text[:NOTE_LIMIT] + "…"


_WEEKDAYS = "월화수목금토일"


def _d(value) -> str:
    """날짜에 요일을 붙인다(LLM이 요일을 추측하지 않도록)."""
    if isinstance(value, date):
        return f"{value.isoformat()}({_WEEKDAYS[value.weekday()]})"
    return _s(value)


def _due(value, status, today: date) -> str:
    if not isinstance(value, date):
        return _s(value)
    if status == "완료":
        return _d(value)
    left = (value - today).days
    mark = "오늘 마감" if left == 0 else (f"D-{left}" if left > 0 else f"{-left}일 경과")
    return f"{_d(value)} {mark}"


def _num(value) -> float:
    try:
        return 0.0 if value is None or isnull(value) else float(value)
    except (TypeError, ValueError):
        return 0.0


def _members(team) -> list[str]:
    skills: dict = {}
    for row in team.skills.to_dict("records"):
        skills.setdefault(row.get("member_id"), []).append(f"{row.get('skill')}({_s(row.get('level'))})")
    lines = ["## 팀원 (이름 | 역할 | 업무 부하% | 역량(수준 1기초·2중급·3전문))"]
    for m in team.member_records():
        lines.append(f"- {m['name']} | {_s(m.get('role'))} | {_s(m.get('workload'))} | "
                     f"{', '.join(skills.get(m['id'], [])) or '-'}")
    return lines


def _tasks(team, today: date) -> list[str]:
    lines = [f"## 업무 (업무명 | 담당 | 대체 담당 | 상태 | 진행률% | 중요도 | 마감(오늘 {_d(today)} 기준) | 필요 역량 | 남은 작업 | 메모)"]
    for t in team.task_records():
        backup = team.name(t["backup_id"]) if not isnull(t.get("backup_id")) else "미지정"
        lines.append(f"- {t['name']} | {team.name(t.get('owner_id'))} | {backup} | {_s(t.get('status'))} | "
                     f"{_s(t.get('progress'))} | {_s(t.get('importance'))} | {_due(t.get('due_date'), t.get('status'), today)} | "
                     f"{_s(t.get('required_skill'))} | {_note(t.get('remaining')) or '-'} | {_note(t.get('notes')) or '-'}")
    if len(lines) == 1:
        lines.append("- 없음")
    return lines


def _leaves(team, today: date) -> list[str]:
    since = today - timedelta(days=LEAVE_PAST_DAYS)
    rows = sorted((lv for lv in team.leave_records() if lv["end"] >= since), key=lambda lv: lv["start"])
    lines = [f"## 휴가 ({since} 이후 종료분, 이름 | 기간 | 유형)"]
    for lv in rows:
        days = (lv["end"] - lv["start"]).days + 1
        lines.append(f"- {team.name(lv['member_id'])} | {_d(lv['start'])}~{_d(lv['end'])} ({days}일) | {_s(lv.get('type'))}")
    if len(lines) == 1:
        lines.append("- 없음")
    return lines


def _versions(team, versions) -> list[str]:
    lines = ["## 버전 (버전 | 상태 | 사양서 기간 | 사양서 담당 | 검증 기간 | 검증 담당 | 사양서 완료/전체 | 검증 완료/전체)"]
    for v in versions or []:
        spec_owners = ", ".join(team.name(i) for i in v.get("spec_owner_ids") or []) or "-"
        verify_owners = ", ".join(team.name(i) for i in v.get("verify_owner_ids") or []) or "-"
        lines.append(f"- {v.get('version')} | {_s(v.get('status'))} | {_d(v.get('spec_start'))}~{_d(v.get('spec_due'))} | "
                     f"{spec_owners} | {_d(v.get('verify_start'))}~{_d(v.get('verify_due'))} | {verify_owners} | "
                     f"{_s(v.get('spec_done'), '0')}/{_s(v.get('spec_total_items'), '0')} | "
                     f"{_s(v.get('verify_done'), '0')}/{_s(v.get('verify_total_items'), '0')}")
    if len(lines) == 1:
        lines.append("- 없음")
    return lines


def _log_summary(team, today: date) -> list[str]:
    since = today - timedelta(days=LOG_RECENT_DAYS - 1)
    lines = [f"## 최근 {LOG_RECENT_DAYS}일 업무 로그 요약 ({since}~{today}, 담당자·업무 유형별 합계. 상세는 search_worklogs)"]
    logs = team.logs
    if logs is None or logs.empty:
        return lines + ["- 없음"]
    agg: dict = {}
    for row in logs.to_dict("records"):
        d = row.get("date")
        if not isinstance(d, date) or not (since <= d <= today):
            continue
        track = row.get("track")
        if track not in METRIC_COLS:
            continue
        key = (team.name(row.get("member_id")), track)
        entry = agg.setdefault(key, {"days": set(), "versions": set(), **{m: 0.0 for m in METRIC_COLS[track]}})
        entry["days"].add(d)
        if _s(row.get("version"), ""):
            entry["versions"].add(str(row["version"]))
        for metric in METRIC_COLS[track]:
            entry[metric] += _num(row.get(metric))
    for (name, track), e in sorted(agg.items()):
        metrics = ", ".join(f"{METRIC_LABELS[m]} {e[m]:.0f}" for m in METRIC_COLS[track])
        lines.append(f"- {name} | {TRACKS[track]} | 기록일 {len(e['days'])}일 | {metrics} | "
                     f"버전 {', '.join(sorted(e['versions'])) or '-'}")
    if len(lines) == 1:
        lines.append("- 없음")
    return lines


def _load_versions():
    try:
        from data import db
        return db.list_version_projects()
    except Exception:
        return []


def data_snapshot(team, today: date, *, versions=None, limit: int = DEFAULT_LIMIT) -> str:
    """기본 데이터 스냅샷 텍스트. versions를 주지 않으면 DB에서 버전 목록을 읽는다."""
    if versions is None:
        versions = _load_versions()
    sections = [_members(team), _tasks(team, today), _leaves(team, today), _versions(team, versions), _log_summary(team, today)]
    text = "\n\n".join("\n".join(section) for section in sections)
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] + "\n- …(분량 제한으로 이후 생략. 필요한 내용은 도구로 조회)"
    return text
