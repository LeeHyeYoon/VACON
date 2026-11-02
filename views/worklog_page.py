from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from data import db
from services import worklog as wl
from services.models import today
from services.internal_projects import is_internal_version
from views.common import fmt_date, fmt_leave, header, show_df


def _render_internal_logs(project, team):
    st.caption("이 버전은 VACON 내부 DB를 사용합니다. 요구사양서 작성과 동적 검증 기록은 별도 테이블에서 조회합니다.")
    spec_logs = db.list_version_worklogs(project["id"], "spec")
    verify_logs = db.list_version_worklogs(project["id"], "verify")
    subsystem_names = sorted({row["subsystem"] for row in [*spec_logs, *verify_logs]}, key=str.casefold)
    options = ["(전체)", *subsystem_names]
    subsystem = st.selectbox("서브시스템", options, key=f"internal_worklog_subsystem_{project['id']}")
    tabs = st.tabs(["요구사양서 작성 기록", "동적 검증 기록"])
    fields = {
        "spec": ["date", "member_name", "subsystem", "completed_tasks", "new_requirements", "modified_requirements", "deleted_requirements", "notes"],
        "verify": ["date", "member_name", "subsystem", "completed_requirements", "completed_functions", "defects", "tc_executed", "tc_created", "tc_modified", "tc_deleted", "notes"],
    }
    labels = {"date": "날짜", "member_name": "담당자", "subsystem": "서브시스템", "completed_tasks": "완료 일감",
              "new_requirements": "신규 요구사양", "modified_requirements": "수정 요구사양", "deleted_requirements": "삭제 요구사양",
              "completed_requirements": "완료 요구사양서", "completed_functions": "완료 함수", "defects": "결함",
              "tc_executed": "실행 TC", "tc_created": "생성 TC", "tc_modified": "수정 TC", "tc_deleted": "삭제 TC", "notes": "특이사항"}
    for tab, phase, rows in zip(tabs, ("spec", "verify"), (spec_logs, verify_logs)):
        with tab:
            filtered = [row for row in rows if subsystem == "(전체)" or row["subsystem"] == subsystem]
            if not filtered:
                st.info("선택한 버전·서브시스템에 등록된 업무 기록이 없습니다.")
                continue
            st.metric("기록 건수", len(filtered))
            show_df(pd.DataFrame(filtered)[fields[phase]].rename(columns=labels))
    if len(team.logs):
        legacy = team.logs[team.logs["version"].fillna("").astype(str).str.strip().str.casefold()
                           == project["version"].strip().casefold()]
        if not legacy.empty:
            with st.expander("기존 Google Sheets 자료 (내부 진척도와 별도 보관)"):
                st.warning("이 자료는 이전 연동 기록입니다. T0080 이상 내부 진척도와 합산하지 않습니다.")
                archived = legacy[[c for c in ("date", "source", "track", "member_id", "subsystem", "req", "func", "tc", "issue")
                                   if c in legacy]].copy()
                archived["담당자"] = archived["member_id"].map(team.name)
                show_df(archived)


