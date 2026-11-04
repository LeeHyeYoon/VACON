import pandas as pd
import streamlit as st

from data import db
from services.models import TeamData
from services.coverage import recommend_version_leave_coverage, scheduled_leave_targets
from views.common import header, show_df


def _render_version_leave_coverage(team):
    targets = scheduled_leave_targets(team)
    if not targets:
        st.info("오늘 이후 휴가 중 등록된 버전 업무 일정과 겹치는 대상이 없습니다.")
        return
    selected = st.selectbox("대체 대상 업무 선택", targets, format_func=lambda row: row["label"],
                            key="scheduled_coverage_target")
    st.write(f"**휴가자 및 기간:** {selected['member_name']} · {selected['leave_start']}~{selected['leave_end']} ({selected['leave_type']})")
    st.write(f"**대상 버전 및 업무:** {selected['version']} · {selected['phase_label']} · 실제 겹치는 기간 {selected['overlap_start']}~{selected['overlap_end']}")
    if selected["status"] != "대상 업무":
        st.warning(selected["reason"])
        return
    project = db.get_version_project(selected["project_id"])
    result = recommend_version_leave_coverage(team, project, selected["member_id"],
        selected["overlap_start"], selected["overlap_end"])
    st.subheader("AI 추천 대체 업무")
    demand_label = "버전 잔여 업무량(개인별 배정 없음)" if result.get("mode") == "project_rebalance" else "대체 가능 잔여량"
    st.write(f"**{demand_label}:** {result.get('total_demand', 0)} · **마감 준수 예측:** {result['summary']}")
    st.caption(f"마감일 {result.get('due') or '데이터 없음'} · 예측 신뢰도 {result['confidence']} · {result['message']}")
    if result["rows"]:
        recommendations = [row for row in result["rows"] if row.get("추가 배정 일감", 0) or row.get("추가 배정 요구사양서", 0) or row.get("추가 배정 함수", 0)]
        summary_cols = st.columns(3)
        summary_cols[0].metric("버전 잔여 업무" if result.get("mode") == "project_rebalance" else "휴가자 잔여 업무", result.get("total_demand", 0))
        summary_cols[1].metric("AI 분배 제안", result.get("total_allocated", 0))
        summary_cols[2].metric("분배 후 미배정", result.get("remaining_unallocated", 0))
        if recommendations:
            show_df(pd.DataFrame([{"추천 담당자": row["담당자"], "기존 업무량": row["기존 업무량"],
            "추가 업무량": (f"일감 {row['추가 배정 일감']}" if selected["phase"] == "spec" else
                           f"요구사양서 {row['추가 배정 요구사양서']} · 함수 {row['추가 배정 함수']}"),
            "예상 완료일": row["예상 완료일"], "마감 준수": row["마감 준수 여부"],
            "추천 사유": row["추천 근거"]} for row in recommendations]))
            for index, row in enumerate(recommendations, 1):
                with st.expander(f"{index}. {row['담당자']} · 추가 업무 배정 근거"):
                    st.write(f"**예상 완료일:** {row['예상 완료일']} · **마감 준수:** {row['마감 준수 여부']}")
                    st.write(row["추천 근거"])
        else:
            st.info("과거 처리 이력과 현재 배정 잔량을 확인할 수 있는 담당자가 없어 추천 수량을 만들지 않았습니다.")
    if result.get("remaining_unallocated", 0):
        st.warning("처리 이력과 실제 가용일 기준으로 분배되지 않은 업무가 있습니다. 추가 인력 또는 일정 조정을 검토하세요.")
    if result.get("confidence") == "낮음":
        st.warning("실제 이력 데이터가 부족해 예측 신뢰도가 낮습니다. 확인되지 않은 처리량은 배정하지 않았습니다.")
    if result.get("mode") == "project_rebalance":
        if result.get("distribution_warnings"):
            for warning in result["distribution_warnings"]:
                st.warning(warning)
        project_assignments = result.get("project_assignments", [])
        if result.get("saveable") and project_assignments:
            approved = st.checkbox("버전 잔여 업무 재분배안을 확인했고 PM 배정으로 승인합니다.",
                                   key=f"scheduled_plan_approved_{selected['id']}")
            if st.button("PM 승인 재분배안 저장", disabled=not approved, key=f"scheduled_plan_save_{selected['id']}"):
                try:
                    count = db.replace_version_assignments(selected["project_id"], selected["phase"], project_assignments)
                    st.cache_data.clear()
                    st.success(f"PM 승인 재분배안 {count}건을 버전 업무 배정으로 저장했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"재분배안을 저장하지 못했습니다: {exc}")
        elif not project_assignments:
            st.info("기존 완료 실적의 담당자와 새 잔여 업무를 모두 반영할 수 없어 저장할 재분배안이 없습니다.")
    else:
        assigned_rows = result.get("assignments", [])
        if assigned_rows:
            approved = st.checkbox("추천 분배안을 확인했고 PM 대체 배정으로 승인합니다.", key=f"scheduled_coverage_approved_{selected['id']}")
            if st.button("PM 승인 대체 배정 저장", disabled=not approved, key=f"scheduled_coverage_save_{selected['id']}"):
                try:
                    count = db.save_version_backup_assignments(selected["project_id"], selected["phase"],
                        selected["member_id"], selected["leave_start"], selected["leave_end"], assigned_rows)
                    st.cache_data.clear()
                    st.success(f"PM 승인 대체 배정 {count}건을 저장했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"대체 배정을 저장하지 못했습니다: {exc}")


def render(team: TeamData, rules):
    header("🤝 대체 담당자", "휴가 기간과 실제 버전 일정이 겹치는 네 명의 단위 검증 업무만 표시합니다.")
    _render_version_leave_coverage(team)
