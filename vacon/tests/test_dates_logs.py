import unittest
from datetime import date, timedelta

from services import sheet_sync as ss
from services import worklog as wl
from services.models import make_team

REF = date(2026, 10, 26)
D = lambda n: date(2026, 10, 12) + timedelta(days=n)


def team(logs=()):
    members = [{"id": i, "name": n, "role": "", "workload": 50, "contact": "", "mbti": ""}
               for i, n in {1: "주해원", 2: "김재강", 3: "황지원"}.items()]
    skills = [{"member_id": m, "skill": "단위테스트", "level": 3} for m in (1, 2, 3)]
    return make_team(members, skills, [], [], logs)


class DateParserTests(unittest.TestCase):
    def test_supported_formats(self):
        expect = date(2026, 10, 19)
        for s in ["2026-10-19", "2026. 10. 19.", "2026/10/19", "2026-10-19 (월)", "2026년 10월 19일", "26.10.19", "26/10/19",
                  "10/19/26", "10/19/2026", "20261019", "2026-10-19T09:30:00", "2026-10-19 09:30", "2026.10.19(월) 오후 2:30",
                  "19.10.2026", "46314", "10/19", "10/19(월)", "10월 19일", "10-19", "10월19일"]:
            with self.subTest(s=s):
                self.assertEqual(ss.parse_sheet_date_ex(s, REF, "past")[0], expect)

    def test_inferred_flag_and_empty(self):
        self.assertEqual(ss.parse_sheet_date_ex("10/19", REF, "past"), (date(2026, 10, 19), True))
        self.assertEqual(ss.parse_sheet_date_ex("2026-10-19", REF, "past"), (date(2026, 10, 19), False))
        self.assertEqual(ss.parse_sheet_date_ex("  ", REF), (None, False))

    def test_year_inference_modes_and_override(self):
        self.assertEqual(ss.parse_sheet_date("12/30", REF, "past"), date(2025, 12, 30))     # 로그는 미래가 아님
        self.assertEqual(ss.parse_sheet_date("12/30", REF, "nearest"), date(2026, 12, 30))  # 마감일은 가까운 해
        self.assertEqual(ss.parse_sheet_date("10/19", REF, "past", year=2025), date(2025, 10, 19))
        self.assertEqual(ss.parse_sheet_date("1/2", date(2026, 1, 1), "past"), date(2026, 1, 2))  # +7일 이내는 올해

    def test_invalid_values_raise(self):
        for s in ["2/30", "합계", "1주차", "내일", "13/45/2026", "abc"]:
            with self.subTest(s=s), self.assertRaises(ValueError):
                ss.parse_sheet_date(s, REF)


HEAD = "날짜,담당자,버전,업무 구분,a,b,c,d,완료한 Req,e,f,g,h,i,실행한 TC\n"
ROW = lambda d, who, req, tc: f"{d},{who},v1,TC 실행,,,,,{req},,,,,,{tc}\n"
CFG = {"role": "log_dynamic", "header_row": 1, "headers": {},
       "columns": {"date": "A", "member": "B", "version": "C", "category": "D", "req": "I", "tc": "O"}}