def render(team, rules):
    header("📈 업무 로그", "버전을 선택하면 해당 버전의 전체 동적검증·요구사양서 로그와 휴가 영향을 집계합니다.")
    t = today()
    pool = team.unit_pool()
    projects = db.list_version_projects()
    versions = {str(v).strip() for v in team.logs.get("version", pd.Series(dtype=str)).dropna() if str(v).strip()}
    versions.update(p["version"] for p in projects)
    if len(team.logs) and (team.logs["version"].isna() | (team.logs["version"].astype(str).str.strip() == "")).any():
        versions.add("(버전 미입력)")
    version_options = sorted(versions,
                             key=lambda value: (value != "(버전 미입력)", wl.version_sort_key(value)),
                             reverse=True)
    if not version_options:
        st.info("버전별 로그가 없습니다. '버전별 동적검증'에서 버전을 등록하거나 '데이터 관리 > 시트 연동'에서 버전 열이 포함된 로그 탭을 연결하세요.")
        return
    preferred = st.session_state.pop("worklog_version", None)
    widget_key = "worklog_version_selector"
    if st.session_state.get(widget_key) not in version_options:
        if preferred in version_options:
            st.session_state[widget_key] = preferred
        else:
            st.session_state.pop(widget_key, None)
    c = st.columns(2)
    selected_version = c[0].selectbox("버전", version_options, key=widget_key,
                                     help="업무 로그는 선택한 버전에 한정해 집계합니다.")
    selected_project = next((p for p in projects if p["version"].casefold() == selected_version.casefold()), None)
    if is_internal_version(selected_version):
        if selected_project:
            _render_internal_logs(selected_project, team)
        else:
            st.info(f"{selected_version}은 내부 DB 관리 대상이지만 버전 등록이 없습니다. '내부 버전 업무'에서 생성하세요.")
        return
    st.caption("단위 테스트 인원: " + (", ".join(team.name(m) for m in sorted(pool)) or "없음(데이터 관리 > 팀원·역량에서 '단위테스트' 역량 지정)")
               + " · 단위 테스트 업무는 이 인원만 수행할 수 있습니다.")
    if selected_project:
        st.caption(f"버전 일정 연결: 사양서 {selected_project['spec_start'] or '미설정'} ~ {selected_project['spec_due'] or '미설정'} · "
                   f"동적검증 {selected_project['verify_start'] or '미설정'} ~ {selected_project['verify_due'] or '미설정'}")
    normalized_log_versions = team.logs["version"].fillna("").astype(str).str.strip().str.casefold()
    version_log_mask = (normalized_log_versions == selected_version.strip().casefold()
                        if selected_version != "(버전 미입력)" else
                        (normalized_log_versions == ""))
    version_logs = team.logs[version_log_mask] if len(team.logs) else team.logs
    normalized_subsystems = version_logs.get("subsystem", pd.Series(index=version_logs.index, dtype=str)).fillna("").astype(str).str.strip()
    if not version_logs.empty and not normalized_subsystems.any():
        st.caption("서브시스템 값이 아직 없습니다. 데이터 관리 > 시트 연동에서 서브시스템 열을 지정한 뒤 로그를 다시 가져오세요.")
    subsystem_values = sorted({v for v in normalized_subsystems if v}, key=str.casefold)
    subsystem_options = ["(전체)", *subsystem_values]
    if (normalized_subsystems == "").any():
        subsystem_options.append("(서브시스템 미입력)")
    selected_subsystem = c[1].selectbox("서브시스템", subsystem_options,
                                        help="해당 버전의 전체 로그 또는 서브시스템 하나만 집계합니다.")
    if selected_subsystem == "(전체)":
        scoped_logs = version_logs
        subsystem_filter = None
    elif selected_subsystem == "(서브시스템 미입력)":
        scoped_logs = version_logs[normalized_subsystems == ""]
        subsystem_filter = ""
    else:
        scoped_logs = version_logs[normalized_subsystems.str.casefold() == selected_subsystem.casefold()]
        subsystem_filter = selected_subsystem
    if not scoped_logs.empty:
        p_start, p_end = scoped_logs["date"].min(), scoped_logs["date"].max()
        st.caption(f"집계 대상: {selected_version} · 등록된 전체 로그 ({p_start} ~ {p_end})")
    else:
        p_start = p_end = t
        st.caption(f"집계 대상: {selected_version} · 등록된 전체 로그")
    if scoped_logs.empty:
        st.info(f"{selected_version}에 연결된 로그가 없습니다. 해당 버전이 표시된 CSV 로그를 동기화하면 여기에 집계됩니다.")
    else:
        src = scoped_logs.groupby("source").agg(기록=("id", "count"), 시작=("date", "min"), 종료=("date", "max")).reset_index()
        show_df(src.rename(columns={"source": "출처"}))
        if (src["source"] == "샘플").any():
            st.warning("선택한 버전에 '샘플(가상)' 로그가 포함되어 있습니다. 실제 시트를 동기화하면 샘플 로그를 삭제할 수 있습니다.")
            if st.button("샘플 로그 삭제"):
                db.delete_logs("샘플")
                st.rerun()

    st.caption(f"기록 기간 내 근무일 {len(wl.workdays(p_start, p_end))}일")
    tab1, tab2, tab3 = st.tabs(["처리량", "휴가 영향 예측", "인력 가용 현황"])

    with tab1:
        tbl = wl.throughput_table(team, p_start, p_end, rules, selected_version if selected_version != "(버전 미입력)" else "",
                                  subsystem_filter)
        for track, name in wl.TRACKS.items():
            st.subheader(name)
            sub = tbl[tbl["업무"] == name].dropna(axis=1, how="all").drop(columns="업무")
            show_df(sub)
            recs = [r for r in scoped_logs.to_dict("records") if r["track"] == track]
            if recs:
                df = pd.DataFrame(recs)
                df["팀원"] = df["member_id"].map(team.name)
                m = wl.METRIC_COLS[track][0]
                daily = df.groupby(["date", "팀원"])[m].sum().reset_index()
                fig = px.bar(daily, x="date", y=m, color="팀원", labels={"date": "", m: wl.METRIC_LABELS[m]})
                fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig)
        st.caption("일평균은 근무일(주말·종일 휴가 제외) 기준이며, 기록이 없는 근무일은 0건으로 계산합니다. 기록 자체가 없는 경우는 '-'로 표시합니다.")

    with tab2:
        leaves = sorted([lv for lv in team.leave_records() if lv["member_id"] in pool and lv["end"] >= t], key=lambda lv: lv["start"])
        if not leaves:
            st.info("오늘 이후 예정된 단위 테스트 인원의 휴가가 없습니다.")
        else:
            by_id = {lv["id"]: lv for lv in leaves}
            lid = st.selectbox("휴가 선택", list(by_id), format_func=lambda i: fmt_leave(team, by_id[i]))
            lv = by_id[lid]
            res = wl.leave_impact(team, lv["member_id"], lv["start"], lv["end"], t, rules=rules, base_start=p_start, base_end=p_end,
                                  version=selected_version if selected_version != "(버전 미입력)" else "",
                                  subsystem=subsystem_filter)
            st.write(f"**{res['name']}** 님 휴가 근무일 **{res['lost_days']}일** — **{selected_version} · {selected_subsystem}** 로그 기준 일평균으로 예상 처리 공백과 분담 시 부담을 계산합니다.")
            if res["pool_overlap_days"]:
                st.warning("단위 테스트 인원 다수가 함께 부재인 날: " + ", ".join(fmt_date(d) for d in res["pool_overlap_days"]))
            for it in res["items"]:
                with st.expander(it["label"], expanded=True):
                    if it["gap"] is None:
                        for w in it["warnings"]:
                            st.warning(w)
                        continue
                    c = st.columns(2)
                    c[0].metric("평소 일평균", f"{it['avg']:.1f}")
                    c[1].metric("휴가 중 예상 처리 공백", f"약 {it['gap']:.0f}")
                    if it["rows"]:
                        show_df(pd.DataFrame(it["rows"]).drop(columns="pct"))
                    for w in it["warnings"]:
                        st.warning(w)
                    for e in it["excluded"]:
                        st.caption("제외: " + e)
            st.caption("예측은 과거 평균에 근거한 참고값입니다. 마감 달성 가능 여부는 남은 물량 정보가 있어야 판단할 수 있습니다.")

    with tab3:
        a_rng = st.date_input("확인할 기간", value=(t, t + timedelta(days=27)), key="wl_avail_range")
        if not isinstance(a_rng, (tuple, list)) or len(a_rng) != 2:
            st.info("시작일과 종료일을 모두 선택하세요.")
            return
        av = wl.pool_availability(team, a_rng[0], a_rng[1], rules)
        if av.empty:
            st.info("표시할 근무일이 없습니다.")
        else:
            fig = px.bar(av.assign(날짜표시=[fmt_date(d) for d in av["날짜"]]), x="날짜표시", y="가용", hover_data=["부재자"],
                         labels={"날짜표시": "", "가용": "가용 인원(명)"})
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig)
            low = av[av["가용"] < av["전체"]]
            if len(low):
                show_df(low.rename(columns={"부재자": "부재 인원"}))
