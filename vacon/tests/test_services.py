import os, tempfile, unittest
from datetime import date, timedelta

from data import db
from services import assistant, calendar as cal, handover, recommend, risk, validation
from services.config import merge_rules, validate_rules
from services.models import make_team

MON = date(2026, 10, 19)  # 월요일
D = lambda n: MON + timedelta(days=n)


def team(tasks=(), leaves=(), extra_members=()):
    members = [
        {"id": 1, "name": "A", "role": "BE", "workload": 80, "contact": ""},
        {"id": 2, "name": "B", "role": "BE", "workload": 50, "contact": "x"},
        {"id": 3, "name": "C", "role": "FE", "workload": 95, "contact": ""},
        *extra_members,
    ]
    skills = [{"member_id": 1, "skill": "백엔드", "level": 3}, {"member_id": 2, "skill": "백엔드", "level": 2},
              {"member_id": 3, "skill": "프론트", "level": 3}]
    return make_team(members, skills, tasks, leaves)


def task(**kw):
    base = dict(id=1, name="T", owner_id=1, backup_id=None, importance="상", status="진행중", progress=10,
                due_date=D(2), required_skill="백엔드", remaining="r", notes="n", load_pct=10)
    base.update(kw)
    return base


def leave(member_id, s, e, typ="연차", id=1):
    return {"id": id, "member_id": member_id, "start": s, "end": e, "type": typ, "note": ""}


class RiskTests(unittest.TestCase):
    def day(self, t, leaves):
        tm = team([t], leaves)
        return risk.assess_task_day(tm, tm.task_records()[0], D(1))

    def test_high_deadline_overlap_no_backup(self):
        r = self.day(task(), [leave(1, D(0), D(4))])
        self.assertEqual(r["level"], risk.HIGH)
        codes = [c for c, _, _ in r["reasons"]]
        self.assertIn("no_backup", codes); self.assertIn("deadline_overlap", codes)

    def test_medium_with_backup(self):
        r = self.day(task(backup_id=2, due_date=D(30)), [leave(1, D(0), D(4))])
        self.assertEqual(r["level"], risk.MEDIUM)
        self.assertNotIn("no_backup", [c for c, _, _ in r["reasons"]])

    def test_low_when_owner_present(self):
        self.assertEqual(self.day(task(), [])["level"], risk.LOW)

    def test_backup_also_absent_raises_score(self):
        a = self.day(task(backup_id=2, due_date=D(30)), [leave(1, D(0), D(4))])
        b = self.day(task(backup_id=2, due_date=D(30)), [leave(1, D(0), D(4)), leave(2, D(0), D(4), id=2)])
        self.assertGreater(b["score"], a["score"])
        self.assertIn("backup_absent", [c for c, _, _ in b["reasons"]])

    def test_missing_owner_is_insufficient_not_safe(self):
        self.assertEqual(self.day(task(owner_id=None), [])["level"], risk.INSUFFICIENT)

    def test_missing_importance_is_insufficient(self):
        self.assertEqual(self.day(task(importance=None), [leave(1, D(0), D(4))])["level"], risk.INSUFFICIENT)

    def test_done_task_is_low(self):
        self.assertEqual(self.day(task(status="완료"), [leave(1, D(0), D(4))])["level"], risk.LOW)

    def test_half_day_not_absent_by_default(self):
        self.assertEqual(self.day(task(), [leave(1, D(1), D(1), "반차")])["level"], risk.LOW)
        tm = team([task()], [leave(1, D(1), D(1), "반차")])
        r = risk.assess_task_day(tm, tm.task_records()[0], D(1), {"half_day_counts_as_absent": True})
        self.assertNotEqual(r["level"], risk.LOW)

    def test_configurable_thresholds(self):
        tm = team([task(backup_id=2, due_date=D(30))], [leave(1, D(0), D(4))])
        r = risk.assess_task_day(tm, tm.task_records()[0], D(1), {"thresholds": {"high": 45, "medium": 20}})
        self.assertEqual(r["level"], risk.HIGH)

    def test_weekend_excluded_and_empty_range_not_safe(self):
        self.assertEqual(len(risk.workdays(D(0), D(6))), 5)
        tm = team([task()], [])
        df = risk.assess_tasks(tm, D(5), D(6))  # 토~일
        self.assertEqual(df.iloc[0]["위험도"], risk.INSUFFICIENT)

    def test_assess_tasks_sorted_and_has_actions(self):
        tm = team([task(id=1), task(id=2, name="L", owner_id=2, importance="하")], [leave(1, D(0), D(4))])
        df = risk.assess_tasks(tm, D(0), D(4))
        self.assertEqual(df.iloc[0]["업무"], "T")
        self.assertTrue(df.iloc[0]["대응 방안"])

    def test_daily_summary_counts_and_insufficient(self):
        tm = team([task(), task(id=2, owner_id=None)], [leave(1, D(0), D(0)), leave(2, D(0), D(0), id=2)])
        ds = risk.daily_summary(tm, D(0), D(1))
        self.assertEqual(ds.iloc[0]["부재"], 2)
        self.assertEqual(ds.iloc[0]["가용 인원"], 1)
        self.assertEqual(ds.iloc[1]["데이터 부족 업무"], 1)


