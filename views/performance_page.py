"""검증 업무 기록: 버전별 요구사양서 작성·동적 검증 실적."""
import hashlib
import pandas as pd
import streamlit as st
from data import db
from services.internal_projects import is_internal_version, recommend_subsystem
from services.coverage import workflow_stage
from services.models import today
from views.common import header, show_df
from views.internal_work_page import _render_recommendations
SPEC = [("completed_tasks", "완료 일감 개수"), ("new_requirements", "신규 요구사양 개수"), ("modified_requirements", "수정 요구사양 개수"), ("deleted_requirements", "삭제 요구사양 개수")]
VERIFY = [("completed_requirements", "완료 요구사양서 개수"), ("completed_functions", "완료 함수 개수"), ("defects", "결함 개수"), ("tc_executed", "실행 TC 개수"), ("tc_created", "생성 TC 개수"), ("tc_modified", "수정 TC 개수"), ("tc_deleted", "삭제 TC 개수")]


def _team_signature(team):
    digest = hashlib.sha256()
    for frame in (team.members, team.skills, team.leaves, team.logs):
        digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


@st.cache_data(ttl=20, show_spinner=False)
def _cached_recommendation(project_id, phase, member_id, work_date, project_signature, team_signature, _team):
    project = db.get_version_project(project_id)
    return recommend_subsystem(project, _team, phase, member_id, work_date)


def _select_recommended_subsystem(state_key, subsystem):
    st.session_state[state_key] = subsystem


