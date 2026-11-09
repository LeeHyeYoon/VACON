"""구글 시트(탭별 CSV) → VACON 업무/일정 동기화. 열 이름은 설정(매핑)으로 지정하므로 시트 양식에 종속되지 않는다.
원칙: 시트가 원천인 값(담당자·상태·진행률·마감일·남은 작업·주의사항)만 갱신하고,
VACON 전용 값(대체 담당자·중요도·예상 부담)은 덮어쓰지 않는다. 시트에서 사라진 업무도 자동 삭제하지 않는다."""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta

from .mail_digest import parse_date
from .models import UNIT_SKILL, TeamData, isnull, required_skills, today
from . import llm_client


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


def discover_sheet_tabs(ref: str, timeout: int = 15) -> list[dict]:
    """공개 스프레드시트의 gid를 찾아 탭 선택 목록을 만든다. 인증/비공개 시트는 지원하지 않는다."""
    sheet_id = extract_sheet_id(ref)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VACON"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            page = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SheetError(f"Google Sheets 탭 목록을 읽을 수 없습니다: {getattr(exc, 'reason', exc)}") from None
    if "accounts.google.com" in page or "ServiceLogin" in page:
        raise SheetError("로그인이 필요한 비공개 시트입니다. 공개 읽기 권한 또는 CSV 내보내기가 필요합니다.")
    gids = list(dict.fromkeys(re.findall(r"(?:gid(?:%3D|=)|\"sheetId\"\s*:\s*)(\d+)", page)))
    if not gids:
        raise SheetError("시트 탭 GID를 찾지 못했습니다. 시트 공개 권한을 확인하거나 탭 주소의 gid를 입력하세요.")
    # Tab titles are not consistently embedded in Google's public HTML. Keep a clear
    # GID label when the title cannot be extracted; never guess a tab's role.
    return [{"label": f"시트 탭 (gid {gid})", "gid": gid} for gid in gids]


LOG_MAPPING_FIELDS = {
    "dynamic": {"date": "날짜", "member": "담당자", "version": "버전", "subsystem": "서브시스템",
                "category": "업무 구분", "req": "완료 요구사양서", "func": "완료 함수", "defect": "결함",
                "tc": "실행 TC", "tc_created": "생성 TC", "tc_modified": "수정 TC", "tc_deleted": "삭제 TC", "notes": "특이사항"},
    "req": {"date": "날짜", "member": "담당자", "version": "버전", "subsystem": "서브시스템",
             "category": "업무 구분", "issue": "완료 일감", "new_req": "신규 요구사양", "modified_req": "수정 요구사양",
             "deleted_req": "삭제 요구사양", "notes": "특이사항"},
}