class LogParseTests(unittest.TestCase):
    def test_yearless_and_weekday_dates_work(self):
        txt = HEAD + ROW("10/19(월)", "주해원", 3, 20) + ROW("10월 20일", "김재강", 2, 15)
        r = wl.parse_logs(txt, CFG, team(), today=REF)
        self.assertEqual([e["date"] for e in r["entries"]], [date(2026, 10, 19), date(2026, 10, 20)])
        self.assertEqual(r["diag"]["inferred_year"], 2); self.assertEqual(r["skipped"], 0)
        self.assertTrue(any("연도가 없는 날짜 2개" in w for w in r["warnings"]))

    def test_year_option(self):
        r = wl.parse_logs(HEAD + ROW("10/19", "주해원", 1, 1), {**CFG, "year": 2025}, team(), today=REF)
        self.assertEqual(r["entries"][0]["date"], date(2025, 10, 19))

    def test_bad_dates_reported_with_examples_not_per_row_spam(self):
        txt = HEAD + ROW("합계", "주해원", 9, 9) + ROW("1주차", "주해원", 9, 9) + ROW("합계", "김재강", 9, 9) + ROW("10/19", "주해원", 1, 1)
        r = wl.parse_logs(txt, CFG, team(), today=REF)
        self.assertEqual(len(r["entries"]), 1)
        self.assertEqual(r["diag"]["bad_date_rows"], 3)
        self.assertEqual(dict(r["diag"]["bad_dates"])["합계"], 2)
        self.assertEqual(sum("날짜 열(A)" in w for w in r["warnings"]), 1)   # 요약 경고 1건

    def test_blank_dates_and_fill_down_for_merged_cells(self):
        txt = HEAD + ROW("10/19", "주해원", 1, 10) + ROW("", "", 2, 20) + ROW("", "김재강", 3, 30) + ROW("10/20", "", 4, 40)
        plain = wl.parse_logs(txt, CFG, team(), today=REF)
        self.assertEqual(len(plain["entries"]), 1); self.assertEqual(plain["diag"]["blank_date_rows"], 1)
        self.assertTrue(any("병합 셀" in w for w in plain["warnings"]))
        filled = wl.parse_logs(txt, {**CFG, "fill_down": True}, team(), today=REF)
        e = filled["entries"]
        self.assertEqual([(x["member_id"], x["date"], x["tc"]) for x in e],
                         [(1, D(7), 10), (1, D(7), 20), (2, D(7), 30), (2, D(8), 40)])

    def test_header_row_guess_and_hint(self):
        txt = "SS1팀 업무 로그,,\n작성 방법: ...,,\n" + HEAD + ROW("10/19", "주해원", 1, 1)
        r = wl.parse_logs(txt, CFG, team(), today=REF)          # header_row=1로 잘못 설정
        self.assertEqual(r["diag"]["header_guess"], 3); self.assertIn("3행", r["diag"]["header_hint"])
        self.assertEqual(len(r["entries"]), 1)                  # 데이터 행은 읽히고 제목/설명 행은 건너뜀
        with self.assertRaisesRegex(ss.SheetError, "3행"):      # 저장된 제목이 있으면 변경 오류에도 힌트 포함
            wl.parse_logs(txt, {**CFG, "headers": {"date": "날짜", "member": "담당자"}}, team(), today=REF)

    def test_all_dates_unreadable_gives_empty_with_diag(self):
        r = wl.parse_logs(HEAD + ROW("월요일", "주해원", 1, 1), CFG, team(), today=REF)
        self.assertEqual(r["entries"], []); self.assertEqual(r["diag"]["bad_dates"], [("월요일", 1)])


class PeriodTests(unittest.TestCase):
    def logs(self):
        mk = lambda d, tc: {"id": None, "source": "T", "track": "dynamic", "date": d, "member_id": 1, "version": "v",
                            "category": "c", "req": 0, "tc": tc, "issue": 0}
        return [mk(D(0), 10), mk(D(1), 20), mk(D(7), 100)]

    def test_custom_range_changes_sums(self):
        t = team(self.logs())
        full = wl.throughput_table(t, D(0), D(11))
        part = wl.throughput_table(t, D(0), D(4))
        pick = lambda df: df[(df["팀원"] == "주해원") & (df["업무"] == "동적 검증")].iloc[0]
        self.assertEqual(pick(full)["실행 TC 합계"], 130)
        self.assertEqual(pick(part)["실행 TC 합계"], 30)
        self.assertEqual(pick(part)["근무일"], 5)
        empty = wl.throughput_table(t, D(14), D(18))
        self.assertEqual(pick(empty)["실행 TC 합계"], "-")       # 기간 내 기록 없음은 0이 아님

    def test_leave_impact_uses_selected_base_period(self):
        t = team(self.logs() + [{"id": None, "source": "T", "track": "dynamic", "date": D(0), "member_id": 2, "version": "v",
                                 "category": "c", "req": 0, "tc": 10, "issue": 0}])
        r = wl.leave_impact(t, 1, D(21), D(23), REF, base_start=D(0), base_end=D(4))
        it = next(i for i in r["items"] if i["metric"] == "tc")
        self.assertEqual(r["base"], (D(0), D(4)))
        self.assertAlmostEqual(it["avg"], 30 / 5)               # 지정한 기준 기간 일평균
        self.assertAlmostEqual(it["gap"], 30 / 5 * 3)
        far = wl.leave_impact(t, 1, D(21), D(23), REF, base_start=D(30), base_end=D(40))
        self.assertIsNone(next(i for i in far["items"] if i["metric"] == "tc")["gap"])


if __name__ == "__main__":
    unittest.main()
