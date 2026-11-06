"""PM용 단위 검증 버전 일정 및 계획 화면."""
import re
from datetime import date

import pandas as pd
import streamlit as st

from data import db
from services import llm
from services.internal_projects import dashboard, is_internal_version, recommend_distribution
from services.coverage import workflow_stage
from services.models import today
from views.common import header, show_df

WORKLOG_PAGE = None


def _save_project(project, **changes):
    fields = {key: project.get(key) for key in (
        "version", "status", "spec_start", "spec_due", "verify_start", "verify_due", "total_items",
        "spec_total_items", "verify_spec_total_items", "verify_total_items", "spec_done", "verify_done",
        "spec_registered", "verify_registered", "spec_owner_id", "verify_owner_id", "spec_owner_ids",
        "verify_owner_ids", "source_info", "notes", "holidays", "daily_spec_capacity", "daily_verify_capacity",
        "spec_review_status", "verification_targets_confirmed", "verification_targets_confirmed_at", "verify_summary_status")}
    fields.update(changes)
    saved = db.save_version_project(fields, project_id=int(project["id"]))
    st.cache_data.clear()
    return saved


def _create_version_form():
    with st.expander("＋ 버전 생성", expanded=False):
        st.caption("계획은 T0800 이상 신규 버전에만 등록합니다. 이전 버전 기록은 Google Sheets 원본을 조회합니다.")
        with st.form("internal_version_create", clear_on_submit=True):
            version = st.text_input("버전명", placeholder="예: T0800")
            c = st.columns(2)
            spec_due = c[0].date_input("요구사양서 작성 마감일", value=today(), key="new_internal_spec_due")
            verify_due = c[1].date_input("동적 검증 마감일", value=today(), key="new_internal_verify_due")
            submit = st.form_submit_button("버전 생성")
        if submit:
            try:
                normalized = str(version or "").strip()
                if not normalized:
                    raise ValueError("버전명을 입력하세요.")
                match = re.fullmatch(r"T\s*(\d+)", normalized, re.IGNORECASE)
                if not match or int(match.group(1)) < 800:
                    raise ValueError("계획 등록은 T0800 이상 버전에서만 가능합니다. 과거 버전은 Google Sheets 조회 전용입니다.")
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
        holidays_text = st.text_input("이 버전의 회사 공휴일 (YYYY-MM-DD, 쉼표 구분)",
            value=", ".join(project.get("holidays", [])),
            help="등록된 날짜는 업무 가능 일수와 완료 예상일 계산에서 제외합니다.",
            key=f"internal_holidays_{project['id']}")
        if st.form_submit_button("마감일 저장"):
            try:
                holiday_values = []
                for token in holidays_text.replace("\n", ",").split(","):
                    token = token.strip()
                    if token:
                        holiday_values.append(date.fromisoformat(token).isoformat())
                _save_project(project, spec_due=spec_due, verify_due=verify_due,
                              status=status,
                              holidays=holiday_values,
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
                        st.cache_data.clear()
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


def _render_phase(project, team, phase):
    title = "요구사양서 작성" if phase == "spec" else "동적 검증"
    workloads = db.list_version_workloads(project["id"], phase)
    logs = db.list_version_worklogs(project["id"], phase)
    report = dashboard(project, phase, workloads, logs, today())
    st.subheader(f"{title} 진척도")
    deadline_name = "요구사양서 작성" if phase == "spec" else "동적 검증"
    due = report["due"]
    if due is None:
        deadline = "마감일 미등록"
    elif due < today():
        deadline = f"마감일이 지났습니다 · { (today() - due).days }일 경과"
    elif due == today():
        deadline = "오늘 마감"
    else:
        deadline = f"마감까지 { (due - today()).days }일 남았습니다"
    st.info(f"{deadline_name} {deadline}")
    totals = report["totals"]
    if phase == "spec":
        c = st.columns(3)
        c[0].metric("전체 일감", totals["total_tasks"])
        c[1].metric("완료 일감", totals["completed_tasks"])
        c[2].metric("전체 진척도", f"{totals['progress']:.0%}" if totals["total_tasks"] else "계획 데이터 없음")
        if totals["total_tasks"]:
            st.progress(min(1.0, max(0.0, totals["progress"])))
        rows = []
        for item in report["subsystems"]:
            rows.append({"서브시스템": item["subsystem"], "전체 일감": item["total"], "완료 일감": item["done"],
                         "잔여 일감": item["remaining"], "진척도": f"{item['progress']:.0%}" if item["total"] else "계획 데이터 없음", "난이도": item["difficulty"]})
        if rows:
            st.write("**서브시스템별 현황**")
            for row in rows:
                st.write(f"**{row['서브시스템']}** · 계획 {row['전체 일감']} · 완료 {row['완료 일감']} · 잔여 {row['잔여 일감']} · {row['진척도']}")
                if row["전체 일감"]:
                    progress = next(x["progress"] for x in report["subsystems"] if x["subsystem"] == row["서브시스템"])
                    st.progress(min(1.0, max(0.0, progress)))
        if report["members"]:
            st.write("**담당자별 완료 및 요구사양 변경량**")
            show_df(pd.DataFrame([{"담당자": r["담당자"], "완료 일감": r["completed_tasks"],
                "신규 요구사양": r["new_requirements"], "수정 요구사양": r["modified_requirements"],
                "삭제 요구사양": r["deleted_requirements"]} for r in report["members"]]))
    else:
        c = st.columns(4)
        c[0].metric("전체 함수", totals["total_functions"])
        c[1].metric("완료 함수", totals["completed_functions"])
        c[2].metric("함수 진척도", f"{totals['function_progress']:.0%}" if totals["total_functions"] else "계획 데이터 없음")
        c[3].metric("결함", totals["defects"])
        c = st.columns(3)
        c[0].metric("전체 요구사양서", totals["total_requirements"])
        c[1].metric("완료 요구사양서", totals["completed_requirements"])
        c[2].metric("요구사양서 진척도", f"{totals['requirement_progress']:.0%}" if totals["total_requirements"] else "계획 데이터 없음")
        if totals["total_requirements"]:
            st.progress(min(1.0, max(0.0, totals["requirement_progress"])))
        rows = []
        for item in report["subsystems"]:
            rows.append({"서브시스템": item["subsystem"], "전체 함수": item["total_functions"],
                         "완료 함수": item["completed_functions"], "잔여 함수": item["functions_remaining"],
                         "함수 진척도": f"{item['functions_progress']:.0%}", "전체 요구사양서": item["total_requirements"],
                         "완료 요구사양서": item["completed_requirements"], "잔여 요구사양서": item["requirements_remaining"],
                         "요구사양서 진척도": f"{item['requirements_progress']:.0%}" if item["total_requirements"] else "계획 데이터 없음", "난이도": item["difficulty"]})
        if rows:
            st.write("**서브시스템별 함수·요구사양서 현황**")
            for row in rows:
                st.write(f"**{row['서브시스템']}** · 요구사양서 계획 {row['전체 요구사양서']} · 완료 {row['완료 요구사양서']} · 잔여 {row['잔여 요구사양서']} · {row['요구사양서 진척도']}")
                if row["전체 요구사양서"]:
                    st.progress(min(1.0, max(0.0, report["subsystems"][[x["subsystem"] for x in report["subsystems"]].index(row["서브시스템"])]["requirements_progress"])))
                st.caption(f"함수 별도 실적: 계획 {row['전체 함수']} · 완료 {row['완료 함수']} · 잔여 {row['잔여 함수']} · 진척도 {row['함수 진척도']}")
        if report["members"]:
            st.write("**담당자별 검증 실적**")
            show_df(pd.DataFrame([{"담당자": r["담당자"], "완료 함수": r["completed_functions"],
                "완료 요구사양서": r["completed_requirements"], "결함": r["defects"], "실행 TC": r["tc_executed"],
                "생성 TC": r["tc_created"], "수정 TC": r["tc_modified"], "삭제 TC": r["tc_deleted"]}
                for r in report["members"]]))


def _render_recommendations(project, team, allow_approval=True):
    st.caption("휴가, 현재 업무량, 등록 역량 및 과거 시트·T0800 이상 내부 버전의 처리 이력을 참고합니다. 추천은 검토용이며 PM 승인 전에는 배정으로 저장하지 않습니다.")
    st.caption("단위 검증 배정 후보: 김재강 · 주해원 · 황지원 · 이혜윤")
    tabs = st.tabs(["요구사양서 작성 배분", "동적 검증 배분"])
    for tab, phase in zip(tabs, ("spec", "verify")):
        with tab:
            if phase == "verify" and workflow_stage(project)["stage"] in ("spec_plan", "spec_work"):
                st.warning("요구사양서 작성 목표가 완료된 뒤 동적 검증 배정을 시작할 수 있습니다.")
                continue
            if phase == "verify" and workflow_stage(project)["stage"] == "done":
                st.info("등록된 동적 검증 목표를 모두 완료했습니다.")
                continue
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
                    "마감 잔여일": ("마감일 미등록" if row.get("deadline_days") is None else
                                   f"{abs(row['deadline_days'])}일 경과" if row["deadline_days"] < 0 else
                                   "오늘 마감" if row["deadline_days"] == 0 else f"{row['deadline_days']}일 남음"),
                    "최근 30일 실적 기록": f"{row.get('recent_worklog_count', 0)}건 · {row.get('recent_worklog_days', 0)}활동일",
                    "해당 서브시스템 경험": f"{row.get('experience_versions', 0)}개 버전 · {row.get('experience_days', 0)}일",
                    "기존 승인 업무 부담": round(row["reserved_effort"], 1),
                    "예상 부담(난이도·수량)": round(row["effort_score"], 1),
                    "일일 예상 부담": round(row["estimated_daily_effort"], 2) if row["estimated_daily_effort"] is not None else "계산 불가",
                    "마감 가능성": row["feasibility"], "추천 근거": row["rationale"]})
            show_df(pd.DataFrame(frame_rows))
            if result["needs_more_staff"]:
                st.error("과거 처리 이력 기준으로 일부 배정이 처리 능력을 초과합니다. 추가 인력 또는 일정 조정 검토가 필요합니다.")
            elif not result["history_available"]:
                st.info("과거 시트 또는 T0800 이상 내부 기록이 쌓이면 담당자별 과거 처리량을 이용해 마감 가능성을 더 정확하게 계산합니다.")
            consent = st.checkbox("추천 요약을 LLM으로 다듬기 (버전·배정 수량·팀원명이 사내 LLM 서버로 전송됨)",
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
            if allow_approval:
                approved = st.checkbox("추천안을 확인했고 이 배정으로 확정합니다.", key=f"approve_recommend_{phase}_{project['id']}")
                if st.button("PM 승인 배정 저장", disabled=not approved, key=f"save_assignments_{phase}_{project['id']}"):
                    db.replace_version_assignments(project["id"], phase, result["rows"])
                    st.cache_data.clear()
                    st.success("PM 승인 배정을 저장했습니다.")
                    st.rerun()
                approved_rows = db.list_version_assignments(project["id"], phase)
                if approved_rows:
                    st.write("**현재 승인된 배정**")
                    show_df(pd.DataFrame([{"담당자": r["member_name"], "서브시스템": r["subsystem"],
                        "일감": r["allocated_tasks"], "함수": r["allocated_functions"],
                        "요구사양서": r["allocated_requirements"], "마감 가능성": r["feasibility"],
                        "승인 시각": r["approved_at"]} for r in approved_rows]))


def _render_pm_review_flow(project, stage):
    st.subheader("PM 검토 완료 처리")
    spec_total, spec_done = stage["spec"]
    spec_complete = spec_total > 0 and spec_done >= spec_total
    verify = stage["verify"]
    verify_targets_exist = any(total > 0 for total, _ in verify.values())
    verify_complete = verify_targets_exist and all(done >= total for total, done in verify.values())
    spec_status = project.get("spec_review_status", "대기")
    verify_status = project.get("verify_summary_status", "대기")

    spec_col, verify_col = st.columns(2)
    with spec_col:
        st.write(f"**요구사양서 PM 검토:** {spec_status}")
        if not spec_complete:
            st.caption(f"작성 실적이 완료되어야 검토할 수 있습니다 · {spec_done}/{spec_total}건")
        elif spec_status == "완료":
            st.success("요구사양서 검토가 완료됐습니다.")
        elif spec_status == "검토 중":
            if st.button("요구사양서 검토 완료 처리", key=f"spec_review_done_{project['id']}"):
                try:
                    db.set_version_workflow_status(project["id"], "spec_review_status", "완료")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"요구사양서 검토를 완료하지 못했습니다: {exc}")
        elif st.button("요구사양서 검토 시작", key=f"spec_review_start_{project['id']}"):
            try:
                db.set_version_workflow_status(project["id"], "spec_review_status", "검토 중")
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"요구사양서 검토를 시작하지 못했습니다: {exc}")

    with verify_col:
        verify_label = {"대기": "대기", "정리 중": "검토 중", "완료": "검토 완료"}.get(verify_status, verify_status)
        st.write(f"**동적 검증 PM 검토:** {verify_label}")
        if spec_status != "완료":
            st.caption("요구사양서 작성 및 PM 검토 완료 후 동적 검증 검토를 진행할 수 있습니다.")
        elif not verify_complete:
            req = verify["requirements"]
            funcs = verify["functions"]
            st.caption(f"검증 실적 완료 후 검토할 수 있습니다 · 요구사양서 {req[1]}/{req[0]}건 · 함수 {funcs[1]}/{funcs[0]}개")
        elif verify_status == "완료":
            st.success("동적 검증 검토가 완료됐습니다.")
        elif verify_status == "정리 중":
            if st.button("동적 검증 검토 완료 처리", key=f"verify_review_done_{project['id']}"):
                try:
                    db.set_version_workflow_status(project["id"], "verify_summary_status", "완료")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"동적 검증 검토를 완료하지 못했습니다: {exc}")
        elif st.button("동적 검증 검토 시작", key=f"verify_review_start_{project['id']}"):
            try:
                db.set_version_workflow_status(project["id"], "verify_summary_status", "정리 중")
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"동적 검증 검토를 시작하지 못했습니다: {exc}")


