"""T0080+ 버전 업무의 VACON 내부 등록·기록·진척도·승인 배정 화면."""
from datetime import date

import pandas as pd
import streamlit as st

from data import db
from services import llm
from services.internal_projects import dashboard, internal_projects, is_internal_version, recommend_distribution
from services.models import today
from views.common import header, show_df

WORKLOG_PAGE = None


SPEC_FIELDS = [
    ("completed_tasks", "완료 일감 개수"), ("new_requirements", "신규 요구사양 개수"),
    ("modified_requirements", "수정 요구사양 개수"), ("deleted_requirements", "삭제 요구사양 개수"),
]
VERIFY_FIELDS = [
    ("completed_requirements", "완료 요구사양서 개수"), ("completed_functions", "완료 함수 개수"),
    ("defects", "결함 개수"), ("tc_executed", "실행 TC 개수"), ("tc_created", "생성 TC 개수"),
    ("tc_modified", "수정 TC 개수"), ("tc_deleted", "삭제 TC 개수"),
]


def _save_project(project, **changes):
    fields = {key: project.get(key) for key in (
        "version", "status", "spec_start", "spec_due", "verify_start", "verify_due", "total_items",
        "spec_total_items", "verify_spec_total_items", "verify_total_items", "spec_done", "verify_done",
        "spec_registered", "verify_registered", "spec_owner_id", "verify_owner_id", "spec_owner_ids",
        "verify_owner_ids", "source_info", "notes", "holidays", "daily_spec_capacity", "daily_verify_capacity")}
    fields.update(changes)
    return db.save_version_project(fields, project_id=int(project["id"]))


