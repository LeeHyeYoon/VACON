"""검증 업무 로그. 모든 원천을 읽기 전용으로 보여준다."""
from datetime import timedelta
import pandas as pd
import plotly.express as px
import streamlit as st
from data import db
from services.internal_projects import is_internal_version
from services.models import today
from views.common import header, show_df


def _show_metric_table(frame, group_fields, metric_fields):
    table = frame.groupby(group_fields, as_index=False)[metric_fields].sum(min_count=1)
    def display(value):
        if pd.isna(value): return "데이터 없음"
        try:
            return str(int(value)) if float(value).is_integer() else str(value)
        except (TypeError, ValueError):
            return str(value)
    for field in metric_fields: table[field] = table[field].map(display)
    show_df(table)


def _records(team):
    rows = []
    imported = team.logs[team.logs["source"].astype(str) != "샘플"] if len(team.logs) else team.logs
    for r in imported.to_dict("records") if len(imported) else []:
        req_phase = r.get("track") == "req"
        rows.append({"날짜": r.get("date"), "담당자": team.name(r.get("member_id")), "버전": str(r.get("version") or "").strip() or "(버전 미입력)",
            "서브시스템": str(r.get("subsystem") or "").strip() or "서브시스템 미입력", "업무 유형": "요구사양서 작성" if req_phase else "동적 검증",
            "완료 일감": r.get("issue") if req_phase else None, "신규 요구사양": r.get("new_req") if req_phase else None,
            "수정 요구사양": r.get("modified_req") if req_phase else None, "삭제 요구사양": r.get("deleted_req") if req_phase else None,
            "완료 요구사양서": r.get("req") if not req_phase else None, "완료 함수": r.get("func"), "결함": r.get("defect"),
            "실행 TC": r.get("tc"), "생성 TC": r.get("tc_created"), "수정 TC": r.get("tc_modified"), "삭제 TC": r.get("tc_deleted"),
            "특이사항": r.get("notes") or "", "출처": r.get("source", "Google Sheets")})
    for phase in ("spec", "verify"):
        for r in db.list_all_internal_worklogs(phase):
            if not is_internal_version(r["version"]):
                continue
            out = {"날짜": r["date"], "담당자": r["member_name"], "버전": r["version"], "서브시스템": r["subsystem"],
                   "업무 유형": "요구사양서 작성" if phase=="spec" else "동적 검증", "완료 일감": 0, "신규 요구사양": 0, "수정 요구사양": 0, "삭제 요구사양": 0,
                   "완료 요구사양서": 0, "완료 함수": 0, "결함": 0, "실행 TC": 0, "생성 TC": 0, "수정 TC": 0, "삭제 TC": 0,
                   "특이사항": r.get("notes", ""), "출처": "VACON DB"}
            if phase=="spec":
                for key,field in (("완료 일감","completed_tasks"),("신규 요구사양","new_requirements"),("수정 요구사양","modified_requirements"),("삭제 요구사양","deleted_requirements")): out[key]=r[field]
            else:
                for key,field in (("완료 요구사양서","completed_requirements"),("완료 함수","completed_functions"),("결함","defects"),("실행 TC","tc_executed"),("생성 TC","tc_created"),("수정 TC","tc_modified"),("삭제 TC","tc_deleted")): out[key]=r[field]
            rows.append(out)
    return pd.DataFrame(rows)