def render(team, rules=None):
    header("🗓️ (PM) 검증 일정 관리", "PM이 버전별 요구사양서 작성·동적 검증 목표와 마감일을 관리합니다. 실제 기록은 검증 업무 기록에서 입력합니다.")
    _create_version_form()
    projects = [p for p in db.list_version_projects() if is_internal_version(p["version"])]
    if not projects:
        st.info("등록된 버전이 없습니다. 새 버전을 생성하세요.")
        return
    by_id = {int(p["id"]): p for p in projects}
    selected_id = st.selectbox("버전", list(by_id), format_func=lambda pid: by_id[pid]["version"], key="internal_version_pick")
    project = db.get_version_project(selected_id)
    st.subheader("\uC5C5\uBB34 \uC9C4\uD589 \uD604\uD669")
    stage = workflow_stage(project)
    st.info(stage["message"])
    spec_total, spec_done = stage["spec"]
    verify_requirements = stage["verify"]["requirements"]
    verify_functions = stage["verify"]["functions"]
    step_cols = st.columns(3)
    step_cols[0].metric("\uC694\uAD6C\uC0AC\uC591\uC11C \uC791\uC131", f"{min(spec_done, spec_total)} / {spec_total}" if spec_total else "\uACC4\uD68D \uBBF8\uB4F1\uB85D")
    step_cols[1].metric("\uB3D9\uC801 \uAC80\uC99D \uB300\uC0C1 \uC694\uAD6C\uC0AC\uC591\uC11C", verify_requirements[0])
    step_cols[2].metric("\uB3D9\uC801 \uAC80\uC99D \uB300\uC0C1 \uD568\uC218", verify_functions[0])
    st.caption(f"요구사양서 작성 전체 일감: {project['spec_total_items']} · 동적 검증 등록 전체 요구사양서: {project['verify_spec_total_items']}")
    split_history = [h for h in db.version_project_history(project["id"])
                     if h["field"] == "verify_spec_total_items" and str(h.get("new_value", "")).startswith("0 — 기존 공용값 분리")]
    if split_history:
        st.info("이전 화면에서 공용으로 쓰던 동적 검증 사양서 수는 변경 이력에 보관했습니다. 실제 동적 검증 목표 수를 별도 입력하세요.")
    _render_pm_review_flow(project, stage)
    if WORKLOG_PAGE is not None:
        def open_worklog(version):
            st.session_state["worklog_version"] = version
            st.switch_page(WORKLOG_PAGE)
        st.button("선택 버전의 전체 업무 로그 보기 →", key=f"internal_worklog_link_{selected_id}",
                  on_click=open_worklog, args=(project["version"],))
    tabs = st.tabs(["일정 및 사전 물량", "요구사양서 작성", "동적 검증"])
    with tabs[0]:
        _schedule_and_workload(project)
    with tabs[1]:
        _render_phase(project, team, "spec")
    with tabs[2]:
        _render_phase(project, team, "verify")
