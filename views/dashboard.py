from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services import risk, summary
from services.models import today
from services.version_projects import aggregate_version_logs, project_summary
from services.internal_projects import dashboard as internal_dashboard, is_internal_version, is_legacy_sheet_version
from data import db
from views.common import LEVEL_COLORS, fmt_date, header, page_surface


def render(team, rules):
    t = today()
    header("📊 대시보드", f"기준일 {t} · 분석 기간: 오늘부터 {rules['horizon_days']}일 · 주말 제외")
    with page_surface("dashboard"):
        if len(team.members) == 0:
            st.info("등록된 팀원이 없습니다. '데이터 관리'에서 팀원을 먼저 추가하세요.")
            return
        m = summary.dashboard_metrics(team, t, rules)

        c1, c2, c3 = st.columns(3)
        c1.metric("오늘의 부재 인원", f"{len(m['absent_today'])}명")
        c1.caption(", ".join(m["absent_today"]) or "부재자 없음")
        c2.metric("이번 주 예정 휴가", f"{len(m['week_leaves'])}건")
        c2.caption(", ".join(team.name(lv["member_id"]) for lv in m["week_leaves"]) or "없음")
        c3.metric("동적 검증 대체 담당자 미지정", f"{len(m['dynamic_backup_unassigned'])}건")
        c3.caption("휴가와 동적 검증 일정이 겹치며 PM 승인 대체 배정이 없는 업무 기준")
        pool = team.unit_pool()
        if pool:
            absent = risk.absent_map(team, t, rules)
            here = [m for m in pool if m not in absent]
            st.caption(f"단위 테스트 인원 오늘 가용 {len(here)}/{len(pool)}명" + ("" if len(here) == len(pool) else
                       " (부재: " + ", ".join(team.name(m) for m in pool if m in absent) + ")"))

        if m["insufficient_count"]:
            st.warning(f"담당자·중요도 미입력 등으로 위험도를 계산하지 못한 업무가 {m['insufficient_count']}건 있습니다. "
                       "이 업무는 '안전'으로 간주하지 않습니다.")

        projects = [p for p in db.list_version_projects() if is_legacy_sheet_version(p["version"])]
        if projects:
            st.subheader("버전별 동적검증 진행 현황")
            version_rows = []
            at_risk = []
            logs_by_version = aggregate_version_logs(team.logs.to_dict("records") if len(team.logs) else [])
            for project in projects:
                logs = logs_by_version.get(project["version"].strip().casefold(), {})
                phases = project_summary(team, project, t, {"spec": logs.get("spec_done_req", 0),
                                                             "verify": logs.get("verify_done_func", 0)})
                spec, verify = phases["spec"], phases["verify"]
                risks = [phase for phase in (spec, verify) if phase["risk"] == "지연 위험"]
                if risks:
                    at_risk.append(f"{project['version']} ({'·'.join('사양서' if x is spec else '검증' for x in risks)})")
                version_rows.append({"버전": project["version"], "상태": project["status"],
                    "사양서": f"{spec['done']}/{spec['total']} ({spec['progress']:.0%}) · 하루 {spec['daily'] if spec['daily'] is not None else '확인 필요'}개",
                    "동적검증": f"{verify['done']}/{verify['total']} ({verify['progress']:.0%}) · 하루 {verify['daily'] if verify['daily'] is not None else '확인 필요'}개",
                    "사양서 마감": spec["due"] or "미설정", "검증 마감": verify["due"] or "미설정"})
            st.dataframe(pd.DataFrame(version_rows), hide_index=True, width="stretch", alt="버전별 동적 검증 진행 현황")
            if at_risk:
                st.warning("마감 위험 버전: " + ", ".join(at_risk) + " · 담당자/휴가/처리 가능량을 확인하세요.")
        st.page_link(VERSION_PAGE, label="버전별 일정 및 변경 이력 관리 →")
        internal = [p for p in db.list_version_projects() if is_internal_version(p["version"])]
        if internal:
            st.subheader("내부 버전 업무 진척 현황 · T0080 이상")
            rows = []
            for project in internal:
                spec = internal_dashboard(project, "spec", db.list_version_workloads(project["id"], "spec"),
                                          db.list_version_worklogs(project["id"], "spec"), t)
                verify = internal_dashboard(project, "verify", db.list_version_workloads(project["id"], "verify"),
                                            db.list_version_worklogs(project["id"], "verify"), t)
                rows.append({"버전": project["version"],
                    "사양서 일감": f"{spec['totals']['completed_tasks']}/{spec['totals']['total_tasks']} ({spec['totals']['progress']:.0%})",
                    "요구사양서 마감": f"{spec['remaining_days']}일" if spec["remaining_days"] is not None else "미설정",
                    "검증 함수": f"{verify['totals']['completed_functions']}/{verify['totals']['total_functions']} ({verify['totals']['function_progress']:.0%})",
                    "검증 요구사양서": f"{verify['totals']['completed_requirements']}/{verify['totals']['total_requirements']} ({verify['totals']['requirement_progress']:.0%})",
                    "검증 마감": f"{verify['remaining_days']}일" if verify["remaining_days"] is not None else "미설정"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", alt="내부 버전별 업무 진척 현황")
        st.page_link(INTERNAL_WORK_PAGE, label="내부 버전 물량·업무 기록·배정 추천 →")

        st.subheader("날짜별 팀 가용 인원")
        daily = risk.daily_summary(team, t, t + timedelta(days=rules["horizon_days"]), rules)
        if daily.empty:
            st.info("분석 기간에 근무일이 없습니다.")
        else:
            fig = go.Figure(go.Bar(
                x=[fmt_date(d) for d in daily["날짜"]], y=daily["가용 인원"],
                marker_color=[LEVEL_COLORS[l] for l in daily["위험도"]],
                customdata=daily[["부재", "High", "Medium", "위험도"]].values,
                hovertemplate="%{x}<br>가용 %{y}명 / 부재 %{customdata[0]}명<br>"
                              "High %{customdata[1]}건 · Medium %{customdata[2]}건<br>일 위험도: %{customdata[3]}<extra></extra>"))
            fig.add_hline(y=len(team.members), line_dash="dot", line_color="#889",
                          annotation_text=f"전체 {len(team.members)}명")
            fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="가용 인원(명)")
            st.plotly_chart(fig)
            st.caption("막대 색 = 해당 날짜 최고 위험도 (🔴 High · 🟠 Medium · 🟢 Low · ⚪ 데이터 부족). 반차는 0.5명 부재로 계산.")
