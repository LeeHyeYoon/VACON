import os
import shutil
import unittest
from datetime import date
from types import SimpleNamespace

from data import db
from services.agent import run_agent
from services.models import make_team
from services.tools import TOOL_DEFINITIONS, execute_tool


class FakeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def tool_use(name, args, ident="toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=ident)


def response(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(os.path.dirname(__file__), "_agent_test_data")
        os.makedirs(self.test_dir, exist_ok=True)
        self.path = os.path.join(self.test_dir, "vacon.db")
        self.old_db = os.environ.get("VACON_DB")
        os.environ["VACON_DB"] = self.path
        db.init_db(self.path, seed=False)
        self.member_id = db.add_member("주해원", "QA", 40, path=self.path)
        db.save_version_project({"version": "T0070", "status": "검증 진행 중", "total_items": 30,
            "spec_done": 5, "verify_done": 10, "spec_start": date(2026, 10, 26), "spec_due": date(2026, 11, 6),
            "verify_start": date(2026, 10, 26), "verify_due": date(2026, 11, 6), "verify_owner_ids": [self.member_id]}, path=self.path)
        self.team = make_team(members=[{"id": self.member_id, "name": "주해원", "role": "QA", "workload": 40}],
                              leaves=[], tasks=[])

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("VACON_DB", None)
        else:
            os.environ["VACON_DB"] = self.old_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_tool_input_validation_and_no_write_tools(self):
        with self.assertRaises(ValueError):
            execute_tool("list_vacations", {"start_date": "bad", "end_date": "2026-10-30"},
                         team=self.team, rules={}, today=date(2026, 10, 26))
        with self.assertRaisesRegex(ValueError, "지원하지 않는"):
            execute_tool("get_version_status", {"version": "T0070", "sql": "DROP TABLE"},
                         team=self.team, rules={}, today=date(2026, 10, 26))
        with self.assertRaises(ValueError):
            execute_tool("update_version", {"version": "T0070"}, team=self.team, rules={}, today=date.today())
        self.assertEqual({x["name"] for x in TOOL_DEFINITIONS},
                         {"list_vacations", "get_version_status", "get_daily_workload", "analyze_work_risks",
                          "recommend_backup", "draft_handover"})

    def test_multistep_tool_call_round_trip(self):
        client = SimpleNamespace(messages=FakeMessages([
            response(tool_use("get_version_status", {"version": "T0070"}, "toolu_1")),
            response(tool_use("list_vacations", {"start_date": "2026-11-02", "end_date": "2026-11-06",
                                                  "member_name": "주해원"}, "toolu_2")),
            response(tool_use("get_daily_workload", {"version": "T0070"}, "toolu_3")),
            response(SimpleNamespace(type="text", text="T0070의 진행과 권장량을 확인했습니다."), stop="end_turn"),
        ]))
        result = run_agent("T0070 일정과 업무량을 확인해줘", self.team, {}, date(2026, 10, 26), client=client)
        self.assertTrue(result.used)
        self.assertIn("권장량", result.text)
        self.assertEqual([c["tool"] for c in result.calls], ["get_version_status", "list_vacations", "get_daily_workload"])
        sent = client.messages.requests[1]["messages"][-1]["content"]
        self.assertEqual(sent[0]["type"], "tool_result")

    def test_repeat_call_is_blocked_and_api_error_is_reported(self):
        repeated = SimpleNamespace(messages=FakeMessages([
            response(tool_use("get_version_status", {"version": "T0070"}, "toolu_1")),
            response(tool_use("get_version_status", {"version": "T0070"}, "toolu_2")),
            response(SimpleNamespace(type="text", text="반복 호출을 중단했습니다."), stop="end_turn"),
        ]))
        result = run_agent("T0070 상태 알려줘", self.team, {}, date(2026, 10, 26), client=repeated)
        self.assertEqual([x["status"] for x in result.calls], ["ok", "error"])
        failed = SimpleNamespace(messages=FakeMessages([RuntimeError("network down")]))
        result = run_agent("질문", self.team, {}, date(2026, 10, 26), client=failed)
        self.assertFalse(result.used)
        self.assertIn("호출에 실패", result.error)


if __name__ == "__main__":
    unittest.main()
