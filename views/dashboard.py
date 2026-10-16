from datetime import timedelta

import plotly.graph_objects as go
import streamlit as st

from services import risk, summary
from services.models import today
from views.common import LEVEL_COLORS, fmt_date, header, risk_table, show_df


def render(team, rules):
    t = today()
    header("📊 대시보드", f"기준일 {t} · 분석 기간: 오늘부터 {rules['horizon_days']}일 · 주말 제외")
    if len(team.members) == 0:
        st.info("등록된 팀원이 없습니다. '데이터 관리'에서 팀원을 먼저 추가하세요.")
        return
    m = summary.dashboard_metrics(team, t, rules)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("오늘의 부재 인원", f"{len(m['absent_today'])}명")
    c1.caption(", ".join(m["absent_today"]) or "부재자 없음")
    c2.metric("이번 주 예정 휴가", f"{len(m['week_leaves'])}건")
    c2.caption(", ".join(team.name(lv["member_id"]) for lv in m["week_leaves"]) or "없음")
    c3.metric("업무 공백 위험 건수", f"{m['risk_count']}건", help="High + Medium 업무 수")
    c3.caption(f"High {m['high_count']}건 · 데이터 부족 {m['insufficient_count']}건")
    c4.metric("대체 담당자 미지정 업무", f"{len(m['no_backup'])}건")
    c4.caption("완료되지 않은 업무 기준")

    if m["insufficient_count"]:
        st.warning(f"담당자·중요도 미입력 등으로 위험도를 계산하지 못한 업무가 {m['insufficient_count']}건 있습니다. "
                   "이 업무는 '안전'으로 간주하지 않습니다.")

    st.subheader("날짜별 팀 가용 인원")
    daily = risk.daily_summary(team, t, t + timedelta(days=rules["horizon_days"]), rules)
    if daily.empty:
        st.info("분석 기간에 근무일이 없습니다.")
    else:
        fig = go.Figure(go.Bar(
            x=[fmt_date(d) for d in daily["날짜"]], y=daily["가용 인원"],
            marker_color=[LEVEL_COLORS[l] for l in daily["위험도"]],
            customdata=daily[["부재", "High", "Medium", "위험도"]].values,
            hovertemplate="%{x}<br>가용 %{y}명 / 부재 %{customdata[0]}명<br>"
                          "High %{customdata[1]}건 · Medium %{customdata[2]}건<br>일 위험도: %{customdata[3]}<extra></extra>"))
        fig.add_hline(y=len(team.members), line_dash="dot", line_color="#889",
                      annotation_text=f"전체 {len(team.members)}명")
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="가용 인원(명)")
        st.plotly_chart(fig)
        st.caption("막대 색 = 해당 날짜 최고 위험도 (🔴 High · 🟠 Medium · 🟢 Low · ⚪ 데이터 부족). 반차는 0.5명 부재로 계산.")

    risks = m["risks"]
    st.subheader("위험도가 높은 업무")
    high = risks[risks["위험도"] == risk.HIGH] if not risks.empty else risks
    if high.empty:
        st.success("분석 기간에 High 위험 업무가 없습니다.")
    else:
        show_df(risk_table(high, ["업무", "담당자", "대체 담당자", "마감일", "위험도", "점수", "대표 날짜", "근거"]))

    st.subheader("위험 항목별 대응 방안")
    targets = risks[risks["위험도"].isin([risk.HIGH, risk.MEDIUM])] if not risks.empty else risks
    if targets.empty:
        st.write("대응이 필요한 위험 항목이 없습니다.")
    for _, r in targets.head(8).iterrows():
        with st.expander(f"{r['위험도']} · {r['업무']} ({r['담당자']}) · {r['점수']:.0f}점"):
            for _, text, pts in r["근거"]:
                st.write(f"- {text}" + (f" **(+{pts})**" if pts else ""))
            st.markdown("**권장 대응**")
            for a in r["대응 방안"] or ["별도 권장 조치 없음"]:
                st.write(f"- {a}")
