from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from data import db
from services import worklog as wl
from services.models import today
from views.common import fmt_date, fmt_leave, header, show_df


def render(team, rules):
    header("📈 업무 로그", "단위 테스트 인원(동적 검증 · 요구사양서 작성)의 처리량과, 휴가로 인한 처리 공백을 분석합니다.")
    t = today()
    pool = team.unit_pool()
    st.caption("단위 테스트 인원: " + (", ".join(team.name(m) for m in sorted(pool)) or "없음(데이터 관리 > 팀원·역량에서 '단위테스트' 역량 지정)")
               + " · 단위 테스트 업무는 이 인원만 수행할 수 있습니다.")
    if len(team.logs) == 0:
        st.info("연동된 업무 로그가 없습니다. '데이터 관리 > 시트 연동'에서 2. Dynamic_Verification / 3. Req_Creation_WBS 탭을 연결하세요.")
        return
    src = team.logs.groupby("source").agg(기록=("id", "count"), 시작=("date", "min"), 종료=("date", "max")).reset_index()
    show_df(src.rename(columns={"source": "출처"}))
    if (src["source"] == "샘플").any():
        st.warning("현재 '샘플(가상)' 로그가 포함되어 있습니다. 실제 시트를 동기화하면 삭제할 수 있습니다.")
        if st.button("샘플 로그 삭제"):
            db.delete_logs("샘플")
            st.rerun()

    logs_min, logs_max = min(team.logs["date"]), max(team.logs["date"])
    if "wl_range" not in st.session_state:
        # Historical sheet logs often belong to older versions. Start with the
        # complete imported range so a recent-activity default cannot hide them.
        st.session_state["wl_range"] = (logs_min, logs_max)

    def _set(a, b):
        st.session_state["wl_range"] = (a, b)

    st.write("**집계 기간**")
    c = st.columns([3, 1, 1, 1, 1])
    rng = c[0].date_input("시작일 ~ 종료일", key="wl_range", help="기간을 직접 지정하세요. 오른쪽 버튼으로 자주 쓰는 기간을 빠르게 채울 수 있습니다.")
    c[1].button("이번 달", on_click=_set, args=(t.replace(day=1), t))
    c[2].button("지난 달", on_click=_set, args=((t.replace(day=1) - timedelta(days=1)).replace(day=1), t.replace(day=1) - timedelta(days=1)))
    c[3].button("이번 주", on_click=_set, args=(t - timedelta(days=t.weekday()), t))
    c[4].button("전체 기록", on_click=_set, args=(logs_min, logs_max))
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.info("시작일과 종료일을 모두 선택하세요.")
        return
    p_start, p_end = rng
    version_values = sorted({str(v).strip() for v in team.logs["version"].dropna()
                             if str(v).strip()}, key=wl.version_sort_key, reverse=True)
    selected_version = st.selectbox("버전", ["(전체)", *version_values], key="wl_version")
    version_filter = None if selected_version == "(전체)" else selected_version
    if not wl.workdays(p_start, p_end):
        st.warning("선택한 기간에 근무일(월~금)이 없습니다.")
        return
    st.caption(f"조회 대상: {selected_version} · 기록 기간 {logs_min} ~ {logs_max} · 선택 기간 근무일 {len(wl.workdays(p_start, p_end))}일")
    tab1, tab2, tab3 = st.tabs(["처리량", "휴가 영향 예측", "인력 가용 현황"])

    with tab1:
        tbl = wl.throughput_table(team, p_start, p_end, rules, version_filter)
        for track, name in wl.TRACKS.items():
            st.subheader(name)
            sub = tbl[tbl["업무"] == name].dropna(axis=1, how="all").drop(columns="업무")
            show_df(sub)
            recs = [r for r in team.logs.to_dict("records") if r["track"] == track and p_start <= r["date"] <= p_end
                    and (version_filter is None or str(r.get("version") or "").strip().casefold() == version_filter.casefold())]
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
                                  version=version_filter)
            st.write(f"**{res['name']}** 님 휴가 근무일 **{res['lost_days']}일** — **{selected_version} · {p_start} ~ {p_end}**의 일평균을 기준으로 예상 처리 공백과 분담 시 부담을 계산합니다.")
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
