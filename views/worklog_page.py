"""검증 업무 로그. 모든 원천을 읽기 전용으로 보여준다."""
from datetime import timedelta
import pandas as pd
import plotly.express as px
import streamlit as st
from data import db
from services.internal_projects import is_internal_version
from services.models import today
from services.worklog import classify_sheet_task
from views.common import header, show_df


def _sheet_task(category, fallback_phase):
    """Use Google Sheets D-column task labels to classify the work phase."""
    return classify_sheet_task(category, fallback_phase)


def _show_metric_table(frame, group_fields, metric_fields):
    table = frame.groupby(group_fields, as_index=False)[metric_fields].sum(min_count=1)
    def display(value):
        if pd.isna(value): return "—"
        try:
            return str(int(value)) if float(value).is_integer() else str(value)
        except (TypeError, ValueError):
            return str(value)
    for field in metric_fields: table[field] = table[field].map(display)
    show_df(table)


def _available_metrics(frame, candidates):
    """Hide fields that have no source values in the selected work type."""
    return [field for field in candidates if field in frame and frame[field].notna().any()]


def _records(team):
    rows = []
    imported = team.logs[team.logs["source"].astype(str) != "샘플"] if len(team.logs) else team.logs
    for r in imported.to_dict("records") if len(imported) else []:
        phase, detail = _sheet_task(r.get("category"), "req" if r.get("track") == "req" else "verify")
        req_phase = phase == "req"
        rows.append({"날짜": r.get("date"), "담당자": team.name(r.get("member_id")), "버전": str(r.get("version") or "").strip() or "(버전 미입력)",
            "서브시스템": str(r.get("subsystem") or "").strip() or "공통 업무", "업무 유형": "요구사양서 작성" if req_phase else "동적 검증",
            "업무 구분": "요구사양서 작성" if req_phase else "동적 검증",
            "세부 업무": detail, "기록 구분": "실적",
            "완료 일감": r.get("issue") if req_phase else None, "신규 요구사양": r.get("new_req") if req_phase else None,
            "수정 요구사양": r.get("modified_req") if req_phase else None, "삭제 요구사양": r.get("deleted_req") if req_phase else None,
            "완료 요구사양서": r.get("req") if not req_phase else None, "완료 함수": r.get("func"), "결함": r.get("defect"),
            "실행 TC": r.get("tc"), "생성 TC": r.get("tc_created"), "수정 TC": r.get("tc_modified"), "삭제 TC": r.get("tc_deleted"),
            "특이사항": r.get("notes") or "", "출처": r.get("source", "Google Sheets")})
    for phase in ("spec", "verify"):
        for r in db.list_all_internal_worklogs(phase):
            if not is_internal_version(r["version"]):
                continue
            task_detail = str(r.get("task_detail") or "").strip() or ("사양서 작성" if phase == "spec" else "동적 검증")
            out = {"날짜": r["date"], "담당자": r["member_name"], "버전": r["version"], "서브시스템": r["subsystem"],
                   "업무 유형": "요구사양서 작성" if phase=="spec" else "동적 검증", "완료 일감": 0, "신규 요구사양": 0, "수정 요구사양": 0, "삭제 요구사양": 0,
                   "업무 구분": "요구사양서 작성" if phase=="spec" else "동적 검증",
                   "세부 업무": task_detail, "기록 구분": "실적",
                   "완료 요구사양서": 0, "완료 함수": 0, "결함": 0, "실행 TC": 0, "생성 TC": 0, "수정 TC": 0, "삭제 TC": 0,
                   "특이사항": r.get("notes", ""), "출처": "VACON DB"}
            if phase=="spec":
                for key,field in (("완료 일감","completed_tasks"),("신규 요구사양","new_requirements"),("수정 요구사양","modified_requirements"),("삭제 요구사양","deleted_requirements")): out[key]=r[field]
            else:
                for key,field in (("완료 요구사양서","completed_requirements"),("완료 함수","completed_functions"),("결함","defects"),("실행 TC","tc_executed"),("생성 TC","tc_created"),("수정 TC","tc_modified"),("삭제 TC","tc_deleted")): out[key]=r[field]
            rows.append(out)
    for item in db.list_version_task_schedules():
        schedule_fallback = "req" if item["phase"] == "spec" else "dynamic"
        scheduled_phase, scheduled_detail = _sheet_task(item["task_detail"], schedule_fallback)
        rows.append({"날짜": item["work_date"], "담당자": item["member_name"], "버전": item["version"],
                     "서브시스템": "공통 업무", "업무 유형": "요구사양서 작성" if scheduled_phase == "req" else "동적 검증",
                     "업무 구분": "요구사양서 작성" if scheduled_phase == "req" else "동적 검증",
                     "세부 업무": scheduled_detail, "기록 구분": "예정 일정",
                     "완료 일감": None, "신규 요구사양": None, "수정 요구사양": None, "삭제 요구사양": None,
                     "완료 요구사양서": None, "완료 함수": None, "결함": None, "실행 TC": None,
                     "생성 TC": None, "수정 TC": None, "삭제 TC": None, "특이사항": "",
                     "출처": "PM 일정"})
    return pd.DataFrame(rows)


