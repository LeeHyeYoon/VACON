"""버전별 동적검증 일정, 진행률 및 마감 업무량 대시보드."""
import pandas as pd
import streamlit as st

from data import db
from services.models import today
from services.internal_projects import is_legacy_sheet_version
from services.version_projects import aggregate_version_logs, project_summary
from views.common import header

STATUSES = ["준비", "사양서 작성 중", "검증 진행 중", "완료", "지연"]


def _options(team):
    return [int(x) for x in team.members["id"].tolist()]


def _owner_label(team, value):
    if value is None or value == []:
        return "미지정"
    if isinstance(value, list):
        return ", ".join(team.name(x) for x in value) or "미지정"
    return team.name(value)


def _date_input(label, value, key):
    return st.date_input(label, value=value, key=key)


def render(team, rules=None):
    header("🧪 버전별 동적검증", "버전마다 사양서와 검증 일정, 진행률, 담당자 휴가를 독립적으로 관리합니다.")
    projects = [p for p in db.list_version_projects() if is_legacy_sheet_version(p["version"])]
    t = today()
    logs_by_version = aggregate_version_logs(team.logs.to_dict("records") if len(team.logs) else [])
    if projects:
        st.subheader("전체 버전 현황")
        records = []
        for p in projects:
            logs = logs_by_version.get(p["version"].strip().casefold(), {})
            s = project_summary(team, p, t, {"spec": logs.get("spec_done_req", 0),
                                             "verify": logs.get("verify_done_func", 0)})
            spec_progress = (f"{s['spec']['done']}/{s['spec']['total']} ({s['spec']['progress']:.0%})"
                             if s["spec"]["registered"] and s["spec"]["total"] > 0 else
                             "전체 수 미입력" if s["spec"]["registered"] else "사양서 단계 미등록")
            records.append({"버전": p["version"], "상태": p["status"],
                            "사양서 작성 목표 수": f"{p['spec_total_items']}개",
                            "동적검증 등록 사양서 수": f"{p['verify_spec_total_items']}개",
                            "사양서 진행 개수": spec_progress,
                            "사양서 일정": "등록" if s["spec"]["registered"] else "미등록",
                            "검증 대상 진행 개수": f"{s['verify']['done']}/{s['verify']['total']} ({s['verify']['progress']:.0%})" if s["verify"]["registered"] else "미등록",
                            "시트별 완료 누계": f"요구사양서 {logs.get('spec_done_req', 0):g} · 검증 Func {logs.get('verify_done_func', 0):g}",
                            "사양서 마감": p["spec_due"] or "미설정", "검증 마감": p["verify_due"] or "미설정",
                            "위험": " / ".join(x["risk"] for x in (s["spec"], s["verify"]) if x["registered"] and x["risk"] not in ("정상", "완료")) or "정상"})
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
        st.caption("요구사양서 작성 시트의 완료 일감 로그 → 사양서 진행 개수 · 동적검증 시트 L열(완료 Func) → 검증 대상 진행 개수입니다. 동적검증 시트 I열 완료 Req와 실행 TC는 참고 활동량으로 분리합니다.")
    else:
        st.info("등록된 버전이 없습니다. 업무 로그에 적힌 버전명은 이 화면에 자동 등록되지 않습니다.")

    with st.expander("＋ 단계별 일정 등록 / 수정", expanded=not projects):
        st.caption("사양서 작성과 동적검증은 각각 별도 시트 로그와 일정으로 관리합니다. 같은 버전명을 사용해도 각 단계의 등록과 실적은 서로 합산되지 않습니다.")
        spec_tab, verify_tab = st.tabs(["사양서 작성 등록", "동적검증 등록"])
        members = _options(team)
        with spec_tab:
            spec_target = st.selectbox("사양서 단계 버전", ["＋ 새 버전", *[p["version"] for p in projects]], key="spec_phase_target")
            spec_project = next((p for p in projects if p["version"] == spec_target), None)
            spec_version = (st.text_input("새 버전명", placeholder="예: T0070", key="spec_version_input")
                            if spec_project is None else spec_project["version"])
            spec_suffix = str(spec_project["id"] if spec_project else "new")
            with st.form("spec_phase_form", clear_on_submit=True):
                c = st.columns(2)
                spec_start = c[0].date_input("사양서 작성 시작일", value=spec_project["spec_start"] if spec_project else None, key=f"spec_form_start_{spec_suffix}")
                spec_due = c[1].date_input("사양서 작성 마감일", value=spec_project["spec_due"] if spec_project else None, key=f"spec_form_due_{spec_suffix}")
                spec_total = st.number_input("전체 사양서 수 (요구사항)", min_value=0, value=int(spec_project["spec_total_items"] if spec_project else 0), step=1,
                                             help="사양서 작성 단계의 목표 수입니다. 완료 실적은 요구사양서 작성 시트 로그로 집계합니다.", key=f"spec_form_total_{spec_suffix}")
                spec_owners = st.multiselect("사양서 작성 담당자", members,
                                             default=[x for x in spec_project["spec_owner_ids"] if x in members] if spec_project else [],
                                             format_func=lambda x: _owner_label(team, x), key=f"spec_form_owners_{spec_suffix}")
                spec_cap = st.number_input("사양서 일일 최대 처리량 (0=미설정)", 0, 100000,
                                           int((spec_project or {}).get("daily_spec_capacity") or 0), key=f"spec_form_capacity_{spec_suffix}")
                if st.form_submit_button("사양서 단계 저장"):
                    try:
                        db.save_version_phase({"version": spec_version, "spec_start": spec_start, "spec_due": spec_due,
                            "spec_total_items": spec_total, "spec_owner_ids": spec_owners,
                            "daily_spec_capacity": spec_cap}, "spec")
                        st.success("사양서 작성 단계를 저장했습니다.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장하지 못했습니다: {e}")
        with verify_tab:
            verify_target = st.selectbox("동적검증 단계 버전", ["＋ 새 버전", *[p["version"] for p in projects]], key="verify_phase_target")
            verify_project = next((p for p in projects if p["version"] == verify_target), None)
            verify_version = (st.text_input("새 버전명", placeholder="예: T0070", key="verify_version_input")
                              if verify_project is None else verify_project["version"])
            verify_suffix = str(verify_project["id"] if verify_project else "new")
            with st.form("verify_phase_form", clear_on_submit=True):
                c = st.columns(2)
                verify_start = c[0].date_input("동적검증 시작일", value=verify_project["verify_start"] if verify_project else None, key=f"verify_form_start_{verify_suffix}")
                verify_due = c[1].date_input("동적검증 마감일", value=verify_project["verify_due"] if verify_project else None, key=f"verify_form_due_{verify_suffix}")
                verify_spec_total = st.number_input("동적검증 등록의 전체 사양서 개수 (요구사항)", min_value=0,
                    value=int(verify_project["verify_spec_total_items"] if verify_project else 0), step=1,
                    help="동적검증 등록에만 저장되는 별도 값입니다. '사양서 작성 등록'의 전체 사양서 수와 독립적으로 관리됩니다.",
                    key=f"verify_form_spec_total_{verify_suffix}")
                verify_total = st.number_input("전체 검증 대상 수 (서브 펑션)", min_value=0, value=int(verify_project["verify_total_items"] if verify_project else 0), step=1,
                                               help="완료 수는 연결된 시트의 완료 Func 누계로 계산합니다.", key=f"verify_form_total_{verify_suffix}")
                verify_owners = st.multiselect("동적검증 담당자", members,
                                               default=[x for x in verify_project["verify_owner_ids"] if x in members] if verify_project else [],
                                               format_func=lambda x: _owner_label(team, x), key=f"verify_form_owners_{verify_suffix}")
                if st.form_submit_button("동적검증 단계 저장"):
                    try:
                        db.save_version_phase({"version": verify_version, "verify_start": verify_start, "verify_due": verify_due,
                            "verify_spec_total_items": verify_spec_total, "verify_total_items": verify_total,
                            "verify_owner_ids": verify_owners}, "verify")
                        st.success("동적검증 단계를 저장했습니다.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장하지 못했습니다: {e}")

    projects = [p for p in db.list_version_projects() if is_legacy_sheet_version(p["version"])]
    if not projects:
        return
    project_by_id = {p["id"]: p for p in projects}
    selected_id = st.selectbox("상세 버전", list(project_by_id), format_func=lambda i: project_by_id[i]["version"])
    p = db.get_version_project(selected_id)
    log_summary = logs_by_version.get(p["version"].strip().casefold(),
                                      {"records": 0, "req_issue_logs": 0.0, "spec_done_req": 0.0,
                                       "dynamic_done_req": 0.0, "verify_done_func": 0.0, "verify_tc": 0.0})
    summary = project_summary(team, p, t, {"spec": log_summary["spec_done_req"],
                                           "verify": log_summary["verify_done_func"]})
    st.subheader(f"{p['version']} · {p['status']}")
    active_phases = [(key, title) for key, title in (("spec", "사양서 작성"), ("verify", "동적검증"))
                     if summary[key]["registered"]]
    phase_cols = st.columns(max(1, len(active_phases)))
    for col, (key, title) in zip(phase_cols, active_phases):
        v = summary[key]
        with col:
            st.markdown(f"### {title}")
            st.markdown("**사양서 진행 개수**" if key == "spec" else "**검증 대상 진행 개수**")
            st.progress(v["progress"], text=f"완료 {v['done']}/{v['total']}개 · 목표 시점 진행률 {v['target_progress']:.0%}")
            st.metric("마감까지 남은 날짜", "미설정" if v["remaining_days"] is None else f"{v['remaining_days']}일")
            st.metric("남은 수량 / 일일 권장량", f"{v['remaining']}개 / {v['daily'] if v['daily'] is not None else '계산 불가'}개")
            st.caption(f"담당자: {_owner_label(team, v['owner_ids'])} · 남은 팀 업무일: {v['available_days']:g}일 · 휴가로 감소한 가용량: {v['lost_person_days']:g}인일")
            if v["risk"] == "지연 위험":
                st.error(v["warning"] or "마감 내 완료 위험을 확인하세요.")
            elif v["risk"] == "로그 초과":
                st.warning(v["warning"])
            elif v["risk"] == "미등록":
                st.info(v["warning"])
            elif v["risk"] == "대상 수 미입력":
                st.warning(v["warning"])
            elif v["risk"] == "주의":
                st.warning("담당자 휴가로 업무 가능일이 줄었습니다.")
            elif v["risk"] == "완료":
                st.success("완료")
            if v["progress"] < v["target_progress"] and v["remaining"]:
                st.warning("목표 진행률보다 실제 진행률이 낮습니다. 우선순위 또는 지원 인력을 검토하세요.")

    st.subheader("업무 로그 누계 · 선택 버전")
    c = st.columns(5)
    c[0].metric("로그 기록", f"{log_summary['records']}건")
    c[1].metric("요구사양서 시트 완료 일감", f"{log_summary['spec_done_req']:g}")
    c[2].metric("동적검증 시트 완료 Req · 참고", f"{log_summary['dynamic_done_req']:g}")
    c[3].metric("동적검증 시트 완료 Func", f"{log_summary['verify_done_func']:g}")
    c[4].metric("실행 TC · 참고 활동량", f"{log_summary['verify_tc']:g}")
    st.caption("사양서 진행 개수는 요구사양서 작성 시트 로그, 검증 대상 진행 개수는 동적검증 시트 L열 완료 Func 로그만 집계합니다. 동적검증 시트 I열 완료 Req는 사양서 작성 실적에 합산하지 않습니다.")
    st.subheader("담당자별 로그")
    if len(team.logs):
        version_logs = team.logs[team.logs["version"].fillna("").astype(str).str.strip().str.casefold() == p["version"].strip().casefold()]
    else:
        version_logs = team.logs
    if version_logs.empty:
        st.info(f"{p['version']} 로그가 아직 없습니다. 데이터 관리 > 시트 연동에서 로그의 버전 열을 매핑하고 동기화하세요.")
    else:
        dynamic = version_logs[version_logs["track"] == "dynamic"]
        req = version_logs[version_logs["track"] == "req"]
        by_member = version_logs.assign(
            서브시스템=version_logs["subsystem"].fillna("").astype(str).str.strip().replace("", "미지정"),
            담당자=version_logs["member_id"].map(team.name)
        ).groupby(["서브시스템", "담당자", "track"], as_index=False)[["req", "func", "tc", "issue"]].sum()
        by_member["업무 유형"] = by_member["track"].map({"dynamic": "동적검증", "req": "사양서 작성"})
        st.dataframe(by_member[["서브시스템", "담당자", "업무 유형", "req", "func", "tc", "issue"]], use_container_width=True, hide_index=True)
    def open_version_logs(version):
        st.session_state["worklog_version"] = version
        st.switch_page(WORKLOG_PAGE)

    st.button("이 버전의 전체 업무 로그 열기 →", key=f"set_worklog_{selected_id}",
              on_click=open_version_logs, args=(p["version"],))

    with st.expander("공통 상태 및 비고 수정"):
        with st.form(f"version_edit_{selected_id}"):
            status = st.selectbox("프로젝트 상태", STATUSES, index=STATUSES.index(p["status"]) if p["status"] in STATUSES else 0)
            source = st.text_input("일정 출처", p["source_info"], key=f"src{selected_id}")
            notes = st.text_area("비고", p["notes"], key=f"notes{selected_id}")
            if st.form_submit_button("변경 저장"):
                try:
                    db.save_version_project({"version": p["version"], "status": status,
                        "spec_start": p["spec_start"], "spec_due": p["spec_due"],
                        "verify_start": p["verify_start"], "verify_due": p["verify_due"],
                        "spec_total_items": p["spec_total_items"], "verify_total_items": p["verify_total_items"],
                        "verify_spec_total_items": p["verify_spec_total_items"],
                        "spec_done": p["spec_done"], "verify_done": p["verify_done"],
                        "spec_registered": p["spec_registered"], "verify_registered": p["verify_registered"],
                        "spec_owner_ids": p["spec_owner_ids"], "verify_owner_ids": p["verify_owner_ids"],
                        "daily_spec_capacity": p["daily_spec_capacity"], "daily_verify_capacity": p["daily_verify_capacity"],
                        "source_info": source, "notes": notes}, project_id=selected_id)
                    st.success("저장했습니다. 일정 변경 이력을 보존했습니다.")
                    st.rerun()
                except Exception as e:
                    st.error(f"저장하지 못했습니다: {e}")
    history = db.version_project_history(selected_id)
    with st.expander(f"일정 및 정보 변경 이력 ({len(history)})"):
        if history:
            st.dataframe(pd.DataFrame([{"변경 시각": x["changed_at"], "항목": x["field"], "이전": x["old_value"], "변경": x["new_value"]} for x in history]),
                         use_container_width=True, hide_index=True)
        else:
            st.caption("변경 이력이 없습니다.")