def analyze_log_mapping(rows: list[list[str]], track: str | None = None) -> dict:
    """헤더와 제한된 샘플을 ALIRA에 보내 업무 로그 열 매핑을 제안한다."""
    from .worklog import col_index, col_letter

    if track is not None and track not in LOG_MAPPING_FIELDS:
        raise SheetError("AI 열 자동 매핑은 요구사양서 작성/동적 검증 로그만 지원합니다.")
    if not llm_client.configured():
        raise SheetError("ALIRA_API_BASE와 ALIRA_MODEL 설정이 필요합니다.")
    fields = LOG_MAPPING_FIELDS[track] if track else {**LOG_MAPPING_FIELDS["dynamic"], **LOG_MAPPING_FIELDS["req"]}
    payload = {"requested_business_type": track or "자동 판별(dynamic 또는 req)",
               "target_fields": fields, "sheet_rows": rows[:30]}
    prompt = ("Google Sheets CSV의 헤더 행과 내용 샘플을 분석해 VACON 업무 로그 열을 매핑하세요. "
              "업무 유형이 지정되지 않았다면 business_type을 dynamic(동적 검증), req(요구사양서 작성), other(관련 없음) 중 하나로 판별하세요. "
              "날짜, 담당자는 실제 헤더를 근거로 지정하고, 숫자 지표는 값의 의미가 분명할 때만 지정하세요. "
              "헤더행 번호는 1부터 세세요. 모르는 값은 null로 두고 추측하지 마세요. "
              "JSON만 반환: {\"business_type\":\"dynamic, req 또는 other\",\"header_row\":1,\"mapping\":{\"date\":\"실제 헤더\",...},\"confidence\":0.0,\"reason\":\"...\"}. "
              "mapping 키는 target_fields의 키만 사용하고 값은 시트 헤더 문자열이어야 합니다.\n" +
              json.dumps(payload, ensure_ascii=False))
    try:
        message = llm_client.chat([{"role": "system", "content": "시트 구조 매핑 도우미입니다. 제공된 표본 외의 사실을 만들지 마세요."},
                                   {"role": "user", "content": prompt}], max_tokens=900, timeout=35)
    except Exception as exc:
        raise SheetError(f"ALIRA 시트 분석에 실패했습니다: {exc}") from None
    content = llm_client.strip_reasoning(message.get("content"))
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    try:
        result = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        raise SheetError("ALIRA 응답에서 JSON 매핑을 해석하지 못했습니다. 다시 분석하거나 수동 확인하세요.") from None
    resolved_track = track or str(result.get("business_type") or "").strip().lower()
    if resolved_track == "other":
        return {"track": "other", "header_row": None, "columns": {}, "missing_required": [],
                "confidence": 0.0, "reason": str(result.get("reason") or "업무 로그 탭이 아닙니다.")[:500], "headers": []}
    if resolved_track not in LOG_MAPPING_FIELDS:
        raise SheetError("ALIRA가 이 탭을 요구사양서/동적 검증 로그로 분류하지 못했습니다. 업무 유형을 확인하세요.")
    fields = LOG_MAPPING_FIELDS[resolved_track]
    try:
        header_row = int(result.get("header_row"))
    except (TypeError, ValueError):
        raise SheetError("ALIRA가 헤더 행을 확정하지 못했습니다. 원본을 확인하세요.") from None
    if not 1 <= header_row <= min(30, len(rows)):
        raise SheetError("ALIRA가 데이터 범위 밖의 헤더 행을 반환했습니다.")
    headers = [str(value).strip() for value in rows[header_row - 1]]
    data_rows = rows[header_row:]
    def legacy_version(value):
        match = re.fullmatch(r"\s*T\s*0*(\d+)\s*", str(value or ""), re.I)
        return bool(match and int(match.group(1)) <= 700)

    mapping = {}
    for field, header in (result.get("mapping") or {}).items():
        if field not in fields or not isinstance(header, str) or not header.strip():
            continue
        matches = [index for index, actual in enumerate(headers) if actual == header.strip()]
        if len(matches) == 1:
            mapping[field] = col_letter(matches[0])
        elif len(matches) > 1 and field == "version":
            # Repeated "버전" headers can include a separate T0800 planning block.
            scores = [(sum(legacy_version(row[i] if i < len(row) else "") for row in data_rows), i)
                      for i in matches]
            best = max(score for score, _ in scores)
            winners = [i for score, i in scores if score == best and score > 0]
            if len(winners) == 1:
                mapping[field] = col_letter(winners[0])
        elif len(matches) > 1 and field == "subsystem" and mapping.get("version"):
            version_index = col_index(mapping["version"])
            scores = []
            for index in matches:
                count = sum(legacy_version(row[version_index] if version_index < len(row) else "")
                            and bool(str(row[index] if index < len(row) else "").strip())
                            for row in data_rows)
                scores.append((count, index))
            best = max(score for score, _ in scores)
            winners = [i for score, i in scores if score == best and score > 0]
            if len(winners) == 1:
                mapping[field] = col_letter(winners[0])

    # "버전" is required to enforce the T0700 ingestion limit. Recover it from
    # actual header values when the AI output omitted or could not disambiguate it.
    if "version" not in mapping:
        version_headers = [i for i, name in enumerate(headers)
                           if re.sub(r"\s+", "", name).casefold() in {"버전", "version"}]
        scores = [(sum(legacy_version(row[i] if i < len(row) else "") for row in data_rows), i)
                  for i in version_headers]
        best = max((score for score, _ in scores), default=0)
        winners = [i for score, i in scores if score == best and score > 0]
        if len(winners) == 1:
            mapping["version"] = col_letter(winners[0])
    required_metrics = {"req", "func"} if resolved_track == "dynamic" else {"issue"}
    required = {"date", "member", "version"}
    missing = sorted((required | required_metrics) - mapping.keys())
    confidence = result.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"track": resolved_track, "header_row": header_row, "columns": mapping, "missing_required": missing,
            "confidence": confidence, "reason": str(result.get("reason") or "")[:500], "headers": headers}


