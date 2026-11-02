"""구글 시트(탭별 CSV) → VACON 업무/일정 동기화. 열 이름은 설정(매핑)으로 지정하므로 시트 양식에 종속되지 않는다.
원칙: 시트가 원천인 값(담당자·상태·진행률·마감일·남은 작업·주의사항)만 갱신하고,
VACON 전용 값(대체 담당자·중요도·예상 부담)은 덮어쓰지 않는다. 시트에서 사라진 업무도 자동 삭제하지 않는다."""
from __future__ import annotations

import csv
import io
import re
import urllib.error
import urllib.request
from datetime import date, timedelta

from .mail_digest import parse_date
from .models import UNIT_SKILL, TeamData, isnull, required_skills, today


class SheetError(Exception):
    pass


TASK_FIELDS = [("name", "업무명 (필수)"), ("owner", "담당자"), ("status", "상태"), ("progress", "진행률"),
               ("due", "마감일"), ("remaining", "남은 작업"), ("notes", "주의사항/비고")]
SCHEDULE_FIELDS = [("name", "업무명 (필수, 기존 업무와 매칭)"), ("due", "마감일 (필수)")]
FIELD_LABELS = dict(TASK_FIELDS + SCHEDULE_FIELDS)


# ---------- 가져오기 ----------
def extract_sheet_id(ref: str) -> str:
    ref = (ref or "").strip()
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", ref) or re.fullmatch(r"([A-Za-z0-9_-]{20,})", ref)
    if not m:
        raise SheetError("올바른 구글 시트 주소(또는 시트 ID)가 아닙니다.")
    return m.group(1)


def csv_url(ref: str, gid) -> str:
    try:
        gid = int(str(gid).strip())
    except ValueError:
        raise SheetError("gid는 숫자여야 합니다(시트 주소의 '#gid=' 뒤 숫자).")
    return f"https://docs.google.com/spreadsheets/d/{extract_sheet_id(ref)}/export?format=csv&gid={gid}"


def fetch_csv(ref: str, gid, timeout: int = 20) -> str:
    url = csv_url(ref, gid)  # 호스트는 항상 docs.google.com 고정(ID만 사용자 입력)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VACON"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, ctype = r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SheetError("접근 권한이 없습니다. 공유 설정이 '링크가 있는 모든 사용자 - 뷰어'인지 확인하세요(회사 정책으로 외부 공유가 막혀 있을 수 있습니다).")
        if e.code == 404:
            raise SheetError("시트 또는 탭(gid)을 찾을 수 없습니다.")
        raise SheetError(f"요청에 실패했습니다(HTTP {e.code}).")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SheetError(f"구글 시트에 연결할 수 없습니다: {getattr(e, 'reason', e)}")
    text = raw.decode("utf-8-sig", errors="replace")
    if "text/html" in ctype or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        raise SheetError("CSV 대신 로그인/오류 페이지가 반환되었습니다. 공유 설정을 확인하거나 CSV 업로드를 사용하세요.")
    return text


def read_table(text: str, header_row: int = 1) -> tuple[list[str], list[dict]]:
    """CSV 텍스트 → (헤더, 레코드). 제목 줄이 위에 있으면 header_row로 지정. 빈/중복 헤더는 이름을 보정."""
    rows = list(csv.reader(io.StringIO(text)))
    if header_row < 1 or header_row > len(rows):
        raise SheetError(f"헤더 행({header_row})이 데이터 범위를 벗어났습니다(총 {len(rows)}행).")
    headers, seen = [], {}
    for i, h in enumerate(rows[header_row - 1]):
        h = h.strip() or f"(열 {i + 1})"
        seen[h] = seen.get(h, 0) + 1
        headers.append(h if seen[h] == 1 else f"{h} #{seen[h]}")
    recs = []
    for n, row in enumerate(rows[header_row:], start=header_row + 1):
        if not any(c.strip() for c in row):
            continue
        row = row + [""] * (len(headers) - len(row))
        recs.append({**dict(zip(headers, row)), "_row": n})
    return headers, recs


def guess_csv_header_row(rows: list[list[str]], max_rows: int = 30) -> int | None:
    """Suggest a likely title row, preferring recognized column labels; otherwise abstain on ties."""
    scores = [(sum(bool(str(cell).strip()) for cell in row), index)
              for index, row in enumerate(rows[:max_rows], start=1)]
    if not scores:
        return None
    first = [str(cell).strip().casefold() for cell in rows[0] if str(cell).strip()]
    header_terms = ("이름", "업무", "담당", "날짜", "시작", "종료", "버전", "상태", "진행", "마감",
                    "name", "task", "owner", "date", "start", "end", "version", "status", "progress", "due")
    if len(first) >= 2 and len(set(first)) == len(first) and any(term in cell for cell in first for term in header_terms):
        return 1
    best = max(score for score, _ in scores)
    candidates = [index for score, index in scores if score == best and score >= 2]
    return candidates[0] if len(candidates) == 1 else None


