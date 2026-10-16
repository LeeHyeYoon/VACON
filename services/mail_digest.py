"""휴가(부재) 기간에 받은 메일을 취합·요약. 메일 서버에는 연결하지 않으며 사용자가 가져온 메일만 처리한다.
규칙 기반(키워드·기한 추출·업무 매칭)이라 '원문 확인이 필요한 참고용 요약'이다."""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from .models import TeamData, isnull

HIGH_KW = ["긴급", "급함", "urgent", "asap", "즉시", "오늘까지", "내일까지", "마감", "승인", "결재", "장애", "감사", "지연", "차질"]
MID_KW = ["회신", "답변", "확인 부탁", "확인 요청", "검토", "요청", "문의", "일정", "미팅", "회의", "리뷰"]
LOW_KW = ["공지", "안내", "참고", "fyi", "뉴스레터", "광고", "수신 확인"]
REPLY_RE = re.compile(r"회신|답변|확인\s*(부탁|요청)|검토\s*(부탁|요청)|알려\s*주|부탁\s*드|요청\s*드|please\s+(reply|confirm|review)", re.I)
GREETING_RE = re.compile(r"^(안녕하세요|안녕하십니까|수고하십니다|감사합니다|hi|hello|dear)", re.I)
STOP_TOKENS = {"정리", "개선", "업데이트", "확대", "보강", "개정", "점검", "대응", "수립", "결과", "검증"}
LEVEL_ORDER = {"높음": 0, "보통": 1, "낮음": 2}

_HEADERS = {
    "sender": ("보낸사람", "발신자", "발신", "from", "sender"),
    "date": ("받은날짜", "받은 날짜", "날짜", "date", "received"),
    "subject": ("제목", "subject"),
    "body": ("본문", "내용", "body"),
}


def _mail(date_, sender="", subject="", body="") -> dict:
    return {"date": date_, "sender": (sender or "").strip(), "subject": (subject or "").strip(),
            "body": (body or "").strip()}


# ---------- 가져오기 ----------
def parse_date(s) -> date | None:
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    s = str(s or "").strip()
    m = re.search(r"(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError, IndexError):
        return None


def _strip_html(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))


def parse_eml(data: bytes) -> dict | None:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(data)
        part = msg.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part else ""
        if part is not None and part.get_content_type() == "text/html":
            body = _strip_html(body)
        return _mail(parse_date(str(msg["date"] or "")), str(msg["from"] or ""), str(msg["subject"] or ""), body)
    except Exception:
        return None


def _find(row: dict, key: str) -> str:
    norm = {str(k).strip().lower(): v for k, v in row.items() if k}
    for alias in _HEADERS[key]:
        if alias.lower() in norm:
            return norm[alias.lower()] or ""
    return ""


