"""회사 메일 서버에서 IMAP으로 받은 메일을 읽기 전용으로 가져온다.

- 받은메일함을 EXAMINE(읽기 전용)으로 열고 BODY.PEEK로 읽어 '읽음' 상태를 바꾸지 않는다.
- 서버 검색(SINCE/BEFORE)으로 기간 메일만 받고, 수신일은 메일 헤더(Date)를 이 PC 시간대로 바꿔 판단한다.
- 표준 라이브러리만 사용한다. 서버는 VACON_IMAP_HOST / VACON_IMAP_PORT 로 바꿀 수 있다.
"""
from __future__ import annotations

import imaplib
import os
import re
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from .mail_digest import parse_eml

DEFAULT_HOST = "gw.suresofttech.com"
DEFAULT_PORT = 993
DEFAULT_DOMAIN = "suresofttech.com"
MAX_MESSAGES = 500
TIMEOUT = 20
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def host() -> str:
    return os.environ.get("VACON_IMAP_HOST", "").strip() or DEFAULT_HOST


def port() -> int:
    try:
        return int(os.environ.get("VACON_IMAP_PORT", "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def login_name(username: str) -> str:
    """Outlook 설정과 같이 메일 주소 전체로 로그인한다. 아이디만 넣으면 회사 도메인을 붙인다."""
    name = (username or "").strip()
    domain = os.environ.get("VACON_MAIL_DOMAIN", "").strip() or DEFAULT_DOMAIN
    return name if "@" in name or not name else f"{name}@{domain}"


def imap_date(d: date) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def search_criteria(start: date, end: date) -> str:
    # 서버 시간대 차이로 경계일 메일이 빠지지 않도록 하루씩 넓게 검색하고, 헤더 날짜로 다시 거른다.
    return f"(SINCE {imap_date(start - timedelta(days=1))} BEFORE {imap_date(end + timedelta(days=2))})"


def received_date(header_date: str | None, internal_date: datetime | None = None) -> date | None:
    """메일 헤더 Date를 이 PC 시간대 기준 날짜로 바꾼다. 없거나 잘못되면 서버 수신 시각을 쓴다."""
    try:
        dt = parsedate_to_datetime(str(header_date or ""))
        return (dt.astimezone() if dt.tzinfo else dt).date()
    except (TypeError, ValueError, IndexError):
        pass
    if internal_date is not None:
        return (internal_date.astimezone() if internal_date.tzinfo else internal_date).date()
    return None


def _connect(username: str, password: str) -> imaplib.IMAP4_SSL:
    if not (username or "").strip() or not password:
        raise ValueError("메일 주소(아이디)와 비밀번호를 입력하세요.")
    try:
        conn = imaplib.IMAP4_SSL(host(), port(), timeout=TIMEOUT)
    except OSError as exc:
        raise RuntimeError(f"메일 서버({host()}:{port()})에 연결하지 못했습니다. 사내망 연결을 확인하세요: {exc}") from None
    try:
        conn.login(login_name(username), password)
    except imaplib.IMAP4.error:
        _close(conn)
        raise RuntimeError("메일 서버 로그인에 실패했습니다. 메일 주소·비밀번호와 IMAP 사용 설정을 확인하세요.") from None
    except UnicodeEncodeError:
        _close(conn)
        raise RuntimeError("메일 주소와 비밀번호는 영문·숫자·기호만 사용할 수 있습니다.") from None
    typ, _ = conn.select("INBOX", readonly=True)
    if typ != "OK":
        _close(conn)
        raise RuntimeError("받은메일함(INBOX)을 열지 못했습니다.")
    return conn


def _close(conn) -> None:
    try:
        conn.logout()
    except Exception:
        pass


def check_login(username: str, password: str) -> str:
    conn = _connect(username, password)
    try:
        typ, data = conn.search(None, "ALL")
        count = len(data[0].split()) if typ == "OK" and data and data[0] else 0
        return f"로그인 확인됨 · 받은메일함 {count}건 (읽기 전용)"
    finally:
        _close(conn)


def _internal_date(meta: bytes) -> datetime | None:
    m = re.search(rb'INTERNALDATE "([^"]+)"', meta or b"")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).decode(), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def message_to_mail(raw: bytes, internal: datetime | None = None) -> dict | None:
    mail = parse_eml(raw)
    if mail is None:
        return None
    header = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    mail["date"] = received_date(header["date"], internal)
    return mail


def fetch_received_mail(start: date, end: date, username: str, password: str) -> tuple[list[dict], bool]:
    """기간[start, end]에 받은 메일을 최신순으로 최대 MAX_MESSAGES건 반환한다. (메일 목록, 한도 초과 여부)"""
    if end < start:
        raise ValueError("조회 종료일은 시작일보다 빠를 수 없습니다.")
    conn = _connect(username, password)
    try:
        typ, data = conn.search(None, search_criteria(start, end))
        if typ != "OK":
            raise RuntimeError("메일 서버에서 기간 검색에 실패했습니다.")
        ids = (data[0] or b"").split() if data else []
        ids.reverse()  # 최신 메일 우선
        mails, truncated = [], False
        for msg_id in ids:
            if len(mails) >= MAX_MESSAGES:
                truncated = True
                break
            typ, parts = conn.fetch(msg_id, "(INTERNALDATE BODY.PEEK[])")
            if typ != "OK":
                continue
            for part in parts or []:
                if not isinstance(part, tuple):
                    continue
                mail = message_to_mail(part[1], _internal_date(part[0]))
                if mail is not None and (mail["date"] is None or start <= mail["date"] <= end):
                    mails.append(mail)
        mails.sort(key=lambda m: m["date"] or date.min, reverse=True)
        return mails, truncated
    except imaplib.IMAP4.error as exc:
        raise RuntimeError(f"메일을 읽는 중 서버 오류가 발생했습니다: {exc}") from None
    finally:
        _close(conn)
