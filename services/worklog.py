"""단위 테스트 업무 로그(동적 검증 / 요구사양서 작성) 가져오기와 분석.
시트 한 행 = 한 사람의 하루 기록(같은 날 여러 행 가능)이라고 보고 합산한다. 열은 '열 문자'(A, B, …)로 지정한다."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import pandas as pd

from .config import merge_rules
from .models import UNIT_SKILL, TeamData
from .risk import absent_map, workdays
from .sheet_sync import SheetError, match_owner, parse_sheet_date

TRACKS = {"dynamic": "동적 검증", "req": "요구사양서 작성"}
LOG_ROLES = {"log_dynamic": "dynamic", "log_req": "req"}
METRIC_COLS = {"dynamic": ("tc", "req"), "req": ("issue",)}
METRIC_LABELS = {"tc": "실행 TC", "req": "완료 Req", "issue": "완료 일감"}
LOG_FIELDS = {
    "dynamic": [("date", "날짜"), ("member", "담당자"), ("version", "버전"), ("category", "업무 구분"),
                ("req", "완료한 Req"), ("tc", "실행한 TC")],
    "req": [("date", "날짜"), ("member", "담당자"), ("version", "버전"), ("category", "업무 구분"), ("issue", "완료 일감")],
}
OVERLOAD_PCT = 30  # 분담 시 평소 대비 증가율이 이 값 이상이면 부담 큼으로 표시


# ---------- 열 문자 ----------
def col_letter(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(letter: str) -> int:
    n = 0
    for ch in letter.strip().upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def valid_letter(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]{1,3}", str(s or "").strip()))


# ---------- 값 해석 ----------
def parse_count(v) -> tuple[float, str | None]:
    """숫자는 그대로, 텍스트(항목 목록)는 항목 개수로 센다. 반환: (값, 종류[None|'text'|'invalid'])"""
    s = str(v or "").strip()
    if not s:
        return 0.0, None
    try:
        x = float(s.replace(",", ""))
        return (x, None) if x >= 0 else (0.0, "invalid")
    except ValueError:
        items = [p for p in re.split(r"[,\n;]+", s) if p.strip()]
        return float(len(items)), "text"


def parse_logs(text: str, cfg: dict, team: TeamData) -> dict:
    """CSV 텍스트 → 로그 엔트리. 해석 불가 행은 건너뛰고 사유를 경고로 반환(추측하지 않음)."""
    track = LOG_ROLES.get(cfg.get("role"))
    if track is None:
        raise SheetError("로그용 탭 설정이 아닙니다.")
    cols = {f: str(c).strip().upper() for f, c in cfg.get("columns", {}).items() if c}
    for f in ("date", "member"):
        if f not in cols:
            raise SheetError(f"'{dict(LOG_FIELDS[track])[f]}' 열이 설정되지 않았습니다.")
    if any(not valid_letter(c) for c in cols.values()):
        raise SheetError("열 문자(A, B, …) 형식이 올바르지 않습니다.")
    if not any(f in cols for f in METRIC_COLS[track]):
        raise SheetError("집계할 숫자 열(" + ", ".join(METRIC_LABELS[m] for m in METRIC_COLS[track]) + ")이 설정되지 않았습니다.")
    rows = list(csv.reader(io.StringIO(text)))
    hr = int(cfg.get("header_row", 1))
    if hr < 1 or hr > len(rows):
        raise SheetError(f"헤더 행({hr})이 데이터 범위를 벗어났습니다(총 {len(rows)}행).")
    header = rows[hr - 1]
    changed = []
    for f, saved in (cfg.get("headers") or {}).items():
        if f in cols and saved:
            i = col_index(cols[f])
            cur = header[i].strip() if i < len(header) else ""
            if cur != saved:
                changed.append(f"{cols[f]}열 제목 '{saved}' → '{cur}'")
    if changed:
        raise SheetError("시트의 열 구성이 바뀐 것 같습니다(" + "; ".join(changed) + "). 열 매핑을 확인하고 다시 저장하세요.")

    entries, warnings, unmatched = [], [], set()
    text_cells = skipped = 0
    for n, row in enumerate(rows[hr:], start=hr + 1):
        if not any(c.strip() for c in row):
            continue
        get = lambda f: (row[col_index(cols[f])].strip() if f in cols and col_index(cols[f]) < len(row) else "")
        try:
            d = parse_sheet_date(get("date"))
        except ValueError as e:
            warnings.append(f"{n}행: {e} → 건너뜀"); skipped += 1
            continue
        if d is None:
            skipped += 1
            continue
        mid, note = match_owner(get("member"), team)
        if mid is None:
            unmatched.add(get("member") or "(빈 값)"); skipped += 1
            continue
        if note:
            warnings.append(f"{n}행: {note}")
        e = {"track": track, "date": d, "member_id": mid, "version": get("version"), "category": get("category"),
             "req": 0.0, "tc": 0.0, "issue": 0.0, "row": n}
        for m in ("req", "tc", "issue"):
            if m in cols:
                v, kind = parse_count(get(m))
                e[m] = v
                if kind == "text":
                    text_cells += 1
                elif kind == "invalid":
                    warnings.append(f"{n}행: {METRIC_LABELS[m]} 값 '{get(m)}'이(가) 음수라 0으로 처리")
        entries.append(e)
    return {"track": track, "entries": entries, "warnings": warnings, "unmatched_members": sorted(unmatched),
            "text_cells": text_cells, "skipped": skipped,
            "date_range": (min(e["date"] for e in entries), max(e["date"] for e in entries)) if entries else None}


# ---------- 분석 ----------
def _records(team: TeamData) -> list[dict]:
    return team.logs.to_dict("records") if len(team.logs) else []


def member_stats(team: TeamData, member_id: int, track: str, start: date, end: date, rules: dict | None = None) -> dict:
    """기간 내 개인 처리량. 일평균은 '근무일(주말·종일 휴가 제외)' 기준. 로그가 전혀 없으면 has_logs=False(0으로 간주하지 않음)."""
    rules = merge_rules(rules)
    days = [d for d in workdays(start, end) if member_id not in absent_map(team, d, rules)]
    recs = [r for r in _records(team) if r["member_id"] == member_id and r["track"] == track and start <= r["date"] <= end]
    cols = METRIC_COLS[track]
    sums = {m: sum(r[m] for r in recs) for m in cols}
    return {"workdays": len(days), "sum": sums, "has_logs": bool(recs),
            "avg": {m: (sums[m] / len(days) if days else None) for m in cols},
            "active_days": len({r["date"] for r in recs if any(r[m] > 0 for m in cols)}),
            "last_date": max((r["date"] for r in recs), default=None)}


def throughput_table(team: TeamData, today: date, window_days: int = 28, rules: dict | None = None) -> pd.DataFrame:
    start, end = today - timedelta(days=window_days - 1), today
    rows = []
    for mid in sorted(team.unit_pool()):
        for track, tname in TRACKS.items():
            st = member_stats(team, mid, track, start, end, rules)
            row = {"팀원": team.name(mid), "업무": tname, "근무일": st["workdays"], "활동일": st["active_days"],
                   "최근 기록일": st["last_date"] or "기록 없음"}
            for m in METRIC_COLS[track]:
                row[f"{METRIC_LABELS[m]} 합계"] = round(st["sum"][m]) if st["has_logs"] else "-"
                row[f"{METRIC_LABELS[m]} 일평균"] = round(st["avg"][m], 1) if st["has_logs"] and st["avg"][m] is not None else "-"
            rows.append(row)
    return pd.DataFrame(rows)


def leave_impact(team: TeamData, member_id: int, start: date, end: date, today: date,
                 window_days: int = 28, rules: dict | None = None) -> dict:
    """휴가로 비는 처리량(최근 일평균 × 휴가 근무일)과, 다른 단위 테스트 인원이 나눠 맡을 때의 추가 부담."""
    rules = merge_rules(rules)
    pool = team.unit_pool()
    out = {"applicable": member_id in pool, "name": team.name(member_id), "items": [], "pool_overlap_days": []}
    if not out["applicable"]:
        return out
    lost = workdays(start, end)
    out["lost_days"] = len(lost)
    w0, w1 = today - timedelta(days=window_days - 1), today
    others = sorted(pool - {member_id})
    out["pool_overlap_days"] = [d for d in lost
                                if sum(1 for o in others if o in absent_map(team, d, rules)) >= max(1, len(others) // 2)]
    for track in TRACKS:
        mine = member_stats(team, member_id, track, w0, w1, rules)
        for m in METRIC_COLS[track]:
            item = {"track": track, "metric": m, "label": f"{TRACKS[track]} · {METRIC_LABELS[m]}", "avg": None,
                    "gap": None, "rows": [], "excluded": [], "warnings": []}
            if not mine["has_logs"] or mine["avg"][m] is None:
                item["warnings"].append(f"최근 {window_days}일 로그가 없어 예측할 수 없습니다(0으로 간주하지 않음).")
                out["items"].append(item); continue
            item["avg"], item["gap"] = mine["avg"][m], mine["avg"][m] * len(lost)
            cand = []
            for o in others:
                avail = [d for d in lost if o not in absent_map(team, d, rules)]
                st = member_stats(team, o, track, w0, w1, rules)
                if not avail:
                    item["excluded"].append(f"{team.name(o)}: 대체 기간 내 근무 가능일 없음"); continue
                if not st["has_logs"] or not st["avg"][m]:
                    item["excluded"].append(f"{team.name(o)}: 최근 로그가 없어 배분에서 제외"); continue
                cand.append((o, len(avail), st["avg"][m]))
            cap = sum(n * a for _, n, a in cand)
            if cap <= 0:
                item["warnings"].append("분담할 수 있는 단위 테스트 인원이 없습니다.")
            else:
                for o, n, a in cand:
                    share = item["gap"] * (n * a) / cap
                    pct = share / n / a * 100
                    item["rows"].append({"팀원": team.name(o), "가용일": n, "현재 일평균": round(a, 1),
                                         "분담 필요량": round(share), "추가 필요(일)": round(share / n, 1),
                                         "평소 대비": f"+{pct:.0f}%", "pct": pct})
                if item["rows"] and item["rows"][0]["pct"] >= OVERLOAD_PCT:
                    item["warnings"].append(f"분담 시 평소 대비 +{item['rows'][0]['pct']:.0f}% 처리량이 필요해 부담이 큽니다. 일정·범위 조정을 검토하세요.")
            out["items"].append(item)
    return out


def pool_availability(team: TeamData, start: date, end: date, rules: dict | None = None) -> pd.DataFrame:
    rules = merge_rules(rules)
    pool = sorted(team.unit_pool())
    rows = []
    for d in workdays(start, end):
        a = absent_map(team, d, rules)
        absent = [team.name(m) for m in pool if m in a]
        rows.append({"날짜": d, "가용": len(pool) - len(absent), "전체": len(pool), "부재자": ", ".join(absent)})
    return pd.DataFrame(rows, columns=["날짜", "가용", "전체", "부재자"])


def recent_summary(team: TeamData, member_id: int, today: date, days: int = 14) -> dict:
    start = today - timedelta(days=days - 1)
    out = {"days": days, "tracks": {}}
    for track in TRACKS:
        recs = [r for r in _records(team) if r["member_id"] == member_id and r["track"] == track and start <= r["date"] <= today]
        if not recs:
            continue
        by_ver: dict[str, dict] = {}
        for r in recs:
            v = by_ver.setdefault(r["version"] or "버전 미입력", {m: 0.0 for m in METRIC_COLS[track]})
            for m in METRIC_COLS[track]:
                v[m] += r[m]
        out["tracks"][track] = {"sum": {m: sum(r[m] for r in recs) for m in METRIC_COLS[track]},
                                "by_version": by_ver, "last_date": max(r["date"] for r in recs)}
    return out