def parse_csv(data: bytes) -> tuple[list[dict], list[str]]:
    """컬럼: 받은날짜/보낸사람/제목/본문 (영문 date/from/subject/body도 허용)."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            text = None
    if text is None:
        return [], ["CSV 인코딩을 읽을 수 없습니다(UTF-8 또는 CP949로 저장하세요)."]
    reader = csv.DictReader(io.StringIO(text))
    names = {str(h).strip().lower() for h in (reader.fieldnames or [])}
    if not any(a.lower() in names for a in _HEADERS["subject"]) or not any(a.lower() in names for a in _HEADERS["date"]):
        return [], ["CSV에 '받은날짜'와 '제목' 컬럼이 필요합니다."]
    mails, errors = [], []
    for n, row in enumerate(reader, start=2):
        d = parse_date(_find(row, "date"))
        if d is None:
            errors.append(f"{n}행: 날짜를 해석할 수 없어 '날짜 없음'으로 분류합니다.")
        mails.append(_mail(d, _find(row, "sender"), _find(row, "subject"), _find(row, "body")))
    return mails, errors


def parse_pasted(text: str) -> tuple[list[dict], list[str]]:
    """붙여넣기 형식: 메일마다 '보낸사람:/받은날짜:/제목:' 줄 다음 본문, 메일 사이는 '---' 한 줄."""
    mails, errors = [], []
    header_re = re.compile(r"^\s*(보낸사람|발신자|받은날짜|날짜|제목)\s*:\s*(.*)$")
    for n, block in enumerate(re.split(r"(?m)^\s*-{3,}\s*$", text or ""), start=1):
        if not block.strip():
            continue
        h, body, in_body = {}, [], False
        for line in block.strip().splitlines():
            m = None if in_body else header_re.match(line)
            if m:
                h[m[1]] = m[2].strip()
            else:
                in_body = True
                body.append(line)
        if not any(k in h for k in ("제목", "받은날짜", "날짜", "보낸사람", "발신자")):
            errors.append(f"{n}번째 블록: 헤더(보낸사람/받은날짜/제목)가 없어 건너뛰었습니다.")
            continue
        d = parse_date(h.get("받은날짜") or h.get("날짜"))
        if d is None:
            errors.append(f"{n}번째 블록: 날짜를 해석할 수 없어 '날짜 없음'으로 분류합니다.")
        mails.append(_mail(d, h.get("보낸사람") or h.get("발신자"), h.get("제목"), "\n".join(body)))
    return mails, errors


# ---------- 분석 ----------
def extract_deadlines(text: str, mail_date: date | None) -> list[date]:
    found = set()
    for m in re.finditer(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text):
        try:
            found.add(date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            pass
    if mail_date:
        for m in re.finditer(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text):
            try:
                d = date(mail_date.year, int(m[1]), int(m[2]))
                if d < mail_date - timedelta(days=30):
                    d = date(mail_date.year + 1, int(m[1]), int(m[2]))
                found.add(d)
            except ValueError:
                pass
    return sorted(found)


def one_line_summary(body: str, limit: int = 90) -> str:
    for s in re.split(r"(?<=[.!?。])\s+|\n+", body or ""):
        s = s.strip()
        if s and not (GREETING_RE.match(s) and len(s) <= 30):
            return s if len(s) <= limit else s[:limit - 1] + "…"
    return "(본문 없음)"


def _tokens(name: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[가-힣A-Za-z0-9]+", name or "") if len(t) >= 2 and t not in STOP_TOKENS]


def match_task(text: str, team: TeamData | None, owner_id=None) -> dict | None:
    """메일 내용과 업무명을 비교해 관련 업무를 '추정'. 업무명 토큰 2개 이상 일치해야 함."""
    if team is None:
        return None
    low, best = text.lower(), None
    for t in team.task_records():
        toks = _tokens(t["name"])
        hits = sum(1 for k in toks if k in low)
        if hits >= 2 or (t["name"].lower() in low):
            score = hits * 2 + (1 if owner_id is not None and t.get("owner_id") == owner_id else 0)
            if best is None or score > best[0]:
                best = (score, t)
    return best[1] if best else None


def analyze_mail(m: dict, team: TeamData | None, owner_id, today: date) -> dict:
    text = f"{m['subject']}\n{m['body']}"
    low = text.lower()
    reasons, score = [], 0
    hi = [k for k in HIGH_KW if k in low]
    mid = [k for k in MID_KW if k in low]
    lo = [k for k in LOW_KW if k in low]
    if hi:
        score += min(len(hi), 2) * 3
        reasons.append("긴급/마감 표현: " + ", ".join(hi[:3]))
    if mid:
        score += min(len(mid), 2)
        reasons.append("요청/회신 표현: " + ", ".join(mid[:3]))
    if lo and not hi:
        score -= 1
        reasons.append("공지/참고성 표현: " + ", ".join(lo[:2]))
    task = match_task(text, team, owner_id)
    if task:
        score += {"상": 2, "중": 1}.get(task.get("importance"), 0)
        reasons.append(f"관련 업무 추정: {task['name']} (중요도 {task.get('importance') or '미입력'})")
    deadlines = extract_deadlines(text, m["date"])
    past = [d for d in deadlines if d < today]
    soon = [d for d in deadlines if today <= d <= today + timedelta(days=3)]
    if past:
        score += 2
        reasons.append("복귀 시점에 이미 지난 기한: " + ", ".join(str(d) for d in past))
    if soon:
        score += 1
        reasons.append("3일 이내 기한: " + ", ".join(str(d) for d in soon))
    level = "높음" if score >= 3 else "보통" if score >= 1 else "낮음"
    return {**m, "level": level, "score": score, "reasons": reasons, "summary": one_line_summary(m["body"]),
            "needs_reply": bool(REPLY_RE.search(text)), "deadlines": deadlines, "past_deadline": bool(past),
            "task": task["name"] if task else None}


def summarize_mails(mails: list[dict], start: date, end: date, team: TeamData | None = None,
                    owner_id=None, today: date | None = None) -> dict:
    today = today or date.today()
    in_range = [m for m in mails if m["date"] and start <= m["date"] <= end]
    undated = [m for m in mails if not m["date"]]
    items = [analyze_mail(m, team, owner_id, today) for m in in_range]
    items.sort(key=lambda x: (LEVEL_ORDER[x["level"]], -x["score"], x["date"]))
    by_task: dict[str, list] = {}
    for it in items:
        if it["task"]:
            by_task.setdefault(it["task"], []).append(it)
    return {
        "period": (start, end), "items": items, "undated": undated,
        "outside_count": len(mails) - len(in_range) - len(undated),
        "stats": {"total": len(items), "높음": sum(i["level"] == "높음" for i in items),
                  "보통": sum(i["level"] == "보통" for i in items), "낮음": sum(i["level"] == "낮음" for i in items),
                  "need_reply": sum(i["needs_reply"] for i in items),
                  "past_deadline": sum(i["past_deadline"] for i in items)},
        "by_task": by_task, "by_sender": Counter(i["sender"] or "(발신자 없음)" for i in items).most_common(),
    }


def digest_markdown(res: dict, who: str = "", today: date | None = None) -> str:
    s, e = res["period"]
    st = res["stats"]
    L = [f"# 부재 기간 메일 요약{' — ' + who if who else ''}", "",
         f"- 대상 기간: {s} ~ {e}", f"- 기간 내 메일: {st['total']}건 (우선순위 높음 {st['높음']} · 보통 {st['보통']} · 낮음 {st['낮음']})",
         f"- 회신/확인이 필요해 보이는 메일: {st['need_reply']}건", f"- 복귀 시점에 기한이 지난 항목이 있는 메일: {st['past_deadline']}건"]
    if res["outside_count"]:
        L.append(f"- 기간 밖이라 제외한 메일: {res['outside_count']}건")
    if res["undated"]:
        L.append(f"- ⚠️ 날짜가 없어 기간 판단이 불가능한 메일: {len(res['undated'])}건 (별도 확인 필요)")
    L += ["", "## 우선 확인할 메일 (높음)"]
    high = [i for i in res["items"] if i["level"] == "높음"]
    L += [f"- [{i['date']}] **{i['subject'] or '(제목 없음)'}** — {i['sender'] or '발신자 없음'}\n  - 요약: {i['summary']}\n  - 근거: {'; '.join(i['reasons']) or '-'}"
          for i in high] or ["- 없음"]
    L += ["", "## 업무별 관련 메일(추정)"]
    L += [f"- {t}: {len(v)}건 — " + ", ".join(i["subject"] or "(제목 없음)" for i in v) for t, v in res["by_task"].items()] \
        or ["- 업무와 연결된 메일을 찾지 못했습니다."]
    L += ["", "## 보낸 사람별"]
    L += [f"- {n}: {c}건" for n, c in res["by_sender"]] or ["- 없음"]
    L += ["", "## 전체 목록"]
    L += [f"- ({i['level']}) [{i['date']}] {i['subject'] or '(제목 없음)'} — {i['summary']}" for i in res["items"]] or ["- 없음"]
    L += ["", f"_규칙 기반 자동 요약({today or date.today()} 생성). 키워드·기한 추출 결과이므로 중요한 메일은 반드시 원문을 확인하세요._"]
    return "\n".join(L)


def sample_mails(start: date, end: date) -> list[dict]:
    """데모용 가상 메일(실제 메일 아님). 기간 안 날짜에 분산, 기간 밖 1건 포함."""
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)] or [start]
    at = lambda i: days[i % len(days)]
    fmt = lambda d: f"{d.month}월 {d.day}일"
    return [
        _mail(at(0), "협력사 담당자(가상)", "[긴급] 기능 안전 분석 보고서 일정 확인 요청",
              f"안녕하세요.\n보고서 초안 제출 기한이 {fmt(end + timedelta(days=2))}까지라 확인 부탁드립니다. 회신 요청드립니다."),
        _mail(at(1), "품질팀(가상)", "감사 대응 자료 요청의 건",
              f"안녕하세요.\n외부 감사 대응을 위해 정적 검증 결과 자료가 필요합니다. {fmt(start + timedelta(days=1))}까지 전달 부탁드립니다."),
        _mail(at(1), "개발팀(가상)", "단위 테스트 케이스 보강 관련 문의",
              "안녕하세요.\n단위 테스트 케이스 보강 범위에 경계값 항목이 포함되는지 문의드립니다. 답변 부탁드립니다."),
        _mail(at(2), "총무팀(가상)", "[공지] 사무실 정기 점검 안내", "안녕하세요.\n이번 주 금요일 사무실 정기 점검이 있습니다. 참고 바랍니다."),
        _mail(at(2), "인사팀(가상)", "하반기 교육 일정 안내", "교육 신청은 사내 포털에서 진행됩니다. 참고하세요."),
        _mail(at(3), "개발팀(가상)", "월간 검증 현황 리포트 양식 변경", "리포트 양식이 변경되었습니다. 다음 보고부터 적용 예정이며 검토 부탁드립니다."),
        _mail(at(4), "외부 도구 벤더(가상)", "뉴스레터: 신규 기능 소개", "이번 달 업데이트 소식을 전합니다. 광고성 정보가 포함되어 있습니다."),
        _mail(start - timedelta(days=1), "팀원(가상)", "휴가 전 마지막 공유", "기간 밖 메일 예시입니다. 필터링되어 요약에서 제외됩니다."),
    ]