SHEET_TASK_DETAILS = {
    "req": ("사양서 작성", "일감 정리", "산출물 검토", "업무 구분"),
    "dynamic": ("동적 검증", "검증데이터 세팅", "결과보고서 세팅", "산출물 검토"),
}


def classify_worklog_categories(entries: list[dict], track: str) -> tuple[dict[str, tuple[str, str]], str | None]:
    """Classify actual D-column labels and ask ALIRA only about unclear labels."""
    from .worklog import classify_sheet_task

    labels = sorted({str(entry.get("category") or "").strip() for entry in entries
                     if str(entry.get("category") or "").strip()})
    if not labels:
        return {}, "시트에 업무 구분 값이 없어 세부 업무를 자동 분류하지 않았습니다."
    mapping = {label: classify_sheet_task(label, track) for label in labels}
    unclear = [label for label in labels if mapping[label][1] == label]
    if not unclear:
        return mapping, None
    if not llm_client.configured():
        return mapping, "ALIRA 연결이 없어 일부 미분류 업무 구분은 원본 값 그대로 보존했습니다."
    prompt = (
        "다음 문자열은 구글 시트 D열의 실제 업무 구분 값입니다. 탭 종류와 무관하게 각 값의 의미로 "
        "VACON 업무 유형과 세부 업무를 판단하세요. 허용된 유형/세부 업무 쌍 중 근거가 명확한 경우만 반환하고, "
        "불명확하면 null로 반환하세요. 서브시스템 값은 업무 유형 판단 근거로 사용하지 마세요.\n"
        + json.dumps({"source_tab_type": track, "allowed": {
            "요구사양서 작성": SHEET_TASK_DETAILS["req"], "동적 검증": SHEET_TASK_DETAILS["dynamic"]},
            "D_column_values": unclear}, ensure_ascii=False)
        + '\nJSON만 반환: {"mapping":{"원본값":{"business_type":"req 또는 dynamic","task_detail":"허용 세부 업무"}}}'
    )
    try:
        response = llm_client.chat([
            {"role": "system", "content": "시트의 업무 구분 값만 분류합니다. 제공되지 않은 데이터를 만들지 마세요."},
            {"role": "user", "content": prompt},
        ], max_tokens=500, timeout=35)
        content = llm_client.strip_reasoning(response.get("content"))
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
        parsed = json.loads(content)
        proposed = parsed.get("mapping", {})
        for source, result in proposed.items():
            if source not in unclear or not isinstance(result, dict):
                continue
            business_type = str(result.get("business_type") or "").strip().lower()
            task_detail = str(result.get("task_detail") or "").strip()
            if business_type in SHEET_TASK_DETAILS and task_detail in SHEET_TASK_DETAILS[business_type]:
                mapping[source] = (business_type, task_detail)
        return mapping, None
    except Exception as exc:
        return mapping, f"ALIRA 세부 업무 분류에 실패해 일부 시트 원본 업무 구분을 보존했습니다: {exc}"


