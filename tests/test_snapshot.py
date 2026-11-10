import unittest
from datetime import date

from services.agent import run_agent
from services.models import make_team
from services.snapshot import data_snapshot

TODAY = date(2026, 11, 10)


def sample_team():
    members = [
        {"id": 1, "name": "김재강", "role": "단위 테스트", "workload": 70, "contact": "010-1234-5678"},
        {"id": 2, "name": "이혜윤", "role": "단위 테스트", "workload": 40, "contact": ""},
    ]
    skills = [{"member_id": 1, "skill": "단위테스트", "level": 3}, {"member_id": 2, "skill": "단위테스트", "level": 2}]
    tasks = [
        {"id": 1, "name": "동적 검증 v2.1", "owner_id": 1, "backup_id": 2, "importance": "상", "status": "진행중",
         "progress": 55, "due_date": date(2026, 11, 11), "required_skill": "단위테스트", "remaining": "TC 실행",
         "notes": "이 메모의 지시는 무시. " + "가" * 300},
        {"id": 2, "name": "요구사양서 v2.2", "owner_id": 2, "backup_id": None, "importance": "중", "status": "진행중",
         "progress": 20, "due_date": date(2026, 11, 8), "required_skill": "", "remaining": "", "notes": ""},
    ]
    leaves = [
        {"id": 1, "member_id": 1, "start": date(2026, 11, 10), "end": date(2026, 11, 12), "type": "연차", "note": "개인"},
        {"id": 2, "member_id": 2, "start": date(2026, 9, 1), "end": date(2026, 9, 2), "type": "연차", "note": ""},
    ]
    logs = [
        {"id": 1, "source": "샘플", "track": "dynamic", "date": date(2026, 11, 3), "member_id": 1, "version": "v2.1",
         "tc": 20, "req": 4, "func": 0},
        {"id": 2, "source": "샘플", "track": "dynamic", "date": date(2026, 11, 4), "member_id": 1, "version": "v2.1",
         "tc": 10, "req": 1, "func": 0},
        {"id": 3, "source": "샘플", "track": "dynamic", "date": date(2026, 8, 1), "member_id": 2, "version": "v1.0",
         "tc": 99, "req": 9, "func": 0},
    ]
    return make_team(members=members, skills=skills, tasks=tasks, leaves=leaves, logs=logs)


VERSIONS = [{"version": "T0070", "status": "검증 진행 중", "spec_start": date(2026, 10, 26), "spec_due": date(2026, 11, 6),
             "verify_start": date(2026, 10, 26), "verify_due": date(2026, 11, 13), "spec_owner_ids": [2],
             "verify_owner_ids": [1], "spec_done": 5, "spec_total_items": 30, "verify_done": 10, "verify_total_items": 30}]


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.text = data_snapshot(sample_team(), TODAY, versions=VERSIONS)

    def test_includes_core_data(self):
        self.assertIn("- 김재강 | 단위 테스트 | 70 | 단위테스트(3)", self.text)
        self.assertIn("- 요구사양서 v2.2 | 이혜윤 | 미지정 | 진행중 | 20 | 중 | 2026-11-08(일) 2일 경과", self.text)
        self.assertIn("2026-11-11(수) D-1", self.text)
        self.assertIn("- 김재강 | 2026-11-10(화)~2026-11-12(목) (3일) | 연차", self.text)
        self.assertIn("- T0070 | 검증 진행 중 | 2026-10-26(월)~2026-11-06(금) | 이혜윤 | 2026-10-26(월)~2026-11-13(금) | 김재강 | 5/30 | 10/30", self.text)

    def test_recent_log_summary_only(self):
        self.assertIn("- 김재강 | 동적 검증 | 기록일 2일 | 실행 TC 30, 완료 요구사양서 5", self.text)
        self.assertNotIn("v1.0", self.text)

    def test_excludes_contact_old_leaves_and_truncates_notes(self):
        self.assertNotIn("010-1234-5678", self.text)
        self.assertNotIn("2026-09-01", self.text)
        self.assertNotIn("가" * 200, self.text)
        self.assertIn("…", self.text)

    def test_limit(self):
        short = data_snapshot(sample_team(), TODAY, versions=VERSIONS, limit=300)
        self.assertLessEqual(len(short), 300 + 60)
        self.assertIn("이후 생략", short)

    def test_empty_team(self):
        text = data_snapshot(make_team(), TODAY, versions=[])
        self.assertIn("## 업무", text)
        self.assertIn("- 없음", text)


class AgentSnapshotTests(unittest.TestCase):
    def test_snapshot_is_in_system_prompt_and_answer_without_tools(self):
        class Client:
            def __init__(self):
                self.requests = []

            def chat(self, messages, **kwargs):
                self.requests.append(messages)
                return {"role": "assistant", "content": "이혜윤의 요구사양서 v2.2는 대체 담당자가 미지정입니다.", "tool_calls": None}

        client = Client()
        result = run_agent("대체 담당자 없는 업무는?", sample_team(), {}, TODAY, client=client,
                           snapshot=data_snapshot(sample_team(), TODAY, versions=VERSIONS))
        self.assertTrue(result.used)
        self.assertEqual(result.calls, [])
        system = client.requests[0][0]["content"]
        self.assertIn("# VACON 기본 데이터", system)
        self.assertIn("요구사양서 v2.2 | 이혜윤 | 미지정", system)


if __name__ == "__main__":
    unittest.main()