def _create_version_form():
    with st.expander("＋ VACON 내부 버전 생성", expanded=False):
        st.caption("T0080 이상은 이후의 사전 물량·업무 기록을 Google Sheets와 분리해 VACON 내부 DB에 저장합니다.")
        with st.form("internal_version_create", clear_on_submit=True):
            version = st.text_input("버전명", placeholder="예: T0080")
            c = st.columns(2)
            spec_due = c[0].date_input("요구사양서 작성 마감일", value=today(), key="new_internal_spec_due")
            verify_due = c[1].date_input("동적 검증 마감일", value=today(), key="new_internal_verify_due")
            submit = st.form_submit_button("버전 생성")
        if submit:
            try:
                normalized = str(version or "").strip()
                if not normalized:
                    raise ValueError("버전명을 입력하세요.")
                if not is_internal_version(normalized):
                    raise ValueError("T0070 이하는 기존 Google Sheets 연동 경로를 사용합니다. 내부 버전은 T0080 이상으로 입력하세요.")
                if any(p["version"].casefold() == normalized.casefold() for p in db.list_version_projects()):
                    raise ValueError("같은 버전이 이미 등록되어 있습니다.")
                db.save_version_project({
                    "version": normalized, "status": "준비", "spec_start": None, "spec_due": spec_due,
                    "verify_start": None, "verify_due": verify_due,
                    "spec_total_items": 0, "verify_spec_total_items": 0, "verify_total_items": 0,
                    "spec_registered": True, "verify_registered": True,
                    "spec_owner_ids": [], "verify_owner_ids": [], "source_info": "VACON 내부 등록",
                })
                st.success(f"{normalized} 버전을 생성했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"버전을 생성하지 못했습니다: {exc}")


def _schedule_and_workload(project):
    st.subheader("버전 마감일")
    with st.form(f"internal_dates_{project['id']}"):
        c = st.columns(3)
        spec_due = c[0].date_input("요구사양서 작성 마감일", value=project["spec_due"], key=f"internal_spec_due_{project['id']}")
        verify_due = c[1].date_input("동적 검증 마감일", value=project["verify_due"], key=f"internal_verify_due_{project['id']}")
        statuses = ["준비", "사양서 작성 중", "검증 진행 중", "완료", "지연"]
        status = c[2].selectbox("버전 상태", statuses,
            index=statuses.index(project["status"]) if project["status"] in statuses else 0,
            key=f"internal_status_{project['id']}")
        if st.form_submit_button("마감일 저장"):
            try:
                _save_project(project, spec_due=spec_due, verify_due=verify_due,
                              status=status,
                              spec_registered=bool(project["spec_registered"] or spec_due),
                              verify_registered=bool(project["verify_registered"] or verify_due))
                st.success("버전 마감일을 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"마감일을 저장하지 못했습니다: {exc}")

    phase_tabs = st.tabs(["요구사양서 작성 물량", "동적 검증 물량"])
    for tab, phase in zip(phase_tabs, ("spec", "verify")):
        with tab:
            if phase == "verify":
                with st.form(f"verify_spec_total_form_{project['id']}"):
                    verify_spec_total = st.number_input("동적 검증 등록의 전체 요구사양서 수", min_value=0,
                        value=int(project.get("verify_spec_total_items", 0) or 0), step=1,
                        help="요구사양서 작성 등록의 전체 일감 수와 별도 관리하는 참고 규모입니다. 함수·서브시스템별 검증 진척도는 아래 개별 목표량으로 계산합니다.",
                        key=f"internal_verify_spec_total_{project['id']}")
                    if st.form_submit_button("동적 검증 요구사양서 수 저장"):
                        _save_project(project, verify_spec_total_items=verify_spec_total)
                        st.success("동적 검증의 전체 요구사양서 수를 별도로 저장했습니다.")
                        st.rerun()
            workloads = db.list_version_workloads(project["id"], phase)
            existing = [w["subsystem"] for w in workloads]
            mode = st.radio("서브시스템 설정", ["새 서브시스템 등록", "기존 서브시스템 수정"],
                            horizontal=True, key=f"workload_mode_{phase}_{project['id']}")
            selected = None
            if mode == "기존 서브시스템 수정":
                if not workloads:
                    st.info("수정할 서브시스템 물량이 없습니다. 새 서브시스템을 등록하세요.")
                    continue
                selected = st.selectbox("수정할 서브시스템", workloads, format_func=lambda w: w["subsystem"],
                                        key=f"workload_pick_{phase}_{project['id']}")
            with st.form(f"workload_form_{phase}_{project['id']}", clear_on_submit=mode == "새 서브시스템 등록"):
                subsystem = st.text_input("서브시스템명", value=selected["subsystem"] if selected else "",
                                          placeholder="예: Audio", key=f"workload_name_{phase}_{project['id']}")
                if phase == "spec":
                    total_tasks = st.number_input("전체 일감 수 (소프트웨어 로직 변경서)", min_value=0,
                        value=int(selected["total_tasks"] if selected else 0), step=1,
                        key=f"spec_tasks_{project['id']}_{selected['id'] if selected else 'new'}")
                    total_functions = total_requirements = 0
                else:
                    c = st.columns(2)
                    total_functions = c[0].number_input("검증 대상 함수 수", min_value=0,
                        value=int(selected["total_functions"] if selected else 0), step=1,
                        key=f"verify_functions_{project['id']}_{selected['id'] if selected else 'new'}")
                    total_requirements = c[1].number_input("검증 대상 요구사양서 수", min_value=0,
                        value=int(selected["total_requirements"] if selected else 0), step=1,
                        key=f"verify_requirements_{project['id']}_{selected['id'] if selected else 'new'}")
                    total_tasks = 0
                difficulty = st.select_slider("업무 난이도", options=[1, 2, 3, 4, 5],
                    value=int(selected["difficulty"] if selected else 3), key=f"difficulty_{phase}_{project['id']}_{selected['id'] if selected else 'new'}",
                    help="PM이 조정합니다. 추천 결과의 예상 부담 점수에 반영됩니다.")
                if st.form_submit_button("물량 저장"):
                    try:
                        db.save_version_workload(project["id"], phase, subsystem, total_tasks=total_tasks,
                            total_functions=total_functions, total_requirements=total_requirements, difficulty=difficulty)
                        st.success("서브시스템 물량을 저장했습니다.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"물량을 저장하지 못했습니다: {exc}")
            workloads = db.list_version_workloads(project["id"], phase)
            if workloads:
                if phase == "spec":
                    frame = [{"서브시스템": w["subsystem"], "전체 일감": w["total_tasks"], "난이도": w["difficulty"]} for w in workloads]
                else:
                    frame = [{"서브시스템": w["subsystem"], "전체 함수": w["total_functions"],
                              "전체 요구사양서": w["total_requirements"], "난이도": w["difficulty"]} for w in workloads]
                show_df(pd.DataFrame(frame))


def _work_metrics(phase):
    return SPEC_FIELDS if phase == "spec" else VERIFY_FIELDS


def _render_log_form(project, team, phase, workloads):
    logs = db.list_version_worklogs(project["id"], phase)
    if not workloads:
        st.info("먼저 '일정/물량' 탭에서 서브시스템별 목표 물량을 등록하세요.")
        return
    members = team.member_records()
    if not members:
        st.info("업무 기록을 위해 데이터 관리에서 담당자를 먼저 등록하세요.")
        return
    action = st.radio("기록 작업", ["신규 기록", "기록 수정/삭제"], horizontal=True,
                      key=f"log_action_{phase}_{project['id']}")
    selected_log = None
    if action == "기록 수정/삭제":
        if not logs:
            st.info("수정할 기록이 없습니다.")
            return
        selected_log = st.selectbox("업무 기록", logs,
            format_func=lambda x: f"#{x['id']} · {x['date']} · {x['member_name']} · {x['subsystem']}",
            key=f"log_pick_{phase}_{project['id']}")
    field_map = {key: label for key, label in _work_metrics(phase)}
    workload_by_name = {w["subsystem"]: w for w in workloads}
    member_ids = [int(m["id"]) for m in members]
    selected_member = int(selected_log["member_id"]) if selected_log else member_ids[0]
    selected_subsystem = selected_log["subsystem"] if selected_log else workloads[0]["subsystem"]
    with st.form(f"log_form_{phase}_{project['id']}_{selected_log['id'] if selected_log else 'new'}"):
        c = st.columns(3)
        entry_date = c[0].date_input("날짜", value=selected_log["date"] if selected_log else today(),
                                     key=f"log_date_{phase}_{project['id']}_{selected_log['id'] if selected_log else 'new'}")
        member_id = c[1].selectbox("담당자", member_ids, index=member_ids.index(selected_member),
            format_func=lambda mid: team.name(mid), key=f"log_member_{phase}_{project['id']}_{selected_log['id'] if selected_log else 'new'}")
        subsystem = c[2].selectbox("서브시스템", list(workload_by_name),
            index=list(workload_by_name).index(selected_subsystem), key=f"log_subsystem_{phase}_{project['id']}_{selected_log['id'] if selected_log else 'new'}")
        fields = {"date": entry_date, "member_id": member_id, "subsystem": subsystem}
        cols = st.columns(2)
        for i, (metric, label) in enumerate(field_map.items()):
            fields[metric] = cols[i % 2].number_input(label, min_value=0,
                value=int(selected_log.get(metric, 0) if selected_log else 0), step=1,
                key=f"metric_{phase}_{metric}_{project['id']}_{selected_log['id'] if selected_log else 'new'}")
        fields["notes"] = st.text_area("특이사항", value=selected_log["notes"] if selected_log else "",
            key=f"log_notes_{phase}_{project['id']}_{selected_log['id'] if selected_log else 'new'}")
        button_label = "업무 기록 수정" if selected_log else "업무 기록 저장"
        if st.form_submit_button(button_label):
            try:
                db.save_version_worklog(project["id"], phase, fields, log_id=selected_log["id"] if selected_log else None)
                st.success("업무 기록을 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"업무 기록을 저장하지 못했습니다: {exc}")
    if selected_log and st.button("선택한 업무 기록 삭제", key=f"log_delete_{phase}_{project['id']}_{selected_log['id']}"):
        if db.delete_version_worklog(project["id"], phase, selected_log["id"]):
            st.success("업무 기록을 삭제했습니다.")
            st.rerun()


def _render_phase(project, team, phase):
    title = "요구사양서 작성" if phase == "spec" else "동적 검증"
    workloads = db.list_version_workloads(project["id"], phase)
    logs = db.list_version_worklogs(project["id"], phase)
    report = dashboard(project, phase, workloads, logs, today())
    st.subheader(f"{title} 진척도")
    deadline_name = "요구사양서 작성" if phase == "spec" else "동적 검증"
    st.metric(f"{deadline_name} 마감까지", "미등록" if report["remaining_days"] is None else f"{report['remaining_days']}일")
    totals = report["totals"]
    if phase == "spec":
        c = st.columns(3)
        c[0].metric("전체 일감", totals["total_tasks"])
        c[1].metric("완료 일감", totals["completed_tasks"])
        c[2].metric("전체 진척도", f"{totals['progress']:.0%}")
        rows = []
        for item in report["subsystems"]:
            rows.append({"서브시스템": item["subsystem"], "전체 일감": item["total"], "완료 일감": item["done"],
                         "잔여 일감": item["remaining"], "진척도": f"{item['progress']:.0%}", "난이도": item["difficulty"]})
        if rows:
            st.write("**서브시스템별 현황**")
            show_df(pd.DataFrame(rows))
        if report["members"]:
            st.write("**담당자별 완료 및 요구사양 변경량**")
            show_df(pd.DataFrame([{"담당자": r["담당자"], "완료 일감": r["completed_tasks"],
                "신규 요구사양": r["new_requirements"], "수정 요구사양": r["modified_requirements"],
                "삭제 요구사양": r["deleted_requirements"]} for r in report["members"]]))
    else:
        c = st.columns(4)
        c[0].metric("전체 함수", totals["total_functions"])
        c[1].metric("완료 함수", totals["completed_functions"])
        c[2].metric("함수 진척도", f"{totals['function_progress']:.0%}")
        c[3].metric("결함", totals["defects"])
        c = st.columns(3)
        c[0].metric("전체 요구사양서", totals["total_requirements"])
        c[1].metric("완료 요구사양서", totals["completed_requirements"])
        c[2].metric("요구사양서 진척도", f"{totals['requirement_progress']:.0%}")
        rows = []
        for item in report["subsystems"]:
            rows.append({"서브시스템": item["subsystem"], "전체 함수": item["total_functions"],
                         "완료 함수": item["completed_functions"], "잔여 함수": item["functions_remaining"],
                         "함수 진척도": f"{item['functions_progress']:.0%}", "전체 요구사양서": item["total_requirements"],
                         "완료 요구사양서": item["completed_requirements"], "잔여 요구사양서": item["requirements_remaining"],
                         "요구사양서 진척도": f"{item['requirements_progress']:.0%}", "난이도": item["difficulty"]})
        if rows:
            st.write("**서브시스템별 함수·요구사양서 현황**")
            show_df(pd.DataFrame(rows))
        if report["members"]:
            st.write("**담당자별 검증 실적**")
            show_df(pd.DataFrame([{"담당자": r["담당자"], "완료 함수": r["completed_functions"],
                "완료 요구사양서": r["completed_requirements"], "결함": r["defects"], "실행 TC": r["tc_executed"],
                "생성 TC": r["tc_created"], "수정 TC": r["tc_modified"], "삭제 TC": r["tc_deleted"]}
                for r in report["members"]]))
    _render_log_form(project, team, phase, workloads)
    logs = db.list_version_worklogs(project["id"], phase)
    if logs:
        st.write("**등록된 업무 기록**")
        if phase == "spec":
            cols = ["date", "member_name", "subsystem", "completed_tasks", "new_requirements",
                    "modified_requirements", "deleted_requirements", "notes"]
        else:
            cols = ["date", "member_name", "subsystem", "completed_requirements", "completed_functions", "defects",
                    "tc_executed", "tc_created", "tc_modified", "tc_deleted", "notes"]
        show_df(pd.DataFrame(logs)[cols].rename(columns={"date": "날짜", "member_name": "담당자", "subsystem": "서브시스템", "notes": "특이사항"}))


def _render_recommendations(project, team):
    st.caption("휴가, 현재 업무량, 등록 역량 및 T0070 이하 시트·T0080 이상 내부 버전의 과거 처리 이력을 참고합니다. 추천은 검토용이며 PM 승인 전에는 배정으로 저장하지 않습니다.")
    tabs = st.tabs(["요구사양서 작성 배분", "동적 검증 배분"])
    for tab, phase in zip(tabs, ("spec", "verify")):
        with tab:
            workloads = db.list_version_workloads(project["id"], phase)
            if not workloads:
                st.info("먼저 일정/물량 탭에서 서브시스템별 물량을 등록하세요.")
                continue
            result = recommend_distribution(team, project, phase, workloads, today())
            if result["warnings"]:
                for warning in result["warnings"]:
                    st.warning(warning)
            if not result["rows"]:
                continue
            frame_rows = []
            for row in result["rows"]:
                frame_rows.append({"담당자": row["member_name"], "서브시스템": row["subsystem"],
                    "배정 일감": row["tasks"] if phase == "spec" else "-",
                    "배정 함수": row["functions"] if phase == "verify" else "-",
                    "배정 요구사양서": row["requirements"] if phase == "verify" else "-",
                    "현재 업무량(%)": row["load"], "남은 가용일": row["available_days"],
                    "기존 승인 업무 부담": round(row["reserved_effort"], 1),
                    "예상 부담(난이도·수량)": round(row["effort_score"], 1),
                    "일일 예상 부담": round(row["estimated_daily_effort"], 2) if row["estimated_daily_effort"] is not None else "계산 불가",
                    "마감 가능성": row["feasibility"], "추천 근거": row["rationale"]})
            show_df(pd.DataFrame(frame_rows))
            if result["needs_more_staff"]:
                st.error("과거 처리 이력 기준으로 일부 배정이 처리 능력을 초과합니다. 추가 인력 또는 일정 조정 검토가 필요합니다.")
            elif not result["history_available"]:
                st.info("T0070 이하 시트 또는 T0080 이상 내부 기록이 쌓이면 담당자별 과거 처리량을 이용해 마감 가능성을 더 정확하게 계산합니다.")
            consent = st.checkbox("추천 요약을 LLM으로 다듬기 (버전·배정 수량·팀원명이 Anthropic API로 전송됨)",
                                  key=f"llm_recommend_consent_{phase}_{project['id']}")
            if consent and st.button("AI 설명 만들기", key=f"llm_recommend_{phase}_{project['id']}"):
                if not llm.llm_available():
                    st.info("LLM을 사용할 수 없어 VACON의 근거 기반 추천 문장을 표시합니다.")
                else:
                    facts = "\n".join(f"{r['member_name']} / {r['subsystem']}: 일감 {r['tasks']}, 함수 {r['functions']}, "
                                      f"요구사양서 {r['requirements']}, 가능성 {r['feasibility']}, 근거 {r['rationale']}"
                                      for r in result["rows"])
                    answer = "추천 결과는 화면의 배정표와 근거를 따릅니다. 과거 처리 이력이 부족한 경우 완료 가능성을 확정하지 않습니다."
                    explanation = llm.rephrase("이 버전의 담당자별 업무 분배 추천 근거를 설명해줘.", answer,
                                               f"버전: {project['version']}\n마감: {result['due']}\n추천 데이터:\n{facts}")
                    st.write(explanation or answer)
            approved = st.checkbox("추천안을 확인했고 이 배정으로 확정합니다.", key=f"approve_recommend_{phase}_{project['id']}")
            if st.button("PM 승인 배정 저장", disabled=not approved, key=f"save_assignments_{phase}_{project['id']}"):
                db.replace_version_assignments(project["id"], phase, result["rows"])
                st.success("PM 승인 배정을 저장했습니다.")
                st.rerun()
            approved_rows = db.list_version_assignments(project["id"], phase)
            if approved_rows:
                st.write("**현재 승인된 배정**")
                show_df(pd.DataFrame([{"담당자": r["member_name"], "서브시스템": r["subsystem"],
                    "일감": r["allocated_tasks"], "함수": r["allocated_functions"],
                    "요구사양서": r["allocated_requirements"], "마감 가능성": r["feasibility"],
                    "승인 시각": r["approved_at"]} for r in approved_rows]))


def render(team, rules=None):
    header("🧭 내부 버전 업무 관리", "T0080 이상은 VACON 내부 DB에서 업무량과 실적을 버전·서브시스템별로 관리합니다.")
    _create_version_form()
    projects = internal_projects()
    if not projects:
        st.info("내부 관리 버전이 없습니다. T0080 이상 버전을 생성하세요. T0070 이하는 기존 시트 연동 화면에 남아 있습니다.")
        return
    by_id = {int(p["id"]): p for p in projects}
    selected_id = st.selectbox("버전", list(by_id), format_func=lambda pid: by_id[pid]["version"], key="internal_version_pick")
    project = db.get_version_project(selected_id)
    st.subheader(f"{project['version']} · 내부 DB")
    st.caption("요구사양서 작성 일감과 동적 검증 함수·요구사양서 목표 및 실적은 서로 다른 테이블에서 관리됩니다.")
    st.caption(f"요구사양서 작성 전체 일감: {project['spec_total_items']} · 동적 검증 등록 전체 요구사양서: {project['verify_spec_total_items']}")
    split_history = [h for h in db.version_project_history(project["id"])
                     if h["field"] == "verify_spec_total_items" and str(h.get("new_value", "")).startswith("0 — 기존 공용값 분리")]
    if split_history:
        st.info("이전 화면에서 공용으로 쓰던 동적 검증 사양서 수는 변경 이력에 보관했습니다. 실제 동적 검증 목표 수를 별도 입력하세요.")
    if WORKLOG_PAGE is not None:
        def open_worklog(version):
            st.session_state["worklog_version"] = version
            st.switch_page(WORKLOG_PAGE)
        st.button("선택 버전의 전체 업무 로그 보기 →", key=f"internal_worklog_link_{selected_id}",
                  on_click=open_worklog, args=(project["version"],))
    tabs = st.tabs(["일정 및 사전 물량", "요구사양서 작성", "동적 검증", "AI 업무 분배"])
    with tabs[0]:
        _schedule_and_workload(project)
    with tabs[1]:
        _render_phase(project, team, "spec")
    with tabs[2]:
        _render_phase(project, team, "verify")
    with tabs[3]:
        _render_recommendations(project, team)
