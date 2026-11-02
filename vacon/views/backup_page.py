import pandas as pd
import plotly.express as px
import streamlit as st

from data import db
from services.models import TeamData, isnull, today
from services.recommend import recommend_backups
from views.common import header, show_df


def render(team: TeamData, rules):
    header("🤝 대체 담당자", "추천은 검토용 참고 정보이며, '지정' 버튼을 눌러야만 반영됩니다.")
    tasks = [t for t in team.task_records() if t.get("status") != "완료" and not isnull(t.get("owner_id"))]
    if not tasks:
        st.info("담당자가 지정된 진행 중 업무가 없습니다.")
        return
    by_id = {t["id"]: t for t in tasks}
    tid = st.selectbox("대상 업무", list(by_id), format_func=lambda i: f"{by_id[i]['name']} ({team.name(by_id[i]['owner_id'])})")
    task = by_id[tid]

    owner_leaves = [lv for lv in team.leave_records() if lv["member_id"] == task["owner_id"] and lv["end"] >= today()]
    default = (owner_leaves[0]["start"], owner_leaves[0]["end"]) if owner_leaves else (today(), today())
    st.caption("기간 기본값: 담당자의 가장 가까운 휴가" if owner_leaves else "담당자의 예정 휴가가 없어 오늘로 설정했습니다. 기간을 직접 선택하세요.")
    rng = st.date_input("대체가 필요한 기간", value=default, key=f"bk_range_{tid}")
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.info("시작일과 종료일을 모두 선택하세요.")
        return
    res = recommend_backups(team, tid, rng[0], rng[1], rules)

    cur = team.name(task.get("backup_id")) if not isnull(task.get("backup_id")) else "미지정"
    st.write(f"**현재 대체 담당자:** {cur} · **필수 역량:** {task.get('required_skill') or '미입력'} · **마감일:** {task.get('due_date') or '미입력'}")
    for w in res["warnings"]:
        st.warning(w)

    cands = res["candidates"]
    if not cands:
        st.error("조건을 만족하는 후보가 없습니다. 아래 제외 사유를 확인하고 일정·역량 배분을 조정하세요.")
    else:
        fig = px.bar(pd.DataFrame(cands), x="추천 점수", y="이름", orientation="h", range_x=[0, 100], text="추천 점수")
        fig.update_layout(height=60 + 48 * len(cands), margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig)
        show_df(pd.DataFrame([{"이름": c["이름"], "직무": c["직무"], "추천 점수": c["추천 점수"],
                               "예상 업무 부담": c["예상 업무 부담"],
                               "주의사항": "; ".join(c["주의사항"]) or "없음"} for c in cands]))
        for c in cands:
            with st.expander(f"{c['이름']} · {c['추천 점수']}점" + (" · 현재 지정" if c["현재 지정"] else "")):
                st.markdown("**추천 이유**")
                for r in c["추천 이유"]:
                    st.write(f"- {r}")
                st.markdown(f"**예상 업무 부담:** {c['예상 업무 부담']}")
                st.markdown("**주의사항**")
                for r in c["주의사항"] or ["특이사항 없음"]:
                    st.write(f"- {r}")
                if not c["현재 지정"] and st.button(f"{c['이름']}님을 대체 담당자로 지정", key=f"assign_{tid}_{c['member_id']}"):
                    db.set_backup(tid, c["member_id"])
                    st.success("지정했습니다.")
                    st.rerun()
        st.caption("점수 = 역량 수준(최대 45) + 기간 내 근무 가능(25) + 여유 업무량(최대 30) ± 가감점. 참고용이며 최종 결정은 담당자가 합니다.")
    if res["excluded"]:
        with st.expander(f"제외된 인원 {len(res['excluded'])}명 (필수 역량 없음 / 기간 중 부재)"):
            show_df(pd.DataFrame(res["excluded"]))