def render(team, rules=None):
    header("📈 검증 업무 로그", "과거 Google Sheets와 VACON 내부 업무 기록을 통합 조회합니다. 이 화면은 조회 전용입니다.")
    frame = _records(team)
    if frame.empty: st.info("실제 시트 또는 VACON 내부 DB 업무 기록이 없습니다. 샘플 데이터는 업무 로그에서 제외합니다. 데이터 관리의 시트 연동에서 시트 로그를 반영하세요."); return
    st.markdown("""
    <style>
    div[data-testid="stSegmentedControl"] { background: rgba(38, 92, 87, .08); border: 1px solid rgba(38, 92, 87, .18); padding: 5px; border-radius: 15px; }
    div[data-testid="stSegmentedControl"] button { min-height: 44px; border-radius: 11px; font-weight: 650; transition: all .16s ease; }
    div[data-testid="stSegmentedControl"] button[aria-pressed="true"] { background: linear-gradient(135deg, #397c72, #24534f); color: white; box-shadow: 0 3px 10px rgba(31, 75, 70, .22); }
    </style>
    """, unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown("**업무 유형**")
        st.caption("확인할 업무 영역을 선택하세요. 작성과 검증 기록은 각각 따로 집계됩니다.")
        selected_phase = st.segmented_control(
            "업무 유형 선택", ["요구사양서 작성", "동적 검증"],
            format_func=lambda value: "📝  요구사양서 작성" if value == "요구사양서 작성" else "🧪  동적 검증",
            selection_mode="single", default="요구사양서 작성", key="worklog_phase",
            label_visibility="collapsed", width="stretch") or "요구사양서 작성"
    frame = frame[frame["업무 유형"] == selected_phase].copy()
    phase_history = frame.copy()
    versions = sorted(frame["버전"].dropna().unique(), key=lambda x: (x!="(버전 미입력)", str(x).casefold()), reverse=True)
    people = sorted(frame["담당자"].dropna().unique()); subs = sorted(frame["서브시스템"].dropna().unique())
    today_date=today(); monday=today_date-timedelta(days=today_date.weekday())
    preset = st.session_state.get("worklog_version")
    version_options = ["(전체)",*versions]
    version_index = version_options.index(preset) if preset in version_options else 0
    c=st.columns(3); version=c[0].selectbox("버전", version_options, index=version_index, key="worklog_version_filter"); person=c[1].selectbox("담당자", ["(전체)",*people], key="worklog_person_filter"); subsystem=c[2].selectbox("서브시스템", ["(전체)",*subs], key="worklog_subsystem_filter")
    if version!="(전체)": frame=frame[frame["버전"]==version]
    if person!="(전체)": frame=frame[frame["담당자"]==person]
    if subsystem!="(전체)": frame=frame[frame["서브시스템"]==subsystem]
    period=st.selectbox("기간", ["전체 기간", "이번 주", "이번 달", "지난달", "직접 기간 설정"], key="worklog_period")
    start=end=None
    if period=="이번 주": start,end=monday,today_date
    elif period=="이번 달": start,end=today_date.replace(day=1),today_date
    elif period=="지난달":
        end=today_date.replace(day=1)-timedelta(days=1); start=end.replace(day=1)
    elif period=="직접 기간 설정":
        rng=st.date_input("조회 기간", value=(today_date-timedelta(days=30),today_date), key="worklog_custom_range")
        if isinstance(rng,(tuple,list)) and len(rng)==2: start,end=rng
    if start and end: frame=frame[frame["날짜"].between(start,end)]
    frame=frame.sort_values("날짜",ascending=False)
    phase_history = phase_history[phase_history["출처"] != "샘플"]
    selected_version_history = phase_history if version == "(전체)" else phase_history[phase_history["버전"] == version]
    if person != "(전체)": selected_version_history = selected_version_history[selected_version_history["담당자"] == person]
    if subsystem != "(전체)": selected_version_history = selected_version_history[selected_version_history["서브시스템"] == subsystem]
    if version != "(전체)":
        project = next((p for p in db.list_version_projects() if p["version"].casefold() == version.casefold()), None)
        if project and is_internal_version(version):
            st.subheader(f"{version} 마감 일정")
            due_cols = st.columns(2)
            for col, phase_key, label in ((due_cols[0], "spec_due", "요구사양서 작성"), (due_cols[1], "verify_due", "동적 검증")):
                due = project.get(phase_key)
                if due is None: msg = "마감일 미등록"
                elif due < today_date: msg = f"마감일이 지났습니다 · {(today_date-due).days}일 경과"
                elif due == today_date: msg = "오늘 마감"
                else: msg = f"마감까지 {(due-today_date).days}일 남았습니다"
                col.metric(label, msg)
        elif not is_internal_version(version):
            st.info("과거 Google Sheets 버전에는 VACON 계획 마감일이 없어 계획 대비 진척률을 산출하지 않습니다.")
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
        part=frame
        metrics=["완료 일감","신규 요구사양","수정 요구사양","삭제 요구사양"] if selected_phase=="요구사양서 작성" else ["완료 요구사양서","완료 함수","결함","실행 TC","생성 TC","수정 TC","삭제 TC"]
        if not part.empty: _show_metric_table(part, ["버전", "서브시스템"], metrics)
    with tabs[1]:
        if not frame.empty: _show_metric_table(frame, ["담당자","버전","서브시스템","업무 유형"], ["완료 일감","신규 요구사양","수정 요구사양","삭제 요구사양","완료 요구사양서","완료 함수","결함","실행 TC","생성 TC","수정 TC","삭제 TC"])
        st.caption("건수, 담당 범위, 난이도, 결함·품질 지표는 별도 지표입니다. 단순 처리량으로 개인의 업무 능력을 판단하지 마세요.")
    with tabs[2]:
        if not frame.empty: _show_metric_table(frame, ["서브시스템","담당자","버전","업무 유형"], ["완료 일감","신규 요구사양","수정 요구사양","삭제 요구사양","완료 요구사양서","완료 함수","결함","실행 TC","생성 TC","수정 TC","삭제 TC"])
    with tabs[3]:
        history = phase_history
        if history.empty:
            st.info("실제 Google Sheets 또는 VACON 내부 DB 업무 기록이 없어 숙련도를 평가할 수 없습니다.")
        else:
            rows = []
            pairs = selected_version_history[["담당자", "서브시스템"]].drop_duplicates().itertuples(index=False, name=None)
            phase_metrics = (["완료 일감", "신규 요구사양", "수정 요구사양", "삭제 요구사양"] if selected_phase == "요구사양서 작성" else
                             ["완료 요구사양서", "완료 함수", "결함", "실행 TC", "생성 TC", "수정 TC", "삭제 TC"])
            for member, subsystem_name in pairs:
                part = history[(history["담당자"] == member) & (history["서브시스템"] == subsystem_name)]
                versions_seen = sorted({str(v) for v in part["버전"] if v and v != "(버전 미입력)"}, key=str.casefold)
                dated = part[part["날짜"].notna()]
                metric_breadth = sum(bool(pd.to_numeric(part[m], errors="coerce").fillna(0).gt(0).any()) for m in phase_metrics)
                if len(dated) < 3 or len(versions_seen) < 2:
                    level = "평가 데이터 부족"
                    basis = f"날짜 있는 기록 {len(dated)}건, 참여 버전 {len(versions_seen)}개; 숙련도 최소 기준(기록 3건·버전 2개)에 미달"
                elif len(dated) >= 8 and len(versions_seen) >= 3 and metric_breadth >= 2:
                    level = "반복·다양 경험"
                    basis = f"{selected_phase} 날짜 있는 기록 {len(dated)}건, 참여 버전 {len(versions_seen)}개, 활동일 {dated['날짜'].nunique()}일, 실적 유형 {metric_breadth}종"
                else:
                    level = "업무 경험 확인"
                    basis = f"{selected_phase} 날짜 있는 기록 {len(dated)}건, 참여 버전 {len(versions_seen)}개, 활동일 {dated['날짜'].nunique()}일, 실적 유형 {metric_breadth}종"
                def total(column):
                    values = pd.to_numeric(part[column], errors="coerce")
                    return values.sum(min_count=1)
                row = {"담당자": member, "서브시스템": subsystem_name, "참여 버전": ", ".join(versions_seen) or "버전 미입력",
                       "참여 버전 수": len(versions_seen), "업무 기록 건수": len(part), "활동일 수": int(dated["날짜"].nunique()),
                       "실적 유형 수": metric_breadth,
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
            st.caption(f"숙련도는 선택한 업무 유형의 실제 기록·활동일·참여 버전·실적 항목 다양성으로 분류한 경험 참고값이며 점수는 없습니다. 업무 건수나 결함 수만으로 능력을 판단하지 않습니다. 기준: 날짜 있는 기록 3건 또는 참여 버전 2개 미만은 평가 데이터 부족; 기록 8건 이상·버전 3개 이상·실적 유형 2종 이상이면 반복·다양 경험으로 표시합니다.")
    with tabs[4]:
        show_df(frame.fillna("데이터 없음").astype(str))
