import unittest
from datetime import date, timedelta

from services.models import make_team
from services.worklog import recommend_daily_count

D0 = date(2026, 10, 1)
MEMBERS = [{"id": 1, "name": "김재강", "role": "단위", "workload": 50},
           {"id": 2, "name": "이혜윤", "role": "단위", "workload": 50},
           {"id": 3, "name": "황지원", "role": "단위", "workload": 50}]
SKILLS = [{"member_id": i, "skill": "단위테스트", "level": 2} for i in (1, 2, 3)]


def log(i, member, day, track, subsystem="BMS", source="Google:sheet", **metrics):
    return {"id": i, "source": source, "track": track, "date": D0 + timedelta(days=day), "member_id": member,
            "version": "T0500", "subsystem": subsystem, **metrics}


class CountRecommendationTests(unittest.TestCase):
    def team(self, logs):
        return make_team(members=MEMBERS, skills=SKILLS, logs=logs)

    def test_spec_uses_completed_issues_of_same_member_and_subsystem(self):
        logs = [log(1, 1, 0, "req", issue=2), log(2, 1, 1, "req", issue=4), log(3, 1, 2, "req", issue=6),
                log(4, 1, 2, "req", issue=1),  # 같은 날 두 행은 합산 → 7
                log(5, 1, 3, "req", subsystem="ADAS", issue=50)]
        rec = recommend_daily_count(self.team(logs), 1, "spec", "bms", date(2026, 11, 1))
        self.assertEqual(rec["value"], 4)  # 일별 합계 [2, 4, 7]의 중앙값
        self.assertEqual(rec["label"], "완료 일감")
        self.assertIn("BMS", rec["scope"].upper())

    def test_verify_uses_completed_requirements(self):
        logs = [log(i, 2, i, "dynamic", req=v, tc=100) for i, v in enumerate([3, 5, 8, 10], start=1)]
        rec = recommend_daily_count(self.team(logs), 2, "verify", "BMS", date(2026, 11, 1))
        self.assertEqual(rec["value"], 6)  # (5+8)/2 = 6.5 → 반올림 6
        self.assertEqual(rec["label"], "완료 요구사양서")
        self.assertEqual((rec["low"], rec["high"]), (3, 10))

    def test_falls_back_to_member_then_team(self):
        logs = [log(i, 1, i, "req", subsystem="ADAS", issue=3) for i in range(1, 4)]
        rec = recommend_daily_count(self.team(logs), 1, "spec", "BMS", date(2026, 11, 1))
        self.assertEqual((rec["value"], rec["scope"]), (3, "김재강님의 전체 기록"))
        logs += [log(10 + i, 2, i, "req", issue=9) for i in range(1, 4)]
        rec = recommend_daily_count(self.team(logs), 3, "spec", "BMS", date(2026, 11, 1))
        self.assertEqual(rec["scope"], "단위 검증 인원 전체 기록")
        self.assertEqual(rec["days"], 6)

    def test_excludes_samples_future_and_zero_days(self):
        logs = [log(i, 1, i, "req", source="샘플", issue=5) for i in range(1, 6)]
        logs += [log(10, 1, 40, "req", issue=5), log(11, 1, 41, "req", issue=5), log(12, 1, 42, "req", issue=5)]
        logs += [log(20 + i, 1, i, "req", issue=0) for i in range(1, 6)]
        self.assertIsNone(recommend_daily_count(self.team(logs), 1, "spec", None, date(2026, 11, 1)))

    def test_uses_recent_30_active_days(self):
        logs = [log(i, 1, i, "dynamic", req=1) for i in range(0, 30)]
        logs += [log(100 + i, 1, 30 + i, "dynamic", req=10) for i in range(0, 20)]
        rec = recommend_daily_count(self.team(logs), 1, "verify", None, date(2026, 12, 31))
        self.assertEqual(rec["days"], 30)
        self.assertEqual(rec["value"], 10)

    def test_invalid_phase(self):
        with self.assertRaises(ValueError):
            recommend_daily_count(self.team([]), 1, "other")


if __name__ == "__main__":
    unittest.main()