def analyze_worklog_sheets(sheets: list[dict], team: TeamData) -> dict:
    """여러 탭을 유형 판별·매핑·로그 파싱하고, 확인이 필요한 항목을 함께 반환한다."""
    from . import worklog

    results, entries, problems, warnings, unmatched = [], [], [], [], set()
    total_skipped = total_text_cells = total_unassigned = excluded_versions = 0
    expected_tabs = {"2.dynamic_verification": "dynamic", "3.req_creation_wbs": "req"}
    seen_tabs = set()
    for sheet in sheets:
        label, rows = str(sheet["label"]), sheet["rows"]
        title = re.sub(r"\s+", " ", str(sheet.get("title") or label)).strip().casefold()
        title_key = re.sub(r"\s+", "", title)
        expected_track = sheet.get("expected_track") or expected_tabs.get(title_key)
        if expected_track is None:
            continue
        if expected_track not in LOG_MAPPING_FIELDS:
            problems.append(f"{label} (업무 유형 설정 오류)")
            continue
        seen_tabs.add(expected_track)
        if not rows or not any(str(cell).strip() for row in rows for cell in row):
            problems.append(label + " (시트에 읽을 데이터가 없음)")
            continue
        analysis = analyze_log_mapping(rows, expected_track)
        item = {"label": label, **analysis}
        results.append(item)
        if analysis["missing_required"] or analysis["confidence"] < 0.6:
            problems.append(label + (" (필수 열 미확인: " + ", ".join(analysis["missing_required"]) + ")"
                                     if analysis["missing_required"] else " (분석 신뢰도 낮음)"))
            continue
        role = "log_dynamic" if analysis["track"] == "dynamic" else "log_req"
        config = {"role": role, "header_row": analysis["header_row"], "columns": analysis["columns"],
                  "headers": {field: analysis["headers"][worklog.col_index(letter)]
                              for field, letter in analysis["columns"].items()},
                  "fill_down": False}
        output = worklog.parse_logs(_rows_to_csv(rows), config, team)
        item["parsed"] = output
        in_scope = []
        for entry in output["entries"]:
            match = re.fullmatch(r"\s*T\s*0*(\d+)\s*", str(entry.get("version") or ""), re.I)
            if not match or int(match.group(1)) > 700:
                excluded_versions += 1
                continue
            number = int(match.group(1))
            entry["version"] = f"T{number:04d}"
            in_scope.append(entry)
        category_map, category_warning = classify_worklog_categories(in_scope, analysis["track"])
        if category_map:
            for entry in in_scope:
                raw_category = str(entry.get("category") or "").strip()
                if raw_category in category_map:
                    entry["track"], entry["category"] = category_map[raw_category]
        item["parsed"] = {**output, "entries": in_scope}
        if category_warning:
            warnings.append(f"{label}: {category_warning}")
        item["accepted_records"] = len(in_scope)
        item["excluded_records"] = len(output["entries"]) - len(in_scope)
        if not in_scope:
            details = []
            if output["unmatched_members"]:
                details.append("팀원 미일치: " + ", ".join(output["unmatched_members"][:8]))
            if output["diag"].get("bad_date_rows"):
                details.append(f"날짜 해석 실패 {output['diag']['bad_date_rows']}행")
            if output["skipped"] and not details:
                details.append(f"행 건너뜀 {output['skipped']}건")
            problems.append(label + " (T0700 이하로 반영할 수 있는 기록 0건" +
                            (" · " + " · ".join(details) if details else "") + ")")
        entries.extend({**entry, "source": label} for entry in in_scope)
        warnings.extend(f"{label}: {warning}" for warning in output["warnings"])
        unmatched.update(output["unmatched_members"])
        total_skipped += output["skipped"]
        total_text_cells += output["text_cells"]
        total_unassigned += output["diag"].get("unassigned_rows", 0)
    missing_tabs = set(LOG_MAPPING_FIELDS) - seen_tabs
    if missing_tabs:
        names = {"dynamic": "2. Dynamic_Verification", "req": "3. Req_Creation_WBS"}
        problems.append("필수 시트 누락: " + ", ".join(names[key] for key in sorted(missing_tabs)))
    if excluded_versions:
        warnings.append(f"T0700 이후 또는 버전이 불명확한 기록 {excluded_versions}건은 반영 대상에서 제외했습니다.")
    return {"sheets": results, "entries": entries, "problems": problems, "warnings": warnings,
            "unmatched_members": sorted(unmatched), "skipped": total_skipped, "text_cells": total_text_cells,
            "unassigned_rows": total_unassigned, "excluded_versions": excluded_versions,
            "ready": bool(entries) and not problems}


def _rows_to_csv(rows: list[list[str]]) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerows(rows)
    return stream.getvalue()


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