class CalendarTests(unittest.TestCase):
    def test_overlap_periods_and_matrix(self):
        tm = team(leaves=[leave(1, D(0), D(2)), leave(2, D(1), D(3), id=2)])
        p = cal.overlap_periods(tm, D(0), D(4))
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["시작"], p[0]["종료"], p[0]["최대 동시 부재"]), (D(1), D(2), 2))
        m = cal.absence_matrix(tm, D(0), D(4))
        self.assertEqual(m.loc["A", D(0)], "연차"); self.assertEqual(m.loc["C", D(0)], "")

    def test_overlap_across_weekend_is_one_period(self):
        tm = team(leaves=[leave(1, D(3), D(8)), leave(2, D(3), D(8), id=2)])
        self.assertEqual(len(cal.overlap_periods(tm, D(0), D(11))), 1)

    def test_week_month_range(self):
        self.assertEqual(cal.week_range(D(2)), (D(0), D(6)))
        self.assertEqual(cal.month_range(date(2026, 2, 10)), (date(2026, 2, 1), date(2026, 2, 28)))


class RecommendTests(unittest.TestCase):
    def test_excludes_unskilled_and_absent(self):
        tm = team([task()], [leave(1, D(0), D(4)), leave(2, D(2), D(2), id=2)])
        r = recommend.recommend_backups(tm, 1, D(0), D(4))
        self.assertEqual(r["candidates"], [])
        reasons = {e["이름"]: e["제외 사유"] for e in r["excluded"]}
        self.assertIn("필수 역량", reasons["C"]); self.assertIn("부재", reasons["B"])

    def test_candidate_burden_and_caution(self):
        tm = team([task(load_pct=20)], [leave(1, D(0), D(4))])
        r = recommend.recommend_backups(tm, 1, D(0), D(4))
        c = r["candidates"][0]
        self.assertEqual(c["이름"], "B")
        self.assertIn("50% → 예상 70%", c["예상 업무 부담"])

    def test_overload_caution_and_ranking(self):
        extra = [{"id": 4, "name": "D", "role": "BE", "workload": 95, "contact": ""}]
        tm = team([task()], [leave(1, D(0), D(4))], extra)
        tm.skills.loc[len(tm.skills)] = {"member_id": 4, "skill": "백엔드", "level": 3}
        r = recommend.recommend_backups(tm, 1, D(0), D(4))
        self.assertEqual([c["이름"] for c in r["candidates"]], ["B", "D"])
        self.assertTrue(any("과부하" in x for x in r["candidates"][1]["주의사항"]))

    def test_missing_skill_warns(self):
        tm = team([task(required_skill="")], [leave(1, D(0), D(4))])
        r = recommend.recommend_backups(tm, 1, D(0), D(4))
        self.assertTrue(any("필수 역량" in w for w in r["warnings"]))
        self.assertEqual(len(r["candidates"]), 2)

    def test_unknown_task_raises(self):
        with self.assertRaises(ValueError):
            recommend.recommend_backups(team(), 99, D(0), D(4))


class HandoverTests(unittest.TestCase):
    def test_sections_and_missing_marker(self):
        tm = team([task(notes="", remaining="")], [leave(1, D(0), D(4))])
        md = handover.generate_handover(tm, 1, D(0), D(4), "연차", D(0))
        for sec in ["휴가 정보", "담당 업무", "미완료 작업", "주요 마감일", "대체 담당자",
                    "업무별 주의사항", "긴급 상황", "체크리스트"]:
            self.assertIn(sec, md)
        self.assertIn("미입력", md); self.assertIn("대체 담당자 지정", md); self.assertIn("휴가 기간 중/직후 마감", md)

    def test_no_tasks_does_not_invent(self):
        md = handover.generate_handover(team(), 3, D(0), D(4))
        self.assertIn("등록된 담당 업무가 없습니다", md)


