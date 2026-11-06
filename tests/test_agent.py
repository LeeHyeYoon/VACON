import json
import os
import shutil
import unittest
from datetime import date
from unittest import mock

from data import db
from services import llm, llm_client
from services.agent import run_agent
from services.models import make_team
from services.tools import TOOL_DEFINITIONS, execute_tool, openai_tools


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def chat(self, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def tool_call(name, args, ident="call_1"):
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": raw}}


def response(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def text(content):
    return {"role": "assistant", "content": content, "tool_calls": None}


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
        client = FakeClient(([
            response(tool_call("get_version_status", {"version": "T0070"}, "call_1")),
            response(tool_call("list_vacations", {"start_date": "2026-11-02", "end_date": "2026-11-06",
                                                  "member_name": "주해원"}, "call_2")),
            response(tool_call("get_daily_workload", {"version": "T0070"}, "call_3")),
            text("T0070의 진행과 권장량을 확인했습니다."),
        ]))
        result = run_agent("T0070 일정과 업무량을 확인해줘", self.team, {}, date(2026, 10, 26), client=client)
        self.assertTrue(result.used)
        self.assertIn("권장량", result.text)
        self.assertEqual([c["tool"] for c in result.calls], ["get_version_status", "list_vacations", "get_daily_workload"])
        sent = client.requests[1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[-2]["tool_calls"][0]["id"], "call_1")
        self.assertEqual((sent[-1]["role"], sent[-1]["tool_call_id"]), ("tool", "call_1"))
        self.assertEqual(client.requests[0]["tools"], openai_tools())

    def test_repeat_call_is_blocked_and_api_error_is_reported(self):
        repeated = FakeClient(([
            response(tool_call("get_version_status", {"version": "T0070"}, "call_1")),
            response(tool_call("get_version_status", {"version": "T0070"}, "call_2")),
            text("반복 호출을 중단했습니다."),
        ]))
        result = run_agent("T0070 상태 알려줘", self.team, {}, date(2026, 10, 26), client=repeated)
        self.assertEqual([x["status"] for x in result.calls], ["ok", "error"])
        failed = FakeClient(([RuntimeError("network down")]))
        result = run_agent("질문", self.team, {}, date(2026, 10, 26), client=failed)
        self.assertFalse(result.used)
        self.assertIn("호출에 실패", result.error)

    def test_bad_arguments_and_reasoning_are_handled(self):
        client = FakeClient([
            response(tool_call("get_version_status", "{not json", "call_1")),
            text("<think>내부 추론</think>버전명을 다시 알려 주세요."),
        ])
        result = run_agent("상태 알려줘", self.team, {}, date(2026, 10, 26), client=client)
        self.assertEqual(result.calls[0]["status"], "error")
        self.assertIn("JSON", result.calls[0]["error"])
        self.assertEqual(result.text, "버전명을 다시 알려 주세요.")

    def test_openai_tool_format(self):
        tools = openai_tools()
        self.assertEqual({t["function"]["name"] for t in tools}, {t["name"] for t in TOOL_DEFINITIONS})
        self.assertTrue(all(t["type"] == "function" and t["function"]["parameters"]["type"] == "object" for t in tools))


class LlmClientTests(unittest.TestCase):
    def test_availability_follows_environment(self):
        with mock.patch.dict(os.environ, {"ALIRA_API_BASE": "", "ALIRA_MODEL": "", "VACON_LLM_MODEL": ""}):
            self.assertFalse(llm.llm_available())
            self.assertIsNone(llm.rephrase("q", "a", "f"))
            self.assertFalse(run_agent("질문", make_team(), {}, date(2026, 10, 26)).available)
        with mock.patch.dict(os.environ, {"ALIRA_API_BASE": "http://llm.test/v1/", "ALIRA_MODEL": "m"}):
            self.assertTrue(llm.llm_available())
            self.assertEqual(llm_client.api_base(), "http://llm.test/v1")

    def test_request_body_disables_thinking_by_default(self):
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"role": "assistant", "content": "답"}}]}).encode()

        def fake_urlopen(req, timeout):
            captured.update(url=req.full_url, body=json.loads(req.data), auth=req.get_header("Authorization"))
            return Resp()

        env = {"ALIRA_API_BASE": "http://llm.test/v1", "ALIRA_MODEL": "Qwen/Qwen3.6-27B",
               "ALIRA_API_KEY": "", "VACON_LLM_THINKING": ""}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(llm_client.complete("sys", "user"), "답")
        self.assertEqual(captured["url"], "http://llm.test/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "Qwen/Qwen3.6-27B")
        self.assertEqual(captured["body"]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIsNone(captured["auth"])

    def test_complete_returns_none_on_failure(self):
        with mock.patch.dict(os.environ, {"ALIRA_API_BASE": "http://llm.test/v1", "ALIRA_MODEL": "m"}),                 mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertIsNone(llm_client.complete("sys", "user"))


if __name__ == "__main__":
    unittest.main()
