import unittest
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from unittest import mock

from services import imap_mail

KST = timezone(timedelta(hours=9))


def raw_mail(subject, when, body="내용입니다."):
    msg = EmailMessage()
    msg["From"] = "홍길동 <hong@suresofttech.com>"
    msg["Subject"] = subject
    if when is not None:
        msg["Date"] = format_datetime(when)
    msg.set_content(body)
    return msg.as_bytes()


class FakeImap:
    instances = []
    preset_messages: dict = {}
    preset_fail_login = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []
        self.messages = dict(FakeImap.preset_messages)
        self.fail_login = FakeImap.preset_fail_login
        FakeImap.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user))
        if self.fail_login:
            raise imap_mail.imaplib.IMAP4.error("auth failed")
        return "OK", [b""]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def search(self, charset, criteria):
        self.calls.append(("search", criteria))
        return "OK", [b" ".join(self.messages)]

    def fetch(self, msg_id, parts):
        self.calls.append(("fetch", msg_id, parts))
        return "OK", [(msg_id + b' (INTERNALDATE "05-Nov-2026 10:00:00 +0900" BODY[] {1}', self.messages[msg_id]), b")"]

    def logout(self):
        self.calls.append(("logout",))


class ImapMailTests(unittest.TestCase):
    def setUp(self):
        FakeImap.instances.clear()
        FakeImap.preset_messages, FakeImap.preset_fail_login = {}, False
        patcher = mock.patch.object(imap_mail.imaplib, "IMAP4_SSL", FakeImap)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_login_name_adds_company_domain(self):
        self.assertEqual(imap_mail.login_name("hong"), "hong@suresofttech.com")
        self.assertEqual(imap_mail.login_name(" hong@suresofttech.com "), "hong@suresofttech.com")

    def test_search_criteria_uses_server_date_range(self):
        self.assertEqual(imap_mail.search_criteria(date(2026, 11, 2), date(2026, 11, 6)),
                         "(SINCE 01-Nov-2026 BEFORE 08-Nov-2026)")

    def test_received_date_uses_local_timezone(self):
        utc_late = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)  # 한국 시간 11/2 05:00
        with mock.patch.dict("os.environ", {"TZ": "Asia/Seoul"}):
            got = imap_mail.received_date(format_datetime(utc_late))
        self.assertEqual(got, utc_late.astimezone().date())
        self.assertIsNone(imap_mail.received_date("잘못된 날짜"))
        self.assertEqual(imap_mail.received_date(None, datetime(2026, 11, 5, 10, tzinfo=KST)),
                         datetime(2026, 11, 5, 10, tzinfo=KST).astimezone().date())

    def test_fetch_is_read_only_and_filters_by_header_date(self):
        FakeImap.preset_messages = {
            b"1": raw_mail("기간 전 메일 v11.3", datetime(2026, 10, 31, 12, tzinfo=KST)),
            b"2": raw_mail("오늘 일정 공유", datetime(2026, 11, 3, 9, tzinfo=KST)),
            b"3": raw_mail("기간 마지막 날", datetime(2026, 11, 6, 15, tzinfo=KST)),
            b"4": raw_mail("기간 후 메일", datetime(2026, 11, 7, 12, tzinfo=KST)),
        }
        mails, truncated = imap_mail.fetch_received_mail(date(2026, 11, 2), date(2026, 11, 6), "hong", "pw")
        self.assertFalse(truncated)
        self.assertEqual([m["subject"] for m in mails], ["기간 마지막 날", "오늘 일정 공유"])
        self.assertEqual(mails[0]["sender"], "홍길동 <hong@suresofttech.com>")
        self.assertIn("내용입니다.", mails[0]["body"])
        conn = FakeImap.instances[-1]
        self.assertIn(("login", "hong@suresofttech.com"), conn.calls)
        self.assertIn(("select", "INBOX", True), conn.calls)
        self.assertTrue(all("BODY.PEEK" in c[2] for c in conn.calls if c[0] == "fetch"))
        self.assertEqual(conn.calls[-1], ("logout",))

    def test_login_failure_is_reported_and_connection_closed(self):
        FakeImap.preset_fail_login = True
        with self.assertRaisesRegex(RuntimeError, "로그인에 실패"):
            imap_mail.check_login("hong", "bad")
        self.assertEqual(FakeImap.instances[-1].calls[-1], ("logout",))

    def test_missing_credentials(self):
        with self.assertRaises(ValueError):
            imap_mail.fetch_received_mail(date(2026, 11, 2), date(2026, 11, 6), "", "")


if __name__ == "__main__":
    unittest.main()
