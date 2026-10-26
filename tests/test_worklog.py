import os, sqlite3, tempfile, unittest
from datetime import date, timedelta

from data import db
from services import assistant, handover, recommend, risk, validation
from services import worklog as wl
from services.models import make_team, required_skills
from services.sheet_sync import SheetError

MON = date(2026, 10, 12)          # 월요일
D = lambda n: MON + timedelta(days=n)
TODAY = date(2026, 10, 23)        # 금요일


def members():
    names = {1: "팀장", 2: "주해원", 3: "김재강", 4: "황지원", 5: "이혜윤", 6: "타팀원"}
    return [{"id": i, "name": n, "role": "", "workload": 50, "contact": "", "mbti": ""} for i, n in names.items()]


def skills():
    s = [{"member_id": m, "skill": "단위테스트", "level": 3} for m in (2, 3, 4, 5)]
    s += [{"member_id": m, "skill": "요구사양서 작성", "level": 2} for m in (2, 3, 4)]
    return s


def log(mid, d, tc=0, req=0, issue=0, track="dynamic", ver="v1"):
    return {"id": None, "source": "T", "track": track, "date": d, "member_id": mid, "version": ver,
            "category": "c", "req": req, "tc": tc, "issue": issue}


def leave(mid, s, e, typ="연차", i=1):
    return {"id": i, "member_id": mid, "start": s, "end": e, "type": typ, "note": ""}


def team(logs=(), leaves=(), tasks=()):
    return make_team(members(), skills(), tasks, leaves, logs)


def weekday_logs(mid, start, end, tc, track="dynamic"):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(log(mid, d, tc=tc if track == "dynamic" else 0, issue=0 if track == "dynamic" else tc, track=track))
        d += timedelta(days=1)
    return out


CSV_DYN = """날짜,담당자,버전,업무 구분,x1,x2,x3,x4,완료한 Req,x6,x7,x8,x9,x10,실행한 TC
2026-10-19,주해원 선임,v1,TC 실행,,,,,3,,,,,,20
2026-10-19,김재강,v1,TC 실행,,,,,"REQ-1, REQ-2",,,,,,15
2026-10-20,없는사람,v1,TC 실행,,,,,1,,,,,,5
모름,주해원,v1,x,,,,,1,,,,,,5
,,,,,,,,,,,,,,
"""
CFG_DYN = {"role": "log_dynamic", "header_row": 1,
           "columns": {"date": "A", "member": "B", "version": "C", "category": "D", "req": "I", "tc": "O"},
           "headers": {"date": "날짜", "member": "담당자", "req": "완료한 Req", "tc": "실행한 TC"}}


class LetterAndParseTests(unittest.TestCase):
    def test_col_letters(self):
        self.assertEqual([wl.col_index(x) for x in "A I O AA".split()], [0, 8, 14, 26])
        self.assertEqual([wl.col_letter(i) for i in (0, 8, 14, 26, 27)], ["A", "I", "O", "AA", "AB"])
        self.assertTrue(wl.valid_letter("o")); self.assertFalse(wl.valid_letter("1")); self.assertFalse(wl.valid_letter("ABCD"))

    def test_parse_count(self):
        self.assertEqual(wl.parse_count("1,234"), (1234.0, None))
        self.assertEqual(wl.parse_count(""), (0.0, None))
        self.assertEqual(wl.parse_count("REQ-1, REQ-2;REQ-3"), (3.0, "text"))
        self.assertEqual(wl.parse_count("-5"), (0.0, "invalid"))

    def test_parse_logs_by_letter(self):
        r = wl.parse_logs(CSV_DYN, CFG_DYN, team())
        self.assertEqual(len(r["entries"]), 2)
        a, b = r["entries"]
        self.assertEqual((a["member_id"], a["date"], a["req"], a["tc"], a["category"]), (2, date(2026, 10, 19), 3, 20, "TC 실행"))
        self.assertEqual(b["req"], 2.0)                       # 텍스트 목록은 항목 개수
        self.assertEqual(r["text_cells"], 1)
        self.assertEqual(r["unmatched_members"], ["없는사람"])
        self.assertEqual(r["skipped"], 2)                     # 없는 팀원 1 + 날짜 해석 불가 1
        self.assertTrue(any("날짜" in w for w in r["warnings"]))
        self.assertEqual(r["date_range"], (date(2026, 10, 19), date(2026, 10, 19)))

    def test_header_change_is_detected_not_silently_wrong(self):
        shifted = "날짜,담당자,새 열,버전,업무 구분\n2026-10-19,주해원,1,v1,x\n"
        with self.assertRaisesRegex(SheetError, "열 구성이 바뀐"):
            wl.parse_logs(shifted, CFG_DYN, team())

    def test_config_errors(self):
        with self.assertRaises(SheetError): wl.parse_logs("a\n", {"role": "tasks"}, team())
        with self.assertRaises(SheetError): wl.parse_logs("a\n", {**CFG_DYN, "columns": {"date": "A"}}, team())
        with self.assertRaises(SheetError): wl.parse_logs("a\n", {**CFG_DYN, "header_row": 9, "headers": {}}, team())
        with self.assertRaises(SheetError): wl.parse_logs("a\n", {**CFG_DYN, "columns": {**CFG_DYN["columns"], "tc": "1"}, "headers": {}}, team())

    def test_req_track_uses_issue_column(self):
        cfg = {"role": "log_req", "header_row": 1, "headers": {}, "columns": {"date": "A", "member": "B", "issue": "G"}}
        csv_ = "날짜,담당자,v,c,d,e,완료 일감\n2026-10-19,황지원,v1,x,,,4\n"
        r = wl.parse_logs(csv_, cfg, team())
        self.assertEqual((r["track"], r["entries"][0]["issue"], r["entries"][0]["tc"]), ("req", 4.0, 0.0))