# ---------- 값 해석 (해석 불가면 ValueError → 경고로 보고, 추측하지 않음) ----------
def parse_progress(v) -> int | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        x = float(s.replace("%", "").replace(",", ""))
    except ValueError:
        raise ValueError(f"진행률 '{s}'을(를) 해석할 수 없음")
    if "%" not in s and 0 < x < 1:
        x *= 100
    if not 0 <= x <= 100:
        raise ValueError(f"진행률 '{s}'이(가) 0~100 범위를 벗어남")
    return int(round(x))


def parse_status(v) -> str | None:
    s = str(v or "").strip().lower()
    if not s:
        return None
    if "미완" in s:
        raise ValueError(f"상태 '{v}' 해석 불가(미완료의 세부 상태를 알 수 없음)")
    for keys, out in ((("완료", "done", "complete", "closed"), "완료"), (("지연", "delay", "late"), "지연"),
                      (("진행", "progress", "wip", "ongoing"), "진행중"),
                      (("대기", "예정", "미착수", "todo", "not started", "planned"), "대기")):
        if any(k in s for k in keys):
            return out
    raise ValueError(f"상태 '{v}' 해석 불가")


_WEEKDAY_RE = re.compile(r"\(\s*[월화수목금토일]\s*\)|[월화수목금토일]요일|(?<![A-Za-z])(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?(?![A-Za-z])", re.I)
_TIME_RE = re.compile(r"(오전|오후|am|pm)?\s*\d{1,2}\s*:\s*\d{2}(\s*:\s*\d{2})?\s*(am|pm)?", re.I)


