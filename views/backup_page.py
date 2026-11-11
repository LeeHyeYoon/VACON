import pandas as pd
import streamlit as st

from data import db
from services.models import TeamData
from services.coverage import recommend_version_leave_coverage, scheduled_leave_targets
from services import coverage_ai, llm_client
from views.common import header, page_surface, show_df


def _activate_session_daily_targets(selected, assignments, recommendations):
    """Expose approved daily goals only for the current Streamlit session."""
    key = (f"{selected['project_id']}:{selected['phase']}:{selected['member_id']}:"
           f"{selected['leave_start']}:{selected['leave_end']}")
    active = st.session_state.setdefault("pm_approved_daily_targets", {})
    active[key] = {
        "project_id": int(selected["project_id"]),
        "phase": selected["phase"],
        "absent_member_id": int(selected["member_id"]),
        "leave_start": str(selected["leave_start"]),
        "leave_end": str(selected["leave_end"]),
        "assignments": [
            {"member_id": int(row["member_id"]), "subsystem": str(row["subsystem"]).casefold(),
             "tasks": int(row.get("tasks", 0) or 0),
             "requirements": int(row.get("requirements", 0) or 0),
             "functions": int(row.get("functions", 0) or 0)}
            for row in assignments
            if any(int(row.get(metric, 0) or 0) > 0 for metric in ("tasks", "requirements", "functions"))
        ],
        "daily_targets": {
            int(row["member_id"]): {
                "tasks": int(row.get("하루 작성 목표", 0) or 0),
                "requirements": int(row.get("하루 검증 요구사양서 목표", 0) or 0),
                "functions": int(row.get("하루 검증 함수 목표", 0) or 0),
            }
            for row in recommendations
        },
    }