def render(team, rules=None):
    header("📈 검증 업무 로그", "과거 Google Sheets와 VACON 내부 업무 기록을 통합 조회합니다. 이 화면은 조회 전용입니다.")
    frame = _records(team)
    if frame.empty: st.info("실제 시트 또는 VACON 내부 DB 업무 기록이 없습니다. 샘플 데이터는 업무 로그에서 제외합니다. 데이터 관리의 시트 연동에서 시트 로그를 반영하세요."); return
    st.html("""
    <style>
      .st-key-worklog_phase_card,
      .st-key-worklog_filter_card,
      .st-key-worklog_version_summary,
      .st-key-worklog_results_panel {
        background: #ffffff;
        border: 1px solid #e7ebf0;
        border-radius: 10px;
        padding: 0.9rem 1.1rem;
        margin: 0.25rem 0 0.9rem;
      }
    </style>
    """)
    with st.container(border=True, key="worklog_phase_card"):
        st.markdown("#### 업무 영역 선택")
        st.caption("작성과 검증 실적은 각각 따로 조회·집계됩니다.")
        selected_phase = st.segmented_control(
            "업무 영역 선택", ["요구사양서 작성", "동적 검증"],
            selection_mode="single", default="요구사양서 작성", key="worklog_phase",
            label_visibility="collapsed", width="stretch") or "요구사양서 작성"
    frame = frame[frame["업무 유형"] == selected_phase].copy()
    experience_frame = frame[frame["기록 구분"] == "실적"].copy()
    frame["세부 업무 필터"] = frame["세부 업무"].fillna("").astype(str).str.strip().replace("", "세부 업무 미분류")
    experience_frame["세부 업무 필터"] = experience_frame["세부 업무"].fillna("").astype(str).str.strip().replace("", "세부 업무 미분류")
    versions = sorted(frame["버전"].dropna().unique(), key=lambda x: (x!="(버전 미입력)", str(x).casefold()), reverse=True)
    people = sorted(frame["담당자"].dropna().unique()); subs = sorted(frame["서브시스템"].dropna().unique())
    details = sorted(frame["세부 업무 필터"].unique())
    today_date=today(); monday=today_date-timedelta(days=today_date.weekday())
    preset = st.session_state.get("worklog_version")
    version_options = ["(전체)",*versions]
    version_index = version_options.index(preset) if preset in version_options else 0
    start=end=None
    with st.container(border=True, key="worklog_filter_card"):
        st.markdown("#### 조회 조건")
        c=st.columns(4, gap="medium"); version=c[0].selectbox("버전", version_options, index=version_index, key="worklog_version_filter"); person=c[1].selectbox("담당자", ["(전체)",*people], key="worklog_person_filter"); subsystem=c[2].selectbox("서브시스템", ["(전체)",*subs], key="worklog_subsystem_filter"); detail=c[3].selectbox("세부 업무", ["(전체)",*details], key="worklog_detail_filter")
        period=st.selectbox("기간", ["전체 기간", "이번 주", "이번 달", "지난달", "직접 기간 설정"], key="worklog_period")
        if period == "직접 기간 설정":
            rng=st.date_input("조회 기간", value=(today_date-timedelta(days=30),today_date), key="worklog_custom_range")
            if isinstance(rng,(tuple,list)) and len(rng)==2: start,end=rng
    if version!="(전체)": frame=frame[frame["버전"]==version]
    if person!="(전체)": frame=frame[frame["담당자"]==person]
    if subsystem!="(전체)": frame=frame[frame["서브시스템"]==subsystem]
    if detail!="(전체)": frame=frame[frame["세부 업무 필터"]==detail]
    if detail!="(전체)": experience_frame=experience_frame[experience_frame["세부 업무 필터"]==detail]
    if period=="이번 주": start,end=monday,today_date
    elif period=="이번 달": start,end=today_date.replace(day=1),today_date
    elif period=="지난달":
        end=today_date.replace(day=1)-timedelta(days=1); start=end.replace(day=1)
    if start and end: frame=frame[frame["날짜"].between(start,end)]
    frame=frame.sort_values("날짜",ascending=False)
    actual_frame = frame[frame["기록 구분"] == "실적"].copy()
    phase_history = experience_frame[experience_frame["출처"] != "샘플"]
    selected_version_history = phase_history
    if person != "(전체)": selected_version_history = selected_version_history[selected_version_history["담당자"] == person]
    if subsystem != "(전체)": selected_version_history = selected_version_history[selected_version_history["서브시스템"] == subsystem]
    if version != "(전체)":
        project = next((p for p in db.list_version_projects() if p["version"].casefold() == version.casefold()), None)
        if project and is_internal_version(version):
            with st.container(border=True, key="worklog_version_summary"):
                st.markdown(f"#### {version} 마감 일정")
                due_cols = st.columns(2, gap="medium")
                for col, phase_key, label in ((due_cols[0], "spec_due", "요구사양서 작성"), (due_cols[1], "verify_due", "동적 검증")):
                    due = project.get(phase_key)
                    if due is None: msg = "마감일 미등록"
                    elif due < today_date: msg = f"마감일이 지났습니다 · {(today_date-due).days}일 경과"
                    elif due == today_date: msg = "오늘 마감"
                    else: msg = f"마감까지 {(due-today_date).days}일 남았습니다"
                    col.metric(label, msg)
        elif not is_internal_version(version):
            original = _records(team).to_dict("records")
            original = [row for row in original if str(row.get("버전") or "").casefold() == version.casefold()]
            date_ranges = []
            for phase_name in ("요구사양서 작성", "동적 검증"):
                phase_dates = [row["날짜"] for row in original
                               if row.get("업무 유형") == phase_name and pd.notna(row.get("날짜"))]
                if phase_dates:
                    date_ranges.append((phase_name, min(phase_dates), max(phase_dates)))
            if date_ranges:
                st.caption("과거 버전의 업무 기간은 원본 실적 날짜 기준입니다(공식 마감일이 아닌 시작~종료 기록일).")
                cols = st.columns(len(date_ranges))
                for col, (phase_name, first_day, last_day) in zip(cols, date_ranges):
                    col.metric(phase_name, f"{first_day:%Y-%m-%d} ~ {last_day:%Y-%m-%d}")
            else:
                st.info("과거 Google Sheets 버전에는 현재 조회할 실적이 없습니다. 시트에서 다시 가져오면 업무 시작~종료 기록일을 표시합니다.")
    with st.container(border=True, key="worklog_results_panel"):
        tabs=st.tabs(["업무 현황","담당자별 현황","서브시스템별 현황","경험·숙련도 분석","전체 업무 기록"])
        with tabs[0]:
            if version != "(전체)":
                project = next((p for p in db.list_version_projects() if p["version"].casefold() == version.casefold()), None)
                if project:
                    internal_phase = "spec" if selected_phase == "요구사양서 작성" else "verify"
                    workloads = db.list_version_workloads(project["id"], internal_phase)
                    logs = db.list_version_worklogs(project["id"], internal_phase)
                    if internal_phase == "spec":
                        total = sum(int(w["total_tasks"]) for w in workloads); done = sum(int(r["completed_tasks"]) for r in logs)
                        st.subheader(f"{version} · 요구사양서 작성 계획 대비 실적")
                        c = st.columns(4); c[0].metric("전체 계획 일감", total if total else "계획 데이터 없음"); c[1].metric("완료 일감", done); c[2].metric("잔여 일감", max(0,total-done) if total else "계획 데이터 없음"); c[3].metric("전체 진척률", f"{min(1,done/total):.0%}" if total else "계획 데이터 없음")
                        if total: st.progress(min(1.0,max(0.0,done/total)))
                        for w in workloads:
                            sub_done=sum(int(r["completed_tasks"]) for r in logs if r["subsystem"]==w["subsystem"]); sub_total=int(w["total_tasks"])
                            st.write(f"**{w['subsystem']}** · 계획 {sub_total} · 완료 {sub_done} · 잔여 {max(0,sub_total-sub_done)} · {min(1,sub_done/sub_total):.0%}" if sub_total else f"**{w['subsystem']}** · 계획 데이터 없음 · 완료 {sub_done}")
                            if sub_total: st.progress(min(1.0,max(0.0,sub_done/sub_total)))
                    else:
                        total = sum(int(w["total_requirements"]) for w in workloads); done = sum(int(r["completed_requirements"]) for r in logs)
                        funcs = sum(int(w["total_functions"]) for w in workloads); func_done=sum(int(r["completed_functions"]) for r in logs)
                        st.subheader(f"{version} · 동적 검증 계획 대비 실적")
                        c = st.columns(4); c[0].metric("검증 대상 요구사양서", total if total else "계획 데이터 없음"); c[1].metric("완료 요구사양서", done); c[2].metric("잔여 요구사양서", max(0,total-done) if total else "계획 데이터 없음"); c[3].metric("검증 진척률", f"{min(1,done/total):.0%}" if total else "계획 데이터 없음")
                        if total: st.progress(min(1.0,max(0.0,done/total)))
                        st.caption(f"완료 함수는 별도 지표입니다: 계획 {funcs if funcs else '계획 데이터 없음'} · 완료 {func_done}")
                        for w in workloads:
                            sub_done=sum(int(r["completed_requirements"]) for r in logs if r["subsystem"]==w["subsystem"]); sub_total=int(w["total_requirements"])
                            st.write(f"**{w['subsystem']}** · 계획 {sub_total} · 완료 {sub_done} · 잔여 {max(0,sub_total-sub_done)} · {min(1,sub_done/sub_total):.0%}" if sub_total else f"**{w['subsystem']}** · 계획 데이터 없음 · 완료 {sub_done}")
                            if sub_total: st.progress(min(1.0,max(0.0,sub_done/sub_total)))
                else:
                    st.info("과거 Google Sheets 버전은 PM 계획 없이 실적만 조회합니다. 계획 대비 진척률 데이터는 없습니다.")
            elif version == "(전체)":
                st.info("버전을 선택하면 해당 버전의 계획 대비 실적과 마감일을 확인할 수 있습니다. 계획이 없는 과거 버전은 실제 기록과 계획 진척률을 구분해 표시합니다.")
            part=actual_frame
            candidates=( ["완료 일감", "신규 요구사양", "수정 요구사양", "삭제 요구사양"]
                        if selected_phase == "요구사양서 작성" else
                        ["완료 요구사양서", "완료 함수", "결함", "실행 TC", "생성 TC", "수정 TC", "삭제 TC"] )
            metrics = _available_metrics(part, candidates)
            if not part.empty and metrics: _show_metric_table(part, ["버전", "서브시스템", "세부 업무"], metrics)
            elif not metrics: st.info("선택한 조건에 표시할 실적 지표가 없습니다.")
        with tabs[1]:
            candidates = (["완료 일감", "신규 요구사양", "수정 요구사양", "삭제 요구사양"]
                      if selected_phase == "요구사양서 작성" else
                      ["완료 요구사양서", "완료 함수", "결함", "실행 TC", "생성 TC", "수정 TC", "삭제 TC"])
            fields = _available_metrics(actual_frame, candidates)
            if not actual_frame.empty and fields: _show_metric_table(actual_frame, ["담당자", "버전", "서브시스템", "세부 업무"], fields)
            elif not fields: st.info("선택한 조건에 표시할 실적 지표가 없습니다.")
            st.caption("건수, 담당 범위, 난이도, 결함·품질 지표는 별도 지표입니다. 단순 처리량으로 개인의 업무 능력을 판단하지 마세요.")
        with tabs[2]:
            candidates = (["완료 일감", "신규 요구사양", "수정 요구사양", "삭제 요구사양"]
                      if selected_phase == "요구사양서 작성" else
                      ["완료 요구사양서", "완료 함수", "결함", "실행 TC", "생성 TC", "수정 TC", "삭제 TC"])
            fields = _available_metrics(actual_frame, candidates)
            if not actual_frame.empty and fields: _show_metric_table(actual_frame, ["서브시스템", "담당자", "버전", "세부 업무"], fields)
            elif not fields: st.info("선택한 조건에 표시할 실적 지표가 없습니다.")
        with tabs[3]:
            history = phase_history
            if history.empty:
                st.info("실제 Google Sheets 또는 VACON 내부 DB 업무 기록이 없어 숙련도를 평가할 수 없습니다.")
            else:
                rows = []
                pairs = selected_version_history[["담당자", "서브시스템"]].drop_duplicates().itertuples(index=False, name=None)
                phase_candidates = (["완료 일감", "신규 요구사양", "수정 요구사양", "삭제 요구사양"] if selected_phase == "요구사양서 작성" else
                                    ["완료 요구사양서", "완료 함수", "결함", "실행 TC", "생성 TC", "수정 TC", "삭제 TC"])
                phase_metrics = _available_metrics(history, phase_candidates)
                for member, subsystem_name in pairs:
                    part = history[(history["담당자"] == member) & (history["서브시스템"] == subsystem_name)]
                    versions_seen = sorted({str(v) for v in part["버전"] if v and v != "(버전 미입력)"}, key=str.casefold)
                    dated = part[part["날짜"].notna()]
                    activity_days = int(dated["날짜"].nunique())
                    detail_types = sorted({str(value).strip() for value in part["세부 업무"]
                                           if str(value or "").strip() and str(value).strip() != "세부 업무 미분류"})
                    metric_breadth = sum(bool(pd.to_numeric(part[m], errors="coerce").fillna(0).gt(0).any()) for m in phase_metrics)
                    if activity_days == 0 or not versions_seen:
                        level = "평가 데이터 부족"
                        basis = f"날짜가 확인된 실제 활동일 {activity_days}일, 참여 버전 {len(versions_seen)}개"
                    elif activity_days >= 5 and len(versions_seen) >= 3 and len(detail_types) >= 2:
                        level = "반복·다양 경험"
                        basis = f"실제 활동일 {activity_days}일, 참여 버전 {len(versions_seen)}개, 세부 업무 {len(detail_types)}종, 실적 지표 {metric_breadth}종"
                    elif activity_days >= 2 or len(versions_seen) >= 2:
                        level = "반복 업무 경험"
                        basis = f"실제 활동일 {activity_days}일, 참여 버전 {len(versions_seen)}개, 세부 업무 {len(detail_types)}종, 실적 지표 {metric_breadth}종"
                    else:
                        level = "경험 기록 확인 · 반복 근거 부족"
                        basis = f"실제 활동일 {activity_days}일, 참여 버전 {len(versions_seen)}개; 반복 경험을 평가하기에는 이력이 적음"
                    def total(column):
                        values = pd.to_numeric(part[column], errors="coerce")
                        return values.sum(min_count=1)
                    row = {"담당자": member, "서브시스템": subsystem_name, "참여 버전": ", ".join(versions_seen) or "버전 미입력",
                           "참여 버전 수": len(versions_seen), "업무 기록 건수": len(part), "활동일 수": int(dated["날짜"].nunique()),
                           "세부 업무 경험": ", ".join(detail_types) or "데이터 없음", "실적 지표 수": metric_breadth,
                           "숙련도": level, "평가 근거": basis}
                    row.update({metric: total(metric) for metric in phase_metrics})
                    rows.append(row)
                report = pd.DataFrame(rows)
                if not report.empty:
                    chart = report.assign(담당_영역=report["담당자"] + " · " + report["서브시스템"])
                    fig = px.scatter(chart, x="참여 버전 수", y="담당_영역", size="활동일 수", color="숙련도",
                                     hover_data=["참여 버전", "업무 기록 건수", "평가 근거"],
                                     title=f"{selected_phase} 경험 범위 · 참여 버전과 반복 활동일")
                    fig.update_layout(xaxis_title="해당 서브시스템 참여 버전 수", yaxis_title="담당자 · 서브시스템")
                    st.plotly_chart(fig, use_container_width=True)
                metric_columns = [c for c in report if c in phase_metrics]
                for column in metric_columns:
                    report[column] = report[column].map(lambda value: "데이터 없음" if pd.isna(value) else str(int(value)) if float(value).is_integer() else str(value))
                show_df(report)
                st.caption("숙련도 표시는 선택한 세부 업무의 전체 기간 실제 활동일·참여 버전·업무 기록에 근거합니다. 한 번의 기록만 확인된 경우 반복 근거 부족으로 구분하며, 버전과 기간 필터는 이력을 자르지 않습니다. 업무량은 숙련도 근거로 사용하지 않습니다.")
        with tabs[4]:
            metric_candidates = (["완료 일감", "신규 요구사양", "수정 요구사양", "삭제 요구사양"]
                             if selected_phase == "요구사양서 작성" else
                             ["완료 요구사양서", "완료 함수", "결함", "실행 TC", "생성 TC", "수정 TC", "삭제 TC"])
            metric_fields = _available_metrics(frame, metric_candidates)
            display_fields = ["날짜", "버전", "담당자", "서브시스템", "세부 업무", "기록 구분", *metric_fields, "특이사항", "출처"]
            display = frame[display_fields].copy()
            display[metric_fields] = display[metric_fields].fillna("—")
            text_fields = [field for field in display_fields if field not in metric_fields]
            display[text_fields] = display[text_fields].fillna("")
            show_df(display.astype(str))
