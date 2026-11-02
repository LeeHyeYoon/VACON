import streamlit as st

from services.handover import generate_handover
from services.models import today
from views.common import fmt_leave, header


def render(team, rules):
    header("📝 인수인계", "선택한 휴가와 담당 업무로 인수인계 문서를 생성합니다. 데이터가 없는 항목은 '미입력'으로 표시됩니다.")
    leaves = team.leave_records()
    if not leaves:
        st.info("등록된 휴가가 없습니다. '휴가 캘린더'에서 먼저 등록하세요.")
        return
    leaves = sorted(leaves, key=lambda lv: (lv["end"] < today(), lv["start"]))
    by_id = {lv["id"]: lv for lv in leaves}
    lid = st.selectbox("휴가 선택", list(by_id), format_func=lambda i: fmt_leave(team, by_id[i]))
    lv = by_id[lid]
    md = generate_handover(team, lv["member_id"], lv["start"], lv["end"], lv["type"], today())
    tab_prev, tab_src = st.tabs(["미리보기", "원문(Markdown)"])
    with tab_prev:
        st.markdown(md)
    with tab_src:
        md = st.text_area("편집 후 다운로드할 수 있습니다", value=md, height=420, key=f"ho_text_{lid}")
    name = f"handover_{team.name(lv['member_id'])}_{lv['start']}"
    c1, c2, _ = st.columns([1, 1, 3])
    c1.download_button("⬇️ Markdown 저장", md, file_name=f"{name}.md", mime="text/markdown")
    c2.download_button("⬇️ TXT 저장", md, file_name=f"{name}.txt", mime="text/plain")
