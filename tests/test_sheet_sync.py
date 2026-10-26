import os, tempfile, unittest
import urllib.error
from datetime import date
from unittest import mock

from data import db
from services import sheet_sync as ss
from services.models import make_team

SID = "1SU9VQEu_GOOvtd4CZHSPLXJWURkQLUQzPi5XRLBLHSs"


def team(tasks=()):
    members = [{"id": 1, "name": "김재강", "role": "", "workload": 50, "contact": "", "mbti": ""},
               {"id": 2, "name": "황지원", "role": "", "workload": 50, "contact": "", "mbti": ""}]
    skills = [{"member_id": 1, "skill": "단위테스트", "level": 3}]  # 김재강만 단위 테스트 인원
    return make_team(members, skills, tasks, [])


def task(id, name, **kw):
    base = dict(id=id, name=name, owner_id=1, backup_id=2, importance="상", status="진행중", progress=10,
                due_date=None, required_skill="단위테스트", remaining="", notes="", load_pct=10, source="")
    base.update(kw)
    return base


CFG = {"label": "T", "role": "tasks", "required_skill": "단위테스트", "default_importance": None,
       "columns": {"name": "업무", "owner": "담당", "status": "상태", "progress": "진행률", "due": "마감"}}

CSV = """프로젝트 제목,,,,
업무,담당,상태,진행률,마감,
단위 테스트 A,김재강 책임,진행 중,60%,2026-10-30,
단위 테스트 B,없는사람,대기,,10/31/2026,
,,,,,
"""


class HelperTests(unittest.TestCase):
    def test_sheet_id_and_url(self):
        u = f"https://docs.google.com/spreadsheets/d/{SID}/edit?gid=0#gid=0"
        self.assertEqual(ss.extract_sheet_id(u), SID)
        self.assertEqual(ss.csv_url(SID, "12"), f"https://docs.google.com/spreadsheets/d/{SID}/export?format=csv&gid=12")
        with self.assertRaises(ss.SheetError): ss.extract_sheet_id("https://evil.example.com/x")
        with self.assertRaises(ss.SheetError): ss.csv_url(SID, "abc")

    def test_read_table_header_row_and_dup_headers(self):
        h, recs = ss.read_table(CSV, header_row=2)
        self.assertEqual(h[:3], ["업무", "담당", "상태"]); self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["_row"], 3)
        h, _ = ss.read_table("a,a,\n1,2,3\n")
        self.assertEqual(h, ["a", "a #2", "(열 3)"])
        with self.assertRaises(ss.SheetError): ss.read_table("a\n", header_row=5)

    def test_parsers(self):
        self.assertEqual(ss.parse_progress("60%"), 60); self.assertEqual(ss.parse_progress("0.6"), 60)
        self.assertEqual(ss.parse_progress("100"), 100); self.assertIsNone(ss.parse_progress(""))
        for bad in ("abc", "150"):
            with self.assertRaises(ValueError): ss.parse_progress(bad)
        self.assertEqual(ss.parse_status("진행 중"), "진행중"); self.assertEqual(ss.parse_status("Done"), "완료")
        with self.assertRaises(ValueError): ss.parse_status("미완료")
        with self.assertRaises(ValueError): ss.parse_status("???")
        self.assertEqual(ss.parse_sheet_date("2026. 10. 30"), date(2026, 10, 30))
        self.assertEqual(ss.parse_sheet_date("10/31/2026"), date(2026, 10, 31))
        self.assertEqual(ss.parse_sheet_date("31/10/2026"), date(2026, 10, 31))
        with self.assertRaises(ValueError): ss.parse_sheet_date("내일")

    def test_match_owner(self):
        t = team()
        self.assertEqual(ss.match_owner("김재강 책임", t)[0], 1)
        oid, note = ss.match_owner("황지원, 김재강", t)
        self.assertEqual(oid, 2); self.assertIn("첫 번째", note)
        self.assertIsNone(ss.match_owner("홍길동", t)[0])

    def test_fetch_errors(self):
        def boom(code):
            return urllib.error.HTTPError("u", code, "m", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=boom(403)):
            with self.assertRaisesRegex(ss.SheetError, "접근 권한"): ss.fetch_csv(SID, 0)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(ss.SheetError, "연결할 수 없습니다"): ss.fetch_csv(SID, 0)
        resp = mock.MagicMock(); resp.__enter__.return_value = resp
        resp.read.return_value = b"<!DOCTYPE html><html>login</html>"; resp.headers.get.return_value = "text/html"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaisesRegex(ss.SheetError, "로그인"): ss.fetch_csv(SID, 0)
        resp.read.return_value = "업무,담당\nA,B\n".encode(); resp.headers.get.return_value = "text/csv"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertIn("업무", ss.fetch_csv(SID, 0))