def _phase(project, team, phase):
    workloads = db.list_version_workloads(project["id"], phase); records = db.list_version_worklogs(project["id"], phase)
    if not workloads:
        st.info("이 버전에 등록된 서브시스템이 없습니다. (PM) 검증 일정 관리에서 먼저 계획을 등록하세요."); return
    if phase == "verify":
        workflow = workflow_stage(db.get_version_project(project["id"]))
        if workflow["stage"] != "verify_work":
            if workflow["stage"] in ("spec_plan", "spec_work"):
                next_step = "요구사양서 작성 목표를 모두 완료하면 동적 검증 실적 입력이 열립니다."
            elif workflow["stage"] == "verify_plan":
                next_step = "(PM) 검증 일정 관리에서 서브시스템별 동적 검증 목표를 등록하세요."
            else:
                next_step = "현재 등록된 동적 검증 목표는 모두 완료되었습니다."
            st.info(workflow["message"] + " · " + next_step)
            return
    action = st.segmented_control(
        "기록 작업", ["신규 기록", "기록 수정/삭제"], selection_mode="single",
        default="신규 기록", key=f"perf_action_{phase}_{project['id']}", width="content")
    selected = None
    if action == "기록 수정/삭제":
        if not records: st.info("수정할 기록이 없습니다."); return
        selected = st.selectbox("업무 기록", records, format_func=lambda x: f"#{x['id']} · {x['date']} · {x['member_name']} · {x['subsystem']}", key=f"perf_pick_{phase}_{project['id']}")
    members = team.unit_members()
    if not members: st.info("단위 검증 담당자 김재강·주해원·황지원·이혜윤을 팀원 데이터에 등록하세요."); return
    member_ids = [int(m["id"]) for m in members]; workloads_by_name = {w["subsystem"]: w for w in workloads}; defaults = selected or {}
    default_mid = int(defaults.get("member_id", member_ids[0])); default_sub = defaults.get("subsystem", workloads[0]["subsystem"]); phase_fields = SPEC if phase == "spec" else VERIFY; suffix = str(defaults.get("id", "new"))
    entry_date = defaults.get("date", today())
    if isinstance(entry_date, str):
        from datetime import date
        entry_date = date.fromisoformat(entry_date)
    entry_date = st.date_input("업무 수행 날짜", value=entry_date, key=f"perf_date_{phase}_{project['id']}_{suffix}")
    if default_mid in member_ids:
        member_id = st.selectbox("담당자", member_ids, index=member_ids.index(default_mid), format_func=team.name,
                                 key=f"perf_member_{phase}_{project['id']}_{suffix}")
    elif selected:
        # Preserve historical ownership while keeping the selectable roster restricted.
        member_id = default_mid
        st.text_input("기존 기록 담당자", value=selected["member_name"], disabled=True,
                      key=f"perf_legacy_member_{phase}_{project['id']}_{suffix}")
        st.caption("이전 기록의 담당자는 보존됩니다. 새 단위 검증 업무는 지정된 네 명에게만 기록할 수 있습니다.")
    else:
        st.error("단위 검증 기록 담당자는 김재강·주해원·황지원·이혜윤 중에서 선택해야 합니다.")
        return
    subsystem_key = f"perf_subsystem_{phase}_{project['id']}_{suffix}"
    assignments = db.list_version_assignments(project["id"], phase)
    project_signature = hashlib.sha256(repr((workloads, records, assignments)).encode("utf-8")).hexdigest()
    recommendation = _cached_recommendation(project["id"], phase, member_id, entry_date,
        project_signature, _team_signature(team), team)
    st.markdown("#### AI 서브시스템 추천")
    st.caption("추천은 업무 유형별 경험, 계획 진척도와 잔량, 마감일, 현재 업무량·부재, 최근 서브시스템 배정의 연속성 및 작성·검증 담당자 분리를 함께 고려합니다. 실적 건수는 업무량 신호이며 실력 점수가 아닙니다. 추천 결과는 참고용이며 PM이 최종 배정을 검토·승인합니다.")
    if recommendation["subsystem"]:
        if recommendation["overloaded"]:
            st.warning(f"{team.name(member_id)}님의 현재 업무 부담이 높습니다. 해당 담당자에게 추가 업무를 추천하지 않습니다.")
        top = recommendation["basis"][0]
        if recommendation["overloaded"]:
            st.warning(f"우선 지원 서브시스템: {top['subsystem']} · 상태: {top['status']}")
        else:
            st.success(f"추천 서브시스템: {top['subsystem']} · 상태: {top['status']}")
        st.write(recommendation["reason"])
        if recommendation.get("separation_exception"):
            st.warning("작성·검증을 분리할 수 있는 적합 담당자가 확인되지 않았습니다. 동일 담당자 배정은 PM 예외 검토가 필요합니다.")
        col = st.columns(6)
        col[0].metric("선택 날짜", entry_date.isoformat())
        col[1].metric("현재 업무량", f"{recommendation['workload_pct']}%")
        col[2].metric("최근 30일 작성 기록", f"{recommendation['recent_spec_records']}건")
        col[3].metric("최근 30일 검증 기록", f"{recommendation['recent_verify_records']}건")
        col[4].metric("현재 버전 같은 날짜 기록", f"{recommendation['same_day_records']}건")
        col[5].metric("다른 버전 승인 업무 부담", f"{recommendation['cross_version_effort']:.1f}점")
        if top["experience"]["versions"]:
            exp = top["experience"]
            volume = (f"완료 일감 {exp['spec_done']:g}개" if phase == "spec" else
                      f"완료 요구사양서 {exp['verify_req_done']:g}개 · 완료 함수 {exp['verify_func_done']:g}개")
            st.caption(f"과거 경험: {exp['versions']}개 버전 · {exp['days']}활동일 · 기록 {exp['records']}건 · {volume}")
        else:
            st.caption("해당 담당자의 과거 이 유형·서브시스템 경험: 데이터 없음")
        if top["support_people"]:
            support = top["support_people"][0]
            st.caption(f"대안 지원 담당자: {support['name']} · {support['basis']} · 업무량 {support['workload_pct']}%")
        for index, item in enumerate(recommendation["basis"][:3]):
            with st.container(border=True):
                st.markdown(f"**{item['priority']} · {item['subsystem']}**　`{item['status']}`")
                detail = st.columns(4)
                detail[0].metric("진척률", f"{item['progress']:.0%}")
                detail[1].metric("완료 / 잔여", f"{item['completed']} / {item['remaining']}")
                detail[2].metric("현재 버전 기록/배정 인원", item["staff_count"])
                detail[3].metric("마감까지", "미등록" if item["days_to_due"] is None else f"{abs(item['days_to_due'])}일 경과" if item["days_to_due"] < 0 else "오늘" if item["days_to_due"] == 0 else f"{item['days_to_due']}일")
                exp = item["experience"]
                historical_volume = (f"완료 일감 {exp['spec_done']:g}개" if phase == "spec" else
                                     f"완료 요구사양서 {exp['verify_req_done']:g}개 · 함수 {exp['verify_func_done']:g}개")
                st.caption(f"선택 담당자의 경험: {exp['versions']}개 버전 · {exp['days']}활동일 · {historical_volume}; 현재 업무 부담 {recommendation['workload_pct']}%; "
                           f"최근 해당 유형 기록 {recommendation['recent_phase_records']}건; 계획 대비 실적 {item['completed']}/{item['total']}")
                rotation = item["rotation"]
                recent_versions = ", ".join(rotation["recent_versions"]) if rotation["recent_versions"] else "없음"
                cross_phase_versions = ", ".join(rotation["cross_phase_versions"]) if rotation["cross_phase_versions"] else "없음"
                st.caption(f"서브시스템 배정 이력: 최근 3개 버전 중 {recent_versions}; 연속 담당 {rotation['consecutive_versions']}개 버전; 작성·검증 모두 수행한 과거 버전 {cross_phase_versions}")
                flags = []
                if item["rotation_recommended"]:
                    flags.append("업무 순환 권장")
                if item["separation_needed"]:
                    flags.append("작성·검증 담당자 분리 필요")
                if item["separation_exception"]:
                    flags.append("분리 대안 없음 · PM 예외 검토")
                if flags:
                    st.warning(" · ".join(flags))
                elif item["counterpart_names"]:
                    st.caption("반대 업무 담당자: " + ", ".join(item["counterpart_names"]))
                support_people = item["support_people"]
                if support_people:
                    st.caption("분리·경험·업무량·부재를 고려한 대안 담당자: " + ", ".join(f"{x['name']} ({x['basis']}, 최근 해당 서브시스템 {len(x['recent_subsystem_versions'])}/3개 버전, 연속 {x['consecutive_subsystem_versions']}개 버전, 업무량 {x['workload_pct']}%)" for x in support_people[:2]))
                st.caption(item["reason"])
                if recommendation["overloaded"]:
                    st.caption("과부하 상태여서 이 담당자에게 추가 배정을 추천하지 않습니다.")
                else:
                    st.button(f"{item['subsystem']} 선택", key=f"perf_choose_{phase}_{project['id']}_{suffix}_{index}",
                              on_click=_select_recommended_subsystem, args=(subsystem_key, item["subsystem"]))
    else:
        st.info(recommendation["reason"])
    subsystem = st.selectbox("실적에 저장할 서브시스템", list(workloads_by_name),
        index=list(workloads_by_name).index(default_sub) if default_sub in workloads_by_name else 0,
        key=subsystem_key)
    with st.form(f"perf_form_{phase}_{project['id']}_{suffix}"):
        st.caption(f"선택: {entry_date} · {team.name(member_id)} · {subsystem}")
        task_detail = st.selectbox("세부 업무", db.ACTUAL_TASK_DETAILS[phase],
                                   index=(db.ACTUAL_TASK_DETAILS[phase].index(defaults["task_detail"])
                                          if defaults.get("task_detail") in db.ACTUAL_TASK_DETAILS[phase] else 0),
                                   key=f"perf_detail_{phase}_{project['id']}_{suffix}")
        fields = {"date": entry_date, "member_id": member_id, "subsystem": subsystem,
                  "task_detail": task_detail}; cols = st.columns(2)
        for i, (metric, label) in enumerate(phase_fields): fields[metric] = cols[i % 2].number_input(label, min_value=0, value=int(defaults.get(metric, 0) or 0), step=1, key=f"perf_{metric}_{phase}_{project['id']}_{suffix}")
        fields["notes"] = st.text_area("특이사항", value=defaults.get("notes", ""), key=f"perf_notes_{phase}_{project['id']}_{suffix}")
        if st.form_submit_button("업무 실적 수정" if selected else "업무 실적 저장"):
            db.save_version_worklog(project["id"], phase, fields, log_id=selected["id"] if selected else None); st.cache_data.clear(); st.success("업무 실적을 저장했습니다."); st.rerun()
    if selected and st.button("선택한 업무 실적 삭제", type="tertiary", key=f"perf_delete_{phase}_{project['id']}_{selected['id']}"):
        db.delete_version_worklog(project["id"], phase, selected["id"]); st.cache_data.clear(); st.success("업무 실적을 삭제했습니다."); st.rerun()
    if records:
        cols = ["date", "member_name", "subsystem", "task_detail", *[k for k, _ in phase_fields], "notes", "created_at", "updated_at"]
        history = pd.DataFrame(records)[cols].rename(columns={"date": "업무 수행 날짜", "member_name": "담당자", "subsystem": "서브시스템", "task_detail": "세부 업무", "notes": "특이사항", "created_at": "등록 시각", "updated_at": "수정 시각"})
        show_df(history)
