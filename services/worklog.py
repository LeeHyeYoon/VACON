"""단위 테스트 업무 로그(동적 검증 / 요구사양서 작성) 가져오기와 분석.
시트 한 행 = 한 사람의 하루 기록(같은 날 여러 행 가능)이라고 보고 합산한다. 열은 '열 문자'(A, B, …)로 지정한다."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import pandas as pd

from .config import merge_rules
from collections import Counter

from .models import UNIT_SKILL, TeamData, today as _today
from .risk import absent_map, workdays
from .sheet_sync import SheetError, match_owner, parse_sheet_date_ex

TRACKS = {"dynamic": "동적 검증", "req": "요구사양서 작성"}
LOG_ROLES = {"log_dynamic": "dynamic", "log_req": "req"}

_SHEET_TASK_LABELS = {
    "사양서작성": ("req", "사양서 작성"), "요구사양서작성": ("req", "사양서 작성"),
    "일감정리": ("req", "일감 정리"), "업무구분": ("req", "업무 구분"),
    "동적검증": ("dynamic", "동적 검증"), "tc실행": ("dynamic", "동적 검증"),
    "regression검증": ("dynamic", "동적 검증"), "검증데이터세팅": ("dynamic", "검증데이터 세팅"),
    "결과보고서세팅": ("dynamic", "결과보고서 세팅"), "결과보고서작성": ("dynamic", "결과보고서 세팅"),
    "결과분석": ("dynamic", "동적 검증"),
}


def classify_sheet_task(category, fallback_track: str) -> tuple[str, str]:
    """Classify work from its D-column label; the tab resolves only ambiguous labels."""
    raw = str(category or "").strip()
    key = re.sub(r"\s+", "", raw).casefold()
    if key == "산출물검토":
        return fallback_track, "산출물 검토"
    return _SHEET_TASK_LABELS.get(key, (fallback_track, raw))

METRIC_COLS = {"dynamic": ("tc", "req", "func"), "req": ("issue",)}
METRIC_LABELS = {"tc": "실행 TC", "req": "완료 요구사양서", "func": "완료 함수", "issue": "완료 일감",
                 "new_req": "신규 요구사양", "modified_req": "수정 요구사양", "deleted_req": "삭제 요구사양",
                 "defect": "결함", "tc_created": "생성 TC", "tc_modified": "수정 TC", "tc_deleted": "삭제 TC"}
LOG_METRICS = {"dynamic": ("req", "func", "defect", "tc", "tc_created", "tc_modified", "tc_deleted"),
               "req": ("issue", "new_req", "modified_req", "deleted_req")}
LOG_HEADER_ALIASES = {
    "dynamic": {"defect": ("결함", "Defect", "Defects"), "tc_created": ("TC 생성", "생성 TC", "TC Created"),
                "tc_modified": ("TC 수정", "수정 TC", "TC Modified"), "tc_deleted": ("TC 삭제", "삭제 TC", "TC Deleted"),
                "notes": ("특이사항", "비고", "Notes")},
    "req": {"new_req": ("생성", "신규", "신규 요구사양", "New Requirements"),
            "modified_req": ("수정", "수정 요구사양", "Modified Requirements"),
            "deleted_req": ("삭제", "삭제 요구사양", "Deleted Requirements"),
            "notes": ("비고(이슈)", "특이사항", "비고", "Notes")},
}
LOG_FIELDS = {
    "dynamic": [("date", "날짜"), ("member", "담당자"), ("version", "버전"), ("subsystem", "서브시스템"), ("category", "업무 구분"),
                ("req", "완료 요구사양서"), ("tc", "실행 TC"), ("func", "완료 함수"), ("defect", "결함"),
                ("tc_created", "생성 TC"), ("tc_modified", "수정 TC"), ("tc_deleted", "삭제 TC"), ("notes", "특이사항")],
    "req": [("date", "날짜"), ("member", "담당자"), ("version", "버전"), ("subsystem", "서브시스템"), ("category", "업무 구분"),
            ("issue", "완료 일감"), ("new_req", "신규 요구사양"), ("modified_req", "수정 요구사양"),
            ("deleted_req", "삭제 요구사양"), ("notes", "특이사항")],
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


def guess_header_row(rows: list[list[str]], saved: dict | None = None) -> int | None:
    """상단 30행 중 '날짜'·'담당자'(또는 저장된 제목들)가 함께 있는 행 번호(1부터). 없으면 None."""
    want = [v for v in (saved or {}).values() if v] or ["날짜", "담당자"]
    for i, row in enumerate(rows[:30], start=1):
        cells = [c.strip() for c in row]
        if all(any(w == c or w in c for c in cells) for w in want[:2]):
            return i
    return None


def guess_func_column(headers: list[str]) -> str | None:
    """헤더명에 Func/서브펑션 완료가 명시된 열만 자동 제안한다."""
    hits = []
    for index, header in enumerate(headers):
        name = re.sub(r"[\s_\-]+", "", str(header or "")).casefold()
        if any(token in name for token in ("func", "function", "서브펑션", "서브함수")):
            hits.append(col_letter(index))
    return hits[0] if len(hits) == 1 else None


def guess_subsystem_column(headers: list[str]) -> str | None:
    """서브시스템 헤더를 유일하게 식별할 때만 열 문자를 제안한다."""
    hits = []
    for index, header in enumerate(headers):
        name = re.sub(r"[\s_\-]+", "", str(header or "")).casefold()
        if any(token in name for token in ("서브시스템", "subsystem", "subsystemname", "subsystemid")):
            hits.append(col_letter(index))
    return hits[0] if len(hits) == 1 else None


def version_sort_key(value: str):
    """버전 문자열 안 숫자를 정수로 비교하는 자연 정렬 키."""
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                 for part in re.split(r"(\d+)", str(value or "")) if part)


def parse_logs(text: str, cfg: dict, team: TeamData, today: date | None = None) -> dict:
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
    ref = today or _today()
    rows = list(csv.reader(io.StringIO(text)))
    hr = int(cfg.get("header_row", 1))
    if hr < 1 or hr > len(rows):
        raise SheetError(f"헤더 행({hr})이 데이터 범위를 벗어났습니다(총 {len(rows)}행).")
    guess = guess_header_row(rows, cfg.get("headers"))
    hint = f" 헤더가 {guess}행에 있는 것 같습니다 — 헤더 행 번호를 {guess}로 바꿔 보세요." if guess and guess != hr else ""
    header = rows[hr - 1]
    changed = []
    moved = []
    for f, saved in (cfg.get("headers") or {}).items():
        if f in cols and saved:
            i = col_index(cols[f])
            cur = header[i].strip() if i < len(header) else ""
            if cur != saved:
                hits = [j for j, name in enumerate(header) if name.strip() == saved]
                if hits:
                    # Saved header text takes precedence over an old column
                    # address, so reordered source columns continue to map.
                    cols[f] = col_letter(hits[0])
                    moved.append(f"{f}: {cols[f]}열에서 '{saved}' 헤더를 찾음")
                else:
                    changed.append(f"{cols[f]}열 제목 '{saved}' → '{cur}' (같은 제목을 찾지 못함)")
    if changed:
        raise SheetError("시트의 열 구성이 바뀐 것 같습니다(" + "; ".join(changed) + "). 열 매핑을 확인하고 다시 저장하세요." + hint)

    # Newly supported fields are matched from the actual, unique header text;
    # no column positions are assumed for these optional source metrics.
    for field, aliases in LOG_HEADER_ALIASES[track].items():
        if field in cols:
            continue
        wanted = {re.sub(r"\s+", "", value).casefold() for value in aliases}
        hits = [i for i, name in enumerate(header) if re.sub(r"\s+", "", name).casefold() in wanted]
        if len(hits) == 1:
            cols[field] = col_letter(hits[0])

    fill, year = bool(cfg.get("fill_down")), cfg.get("year") or None
    entries, warnings, unmatched = [], [], set()
    bad_dates: Counter = Counter()
    text_cells = skipped = blank_date_rows = unassigned_rows = inferred = 0
    last_date = last_member = None
    if moved:
        warnings.append("저장된 헤더 이름을 기준으로 이동한 열을 다시 연결했습니다: " + "; ".join(moved))
    for n, row in enumerate(rows[hr:], start=hr + 1):
        if not any(c.strip() for c in row):
            continue
        get = lambda f: (row[col_index(cols[f])].strip() if f in cols and col_index(cols[f]) < len(row) else "")
        raw_d, raw_m = get("date"), get("member")
        if fill:                                   # 병합 셀 대응: 빈 칸은 위 행 값 사용
            raw_m = raw_m or (last_member or "")
        if not raw_d and not (fill and last_date and raw_m):
            if raw_m:
                blank_date_rows += 1
            else:
                unassigned_rows += 1
            skipped += 1
            continue
        if raw_d:
            try:
                d, inf = parse_sheet_date_ex(raw_d, ref, "past", year)
            except ValueError:
                bad_dates[raw_d] += 1; skipped += 1
                continue
            last_date = d
            inferred += int(inf)
        else:
            d = last_date
        if get("member"):
            last_member = get("member")
        mid, note = match_owner(raw_m, team)
        if mid is None:
            unmatched.add(raw_m or "(빈 값)"); skipped += 1
            continue
        if note:
            warnings.append(f"{n}행: {note}")
        e = {"track": track, "date": d, "member_id": mid, "version": get("version"),
             "subsystem": get("subsystem"), "category": get("category"),
             **{m: None for m in ("req", "tc", "func", "issue", "new_req", "modified_req", "deleted_req",
                                  "defect", "tc_created", "tc_modified", "tc_deleted")},
             "notes": get("notes"), "row": n}
        for m in LOG_METRICS[track]:
            if m in cols:
                v, kind = parse_count(get(m))
                e[m] = v
                if kind == "text":
                    text_cells += 1
                elif kind == "invalid":
                    warnings.append(f"{n}행: {METRIC_LABELS[m]} 값 '{get(m)}'이(가) 음수라 0으로 처리")
        entries.append(e)
    diag = {"bad_dates": bad_dates.most_common(10), "bad_date_rows": sum(bad_dates.values()),
            "blank_date_rows": blank_date_rows, "unassigned_rows": unassigned_rows,
            "inferred_year": inferred, "header_guess": guess,
            "header_hint": hint.strip(), "date_col": cols["date"]}
    if diag["bad_date_rows"]:
        ex = ", ".join(f"'{v}'×{c}" for v, c in diag["bad_dates"][:5])
        warnings.insert(0, f"날짜 열({cols['date']})에서 날짜로 읽지 못해 건너뛴 행 {diag['bad_date_rows']}개 — 값 예시: {ex}")
    if blank_date_rows:
        warnings.insert(0, f"날짜 칸이 비어 있고 담당자는 있는 행 {blank_date_rows}개를 건너뜀 — 날짜가 병합 셀이면 '병합 셀 대응'을 켜세요.")
    if inferred:
        warnings.append(f"연도가 없는 날짜 {inferred}개는 {year or '오늘 기준 가장 가까운 과거'}년으로 추정했습니다(연도를 직접 지정할 수 있습니다).")
    return {"track": track, "entries": entries, "warnings": warnings, "unmatched_members": sorted(unmatched),
            "text_cells": text_cells, "skipped": skipped, "diag": diag,
            "date_range": (min(e["date"] for e in entries), max(e["date"] for e in entries)) if entries else None}


# ---------- 분석 ----------
def _records(team: TeamData) -> list[dict]:
    return team.logs.to_dict("records") if len(team.logs) else []


def _number(value) -> float:
    try:
        number = float(value)
        return number if number == number else 0.0
    except (TypeError, ValueError):
        return 0.0


def _text_value(value) -> str:
    """Normalize optional text fields, including pandas NaN, to a comparable string."""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value or "").strip()


def member_stats(team: TeamData, member_id: int, track: str, start: date, end: date,
                 rules: dict | None = None, version: str | None = None,
                 subsystem: str | None = None) -> dict:
    """기간/버전 내 개인 처리량. 일평균은 근무일 기준이며, 로그가 없으면 0으로 간주하지 않는다."""
    rules = merge_rules(rules)
    days = [d for d in workdays(start, end) if member_id not in absent_map(team, d, rules)]
    recs = [r for r in _records(team) if r["member_id"] == member_id and r["track"] == track
            and start <= r["date"] <= end and (version is None or _text_value(r.get("version")).casefold() == version.strip().casefold())
            and (subsystem is None or _text_value(r.get("subsystem")).casefold() == subsystem.strip().casefold())]
    cols = METRIC_COLS[track]
    sums = {m: sum(_number(r.get(m)) for r in recs) for m in cols}
    return {"workdays": len(days), "sum": sums, "has_logs": bool(recs),
            "avg": {m: (sums[m] / len(days) if days else None) for m in cols},
            "active_days": len({r["date"] for r in recs if any(r[m] > 0 for m in cols)}),
            "last_date": max((r["date"] for r in recs), default=None)}


def throughput_table(team: TeamData, start: date, end: date, rules: dict | None = None,
                     version: str | None = None, subsystem: str | None = None) -> pd.DataFrame:
    """[start, end] 기간의 팀원·업무별 처리량. version 지정 시 해당 버전 로그만 집계."""
    rows = []
    for mid in sorted(team.unit_pool()):
        for track, tname in TRACKS.items():
            st = member_stats(team, mid, track, start, end, rules, version, subsystem)
            row = {"팀원": team.name(mid), "업무": tname, "근무일": st["workdays"], "활동일": st["active_days"],
                   "최근 기록일": st["last_date"] or "기록 없음"}
            for m in METRIC_COLS[track]:
                row[f"{METRIC_LABELS[m]} 합계"] = round(st["sum"][m]) if st["has_logs"] else "-"
                row[f"{METRIC_LABELS[m]} 일평균"] = round(st["avg"][m], 1) if st["has_logs"] and st["avg"][m] is not None else "-"
            rows.append(row)
    return pd.DataFrame(rows)


def leave_impact(team: TeamData, member_id: int, start: date, end: date, today: date,
                 window_days: int = 28, rules: dict | None = None,
                 base_start: date | None = None, base_end: date | None = None,
                 version: str | None = None, subsystem: str | None = None) -> dict:
    """휴가로 비는 처리량(최근 일평균 × 휴가 근무일)과, 다른 단위 테스트 인원이 나눠 맡을 때의 추가 부담."""
    rules = merge_rules(rules)
    pool = team.unit_pool()
    out = {"applicable": member_id in pool, "name": team.name(member_id), "items": [], "pool_overlap_days": []}
    if not out["applicable"]:
        return out
    lost = workdays(start, end)
    out["lost_days"] = len(lost)
    w0, w1 = base_start or today - timedelta(days=window_days - 1), base_end or today
    out["base"] = (w0, w1)
    others = sorted(pool - {member_id})
    out["pool_overlap_days"] = [d for d in lost
                                if sum(1 for o in others if o in absent_map(team, d, rules)) >= max(1, len(others) // 2)]
    for track in TRACKS:
        mine = member_stats(team, member_id, track, w0, w1, rules, version, subsystem)
        for m in METRIC_COLS[track]:
            item = {"track": track, "metric": m, "label": f"{TRACKS[track]} · {METRIC_LABELS[m]}", "avg": None,
                    "gap": None, "rows": [], "excluded": [], "warnings": []}
            if not mine["has_logs"] or mine["avg"][m] is None:
                item["warnings"].append(f"기준 기간({w0}~{w1})에 로그가 없어 예측할 수 없습니다(0으로 간주하지 않음).")
                out["items"].append(item); continue
            item["avg"], item["gap"] = mine["avg"][m], mine["avg"][m] * len(lost)
            cand = []
            for o in others:
                avail = [d for d in lost if o not in absent_map(team, d, rules)]
                st = member_stats(team, o, track, w0, w1, rules, version, subsystem)
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
                v[m] += _number(r.get(m))
        out["tracks"][track] = {"sum": {m: sum(_number(r.get(m)) for r in recs) for m in METRIC_COLS[track]},
                                "by_version": by_ver, "last_date": max(r["date"] for r in recs)}
    return out


# ---------- 검증 업무 기록: 일일 처리 개수 추천 ----------
RECOMMEND_METRIC = {"spec": ("req", "issue", "완료 일감"), "verify": ("dynamic", "req", "완료 요구사양서")}
RECOMMEND_MIN_DAYS = 3
RECOMMEND_RECENT_DAYS = 30   # 기준별로 최근 활동일 30일만 사용해 현재 처리 속도를 반영한다


def _daily_totals(rows: list[dict], metric: str) -> list[tuple[date, float]]:
    totals: dict = {}
    for r in rows:
        totals[r["date"]] = totals.get(r["date"], 0.0) + _number(r.get(metric))
    return sorted((d, v) for d, v in totals.items() if v > 0)


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def recommend_daily_count(team: TeamData, member_id: int, phase: str, subsystem: str | None = None,
                          ref_date: date | None = None) -> dict | None:
    """반영(승인)된 업무 로그로 하루 처리 개수를 추천한다.
    phase='spec' → 요구사양서 작성 완료 일감 개수, phase='verify' → 동적 검증 완료 요구사양서 개수.
    같은 담당자·서브시스템 → 같은 담당자 → 단위 검증 인원 전체 순으로 활동일이 3일 이상인 기준을 쓰고,
    그 기준의 최근 활동일 30일의 일별 합계 중앙값을 추천값으로 한다. 샘플 데이터와 기준일 이후 기록은 제외한다."""
    if phase not in RECOMMEND_METRIC:
        raise ValueError("phase는 spec 또는 verify여야 합니다.")
    track, metric, label = RECOMMEND_METRIC[phase]
    ref_date = ref_date or _today()
    rows = [r for r in _records(team) if r.get("track") == track and _text_value(r.get("source")) != "샘플"
            and isinstance(r.get("date"), date) and r["date"] <= ref_date]
    sub = _text_value(subsystem).casefold()
    name = team.name(member_id)
    pool = team.unit_pool() or {member_id}
    scopes = []
    if sub:
        scopes.append((f"{name}님의 {subsystem} 기록",
                       [r for r in rows if r.get("member_id") == member_id and _text_value(r.get("subsystem")).casefold() == sub]))
    scopes.append((f"{name}님의 전체 기록", [r for r in rows if r.get("member_id") == member_id]))
    scopes.append(("단위 검증 인원 전체 기록", [r for r in rows if r.get("member_id") in pool]))
    for scope, scoped in scopes:
        if scope.startswith("단위") and len(scoped):
            # 인원 전체는 사람·날짜별 합계를 한 표본으로 본다.
            by_member: dict = {}
            for r in scoped:
                by_member.setdefault(r.get("member_id"), []).append(r)
            daily = sorted((d, v) for rs in by_member.values() for d, v in _daily_totals(rs, metric))
        else:
            daily = _daily_totals(scoped, metric)
        if len(daily) < RECOMMEND_MIN_DAYS:
            continue
        recent = daily[-RECOMMEND_RECENT_DAYS:]
        values = [v for _, v in recent]
        value = int(round(_median(values)))
        return {"value": max(value, 1), "label": label, "scope": scope, "days": len(recent),
                "low": int(min(values)), "high": int(max(values)),
                "period": (recent[0][0], recent[-1][0]),
                "basis": (f"{scope}의 최근 활동일 {len(recent)}일({recent[0][0]}~{recent[-1][0]}) "
                          f"일별 {label} 개수 중앙값 {_median(values):g}개 (범위 {min(values):g}~{max(values):g}개)")}
    return None