def _render_version_leave_coverage(team):
    targets = scheduled_leave_targets(team)
    if not targets:
        st.info("오늘 이후 휴가 중 등록된 버전 업무 일정과 겹치는 대상이 없습니다.")
        return
    selected = st.selectbox("대체 대상 업무 선택", targets, format_func=lambda row: row["label"],
                            key="scheduled_coverage_target")
    with st.container(border=True):
        st.markdown("**선택한 대체 대상**")
        info = st.columns(3)
        info[0].caption("휴가자")
        info[0].markdown(f"**{selected['member_name']}** · {selected['leave_type']}")
        info[1].caption("휴가 기간")
        info[1].markdown(f"**{selected['leave_start']} ~ {selected['leave_end']}**")
        info[2].caption("대상 업무")
        info[2].markdown(f"**{selected['version']} · {selected['phase_label']}**")
        st.caption(f"실제 일정과 겹치는 기간　{selected['overlap_start']} ~ {selected['overlap_end']}")
    if selected["status"] != "대상 업무":
        st.warning(selected["reason"])
        return
    project = db.get_version_project(selected["project_id"])
    result = recommend_version_leave_coverage(team, project, selected["member_id"],
        selected["overlap_start"], selected["overlap_end"])
    phase = result.get("phase", selected["phase"])
    demand = result.get("demand_by_metric", {})
    if not any(int(value or 0) > 0 for value in demand.values()):
        phase_label = "일감 작성" if phase == "spec" else "동적 검증"
        st.info(f"휴가자의 {phase_label} 배정 업무가 모두 완료되어 대체 추천할 잔여 업무가 없습니다.")
        st.caption(f"단계 마감일 {result.get('due') or '미등록'} · 대체 분배할 잔여 업무 없음")
        return
    st.divider()
    st.subheader("AI 추천 대체 업무")
    st.caption("담당자별 최근 처리량 중앙값의 비율로 팀 전체 하루 필요량을 나눕니다. 휴가 기간이 겹치는 담당자는 대체 후보에서 제외합니다. PM 승인 배정은 실제 완료 실적이 기록될 때까지 잔여 업무로 표시합니다.")
    team_daily = result.get("team_daily_required", {})
    remaining_days = result.get("remaining_workdays", 0)
    if phase == "spec":
        st.write(f"**마감까지 실제 근무일:** {remaining_days}일 · **팀 전체 하루 필요 작성량:** "
                 f"{team_daily.get('tasks'):.1f}개/일" if team_daily.get("tasks") is not None else
                 f"**마감까지 실제 근무일:** {remaining_days}일 · **팀 전체 하루 필요 작성량:** 계산 불가")
    else:
        req_daily = team_daily.get("requirements")
        func_daily = team_daily.get("functions")
        req_text = f"{req_daily:.1f}개/일" if req_daily is not None else "계산 불가"
        func_text = f"{func_daily:.1f}개/일" if func_daily is not None else "계산 불가"
        st.write(f"**마감까지 실제 근무일:** {remaining_days}일 · **팀 전체 하루 필요량:** "
                 f"요구사양서 {req_text} · 함수 {func_text}")
    st.caption(f"마감일 {result.get('due') or '데이터 없음'} · 과거 실제 처리량 기준: {result['summary']} · 신뢰도 {result['confidence']}")
    recommendations = []
    if result["rows"]:
        recommendations = [row for row in result["rows"]
                           if any(int(row.get(field, 0) or 0) for field in
                                  ("추가 배정 일감", "추가 배정 요구사양서", "추가 배정 함수"))]
        if recommendations:
            fingerprint = coverage_ai.input_fingerprint(result, selected)
            recommendation_key = f"coverage_ai_recommendation_{selected['id']}"
            saved_ai = st.session_state.get(recommendation_key)
            if saved_ai and saved_ai.get("fingerprint") != fingerprint:
                st.session_state.pop(recommendation_key, None)
                saved_ai = None
            show_key = f"coverage_show_recommendations_{selected['id']}"
            if st.session_state.get(show_key) != fingerprint:
                if st.button("추천 대체 담당자 보기", icon=":material/person_search:",
                             type="primary", width="stretch",
                             key=f"coverage_show_button_{selected['id']}"):
                    st.session_state[show_key] = fingerprint
                else:
                    st.caption("버튼을 누르면 ALIRA가 후보를 비교하고 추천 담당자를 표시합니다.")
                    return
            error_key = f"coverage_ai_error_{selected['id']}"
            if st.session_state.get(error_key, {}).get("fingerprint") != fingerprint:
                st.session_state.pop(error_key, None)
            st.caption("ALIRA가 업무 경험과 가용량을 비교해 대체 담당자를 추천합니다. 배정 수량은 VACON의 실제 업무·일정 데이터로 계산됩니다.")
            if not llm_client.configured():
                st.info("ALIRA 설정이 없어 대체 담당자를 추천할 수 없습니다. ALIRA_API_BASE와 ALIRA_MODEL 환경변수를 확인하세요.")
            elif saved_ai is None and st.session_state.get(error_key) is None:
                try:
                    with st.spinner("ALIRA가 업무 이력과 후보별 가용량을 비교하고 있습니다…"):
                        saved_ai = coverage_ai.recommend_candidates(result, selected)
                    st.session_state[recommendation_key] = saved_ai
                except Exception as exc:
                    st.session_state[error_key] = {"fingerprint": fingerprint, "message": str(exc)}

            saved_error = st.session_state.get(error_key)
            if saved_error and saved_error.get("fingerprint") == fingerprint:
                st.error(f"ALIRA 후보 추천에 실패했습니다: {saved_error['message']}")
            display_number = lambda value: "계산 불가" if value is None else f"{int(value)}개/일"
            if saved_ai:
                row_by_id = {row["member_id"]: row for row in recommendations}
                table = []
                for item in saved_ai["recommendations"]:
                    row = row_by_id[item["member_id"]]
                    if phase == "spec":
                        values = {"대체 담당자": row["담당자"],
                                  "하루 작성 추천 개수": display_number(row.get("하루 작성 목표"))}
                    else:
                        values = {"대체 담당자": row["담당자"],
                                  "하루 검증 요구사양서 추천 개수": display_number(row.get("하루 검증 요구사양서 목표")),
                                  "하루 검증 함수 추천 개수": display_number(row.get("하루 검증 함수 목표"))}
                    table.append(values)
                show_df(pd.DataFrame(table))
        else:
            st.info("과거 처리 이력과 현재 배정 잔량을 확인할 수 있는 담당자가 없어 추천 수량을 만들지 않았습니다.")
    if result.get("confidence") == "예측 불가":
        if phase == "verify":
            st.warning("마감 완료 가능성은 검증 함수 처리량 등 일부 이력이 부족해 확정할 수 없습니다. 하루 검증 요구사양서 추천값은 검증 업무 기록과 동일한 담당자 전체 기록 중앙값이며, 남은 수량과 실제 가용 근무일은 마감 판단에 반영됩니다.")
        else:
            st.warning("마감 완료 가능성은 일부 처리량 자료가 부족해 확정할 수 없습니다. 하루 작성 추천값은 검증 업무 기록과 동일한 담당자 전체 기록 중앙값이며, 남은 일감과 실제 가용 근무일은 마감 판단에 반영됩니다.")
    if result.get("mode") == "project_rebalance":
        if result.get("distribution_warnings"):
            for warning in result["distribution_warnings"]:
                st.warning(warning)
        project_assignments = result.get("project_assignments", [])
        if result.get("saveable") and project_assignments:
            approved = st.toggle("버전 잔여 업무 재분배안을 확인했고 PM 배정으로 승인합니다.",
                                   key=f"scheduled_plan_approved_{selected['id']}")
            if st.button("승인 재분배안 저장", type="primary", disabled=not approved, key=f"scheduled_plan_save_{selected['id']}"):
                try:
                    count = db.replace_version_assignments(selected["project_id"], selected["phase"],
                        project_assignments, approved_rebalance=True,
                        leave_coverage={"absent_member_id": selected["member_id"],
                            "leave_start": selected["leave_start"], "leave_end": selected["leave_end"],
                            "assignments": result.get("assignments", []),
                            "daily_targets": [
                                {"member_id": row["member_id"],
                                 "requirements": row.get("하루 검증 요구사양서 목표", 0),
                                 "functions": row.get("하루 검증 함수 목표", 0),
                                 "tasks": row.get("하루 작성 목표", 0)}
                                for row in recommendations]})
                    _activate_session_daily_targets(selected, result.get("assignments", []), recommendations)
                    st.cache_data.clear()
                    st.success(f"PM 승인 재분배안 {count}건을 버전 업무 배정으로 저장했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"재분배안을 저장하지 못했습니다: {exc}")
        elif not project_assignments:
            st.info("기존 완료 실적의 담당자와 새 잔여 업무를 모두 반영할 수 없어 저장할 재분배안이 없습니다.")
    else:
        assigned_rows = result.get("assignments", [])
        coverage_rows = result.get("coverage_assignments", [])
        if assigned_rows or coverage_rows:
            if coverage_rows and not assigned_rows:
                st.info("현재 표시된 대체 업무는 이미 저장되어 있습니다. 아래에서 PM 승인 내용을 다시 확인할 수 있습니다.")
            approved = st.toggle("추천 분배안을 확인했고 PM 대체 배정으로 승인합니다.", key=f"scheduled_coverage_approved_{selected['id']}")
            if st.button("PM 승인 대체 업무 저장", type="primary", disabled=not approved, key=f"scheduled_coverage_save_{selected['id']}"):
                try:
                    if assigned_rows:
                        count = db.save_version_backup_assignments(selected["project_id"], selected["phase"],
                            selected["member_id"], selected["leave_start"], selected["leave_end"], assigned_rows,
                            replace_existing=True,
                            daily_targets=[
                                {"member_id": row["member_id"],
                                 "requirements": row.get("하루 검증 요구사양서 목표", 0),
                                 "functions": row.get("하루 검증 함수 목표", 0),
                                 "tasks": row.get("하루 작성 목표", 0)}
                                for row in recommendations])
                        message = f"새 PM 승인 대체 배정 {count}건을 저장했습니다."
                    else:
                        count = db.confirm_version_backup_approval(selected["project_id"], selected["phase"],
                            selected["member_id"], selected["leave_start"], selected["leave_end"])
                        db.save_version_backup_daily_targets(selected["project_id"], selected["phase"],
                            selected["member_id"], selected["leave_start"], selected["leave_end"], [
                                {"member_id": row["member_id"],
                                 "requirements": row.get("하루 검증 요구사양서 목표", 0),
                                 "functions": row.get("하루 검증 함수 목표", 0),
                                 "tasks": row.get("하루 작성 목표", 0)}
                                for row in recommendations])
                        message = ("기존 PM 승인 대체 업무를 다시 확인했습니다. 검증 업무 기록에 반영되어 있습니다."
                                   if count else "해당 휴가 기간에 저장된 승인 배정을 찾지 못했습니다.")
                    if assigned_rows or count:
                        _activate_session_daily_targets(selected, result.get("assignments", []), recommendations)
                    st.cache_data.clear()
                    st.success(message)
                    st.rerun()
                except Exception as exc:
                    st.error(f"대체 배정을 저장하지 못했습니다: {exc}")


def render(team: TeamData, rules):
    header("🤝 대체 담당자", "휴가 기간과 실제 버전 일정이 겹치는 네 명의 단위 검증 업무만 표시합니다.")
    with page_surface("backup"):
        _render_version_leave_coverage(team)
