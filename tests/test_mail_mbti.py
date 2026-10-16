import os, sqlite3, tempfile, unittest
from datetime import date, timedelta

from data import db
from services import mail_digest as md
from services.models import make_team
from services.validation import validate_member

S, E = date(2026, 10, 19), date(2026, 10, 23)
TODAY = date(2026, 10, 26)


def mail(d, subject="제목", body="", sender="a"):
    return {"date": d, "sender": sender, "subject": subject, "body": body}


def team():
    return make_team(
        [{"id": 1, "name": "A", "role": "", "workload": 50, "contact": "", "mbti": ""}], [],
        [{"id": 1, "name": "기능 안전 분석 보고서", "owner_id": 1, "importance": "상", "status": "진행중", "backup_id": None,
          "due_date": None, "required_skill": "", "remaining": "", "notes": "", "load_pct": 10, "progress": 0}])


class ParseTests(unittest.TestCase):
    def test_parse_date_formats(self):
        self.assertEqual(md.parse_date("2026-10-20 09:30"), date(2026, 10, 20))
        self.assertEqual(md.parse_date("2026.10.20"), date(2026, 10, 20))
        self.assertEqual(md.parse_date("Tue, 20 Oct 2026 09:00:00 +0900"), date(2026, 10, 20))
        self.assertIsNone(md.parse_date("어제"))
        self.assertIsNone(md.parse_date("2026-13-40"))

    def test_parse_eml(self):
        raw = ("From: 홍길동 <h@example.com>\nDate: Tue, 20 Oct 2026 09:00:00 +0900\n"
               "Subject: =?utf-8?B?7YWM7Iqk7Yq4?=\nContent-Type: text/plain; charset=utf-8\n\n안녕하세요. 본문입니다.\n").encode()
        m = md.parse_eml(raw)
        self.assertEqual((m["date"], m["subject"]), (date(2026, 10, 20), "테스트"))
        self.assertIn("본문입니다", m["body"])

    def test_parse_csv_and_errors(self):
        data = "받은날짜,보낸사람,제목,본문\n2026-10-20,x,s1,b1\n모름,y,s2,b2\n".encode("utf-8-sig")
        ms, errs = md.parse_csv(data)
        self.assertEqual(len(ms), 2); self.assertIsNone(ms[1]["date"]); self.assertEqual(len(errs), 1)
        _, errs = md.parse_csv("a,b\n1,2\n".encode())
        self.assertTrue(errs)

    def test_parse_pasted(self):
        txt = "보낸사람: 김\n받은날짜: 2026-10-20\n제목: 안건\n본문 첫줄\n---\n그냥 텍스트\n---\n제목: 두번째\n받은날짜: 2026-10-21\n"
        ms, errs = md.parse_pasted(txt)
        self.assertEqual([m["subject"] for m in ms], ["안건", "두번째"])
        self.assertEqual(ms[0]["body"], "본문 첫줄")
        self.assertEqual(len(errs), 1)


class AnalyzeTests(unittest.TestCase):
    def test_period_filter_and_undated(self):
        mails = [mail(S), mail(E), mail(S - timedelta(days=1)), mail(E + timedelta(days=1)), mail(None)]
        r = md.summarize_mails(mails, S, E, today=TODAY)
        self.assertEqual(r["stats"]["total"], 2)
        self.assertEqual(r["outside_count"], 2)
        self.assertEqual(len(r["undated"]), 1)

    def test_priority_levels(self):
        hi = md.analyze_mail(mail(S, "[긴급] 승인 요청", "오늘까지 회신 부탁드립니다"), None, None, TODAY)
        lo = md.analyze_mail(mail(S, "공지: 안내", "참고하세요"), None, None, TODAY)
        self.assertEqual(hi["level"], "높음"); self.assertTrue(hi["needs_reply"])
        self.assertEqual(lo["level"], "낮음"); self.assertFalse(lo["needs_reply"])

    def test_task_match_requires_two_tokens(self):
        t = team()
        a = md.analyze_mail(mail(S, "기능 안전 분석 보고서 확인", ""), t, 1, TODAY)
        b = md.analyze_mail(mail(S, "기능 점심 메뉴", ""), t, 1, TODAY)
        self.assertEqual(a["task"], "기능 안전 분석 보고서")
        self.assertIsNone(b["task"])
        self.assertIn("관련 업무 추정", " ".join(a["reasons"]))

    def test_deadlines_and_past_flag(self):
        self.assertEqual(md.extract_deadlines("기한은 2026-10-21 입니다", S), [date(2026, 10, 21)])
        self.assertEqual(md.extract_deadlines("10월 22일까지", S), [date(2026, 10, 22)])
        self.assertEqual(md.extract_deadlines("1월 5일", date(2026, 12, 20)), [date(2027, 1, 5)])
        r = md.analyze_mail(mail(S, "자료", "10월 21일까지 제출"), None, None, TODAY)
        self.assertTrue(r["past_deadline"]); self.assertEqual(r["level"], "보통")

    def test_summary_skips_greeting_and_empty_body(self):
        self.assertEqual(md.one_line_summary("안녕하세요.\n핵심 내용입니다."), "핵심 내용입니다.")
        self.assertEqual(md.one_line_summary(""), "(본문 없음)")

    def test_digest_and_sample(self):
        r = md.summarize_mails(md.sample_mails(S, E), S, E, team(), 1, TODAY)
        self.assertEqual(r["outside_count"], 1)
        self.assertGreater(r["stats"]["높음"], 0)
        doc = md.digest_markdown(r, "A", TODAY)
        for sec in ["우선 확인할 메일", "업무별 관련 메일", "보낸 사람별", "전체 목록"]:
            self.assertIn(sec, doc)

    def test_empty(self):
        r = md.summarize_mails([], S, E, today=TODAY)
        self.assertEqual(r["stats"]["total"], 0)
        self.assertIn("없음", md.digest_markdown(r))


class MbtiTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_validation(self):
        self.assertFalse(validate_member("A", 50, ""))
        self.assertFalse(validate_member("A", 50, "intj"))
        self.assertTrue(validate_member("A", 50, "ABCD"))

    def test_crud_uppercases(self):
        db.init_db(self.path, seed=False)
        mid = db.add_member("A", "r", 10, "", "enfp", self.path)
        self.assertEqual(db.load_team_data(self.path).members.set_index("id").loc[mid, "mbti"], "ENFP")
        db.update_member(mid, "A", "r", 10, "", "", self.path)
        self.assertEqual(db.load_team_data(self.path).members.set_index("id").loc[mid, "mbti"], "")

    def test_migrates_existing_db_keeping_data(self):
        c = sqlite3.connect(self.path)
        c.executescript("CREATE TABLE members(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                        "role TEXT DEFAULT '', workload INTEGER DEFAULT 0, contact TEXT DEFAULT '');"
                        "INSERT INTO members(name,role,workload) VALUES ('기존',' r',30);")
        c.commit(); c.close()
        db.init_db(self.path)  # 마이그레이션 후에도 기존 팀원은 보존(샘플 재주입 없음)
        t = db.load_team_data(self.path)
        self.assertEqual(list(t.members["name"]), ["기존"])
        self.assertIn("mbti", t.members.columns)
        db.update_member(1, "기존", "r", 30, "", "INTJ", self.path)
        self.assertEqual(db.load_team_data(self.path).members.iloc[0]["mbti"], "INTJ")


if __name__ == "__main__":
    unittest.main()
