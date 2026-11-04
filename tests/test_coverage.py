import os
import shutil
import unittest
from datetime import date

from data import db
from services.coverage import recommend_version_leave_coverage, scheduled_leave_targets, workflow_stage


class VersionCoverageTests(unittest.TestCase):
    roster = ("김재강", "주해원", "황지원", "이혜윤")

    def setUp(self):
        self.root = os.path.join(os.path.dirname(__file__), "_coverage_test_data")
        os.makedirs(self.root, exist_ok=True)
        self.path = os.path.join(self.root, "vacon.db")
        db.init_db(self.path, seed=False)
        self.members = {name: db.add_member(name, "QA", 20, path=self.path) for name in self.roster}
        self.project_id = db.save_version_project({"version": "T0800", "status": "준비",
            "spec_due": date(2026, 11, 13), "verify_due": date(2026, 12, 22),
            "spec_registered": True, "verify_registered": True,
            "spec_total_items": 1, "verify_spec_total_items": 40, "verify_total_items": 40}, path=self.path)
        db.save_version_workload(self.project_id, "spec", "HCU", total_tasks=1, path=self.path)
        db.save_version_workload(self.project_id, "verify", "HCU", total_functions=40,
                                 total_requirements=40, path=self.path)
        db.save_version_worklog(self.project_id, "spec", {"date": date(2026, 11, 2),
            "member_id": self.members["김재강"], "subsystem": "HCU", "completed_tasks": 1}, path=self.path)
        db.set_version_workflow_status(self.project_id, "spec_review_status", "완료", self.path)
        db.set_version_workflow_status(self.project_id, "verification_targets_confirmed", True, self.path)
        db.replace_version_assignments(self.project_id, "verify", [
            {"subsystem": "HCU", "member_id": self.members[name], "functions": 10, "requirements": 10}
            for name in self.roster], self.path)
        for name, member_id in self.members.items():
            db.replace_logs(f"history-{name}", [
                {"track": "dynamic", "date": day, "member_id": member_id, "version": version,
                 "subsystem": "HCU", "func": 5, "req": 5}
                for version, day in (("T0500", date(2026, 10, 20)), ("T0600", date(2026, 10, 27)),
                                     ("T0700", date(2026, 11, 2)))], self.path)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_each_unit_member_can_be_absent_and_remaining_three_get_workload(self):
        for absent_name in self.roster:
            with self.subTest(absent=absent_name):
                member_id = self.members[absent_name]
                leave_id = db.add_leave(member_id, date(2026, 11, 10), date(2026, 11, 12), "연차", path=self.path)
                project = db.get_version_project(self.project_id, self.path)
                result = recommend_version_leave_coverage(db.load_team_data(self.path), project, member_id,
                    date(2026, 11, 10), date(2026, 11, 12), date(2026, 11, 4), self.path)
                self.assertEqual(result["phase"], "verify")
                self.assertEqual(result["total_demand"], 20)
                self.assertEqual(result["total_allocated"], 20)
                self.assertEqual(len(result["assignments"]), 3)
                self.assertNotIn(member_id, [row["member_id"] for row in result["assignments"]])
                self.assertTrue(all(row["functions"] + row["requirements"] > 0 for row in result["assignments"]))
                db.delete_leave(leave_id, self.path)

    def test_verification_cannot_be_assigned_before_pm_review_and_scope_confirmation(self):
        # A fresh version has a plan but has not completed the sequential workflow.
        project_id = db.save_version_project({"version": "T0810", "status": "준비",
            "spec_due": date(2026, 12, 18), "verify_due": date(2026, 12, 22),
            "spec_registered": True, "verify_registered": True}, path=self.path)
        db.save_version_workload(project_id, "spec", "HCU", total_tasks=2, path=self.path)
        db.save_version_workload(project_id, "verify", "HCU", total_functions=2, total_requirements=2, path=self.path)
        with self.assertRaises(ValueError):
            db.replace_version_assignments(project_id, "verify", [{"subsystem": "HCU",
                "member_id": self.members["김재강"], "functions": 2, "requirements": 2}], self.path)
        self.assertEqual(workflow_stage(db.get_version_project(project_id, self.path), self.path)["stage"], "spec_work")

    def test_targets_are_selected_by_real_schedule_and_split_at_version_transition(self):
        member_id = self.members["김재강"]
        leave_id = db.add_leave(member_id, date(2026, 11, 10), date(2026, 11, 20), "연차", path=self.path)
        next_id = db.save_version_project({"version": "T0810", "status": "준비", "spec_start": date(2026, 11, 15),
            "spec_due": date(2026, 12, 10), "spec_registered": True, "spec_owner_ids": [member_id],
            "spec_total_items": 2}, path=self.path)
        db.save_version_workload(next_id, "spec", "HCU", total_tasks=2, path=self.path)
        targets = scheduled_leave_targets(db.load_team_data(self.path), date(2026, 11, 4), self.path)
        own = [row for row in targets if row["leave_id"] == leave_id]
        self.assertEqual([(row["version"], row["phase"], row["overlap_start"], row["overlap_end"])
                          for row in own], [
            ("T0800", "verify", date(2026, 11, 10), date(2026, 11, 14)),
            ("T0810", "spec", date(2026, 11, 15), date(2026, 11, 20)),
        ])

    def test_dynamic_target_starts_after_spec_completion_without_manual_review_gate(self):
        member_id = self.members["김재강"]
        prior = db.get_version_project(self.project_id, self.path)
        prior["verify_due"] = date(2026, 11, 9)
        db.save_version_project(prior, self.path, self.project_id)
        project_id = db.save_version_project({"version": "T0820", "status": "준비",
            "spec_start": date(2026, 11, 1), "spec_due": date(2026, 11, 5),
            "verify_start": date(2026, 11, 6), "verify_due": date(2026, 12, 10),
            "spec_registered": True, "verify_registered": True, "spec_owner_ids": [member_id],
            "verify_owner_ids": [member_id], "spec_total_items": 1,
            "verify_spec_total_items": 2, "verify_total_items": 2}, path=self.path)
        db.save_version_workload(project_id, "spec", "HCU", total_tasks=1, path=self.path)
        db.save_version_workload(project_id, "verify", "HCU", total_functions=2, total_requirements=2, path=self.path)
        db.save_version_worklog(project_id, "spec", {"date": date(2026, 11, 5), "member_id": member_id,
            "subsystem": "HCU", "completed_tasks": 1}, path=self.path)
        db.replace_version_assignments(project_id, "verify", [{"subsystem": "HCU", "member_id": member_id,
            "functions": 2, "requirements": 2}], self.path)
        leave_id = db.add_leave(member_id, date(2026, 11, 10), date(2026, 11, 12), "연차", path=self.path)
        rows = [row for row in scheduled_leave_targets(db.load_team_data(self.path), date(2026, 11, 4), self.path)
                if row["leave_id"] == leave_id and row["project_id"] == project_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "verify")
        self.assertEqual(rows[0]["status"], "대상 업무")
        db.save_version_worklog(project_id, "verify", {"date": date(2026, 11, 10), "member_id": member_id,
            "subsystem": "HCU", "completed_functions": 1, "completed_requirements": 1}, path=self.path)

    def test_verification_worklog_opens_after_spec_completion_without_member_assignment(self):
        member_id = self.members["주해원"]
        prior = db.get_version_project(self.project_id, self.path)
        prior["verify_due"] = date(2026, 11, 9)
        db.save_version_project(prior, self.path, self.project_id)
        project_id = db.save_version_project({"version": "T0840", "status": "준비",
            "spec_start": date(2026, 11, 1), "spec_due": date(2026, 11, 5),
            "verify_start": date(2026, 11, 6), "verify_due": date(2026, 12, 10),
            "spec_registered": True, "verify_registered": True, "spec_owner_ids": [member_id],
            "verify_owner_ids": [member_id], "spec_total_items": 1,
            "verify_spec_total_items": 1, "verify_total_items": 1}, path=self.path)
        db.save_version_workload(project_id, "spec", "HCU", total_tasks=1, path=self.path)
        db.save_version_workload(project_id, "verify", "HCU", total_functions=1, total_requirements=1, path=self.path)
        db.save_version_worklog(project_id, "spec", {"date": date(2026, 11, 5), "member_id": member_id,
            "subsystem": "HCU", "completed_tasks": 1}, path=self.path)
        project = db.get_version_project(project_id, self.path)
        self.assertEqual(workflow_stage(project, self.path)["stage"], "verify_work")
        self.assertEqual(db.list_version_assignments(project_id, "verify", self.path), [])
        leave_id = db.add_leave(member_id, date(2026, 11, 10), date(2026, 11, 12), "연차", path=self.path)
        scheduled = [row for row in scheduled_leave_targets(db.load_team_data(self.path), date(2026, 11, 4), self.path)
                     if row["leave_id"] == leave_id and row["project_id"] == project_id]
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0]["status"], "대상 업무")
        db.save_version_worklog(project_id, "verify", {"date": date(2026, 11, 6), "member_id": member_id,
            "subsystem": "HCU", "completed_functions": 1, "completed_requirements": 1}, path=self.path)
        self.assertEqual(len(db.list_version_worklogs(project_id, "verify", self.path)), 1)

    def test_vacation_redistribution_uses_remaining_project_plan_without_person_assignments(self):
        absent_id = self.members["황지원"]
        project_id = db.save_version_project({"version": "T0860", "status": "준비",
            "spec_start": date(2026, 11, 1), "spec_due": date(2026, 11, 5),
            "verify_start": date(2026, 11, 6), "verify_due": date(2026, 12, 10),
            "spec_registered": True, "verify_registered": True,
            "spec_total_items": 1, "verify_spec_total_items": 8, "verify_total_items": 8}, path=self.path)
        db.save_version_workload(project_id, "spec", "HCU", total_tasks=1, path=self.path)
        db.save_version_workload(project_id, "verify", "HCU", total_functions=8,
                                 total_requirements=8, path=self.path)
        db.save_version_worklog(project_id, "spec", {"date": date(2026, 11, 5),
            "member_id": self.members["김재강"], "subsystem": "HCU", "completed_tasks": 1}, path=self.path)
        leave_start, leave_end = date(2026, 11, 10), date(2026, 11, 12)
        db.add_leave(absent_id, leave_start, leave_end, "연차", path=self.path)
        result = recommend_version_leave_coverage(db.load_team_data(self.path), db.get_version_project(project_id, self.path),
            absent_id, leave_start, leave_end, date(2026, 11, 4), self.path)
        self.assertEqual(result["mode"], "project_rebalance")
        self.assertEqual(result["total_demand"], 16)
        self.assertEqual(result["total_allocated"], 16)
        self.assertTrue(result["saveable"])
        assignments = result["project_assignments"]
        self.assertNotIn(absent_id, [row["member_id"] for row in assignments])
        self.assertEqual(sum(row["requirements"] for row in assignments), 8)
        self.assertEqual(sum(row["functions"] for row in assignments), 8)
        db.replace_version_assignments(project_id, "verify", assignments, self.path)
        self.assertEqual(len(db.list_version_assignments(project_id, "verify", self.path)), len(assignments))

    def test_vacation_redistribution_rebuilds_plan_when_only_other_members_are_assigned(self):
        absent_id = self.members["황지원"]
        other_id = self.members["김재강"]
        project_id = db.save_version_project({"version": "T0870", "status": "검증 진행 중",
            "spec_start": date(2026, 11, 1), "spec_due": date(2026, 11, 5),
            "verify_start": date(2026, 11, 6), "verify_due": date(2026, 12, 10),
            "spec_registered": True, "verify_registered": True, "verify_owner_ids": [absent_id],
            "spec_total_items": 1, "verify_spec_total_items": 4, "verify_total_items": 4}, path=self.path)
        db.save_version_workload(project_id, "spec", "HCU", total_tasks=1, path=self.path)
        db.save_version_worklog(project_id, "spec", {"date": date(2026, 11, 5),
            "member_id": other_id, "subsystem": "HCU", "completed_tasks": 1}, path=self.path)
        db.save_version_workload(project_id, "verify", "HCU", total_functions=4,
                                 total_requirements=4, path=self.path)
        # The phase has a PM assignment, but only to someone other than the vacationer.
        db.replace_version_assignments(project_id, "verify", [{"member_id": other_id,
            "subsystem": "HCU", "functions": 4, "requirements": 4}], self.path)
        leave_start, leave_end = date(2026, 11, 10), date(2026, 11, 12)
        result = recommend_version_leave_coverage(db.load_team_data(self.path),
            db.get_version_project(project_id, self.path), absent_id, leave_start,
            leave_end, date(2026, 11, 4), self.path)
        self.assertEqual(result["mode"], "project_rebalance")
        self.assertEqual(result["total_demand"], 8)
        self.assertEqual(result["total_allocated"], 8)
        self.assertNotIn(absent_id, [row["member_id"] for row in result["project_assignments"]])
        self.assertEqual(sum(row["requirements"] for row in result["project_assignments"]), 4)
        self.assertEqual(sum(row["functions"] for row in result["project_assignments"]), 4)

    def test_inconsistent_verification_status_and_missing_dynamic_logs_are_surfaced_for_confirmation(self):
        member_id = self.members["황지원"]
        prior = db.get_version_project(self.project_id, self.path)
        prior["verify_due"] = date(2026, 11, 3)
        db.save_version_project(prior, self.path, self.project_id)
        project_id = db.save_version_project({"version": "T0830", "status": "검증 진행 중",
            "spec_start": date(2026, 10, 1), "spec_due": date(2026, 10, 22),
            "verify_start": date(2026, 10, 20), "verify_due": date(2026, 11, 10),
            "spec_registered": True, "verify_registered": True, "spec_owner_ids": [member_id],
            "verify_owner_ids": [member_id], "spec_total_items": 1,
            "verify_spec_total_items": 2, "verify_total_items": 2}, path=self.path)
        db.save_version_workload(project_id, "spec", "HCU", total_tasks=1, path=self.path)
        db.save_version_workload(project_id, "verify", "HCU", total_functions=2, total_requirements=2, path=self.path)
        leave_id = db.add_leave(member_id, date(2026, 11, 4), date(2026, 11, 4), "연차", path=self.path)
        rows = [row for row in scheduled_leave_targets(db.load_team_data(self.path), date(2026, 11, 4), self.path)
                if row["leave_id"] == leave_id and row["project_id"] == project_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"].split(" · ")[0], "황지원 - 동적 검증 (T0830)")
        self.assertEqual(rows[0]["status"], "확인 필요")
        self.assertIn("요구사양서 작성 실적은 0/1건", rows[0]["reason"])
        self.assertIn("동적 검증 담당 배정 0명·실적 0건", rows[0]["reason"])


if __name__ == "__main__":
    unittest.main()
