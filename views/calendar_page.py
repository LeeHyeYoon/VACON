import plotly.graph_objects as go
import streamlit as st

from data import db
from services import calendar as cal
from services import risk
from services.models import LEAVE_TYPES, today
from services.validation import validate_leave
from views.common import errors_box, fmt_date, fmt_leave, header, show_df


def render(team, rules):
    header("🗓️ 휴가 캘린더", "팀원별 부재 현황과 휴가 일정을 관리합니다.")
    tab_view, tab_edit = st.tabs(["일정 조회", "등록 · 수정 · 삭제"])
    with tab_view:
        _view(team, rules)
    with tab_edit:
        _edit(team)


def _view(team, rules):
    c1, c2 = st.columns([1, 2])
    mode = c1.radio("보기", ["주간", "월간"], horizontal=True)
    anchor = c2.date_input("기준 날짜", value=today())
    s, e = cal.week_range(anchor) if mode == "주간" else cal.month_range(anchor)
    st.caption(f"{s} ~ {e} (주말 제외)")
    if len(team.members) == 0:
        st.info("등록된 팀원이 없습니다.")
        return

    mat = cal.absence_matrix(team, s, e)
    z = mat.apply(lambda col: col.map(lambda v: 1 if v else 0))
    fig = go.Figure(go.Heatmap(
        z=z.values, x=[fmt_date(d) for d in mat.columns], y=list(mat.index), text=mat.values,
        texttemplate="%{text}", colorscale=[[0, "#EEF0F3"], [1, "#6E56CF"]], showscale=False,
        xgap=2, ygap=2, hovertemplate="%{y} · %{x}<br>%{text}<extra></extra>"))
    fig.update_layout(height=max(260, 36 * len(mat.index) + 90), margin=dict(l=10, r=10, t=10, b=10),
                      yaxis=dict(autorange="reversed"), xaxis=dict(side="top"))
    st.plotly_chart(fig)

    daily = risk.daily_summary(team, s, e, rules)
    if not daily.empty:
        bar = go.Figure(go.Bar(x=[fmt_date(d) for d in daily["날짜"]], y=daily["부재"], marker_color="#6E56CF"))
        bar.update_layout(height=220, margin=dict(l=10, r=10, t=30, b=10), title="날짜별 부재 인원", yaxis_title="명")
        st.plotly_chart(bar)

    st.subheader("휴가 중복 구간 (동시 2명 이상 부재)")
    ov = cal.overlap_periods(team, s, e)
    if ov:
        import pandas as pd
        show_df(pd.DataFrame(ov))
    else:
        st.success("이 기간에 2명 이상 겹치는 휴가가 없습니다.")

    st.subheader("기간 내 휴가 목록")
    rows = [{"팀원": team.name(lv["member_id"]), "유형": lv["type"], "시작": lv["start"], "종료": lv["end"],
             "메모": lv["note"]} for lv in team.leave_records() if lv["start"] <= e and s <= lv["end"]]
    if rows:
        import pandas as pd
        show_df(pd.DataFrame(rows))
    else:
        st.write("해당 기간에 등록된 휴가가 없습니다.")


def _edit(team):
    if len(team.members) == 0:
        st.info("먼저 '데이터 관리'에서 팀원을 등록하세요.")
        return
    ids = [int(i) for i in team.members["id"]]
    st.subheader("휴가 등록")
    with st.form("add_leave", clear_on_submit=True):
        c1, c2 = st.columns(2)
        member = c1.selectbox("팀원", ids, format_func=team.name)
        ltype = c2.selectbox("휴가 유형", LEAVE_TYPES)
        d1, d2 = st.columns(2)
        start = d1.date_input("시작일", value=today())
        end = d2.date_input("종료일", value=today())
        note = st.text_input("메모(선택)")
        if st.form_submit_button("등록"):
            if not errors_box(validate_leave(team, member, start, end, ltype)):
                db.add_leave(member, start, end, ltype, note)
                st.success("휴가를 등록했습니다.")
                st.rerun()

    st.subheader("휴가 수정 · 삭제")
    leaves = team.leave_records()
    if not leaves:
        st.write("등록된 휴가가 없습니다.")
        return
    by_id = {lv["id"]: lv for lv in leaves}
    lid = st.selectbox("대상 휴가", list(by_id), format_func=lambda i: fmt_leave(team, by_id[i]))
    lv = by_id[lid]
    with st.form(f"edit_leave_{lid}"):
        c1, c2 = st.columns(2)
        member = c1.selectbox("팀원", ids, index=ids.index(int(lv["member_id"])), format_func=team.name)
        ltype = c2.selectbox("휴가 유형", LEAVE_TYPES, index=LEAVE_TYPES.index(lv["type"]))
        d1, d2 = st.columns(2)
        start = d1.date_input("시작일", value=lv["start"])
        end = d2.date_input("종료일", value=lv["end"])
        note = st.text_input("메모", value=lv["note"] or "")
        if st.form_submit_button("수정 저장"):
            if not errors_box(validate_leave(team, member, start, end, ltype, exclude_id=lid)):
                db.update_leave(lid, member, start, end, ltype, note)
                st.success("수정했습니다.")
                st.rerun()
    if st.button("🗑️ 이 휴가 삭제", key=f"del_leave_{lid}"):
        db.delete_leave(lid)
        st.rerun()
