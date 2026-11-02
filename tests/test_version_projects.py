import os
import shutil
import unittest
from datetime import date

from data import db
from services.models import make_team
from services.version_projects import aggregate_version_logs, project_summary


class VersionProjectTests(unittest.TestCase):
    def test_log_activity_is_aggregated_per_normalized_version(self):
        result = aggregate_version_logs([
            {"version": "T0070", "track": "dynamic", "tc": 12, "req": 3},
            {"version": " t0070 ", "track": "dynamic", "tc": 8, "req": 2, "func": 6},
            {"version": "T0070", "track": "req", "issue": 4},
            {"version": "T0080", "track": "dynamic", "tc": 90, "req": 10},
            {"version": "", "track": "req", "issue": 99},
        ])
        self.assertEqual(result["t0070"], {"version": "T0070", "records": 3, "req_issue_logs": 4.0,
                                           "spec_done_req": 4.0, "dynamic_done_req": 5.0,
                                           "verify_done_func": 6.0, "verify_tc": 20.0})
        self.assertEqual(result["t0080"]["verify_tc"], 90)

    def setUp(self):
        self.test_dir = os.path.join(os.path.dirname(__file__), "_version_project_test_data")
        os.makedirs(self.test_dir, exist_ok=True)
        self.path = os.path.join(self.test_dir, "vacon.db")
        db.init_db(self.path, seed=False)
        self.owner = db.add_member("테스터", "QA", 50, path=self.path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def create(self, version="T0070", **extra):
        fields = {"version": version, "status": "준비", "spec_start": date(2026, 10, 26),
                  "spec_due": date(2026, 11, 6), "verify_start": date(2026, 11, 2),
                  "verify_due": date(2026, 11, 13), "total_items": 40,
                  "spec_owner_id": self.owner, "verify_owner_id": self.owner, **extra}
        return db.save_version_project(fields, path=self.path)

    def test_duplicate_versions_are_rejected_case_insensitively(self):
        self.create()
        with self.assertRaises(Exception):
            self.create("t0070")

    def test_version_projects_are_listed_newest_numeric_version_first(self):
        self.create("T0070")
        self.create("T0800")
        self.create("T0090")
        self.assertEqual([p["version"] for p in db.list_version_projects(self.path)],
                         ["T0800", "T0090", "T0070"])

    def test_phase_registration_merges_without_overwriting_other_phase(self):
        pid = db.save_version_phase({"version": "T0090", "spec_start": date(2026, 10, 26),
                                     "spec_due": date(2026, 11, 6), "spec_total_items": 40}, "spec", self.path)
        same_id = db.save_version_phase({"version": "t0090", "verify_start": date(2026, 11, 2),
                                         "verify_due": date(2026, 11, 13), "verify_spec_total_items": 17,
                                         "verify_total_items": 25}, "verify", self.path)
        row = db.get_version_project(pid, self.path)
        self.assertEqual(pid, same_id)
        self.assertEqual((row["spec_total_items"], row["verify_total_items"]), (40, 25))
        self.assertEqual((row["spec_total_items"], row["verify_spec_total_items"]), (40, 17))
        self.assertEqual((row["spec_due"], row["verify_due"]), (date(2026, 11, 6), date(2026, 11, 13)))

    def test_verification_only_registration_does_not_register_spec_phase(self):
        pid = db.save_version_phase({"version": "T0095", "verify_start": date(2026, 10, 26),
                                     "verify_due": date(2026, 11, 6), "verify_spec_total_items": 40,
                                     "verify_total_items": 20}, "verify", self.path)
        row = db.get_version_project(pid, self.path)
        summary = project_summary(db.load_team_data(self.path), row, date(2026, 10, 26),
                                  {"spec": 12, "verify": 7})
        self.assertFalse(summary["spec"]["registered"])
        self.assertEqual(summary["spec"]["risk"], "미등록")
        self.assertTrue(summary["verify"]["registered"])
        self.assertEqual(summary["spec"]["total"], 0)
        self.assertEqual(row["verify_spec_total_items"], 40)
        self.assertEqual(summary["spec"]["done"], 0)
        self.assertEqual(summary["spec"]["progress"], 0.0)

    def test_invalid_schedule_and_completion_counts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "시작일"):
            self.create(spec_start=date(2026, 11, 7), spec_due=date(2026, 11, 6))
        with self.assertRaisesRegex(ValueError, "완료 수"):
            self.create(total_items=5, spec_done=6)

    def test_phases_are_independent_and_leave_recalculates_capacity(self):
        pid = self.create(spec_done=10, verify_done=20)
        db.add_leave(self.owner, date(2026, 10, 27), date(2026, 10, 28), "연차", path=self.path)
        team = db.load_team_data(self.path)
        summary = project_summary(team, db.get_version_project(pid, self.path), date(2026, 10, 26))
        self.assertEqual(summary["spec"]["remaining"], 30)
        self.assertEqual(summary["verify"]["remaining"], 20)
        self.assertEqual(summary["spec"]["available_days"], 8)
        self.assertEqual(summary["verify"]["available_days"], 10)
        self.assertEqual(summary["spec"]["daily"], 4)
        self.assertEqual(summary["verify"]["daily"], 2)

    def test_zero_remaining_and_zero_capacity_are_safe(self):
        pid = self.create(spec_done=40, verify_done=0, verify_start=date(2026, 10, 1),
                          verify_due=date(2026, 10, 25))
        summary = project_summary(db.load_team_data(self.path), db.get_version_project(pid, self.path), date(2026, 10, 26))
        self.assertEqual(summary["spec"]["daily"], 0)
        self.assertIsNone(summary["verify"]["daily"])
        self.assertEqual(summary["verify"]["risk"], "지연 위험")

    def test_spec_and_verification_totals_are_independent(self):
        pid = self.create(total_items=0, spec_total_items=8, verify_total_items=20,
                          spec_done=3, verify_done=7)
        result = project_summary(db.load_team_data(self.path), db.get_version_project(pid, self.path), date(2026, 10, 26))
        self.assertEqual((result["spec"]["total"], result["spec"]["remaining"]), (8, 5))
        self.assertEqual((result["verify"]["total"], result["verify"]["remaining"]), (20, 13))

    def test_legacy_verification_capacity_is_ignored(self):
        pid = self.create(total_items=20, verify_total_items=20, verify_done=0,
                          daily_verify_capacity=1)
        result = project_summary(db.load_team_data(self.path), db.get_version_project(pid, self.path), date(2026, 10, 26))
        self.assertEqual(result["verify"]["shortfall"], 0)
        self.assertEqual(result["verify"]["risk"], "정상")

    def test_edit_preserves_history_and_versions_remain_separate(self):
        first = self.create()
        second = self.create("T0080", spec_due=date(2026, 11, 20))
        changed = db.get_version_project(first, self.path)
        changed["spec_due"] = date(2026, 11, 9)
        db.save_version_project(changed, path=self.path, project_id=first)
        self.assertEqual(db.get_version_project(second, self.path)["spec_due"], date(2026, 11, 20))
        history = db.version_project_history(first, self.path)
        self.assertTrue(any(row["field"] == "spec_due" for row in history))

    def test_company_holiday_setting_does_not_change_capacity(self):
        pid = self.create(spec_due=date(2026, 10, 30), total_items=5,
                          holidays=["2026-10-27"], spec_owner_id=None, verify_owner_id=None)
        result = project_summary(make_team(), db.get_version_project(pid, self.path), date(2026, 10, 26))
        self.assertEqual(result["spec"]["available_days"], 5)
        self.assertEqual(result["spec"]["daily"], 1)

    def test_multiple_assignees_have_individual_leave_capacity(self):
        second = db.add_member("두번째", "QA", 50, path=self.path)
        pid = self.create(spec_done=0, spec_owner_ids=[self.owner, second])
        db.add_leave(self.owner, date(2026, 10, 27), date(2026, 10, 27), "연차", path=self.path)
        team = db.load_team_data(self.path)
        result = project_summary(team, db.get_version_project(pid, self.path), date(2026, 10, 26))["spec"]
        self.assertEqual(result["person_days"], 19)
        self.assertEqual(result["available_days"], 10)
        self.assertEqual(result["daily"], 4)
        self.assertEqual(result["risk"], "주의")


if __name__ == "__main__":
    unittest.main()