class StatsTests(unittest.TestCase):
    def test_avg_excludes_weekend_and_leave_days(self):
        logs = weekday_logs(3, D(0), D(4), 20)                 # 월~금 20TC
        t = team(logs, [leave(3, D(3), D(4))])                 # 목·금 휴가
        st = wl.member_stats(t, 3, "dynamic", D(0), D(6))
        self.assertEqual(st["workdays"], 3)
        self.assertEqual(st["sum"]["tc"], 100)
        self.assertAlmostEqual(st["avg"]["tc"], 100 / 3)

    def test_no_logs_is_not_zero(self):
        st = wl.member_stats(team(), 3, "dynamic", D(0), D(4))
        self.assertFalse(st["has_logs"])
        tbl = wl.throughput_table(team(), TODAY)
        self.assertTrue((tbl["최근 기록일"] == "기록 없음").all())

    def test_throughput_table_only_unit_pool(self):
        t = team(weekday_logs(6, D(0), D(4), 99))              # 단위 테스트 인원 아님
        self.assertNotIn("타팀원", set(wl.throughput_table(t, TODAY)["팀원"]))


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = D(14), D(16)                    # 휴가: 다음 주 월~수
        base = D(-14), D(11)
        self.logs = (weekday_logs(2, *base, 10) + weekday_logs(3, *base, 30) + weekday_logs(4, *base, 20))

    def test_gap_and_proportional_split(self):
        t = team(self.logs, [leave(2, self.start, self.end)])
        r = wl.leave_impact(t, 2, self.start, self.end, TODAY, 28)
        it = next(i for i in r["items"] if i["metric"] == "tc")
        self.assertEqual(r["lost_days"], 3)
        self.assertAlmostEqual(it["gap"], it["avg"] * 3)
        rows = {x["팀원"]: x for x in it["rows"]}
        self.assertEqual(set(rows), {"김재강", "황지원"})
        self.assertAlmostEqual(sum(x["분담 필요량"] for x in it["rows"]), it["gap"], delta=1)
        self.assertGreater(rows["김재강"]["분담 필요량"], rows["황지원"]["분담 필요량"])   # 처리량 큰 사람이 더 많이 분담
        self.assertEqual(rows["김재강"]["평소 대비"], rows["황지원"]["평소 대비"])         # 용량 비례라 증가율 동일

    def test_absent_colleague_excluded_and_none_available(self):
        t = team(self.logs, [leave(2, self.start, self.end), leave(3, self.start, self.end, i=2)])
        it = next(i for i in wl.leave_impact(t, 2, self.start, self.end, TODAY)["items"] if i["metric"] == "tc")
        self.assertEqual([x["팀원"] for x in it["rows"]], ["황지원"])
        self.assertTrue(any("김재강" in e for e in it["excluded"]))
        t2 = team(self.logs, [leave(m, self.start, self.end, i=m) for m in (2, 3, 4, 5)])
        it2 = next(i for i in wl.leave_impact(t2, 2, self.start, self.end, TODAY)["items"] if i["metric"] == "tc")
        self.assertEqual(it2["rows"], []); self.assertTrue(it2["warnings"])

    def test_overload_warning_and_no_logs_and_non_pool(self):
        t = team(weekday_logs(2, D(-14), D(11), 100) + weekday_logs(3, D(-14), D(11), 5), [leave(2, self.start, self.end)])
        it = next(i for i in wl.leave_impact(t, 2, self.start, self.end, TODAY)["items"] if i["metric"] == "tc")
        self.assertTrue(any("부담이 큽니다" in w for w in it["warnings"]))
        r = wl.leave_impact(team(), 2, self.start, self.end, TODAY)
        self.assertIsNone(r["items"][0]["gap"]); self.assertIn("예측할 수 없습니다", r["items"][0]["warnings"][0])
        self.assertFalse(wl.leave_impact(team(), 6, self.start, self.end, TODAY)["applicable"])

    def test_pool_availability(self):
        t = team(leaves=[leave(2, D(0), D(0)), leave(3, D(0), D(0), i=2)])
        av = wl.pool_availability(t, D(0), D(1))
        self.assertEqual((av.iloc[0]["가용"], av.iloc[0]["전체"]), (2, 4)); self.assertEqual(av.iloc[1]["가용"], 4)

    def test_recent_summary(self):
        t = team([log(2, D(10), tc=5, ver="v1"), log(2, D(11), tc=7, ver="v2"), log(2, D(-30), tc=99)])
        s = wl.recent_summary(t, 2, TODAY, 14)
        self.assertEqual(s["tracks"]["dynamic"]["sum"]["tc"], 12)
        self.assertEqual(set(s["tracks"]["dynamic"]["by_version"]), {"v1", "v2"})