class ValidationTests(unittest.TestCase):
    def test_leave_rules(self):
        tm = team(leaves=[leave(1, D(0), D(4))])
        self.assertTrue(validation.validate_leave(tm, 1, D(4), D(2), "연차"))            # 역전
        self.assertTrue(validation.validate_leave(tm, 1, D(3), D(6), "연차"))            # 본인 중복
        self.assertTrue(validation.validate_leave(tm, 1, D(10), D(11), "반차"))          # 반차 다일
        self.assertTrue(validation.validate_leave(tm, 99, D(10), D(11), "연차"))         # 없는 팀원
        self.assertTrue(validation.validate_leave(tm, 1, D(10), D(11), "기타"))          # 잘못된 유형
        self.assertFalse(validation.validate_leave(tm, 2, D(0), D(4), "연차"))           # 타인과 중복은 허용
        self.assertFalse(validation.validate_leave(tm, 1, D(0), D(4), "연차", exclude_id=1))  # 자기 자신 수정

    def test_rules_validation(self):
        r = merge_rules({"thresholds": {"high": 30, "medium": 50}})
        self.assertTrue(validate_rules(r))
        self.assertFalse(validate_rules(merge_rules(None)))


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tm = team([task(), task(id=2, name="Z", owner_id=3, backup_id=1, importance="하")],
                       [leave(1, D(0), D(4))])

    def ask(self, q, today=MON):
        return assistant.answer_question(q, self.tm, today)

    def test_risky_dates(self):
        a = self.ask("이번 주에 업무 공백 위험이 높은 날짜는?")
        self.assertIn(str(D(1)), a.text)

    def test_key_owner(self):
        a = self.ask("휴가 중인 핵심 담당자가 누구야?")
        self.assertIn("A", a.text)

    def test_no_backup_list(self):
        a = self.ask("대체 담당자가 지정되지 않은 업무를 알려줘.")
        self.assertEqual(list(a.tables[0]["업무"]), ["T"])

    def test_handover(self):
        a = self.ask("이번 주 휴가 인수인계 문서를 만들어줘.")
        self.assertEqual(len(a.documents), 1)

    def test_unsupported_does_not_guess(self):
        a = self.ask("내년 매출 예측해줘")
        self.assertFalse(a.supported)

    def test_empty_inputs(self):
        self.assertFalse(self.ask("   ").supported)
        empty = make_team()
        self.assertFalse(assistant.answer_question("대체 담당자 없는 업무", empty, MON).supported)


class DBTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        os.environ["VACON_TODAY"] = "2026-10-15"
        db.init_db(self.path)

    def tearDown(self):
        os.environ.pop("VACON_TODAY", None)
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_sample_meets_requirements(self):
        t = db.load_team_data(self.path)
        self.assertGreaterEqual(len(t.members), 8); self.assertGreaterEqual(len(t.tasks), 10)
        self.assertTrue(t.tasks["backup_id"].isna().any() or any(v is None for v in t.tasks["backup_id"]))
        self.assertTrue(any(v is not None for v in t.tasks["backup_id"]))

    def test_leave_crud(self):
        lid = db.add_leave(1, D(40), D(41), "연차", "메모", self.path)
        t = db.load_team_data(self.path)
        self.assertIn(lid, list(t.leaves["id"]))
        db.update_leave(lid, 1, D(40), D(42), "병가", "수정", self.path)
        row = db.load_team_data(self.path).leaves.set_index("id").loc[lid]
        self.assertEqual((row["end"], row["type"]), (D(42), "병가"))
        db.delete_leave(lid, self.path)
        self.assertNotIn(lid, list(db.load_team_data(self.path).leaves["id"]))

    def test_member_delete_cascades(self):
        db.delete_member(2, self.path)
        t = db.load_team_data(self.path)
        self.assertNotIn(2, list(t.leaves["member_id"]))
        self.assertTrue(all(o != 2 for o in t.tasks["owner_id"]))

    def test_rules_roundtrip_and_reset(self):
        r = db.load_rules(self.path); r["thresholds"]["high"] = 90
        db.save_rules(r, self.path)
        self.assertEqual(db.load_rules(self.path)["thresholds"]["high"], 90)
        db.reset_sample(self.path)
        self.assertEqual(db.load_rules(self.path)["thresholds"]["high"], 75)

    def test_task_crud_and_backup(self):
        tid = db.add_task({"name": "신규", "importance": "중", "status": "대기"}, self.path)
        db.set_backup(tid, 2, self.path)
        row = db.load_team_data(self.path).tasks.set_index("id").loc[tid]
        self.assertEqual(row["backup_id"], 2)


if __name__ == "__main__":
    unittest.main()
