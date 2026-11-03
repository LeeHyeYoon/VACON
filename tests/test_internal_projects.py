import os
import shutil
import sqlite3
import unittest
from datetime import date, timedelta

from data import db
from services.internal_projects import dashboard, is_internal_version, is_legacy_sheet_version, recommend_distribution, recommend_subsystem


class InternalProjectTests(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.path.dirname(__file__), "_internal_project_test_data")
        os.makedirs(self.root, exist_ok=True)
        self.path = os.path.join(self.root, "vacon.db")
        db.init_db(self.path, seed=False)
        self.member = db.add_member("김재강", "QA", 20, path=self.path)
        db.set_skill(self.member, "단위테스트", 2, path=self.path)
        db.set_skill(self.member, "요구사양서 작성", 2, path=self.path)
        self.project_id = self.create("T0800")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def create(self, version):
        return db.save_version_project({"version": version, "status": "준비",
            "spec_due": date(2026, 12, 18), "verify_due": date(2026, 12, 22),
            "spec_registered": True, "verify_registered": True,
            "spec_total_items": 0, "verify_spec_total_items": 0, "verify_total_items": 0}, path=self.path)

    def test_version_boundary_keeps_legacy_sheets_and_internal_db_separate(self):
        self.assertFalse(is_internal_version("T0070"))
        self.assertTrue(is_legacy_sheet_version("T0070"))
        self.assertFalse(is_internal_version("T0080"))
        self.assertTrue(is_legacy_sheet_version("T0080"))
        self.assertFalse(is_internal_version("T0700"))
        self.assertTrue(is_legacy_sheet_version("T0700"))
        self.assertFalse(is_internal_version("T0600"))
        self.assertTrue(is_legacy_sheet_version("T0600"))
        self.assertTrue(is_internal_version("T0800"))
        self.assertFalse(is_legacy_sheet_version("T0800"))
        self.assertTrue(is_internal_version("T0900"))
        self.assertTrue(is_internal_version("CUSTOM-2026"))

    def test_migration_removes_old_shared_value_from_dynamic_field_once_and_audits_it(self):
        path = os.path.join(self.root, "shared-count.db")
        old_schema = db.SCHEMA.replace("    verify_spec_total_items INTEGER NOT NULL DEFAULT 0,\n", "")
        self.assertNotEqual(old_schema, db.SCHEMA)
        conn = sqlite3.connect(path)
        conn.executescript(old_schema)
        conn.execute("INSERT INTO version_projects(version,status,spec_total_items,verify_total_items) VALUES (?,?,?,?)",
                     ("T0800", "준비", 1348, 115))
        conn.execute("ALTER TABLE version_projects ADD COLUMN verify_spec_total_items INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE version_projects SET verify_spec_total_items=spec_total_items")
        conn.commit()
        conn.close()
        db.init_db(path, seed=False)
        project = db.list_version_projects(path)[0]
        self.assertEqual((project["spec_total_items"], project["verify_spec_total_items"]), (1348, 0))
        history = db.version_project_history(project["id"], path)
        self.assertTrue(any(h["field"] == "verify_spec_total_items" and h["old_value"] == "1348" for h in history))
        db.save_version_phase({"version": "T0800", "verify_spec_total_items": 275}, "verify", path)
        db.init_db(path, seed=False)
        project = db.get_version_project(project["id"], path)
        self.assertEqual((project["spec_total_items"], project["verify_spec_total_items"]), (1348, 275))

    def test_phase_workloads_and_global_spec_counts_are_independent(self):
        db.save_version_phase({"version": "T0800", "spec_total_items": 25}, "spec", self.path)
        db.save_version_phase({"version": "T0800", "verify_spec_total_items": 11,
                               "verify_total_items": 40}, "verify", self.path)
        project = db.get_version_project(self.project_id, self.path)
        self.assertEqual((project["spec_total_items"], project["verify_spec_total_items"],
                          project["verify_total_items"]), (25, 11, 40))
        db.save_version_workload(self.project_id, "spec", "Audio", total_tasks=7, difficulty=3, path=self.path)
        db.save_version_workload(self.project_id, "spec", "Bluetooth", total_tasks=4, difficulty=2, path=self.path)
        db.save_version_workload(self.project_id, "verify", "Audio", total_functions=20,
                                 total_requirements=12, difficulty=4, path=self.path)
        spec_workloads = db.list_version_workloads(self.project_id, "spec", self.path)
        self.assertEqual(sum(w["total_tasks"] for w in spec_workloads), 11)
        self.assertEqual(len(spec_workloads), 2)
        verify = db.list_version_workloads(self.project_id, "verify", self.path)[0]
        self.assertEqual((verify["total_functions"], verify["total_requirements"]), (20, 12))
        second_id = self.create("T0090")
        self.assertEqual(db.list_version_workloads(second_id, "spec", self.path), [])

    def test_spec_and_dynamic_records_are_isolated_and_edit_delete_refresh_aggregates(self):
        db.save_version_workload(self.project_id, "spec", "Audio", total_tasks=10, path=self.path)
        db.save_version_workload(self.project_id, "verify", "Audio", total_functions=20,
                                 total_requirements=15, path=self.path)
        spec_id = db.save_version_worklog(self.project_id, "spec", {"date": date(2026, 11, 2),
            "member_id": self.member, "subsystem": "Audio", "completed_tasks": 3,
            "new_requirements": 5, "modified_requirements": 2}, path=self.path)
        verify_id = db.save_version_worklog(self.project_id, "verify", {"date": date(2026, 11, 2),
            "member_id": self.member, "subsystem": "Audio", "completed_functions": 4,
            "completed_requirements": 6, "defects": 1, "tc_executed": 12}, path=self.path)
        spec = dashboard(db.get_version_project(self.project_id, self.path), "spec",
                          db.list_version_workloads(self.project_id, "spec", self.path),
                          db.list_version_worklogs(self.project_id, "spec", self.path), date(2026, 11, 2))
        verify = dashboard(db.get_version_project(self.project_id, self.path), "verify",
                            db.list_version_workloads(self.project_id, "verify", self.path),
                            db.list_version_worklogs(self.project_id, "verify", self.path), date(2026, 11, 2))
        self.assertEqual((spec["totals"]["completed_tasks"], spec["totals"]["progress"]), (3, 0.3))
        self.assertEqual((verify["totals"]["completed_functions"], verify["totals"]["completed_requirements"]), (4, 6))
        db.save_version_worklog(self.project_id, "spec", {"date": date(2026, 11, 3),
            "member_id": self.member, "subsystem": "Audio", "completed_tasks": 8,
            "new_requirements": 5}, log_id=spec_id, path=self.path)
        db.delete_version_worklog(self.project_id, "verify", verify_id, path=self.path)
        self.assertEqual(db.list_version_worklogs(self.project_id, "spec", self.path)[0]["completed_tasks"], 8)
        self.assertEqual(db.list_version_worklogs(self.project_id, "verify", self.path), [])

    def test_worklog_keeps_work_date_and_created_updated_timestamps_separate(self):
        db.save_version_workload(self.project_id, "spec", "Audio", total_tasks=10, path=self.path)
        work_id = db.save_version_worklog(self.project_id, "spec", {"date": date(2026, 10, 15),
            "member_id": self.member, "subsystem": "Audio", "completed_tasks": 2}, path=self.path)
        original = db.list_version_worklogs(self.project_id, "spec", self.path)[0]
        self.assertEqual(original["date"], date(2026, 10, 15))
        self.assertTrue(original["created_at"])
        self.assertTrue(original["updated_at"])
        db.save_version_worklog(self.project_id, "spec", {"date": date(2026, 10, 12),
            "member_id": self.member, "subsystem": "Audio", "completed_tasks": 3}, log_id=work_id, path=self.path)
        edited = db.list_version_worklogs(self.project_id, "spec", self.path)[0]
        self.assertEqual(edited["date"], date(2026, 10, 12))
        self.assertEqual(edited["created_at"], original["created_at"])
        self.assertNotEqual(edited["updated_at"], edited["created_at"])

    def test_google_sheet_import_skips_internal_version_without_deleting_legacy_logs(self):
        db.replace_logs("old-source", [{"track": "dynamic", "date": date(2026, 11, 2),
            "member_id": self.member, "version": "T0070", "func": 2}], self.path)
        result = db.replace_logs("old-source", [{"track": "dynamic", "date": date(2026, 11, 3),
            "member_id": self.member, "version": "T0800", "func": 4}], self.path)
        self.assertEqual(result, {"removed": 0, "added": 0, "ignored_internal": 1})
        with db.connect(self.path) as conn:
            record = conn.execute("SELECT version,func_done FROM work_logs WHERE source='old-source'").fetchone()
        self.assertEqual((record["version"], record["func_done"]), ("T0070", 2))

    def test_mixed_sheet_import_preserves_legacy_and_excludes_internal_rows(self):
        entries = [{"track": "dynamic", "date": date(2026, 11, 2), "member_id": self.member,
                    "version": "T0700", "func": 3},
                   {"track": "dynamic", "date": date(2026, 11, 3), "member_id": self.member,
                    "version": "T0800", "func": 9}]
        result = db.replace_logs("mixed", entries, self.path)
        self.assertEqual(result, {"removed": 0, "added": 1, "ignored_internal": 1})
        saved = db.load_team_data(self.path).logs
        self.assertEqual(saved[saved["source"] == "mixed"]["version"].tolist(), ["T0700"])

    def test_google_sheet_import_accepts_four_digit_legacy_versions(self):
        result = db.replace_logs("legacy-0700", [{"track": "dynamic", "date": date(2026, 11, 3),
            "member_id": self.member, "version": "T0700", "func": 5}], self.path)
        self.assertEqual(result["added"], 1)
        record = db.load_team_data(self.path).logs.iloc[-1]
        self.assertEqual((record["version"], record["func"]), ("T0700", 5))

    def test_recommendation_uses_workload_leaves_and_approved_assignments(self):
        db.save_version_workload(self.project_id, "verify", "Audio", total_functions=18,
                                 total_requirements=9, difficulty=3, path=self.path)
        db.add_leave(self.member, date(2026, 11, 3), date(2026, 11, 3), "연차", path=self.path)
        team = db.load_team_data(self.path)
        result = recommend_distribution(team, db.get_version_project(self.project_id, self.path), "verify",
            db.list_version_workloads(self.project_id, "verify", self.path), date(2026, 11, 2), self.path)
        self.assertEqual(sum(row["functions"] for row in result["rows"]), 18)
        self.assertEqual(sum(row["requirements"] for row in result["rows"]), 9)
        self.assertEqual(result["rows"][0]["available_days"],
                         len([d for d in (date(2026, 11, 2) + timedelta(days=i) for i in range(51))
                              if d.weekday() < 5 and d <= date(2026, 12, 22)]) - 1)
        self.assertFalse(result["history_available"])
        self.assertEqual(db.replace_version_assignments(self.project_id, "verify", result["rows"], self.path), 1)
        approved = db.list_version_assignments(self.project_id, "verify", self.path)
        self.assertEqual((approved[0]["member_id"], approved[0]["allocated_functions"],
                          approved[0]["allocated_requirements"]), (self.member, 18, 9))

        second = db.add_member("황지원", "QA", 20, path=self.path)
        db.set_skill(second, "단위테스트", 2, path=self.path)
        prior_project_id = self.create("T0900")
        db.replace_version_assignments(prior_project_id, "verify", [{"subsystem": "Other", "member_id": self.member,
            "functions": 10, "effort_score": 500}], self.path)
        balanced = recommend_distribution(db.load_team_data(self.path), db.get_version_project(self.project_id, self.path),
            "verify", db.list_version_workloads(self.project_id, "verify", self.path), date(2026, 11, 2), self.path)
        counts = {mid: 0 for mid in (self.member, second)}
        for row in balanced["rows"]:
            counts[row["member_id"]] += row["functions"] + row["requirements"]
        self.assertLess(counts[self.member], counts[second])

    def test_legacy_sheet_data_is_read_only_productivity_history_for_internal_recommendation(self):
        db.save_version_workload(self.project_id, "spec", "Audio", total_tasks=4, difficulty=1, path=self.path)
        db.replace_logs("legacy-req", [{"track": "req", "date": date(2026, 10, 30),
            "member_id": self.member, "version": "T0070", "issue": 4}], self.path)
        team = db.load_team_data(self.path)
        result = recommend_distribution(team, db.get_version_project(self.project_id, self.path), "spec",
            db.list_version_workloads(self.project_id, "spec", self.path), date(2026, 11, 2), self.path)
        self.assertTrue(result["history_available"])
        self.assertEqual(sum(row["tasks"] for row in result["rows"]), 4)
        self.assertEqual(db.list_version_worklogs(self.project_id, "spec", self.path), [])

    def test_live_subsystem_recommendation_uses_selected_phase_plan_and_progress_bottleneck(self):
        db.save_version_workload(self.project_id, "spec", "Audio", total_tasks=10, path=self.path)
        db.save_version_workload(self.project_id, "spec", "Bluetooth", total_tasks=10, path=self.path)
        db.save_version_workload(self.project_id, "verify", "Audio", total_functions=20,
                                 total_requirements=10, path=self.path)
        db.save_version_workload(self.project_id, "verify", "Bluetooth", total_functions=20,
                                 total_requirements=10, path=self.path)
        db.save_version_worklog(self.project_id, "spec", {"date": date(2026, 10, 30),
            "member_id": self.member, "subsystem": "Audio", "completed_tasks": 8}, path=self.path)
        db.replace_logs("legacy-skill", [{"track": "req", "date": date(2026, 10, 1), "member_id": self.member,
            "version": "T0070", "subsystem": "Audio", "issue": 2}], self.path)
        helper = db.add_member("주해원", "QA", 25, path=self.path)
        db.set_skill(helper, "요구사양서 작성", 2, path=self.path)
        db.replace_logs("legacy-helper", [{"track": "req", "date": date(2026, 9, 1), "member_id": helper,
            "version": "T0050", "subsystem": "Bluetooth", "issue": 3}], self.path)
        project = db.get_version_project(self.project_id, self.path)
        team = db.load_team_data(self.path)
        spec = recommend_subsystem(project, team, "spec", self.member, date(2026, 11, 3), self.path)
        verify = recommend_subsystem(project, team, "verify", self.member, date(2026, 11, 3), self.path)
        self.assertEqual(spec["subsystem"], "Bluetooth")
        self.assertEqual(spec["basis"][0]["support_people"][0]["member_id"], helper)
        # The recommendation remains phase-isolated and may prioritize the
        # remaining verification bottleneck.
        self.assertEqual(verify["subsystem"], "Bluetooth")
        self.assertIn("과거", spec["reason"])
        db.add_leave(helper, date(2026, 11, 3), date(2026, 11, 3), "연차", path=self.path)
        spec_on_helper_leave = recommend_subsystem(project, db.load_team_data(self.path), "spec", self.member,
            date(2026, 11, 3), self.path)
        self.assertNotIn(helper, [candidate["member_id"] for candidate in spec_on_helper_leave["basis"][0]["support_people"]])
        db.add_leave(self.member, date(2026, 11, 4), date(2026, 11, 4), "연차", path=self.path)
        absent = recommend_subsystem(project, db.load_team_data(self.path), "spec", self.member, date(2026, 11, 4), self.path)
        self.assertIsNone(absent["subsystem"])
        db.update_member(self.member, "김재강", "QA", 95, path=self.path)
        overloaded = recommend_subsystem(project, db.load_team_data(self.path), "spec", self.member, date(2026, 11, 3), self.path)
        self.assertTrue(overloaded["overloaded"])
        self.assertIn("추가 업무를 권하지 않습니다", overloaded["reason"])

    def test_live_recommendation_marks_rotation_after_consecutive_subsystem_versions(self):
        for version in ("T0500", "T0600", "T0700"):
            db.replace_logs(f"rotation-{version}", [{"track": "req", "date": date(2026, 9, 1),
                "member_id": self.member, "version": version, "subsystem": "HCU", "issue": 2}], self.path)
        db.save_version_workload(self.project_id, "spec", "HCU", total_tasks=10, path=self.path)
        result = recommend_subsystem(db.get_version_project(self.project_id, self.path), db.load_team_data(self.path),
            "spec", self.member, date(2026, 11, 3), self.path)
        item = result["basis"][0]
        self.assertEqual(item["rotation"]["recent_versions"], ["T0500", "T0600", "T0700"])
        self.assertEqual(item["rotation"]["consecutive_versions"], 3)
        self.assertTrue(item["rotation_recommended"])
        self.assertIn("업무 순환", item["reason"])

    def test_live_recommendation_prioritizes_separate_phase_owner_and_reports_pm_exception(self):
        helper = db.add_member("주해원", "QA", 20, path=self.path)
        db.set_skill(helper, "요구사양서 작성", 2, path=self.path)
        db.save_version_workload(self.project_id, "spec", "HCU", total_tasks=10, path=self.path)
        db.save_version_workload(self.project_id, "verify", "HCU", total_functions=10,
                                 total_requirements=5, path=self.path)
        db.save_version_worklog(self.project_id, "verify", {"date": date(2026, 11, 2),
            "member_id": self.member, "subsystem": "HCU", "completed_functions": 1}, path=self.path)
        result = recommend_subsystem(db.get_version_project(self.project_id, self.path), db.load_team_data(self.path),
            "spec", self.member, date(2026, 11, 3), self.path)
        item = result["basis"][0]
        self.assertTrue(item["separation_needed"])
        self.assertFalse(item["separation_exception"])
        self.assertEqual(item["counterpart_names"], ["김재강"])
        self.assertEqual(item["support_people"][0]["member_id"], helper)
        self.assertIn("담당자 분리", item["reason"])

        db.add_leave(helper, date(2026, 11, 3), date(2026, 11, 3), "연차", path=self.path)
        no_alternative = recommend_subsystem(db.get_version_project(self.project_id, self.path),
            db.load_team_data(self.path), "spec", self.member, date(2026, 11, 3), self.path)
        self.assertTrue(no_alternative["separation_exception"])
        self.assertIn("PM", no_alternative["reason"])

    def test_unit_verification_recommendations_are_limited_to_named_four_person_roster(self):
        outsider = db.add_member("정고성", "팀장", 5, path=self.path)
        db.set_skill(outsider, "단위테스트", 3, path=self.path)
        db.set_skill(outsider, "요구사양서 작성", 3, path=self.path)
        team = db.load_team_data(self.path)
        self.assertEqual({row["name"] for row in team.unit_members()}, {"김재강"})

        db.save_version_workload(self.project_id, "spec", "HCU", total_tasks=8, path=self.path)
        project = db.get_version_project(self.project_id, self.path)
        plan = recommend_distribution(team, project, "spec",
            db.list_version_workloads(self.project_id, "spec", self.path), date(2026, 11, 3), self.path)
        self.assertEqual({row["member_name"] for row in plan["rows"]}, {"김재강"})
        self.assertNotIn(outsider, [row["member_id"] for row in plan["rows"]])
        recommendation = recommend_subsystem(project, team, "spec", outsider, date(2026, 11, 3), self.path)
        self.assertIsNone(recommendation["subsystem"])
        self.assertIn("김재강", recommendation["reason"])


if __name__ == "__main__":
    unittest.main()
