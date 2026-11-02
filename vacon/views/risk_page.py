from datetime import timedelta

import plotly.express as px
import streamlit as st

from services import risk
from services.models import today
from views.common import LEVEL_COLORS, fmt_date, header, risk_table, show_df


def render(team, rules):
    header("⚠️ 위험 분석", "휴가 기간의 업무 공백 위험을 날짜별·업무별로 분석하고 판단 근거를 보여줍니다.")
    t = today()
    rng = st.date_input("분석 기간", value=(t, t + timedelta(days=rules["horizon_days"] - 1)))
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.info("시작일과 종료일을 모두 선택하세요.")
        return
    s, e = rng
    if len(team.tasks) == 0:
        st.info("등록된 업무가 없습니다. '데이터 관리'에서 업무를 추가하세요.")
        return

    with st.expander("현재 위험도 판단 규칙 (데이터 관리 > 위험 규칙에서 변경)"):
        st.json(rules)

    tab_task, tab_day = st.tabs(["업무별", "날짜별"])
    with tab_task:
        df = risk.assess_tasks(team, s, e, rules)
        show_df(risk_table(df, ["업무", "담당자", "대체 담당자", "중요도", "마감일", "위험도", "점수", "대표 날짜", "부재일수"]))
        st.caption("대표 날짜 = 기간 중 점수가 가장 높은 날. 위험도 '데이터 부족'은 담당자/중요도 미입력 등으로 계산 불가한 업무입니다.")
        if not df.empty:
            pick = st.selectbox("상세 근거를 볼 업무", list(df["task_id"]),
                                format_func=lambda i: df[df["task_id"] == i].iloc[0]["업무"])
            r = df[df["task_id"] == pick].iloc[0]
            st.markdown(f"**{r['업무']}** — {r['위험도']} ({'-' if r['점수'] != r['점수'] else int(r['점수'])}점, 대표 날짜 {r['대표 날짜']})")
            for _, text, pts in r["근거"]:
                st.write(f"- {text}" + (f" **(+{pts})**" if pts else ""))
            st.markdown("**권장 대응**")
            for a in r["대응 방안"] or ["별도 권장 조치 없음"]:
                st.write(f"- {a}")
    with tab_day:
        daily = risk.daily_summary(team, s, e, rules)
        if daily.empty:
            st.info("선택한 기간에 근무일이 없습니다.")
            return
        fig = px.bar(daily.assign(날짜표시=[fmt_date(d) for d in daily["날짜"]]), x="날짜표시", y=["High", "Medium"],
                     color_discrete_map={"High": LEVEL_COLORS[risk.HIGH], "Medium": LEVEL_COLORS[risk.MEDIUM]},
                     labels={"value": "위험 업무 수", "날짜표시": "", "variable": "등급"})
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig)
        day = st.selectbox("날짜 선택", list(daily["날짜"]), format_func=fmt_date)
        row = daily[daily["날짜"] == day].iloc[0]
        st.write(f"부재 {int(row['부재'])}명 · 가용 {row['가용 인원']:.1f}명 · 일 위험도 **{row['위험도']}**")
        show_df(risk_table(risk.tasks_on_day(team, day, rules), ["업무", "담당자", "위험도", "점수", "근거"]))