class UnitPoolRuleTests(unittest.TestCase):
    def test_required_skills_and_holders(self):
        self.assertEqual(required_skills("단위테스트, 요구사양서 작성"), ["단위테스트", "요구사양서 작성"])
        self.assertEqual(required_skills("HIL/SIL"), ["HIL/SIL"])
        self.assertEqual(team().skill_holders("단위테스트, 요구사양서 작성"), {2, 3, 4})
        self.assertEqual(team().unit_pool(), {2, 3, 4, 5})

    def test_recommend_only_unit_pool_with_all_skills(self):
        task = {"id": 1, "name": "요구사양서", "owner_id": 2, "backup_id": None, "importance": "중", "status": "진행중",
                "progress": 0, "due_date": None, "required_skill": "단위테스트, 요구사양서 작성", "remaining": "", "notes": "", "load_pct": 10}
        r = recommend.recommend_backups(team(tasks=[task], leaves=[leave(2, D(0), D(4))]), 1, D(0), D(4))
        self.assertEqual({c["이름"] for c in r["candidates"]}, {"김재강", "황지원"})
        ex = {e["이름"]: e["제외 사유"] for e in r["excluded"]}
        self.assertIn("요구사양서 작성", ex["이혜윤"]); self.assertIn("단위테스트", ex["타팀원"])

    def test_validate_assignees(self):
        t = team()
        self.assertTrue(validation.validate_assignees(t, "단위테스트", 6, None))
        self.assertTrue(validation.validate_assignees(t, "단위테스트, 요구사양서 작성", 2, 6))
        self.assertFalse(validation.validate_assignees(t, "단위테스트", 2, 3))
        self.assertFalse(validation.validate_assignees(t, "기능안전", 6, None))      # 다른 역량 업무는 제한 없음

    def test_risk_pool_low_reason(self):
        task = {"id": 1, "name": "T", "owner_id": 2, "backup_id": 3, "importance": "하", "status": "진행중", "progress": 0,
                "due_date": None, "required_skill": "단위테스트", "remaining": "", "notes": "", "load_pct": 10}
        two_absent = team(leaves=[leave(2, D(0), D(4)), leave(4, D(0), D(4), i=2), leave(5, D(0), D(4), i=3)], tasks=[task])
        codes = [c for c, _, _ in risk.assess_task_day(two_absent, task, D(1))["reasons"]]
        self.assertIn("skill_pool_low", codes)                 # 4명 중 3명 부재(근무 1/4)
        one_absent = team(leaves=[leave(2, D(0), D(4))], tasks=[task])
        codes = [c for c, _, _ in risk.assess_task_day(one_absent, task, D(1))["reasons"]]
        self.assertNotIn("skill_pool_low", codes)              # 3/4 근무 → 해당 없음


class DbAndIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        os.environ["VACON_TODAY"] = "2026-10-20"

    def tearDown(self):
        os.environ.pop("VACON_TODAY", None)
        if os.path.exists(self.path): os.remove(self.path)

    def test_replace_delete_and_cascade(self):
        db.init_db(self.path)
        n_sample = len(db.load_team_data(self.path).logs)
        self.assertGreater(n_sample, 0)
        ents = wl.parse_logs(CSV_DYN, CFG_DYN, db.load_team_data(self.path))["entries"]
        # CSV의 이름이 샘플 팀원 이름과 달라 0건일 수 있으므로 직접 엔트리 구성
        ents = [{"track": "dynamic", "date": date(2026, 10, 19), "member_id": 4, "version": "v1", "category": "c", "req": 3, "tc": 20, "row": 2}]
        self.assertEqual(db.replace_logs("시트A", ents, self.path), {"removed": 0, "added": 1})
        self.assertEqual(db.replace_logs("시트A", ents + ents, self.path), {"removed": 1, "added": 2})   # 교체(누적 아님)
        t = db.load_team_data(self.path)
        self.assertEqual(int((t.logs["source"] == "시트A").sum()), 2)
        self.assertEqual(len(t.logs), n_sample + 2)
        db.delete_logs("샘플", self.path)
        self.assertEqual(set(db.load_team_data(self.path).logs["source"]), {"시트A"})
        db.delete_member(4, self.path)
        self.assertEqual(len(db.load_team_data(self.path).logs), 0)

    def test_migration_creates_work_logs_on_old_db(self):
        c = sqlite3.connect(self.path)
        c.executescript("CREATE TABLE members(id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT, workload INTEGER, contact TEXT);"
                        "INSERT INTO members(name) VALUES('기존');")
        c.commit(); c.close()
        db.init_db(self.path)
        t = db.load_team_data(self.path)
        self.assertEqual(list(t.members["name"]), ["기존"]); self.assertEqual(len(t.logs), 0)

    def test_default_sync_presets_use_user_columns(self):
        db.init_db(self.path)
        src = {s["role"]: s for s in db.load_sync_config(self.path)["sources"]}
        self.assertEqual(src["log_dynamic"]["columns"], {"date": "A", "member": "B", "version": "C", "category": "D", "req": "I", "tc": "O"})
        self.assertEqual(src["log_req"]["columns"], {"date": "A", "member": "B", "version": "C", "category": "D", "issue": "G"})

    def test_sample_meets_unit_rule_and_demo_scenarios(self):
        db.init_db(self.path)
        t = db.load_team_data(self.path)
        self.assertEqual({t.name(m) for m in t.unit_pool()}, {"주해원", "김재강", "황지원", "이혜윤"})
        for task in t.task_records():                          # 샘플 업무의 담당/대체는 필수 역량 보유자
            for who in (task["owner_id"], task["backup_id"]):
                if who is not None and task["required_skill"]:
                    self.assertIn(who, t.skill_holders(task["required_skill"]), task["name"])
        from services.models import today
        df = risk.assess_tasks(t, today(), today() + timedelta(days=14))
        self.assertIn("High", set(df["위험도"])); self.assertIn("데이터 부족", set(df["위험도"]))

    def test_handover_and_assistant_use_logs(self):
        db.init_db(self.path)
        t = db.load_team_data(self.path)
        lv = next(l for l in t.leave_records() if l["member_id"] == 3)
        md = handover.generate_handover(t, 3, lv["start"], lv["end"], lv["type"], date(2026, 10, 20))
        self.assertIn("최근 업무 로그", md); self.assertIn("예상 처리 공백", md); self.assertIn("동적 검증", md)
        non_pool = handover.generate_handover(t, 1, date(2026, 10, 20), date(2026, 10, 21))
        self.assertNotIn("예상 처리 공백", non_pool)
        a = assistant.answer_question("단위 테스트 인원별 처리량 알려줘", t, date(2026, 10, 20))
        self.assertTrue(a.supported and len(a.tables) == 2)
        g = assistant.answer_question("김재강 휴가 때 처리 공백이 얼마나 생겨?", t, date(2026, 10, 20))
        self.assertIn("공백", g.text); self.assertTrue(g.supported)
        self.assertFalse(assistant.answer_question("타팀 휴가 처리 공백", t, date(2026, 10, 20)).supported)
        empty = make_team(members(), skills(), [], [], [])
        self.assertFalse(assistant.answer_question("처리량 알려줘", empty, TODAY).supported)


if __name__ == "__main__":
    unittest.main()