def _mk(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _infer_year(m: int, d: int, ref: date, mode: str, year: int | None) -> date | None:
    """연도가 없는 날짜의 연도 결정. year 지정 시 그 해. mode='past'(업무 로그): 기준일+7일 이내의 가장 최근 해,
    mode='nearest'(마감일 등): 기준일에 가장 가까운 해."""
    if year:
        return _mk(year, m, d)
    cands = [c for c in (_mk(ref.year + k, m, d) for k in (-1, 0, 1)) if c]
    if not cands:
        return None
    if mode == "past":
        ok = [c for c in cands if c <= ref + timedelta(days=7)]
        return max(ok) if ok else min(cands)
    return min(cands, key=lambda c: abs((c - ref).days))


def parse_sheet_date_ex(v, ref: date | None = None, mode: str = "nearest", year: int | None = None) -> tuple[date | None, bool]:
    """시트 셀 → (날짜, 연도를 추정했는지). 빈 칸은 (None, False), 해석 불가는 ValueError.
    지원: 2026-10-19 / 2026. 10. 19. / 2026/10/19 / 2026년 10월 19일 / 26.10.19 / 20261019 / 10/19/2026 /
    19.10.2026 / 10/19 / 10월 19일 / 요일·시간 포함(10/19(월), 2026-10-19 09:30) / 엑셀 일련번호."""
    raw = str(v or "").strip()
    if not raw:
        return None, False
    ref = ref or today()
    t = re.sub(r"T\d.*$", "", raw)
    t = _TIME_RE.sub(" ", _WEEKDAY_RE.sub(" ", t)).strip(" ,~·")
    if re.fullmatch(r"\d{5}(\.\d+)?", t) and 20000 <= float(t) <= 80000:  # 엑셀/시트 일련번호
        return date(1899, 12, 30) + timedelta(days=int(float(t))), False
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", t)
    if m and (d := _mk(int(m[1]), int(m[2]), int(m[3]))):
        return d, False
    m = re.search(r"(?<!\d)(\d{4})\s*(?:[-./]|년)\s*(\d{1,2})\s*(?:[-./]|월)\s*(\d{1,2})\s*일?(?!\d)", t)
    if m and (d := _mk(int(m[1]), int(m[2]), int(m[3]))):
        return d, False
    m = re.search(r"(?<!\d)(\d{1,2})\s*([-./])\s*(\d{1,2})\s*\2\s*(\d{4})(?!\d)", t)  # 월/일/연 또는 일.월.연
    if m:
        a, sep, b, y = int(m[1]), m[2], int(m[3]), int(m[4])
        for d in ([_mk(y, a, b), _mk(y, b, a)] if sep == "/" else [_mk(y, b, a), _mk(y, a, b)]):
            if d:
                return d, False
    m = re.search(r"(?<!\d)(\d{2})\s*([-./])\s*(\d{1,2})\s*\2\s*(\d{1,2})(?!\d)", t)  # 두 자리 연도
    if m:
        x, sep, y2, z = int(m[1]), m[2], int(m[3]), int(m[4])
        order = [(2000 + x, y2, z)] if (x > 12 or sep != "/") else []
        order += [(2000 + z, x, y2)] if sep == "/" else []          # 10/19/26 → 월/일/연
        order += [(2000 + x, y2, z), (2000 + z, x, y2)]
        for yy, mm, dd in order:
            if d := _mk(yy, mm, dd):
                return d, False
    m = re.search(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일?(?!\d)", t) or \
        re.fullmatch(r"(\d{1,2})\s*[-./]\s*(\d{1,2})\.?", t)                      # 연도 없는 월/일
    if m and (d := _infer_year(int(m[1]), int(m[2]), ref, mode, year)):
        return d, True
    raise ValueError(f"날짜 '{raw}' 해석 불가")


def parse_sheet_date(v, ref: date | None = None, mode: str = "nearest", year: int | None = None) -> date | None:
    return parse_sheet_date_ex(v, ref, mode, year)[0]


def match_owner(cell: str, team: TeamData) -> tuple[int | None, str]:
    """셀 안에서 팀원 이름을 찾는다. 여러 명이면 가장 앞의 이름. 반환: (member_id, 메모)."""
    cell = str(cell or "").strip()
    if not cell:
        return None, ""
    hits = sorted((cell.find(m["name"]), m["id"], m["name"]) for m in team.member_records() if m["name"] and m["name"] in cell)
    if not hits:
        return None, f"담당자 '{cell}'이(가) 팀원 목록에 없음"
    note = f"담당자 여러 명('{cell}') 중 첫 번째({hits[0][2]})만 사용" if len(hits) > 1 else ""
    return hits[0][1], note


def _key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


# ---------- 계획(미리보기) ----------
def plan_sync(team: TeamData, records: list[dict], cfg: dict) -> dict:
    """cfg: {label, role('tasks'|'schedule'), columns{field: 헤더}, required_skill, default_importance}"""
    cols, role, label = cfg.get("columns", {}), cfg.get("role", "tasks"), cfg.get("label", "")
    if not cols.get("name"):
        raise SheetError("'업무명' 열이 매핑되지 않았습니다.")
    if role == "schedule" and not cols.get("due"):
        raise SheetError("일정 탭은 '마감일' 열 매핑이 필요합니다.")
    existing = {_key(t["name"]): t for t in team.task_records()}
    plan = {"add": [], "update": [], "unchanged": 0, "warnings": [], "unmatched_owners": set(),
            "missing": [], "unmatched_names": []}
    seen = set()
    for rec in records:
        name = str(rec.get(cols["name"], "")).strip()
        if not name:
            continue
        row = rec.get("_row", "?")
        key = _key(name)
        if key in seen:
            plan["warnings"].append(f"{row}행: 업무명 '{name}'이(가) 중복되어 건너뜀")
            continue
        seen.add(key)
        vals = {}

        def grab(field, parser):
            col = cols.get(field)
            if not col:
                return
            try:
                v = parser(rec.get(col, ""))
            except ValueError as e:
                plan["warnings"].append(f"{row}행 '{name}': {e} → 해당 값은 반영하지 않음")
                return
            if v is not None:
                vals[field] = v

        grab("due", lambda v: parse_sheet_date(v, today(), "nearest"))
        if role == "tasks":
            grab("status", parse_status)
            grab("progress", parse_progress)
            for f_, k in (("remaining", "remaining"), ("notes", "notes")):
                if cols.get(f_) and str(rec.get(cols[f_], "")).strip():
                    vals[k] = str(rec[cols[f_]]).strip()
            if cols.get("owner") and str(rec.get(cols["owner"], "")).strip():
                oid, note = match_owner(rec[cols["owner"]], team)
                req_ = required_skills(cfg.get("required_skill") or (existing.get(key) or {}).get("required_skill"))
                if oid is None:
                    plan["unmatched_owners"].add(str(rec[cols["owner"]]).strip())
                elif UNIT_SKILL in req_ and oid not in team.unit_pool():
                    plan["warnings"].append(f"{row}행 '{name}': 단위 테스트 업무는 단위 테스트 인원만 담당할 수 있어 담당자 '{team.name(oid)}'은(는) 반영하지 않음")
                else:
                    vals["owner"] = oid
                if note:
                    plan["warnings"].append(f"{row}행 '{name}': {note}")

        t = existing.get(key)
        mapping = {"owner": "owner_id", "due": "due_date"}
        if t is None:
            if role == "schedule":
                plan["unmatched_names"].append(name)
                continue
            imp = cfg.get("default_importance")
            plan["add"].append({"name": name, "owner_id": vals.get("owner"), "status": vals.get("status"),
                                "progress": vals.get("progress"), "due_date": vals.get("due"),
                                "remaining": vals.get("remaining"), "notes": vals.get("notes"),
                                "required_skill": cfg.get("required_skill") or "",
                                "importance": imp if imp in ("상", "중", "하") else None})
            continue
        changes = {}
        for f_, v in vals.items():
            fld = mapping.get(f_, f_)
            old = t.get(fld)
            if isnull(old) or old == "":
                old = None
            if old != v:
                changes[fld] = (old, v)
        if changes:
            plan["update"].append({"id": t["id"], "name": t["name"], "changes": changes})
        else:
            plan["unchanged"] += 1
    if role == "tasks" and label:
        plan["missing"] = [t["name"] for t in team.task_records()
                           if t.get("source") == label and _key(t["name"]) not in seen]
    plan["unmatched_owners"] = sorted(plan["unmatched_owners"])
    return plan