def render(team, rules=None):
    header("✍️ 검증 업무 기록", "완료한 업무를 날짜별로 기록합니다. 요구사양서 작성과 동적 검증은 별도로 저장됩니다.")
    st.caption("단위 검증 담당자: 김재강 · 주해원 · 황지원 · 이혜윤")
    projects = [p for p in db.list_version_projects() if is_internal_version(p["version"])]
    if not projects: st.info("먼저 (PM) 검증 일정 관리에서 버전을 등록하세요."); return
    by_id = {int(p["id"]): p for p in projects}; project_id = st.selectbox("버전", list(by_id), format_func=lambda x: by_id[x]["version"], key="performance_version"); project = by_id[project_id]
    with st.expander("담당자별 업무·서브시스템 추천", expanded=False):
        st.caption("과거 버전의 해당 업무·서브시스템 기록, 최근 30일 작성·검증 실적, 현재 계획·승인 배정, 등록된 업무량, 마감일과 부재를 반영한 검토용 추천입니다. 추천은 실적이나 배정으로 저장되지 않습니다.")
        _render_recommendations(project, team, allow_approval=False)
    with st.container(border=True, key="performance_work_record_shell"):
        st.markdown("### 업무 실적 등록")
        spec_tab, verify_tab = st.tabs(["요구사양서 작성 실적", "동적 검증 실적"])
        with spec_tab: _phase(project, team, "spec")
        with verify_tab: _phase(project, team, "verify")