class PlanTests(unittest.TestCase):
    def test_add_and_unmatched_owner_and_warnings(self):
        _, recs = ss.read_table(CSV, 2)
        p = ss.plan_sync(team(), recs, CFG)
        self.assertEqual([t["name"] for t in p["add"]], ["단위 테스트 A", "단위 테스트 B"])
        a, b = p["add"]
        self.assertEqual((a["owner_id"], a["status"], a["progress"], a["due_date"]), (1, "진행중", 60, date(2026, 10, 30)))
        self.assertIsNone(b["owner_id"]); self.assertEqual(p["unmatched_owners"], ["없는사람"])
        self.assertEqual(b["due_date"], date(2026, 10, 31))
        self.assertIsNone(a["importance"])  # 시트에 없는 값은 추측하지 않음

    def test_update_keeps_vacon_owned_fields_and_detects_no_change(self):
        t = team([task(1, "단위 테스트 A", status="대기", progress=60, due_date=date(2026, 10, 30))])
        _, recs = ss.read_table(CSV, 2)
        p = ss.plan_sync(t, recs, CFG)
        u = p["update"][0]
        self.assertEqual(set(u["changes"]), {"status"})            # 진행률·마감일은 동일 → 변경 없음
        self.assertNotIn("importance", u["changes"]); self.assertNotIn("backup_id", u["changes"])
        self.assertEqual(len(p["add"]), 1)                          # B만 신규

    def test_empty_cell_does_not_overwrite(self):
        t = team([task(1, "단위 테스트 B", progress=30, status="진행중")])
        _, recs = ss.read_table(CSV, 2)
        p = ss.plan_sync(t, recs, CFG)
        ch = next(u for u in p["update"] if u["name"] == "단위 테스트 B")["changes"]
        self.assertNotIn("progress", ch)                            # 시트 진행률 칸이 비어 있음

    def test_bad_values_become_warnings_not_guesses(self):
        recs = [{"업무": "X", "담당": "", "상태": "미완료", "진행률": "abc", "마감": "내일", "_row": 5}]
        p = ss.plan_sync(team(), recs, CFG)
        self.assertEqual(len(p["warnings"]), 3)
        self.assertIsNone(p["add"][0]["status"]); self.assertIsNone(p["add"][0]["due_date"])

    def test_schedule_role_updates_due_only(self):
        t = team([task(1, "단위 테스트 A", due_date=date(2026, 1, 1))])
        cfg = {"label": "S", "role": "schedule", "columns": {"name": "업무", "due": "마감"}}
        p = ss.plan_sync(t, [{"업무": "단위테스트A", "마감": "2026-10-30", "_row": 2},
                             {"업무": "없는 업무", "마감": "2026-10-31", "_row": 3}], cfg)
        self.assertEqual(p["update"][0]["changes"], {"due_date": (date(2026, 1, 1), date(2026, 10, 30))})
        self.assertEqual(p["unmatched_names"], ["없는 업무"]); self.assertEqual(p["add"], [])

    def test_missing_and_config_errors(self):
        t = team([task(1, "사라진 업무", source="T")])
        self.assertEqual(ss.plan_sync(t, [], CFG)["missing"], ["사라진 업무"])
        with self.assertRaises(ss.SheetError): ss.plan_sync(t, [], {"columns": {}})


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        os.environ["VACON_TODAY"] = "2026-10-15"
        db.init_db(self.path)

    def tearDown(self):
        os.environ.pop("VACON_TODAY", None)
        if os.path.exists(self.path): os.remove(self.path)

    def test_apply_add_update_and_source_tracking(self):
        team_ = db.load_team_data(self.path)
        name = team_.members.iloc[0]["name"]
        existing = team_.task_records()[0]
        csv_text = f"업무,담당,상태,진행률,마감\n새 업무 Z,{name},진행중,40%,2026-11-02\n{existing['name']},{name},지연,,\n"
        _, recs = ss.read_table(csv_text)
        p = ss.plan_sync(team_, recs, CFG)
        r = db.apply_sync_plan(p, "T", self.path)
        self.assertEqual(r, {"added": 1, "updated": 1})
        t = db.load_team_data(self.path)
        new = next(x for x in t.task_records() if x["name"] == "새 업무 Z")
        self.assertEqual((new["status"], new["progress"], new["due_date"], new["source"], new["required_skill"]),
                         ("진행중", 40, date(2026, 11, 2), "T", "단위테스트"))
        self.assertIsNone(new["backup_id"])
        upd = next(x for x in t.task_records() if x["id"] == existing["id"])
        self.assertEqual((upd["status"], upd["source"]), ("지연", "T"))
        self.assertEqual(upd["importance"], existing["importance"])    # VACON 전용 값 유지

    def test_sync_config_roundtrip_and_source_migration(self):
        db.save_sync_config({"sheet": "u", "sources": [CFG]}, self.path)
        self.assertEqual(db.load_sync_config(self.path)["sources"][0]["label"], "T")
        import sqlite3
        p2 = tempfile.mktemp(suffix=".db"); c = sqlite3.connect(p2)
        c.executescript("CREATE TABLE members(id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT, workload INTEGER, contact TEXT);"
                        "CREATE TABLE tasks(id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_id INTEGER, backup_id INTEGER, importance TEXT,"
                        "status TEXT, progress INTEGER, due_date TEXT, required_skill TEXT, remaining TEXT, notes TEXT, load_pct INTEGER);"
                        "INSERT INTO members(name) VALUES('기존');INSERT INTO tasks(name) VALUES('기존 업무');")
        c.commit(); c.close()
        db.init_db(p2)
        self.assertEqual(db.load_team_data(p2).tasks.iloc[0]["name"], "기존 업무")
        self.assertIn("source", db.load_team_data(p2).tasks.columns)
        os.remove(p2)


if __name__ == "__main__":
    unittest.main()
