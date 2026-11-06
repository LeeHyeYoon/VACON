import hashlib

import streamlit as st

from services import llm
from services.handover import generate_ai_handover, generate_handover
from services.models import today
from views.common import fmt_leave, header


def render(team, rules):
    header("📝 인수인계", "선택한 휴가의 담당 업무·대체 담당자 추천·위험 분석을 근거로 인수인계 문서를 자동 작성합니다.")
    leaves = team.leave_records()
    if not leaves:
        st.info("등록된 휴가가 없습니다. '휴가 캘린더'에서 먼저 등록하세요.")
        return
    leaves = sorted(leaves, key=lambda lv: (lv["end"] < today(), lv["start"]))
    by_id = {lv["id"]: lv for lv in leaves}
    lid = st.selectbox("휴가 선택", list(by_id), format_func=lambda i: fmt_leave(team, by_id[i]))
    lv = by_id[lid]
    template = generate_handover(team, lv["member_id"], lv["start"], lv["end"], lv["type"], today())
    use_ai = llm.llm_available()
    if use_ai:
        st.caption("AI 작성 — 생성 시 담당 업무·추천·위험 데이터가 사내 LLM 서버로 전송됩니다. "
                   "추천 대체 담당자는 확정이 아니며, 사용 전 휴가자와 대체 담당자가 검토하세요.")
    else:
        st.caption("LLM 미설정 — 템플릿으로 생성합니다. ALIRA_API_BASE와 ALIRA_MODEL을 설정하면 AI가 작성합니다. "
                   "데이터가 없는 항목은 '미입력'으로 표시됩니다.")
    cache = st.session_state.setdefault("ho_ai_cache", {})
    key = (lid, template)  # 데이터가 바뀌면 템플릿이 달라져 다시 생성해야 한다
    label = "🔄 다시 생성" if key in cache else "📝 인수인계 문서 생성"
    if st.button(label, key=f"ho_gen_{lid}", type="secondary" if key in cache else "primary"):
        if use_ai:
            with st.spinner("LLM이 인수인계 문서를 작성하는 중입니다... (30초~1분)"):
                cache[key] = generate_ai_handover(team, lv["member_id"], lv["start"], lv["end"], lv["type"],
                                                  today(), rules)
        else:
            cache[key] = (template, False)
        st.rerun()
    if key not in cache:
        st.info("휴가를 선택한 뒤 '인수인계 문서 생성'을 누르세요.")
        return
    md, used = cache[key]
    if use_ai and not used:
        st.warning("LLM 호출에 실패해 템플릿 문서를 표시합니다.")
    tab_prev, tab_src = st.tabs(["미리보기", "원문(Markdown)"])
    with tab_prev:
        st.markdown(md)
    with tab_src:
        digest = hashlib.md5(md.encode("utf-8")).hexdigest()[:8]
        md = st.text_area("필요하면 수정한 뒤 다운로드할 수 있습니다", value=md, height=420, key=f"ho_text_{lid}_{digest}")
    name = f"handover_{team.name(lv['member_id'])}_{lv['start']}"
    c1, c2, _ = st.columns([1, 1, 3])
    c1.download_button("⬇️ Markdown 저장", md, file_name=f"{name}.md", mime="text/markdown")
    c2.download_button("⬇️ TXT 저장", md, file_name=f"{name}.txt", mime="text/plain")
