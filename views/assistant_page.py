import streamlit as st

from services import llm
from services.assistant import SUPPORTED, answer_question
from services.models import today
from views.common import header, show_df


def _render_msg(msg, idx):
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])
        if msg.get("ai_text"):
            st.info("🤖 AI 요약: " + msg["ai_text"])
        for df in msg.get("tables", []):
            show_df(df)
        for j, (fname, md) in enumerate(msg.get("documents", [])):
            with st.expander(f"📄 {fname}"):
                st.markdown(md)
            st.download_button(f"⬇️ {fname}", md, file_name=fname, mime="text/markdown", key=f"dl_{idx}_{j}")


def render(team, rules):
    header("💬 AI 비서", "실제 데이터에 근거해 답하며, 지원하지 않는 질문은 추측하지 않고 안내합니다.")
    st.caption("LLM 상태: " + ("연결됨(규칙 기반 결과를 자연어로 요약)" if llm.llm_available()
                             else "미설정 — 규칙 기반 질의로 동작합니다(API 키 불필요)"))
    st.session_state.setdefault("chat", [])
    st.write("예시 질문")
    cols = st.columns(len(SUPPORTED[:4]))
    for col, q in zip(cols, SUPPORTED[:4]):
        col.button(q, key=f"ex_{q}", on_click=lambda q=q: st.session_state.update(pending=q))

    typed = st.chat_input("질문을 입력하세요 (예: 다음 주에 업무 공백 위험이 높은 날짜는?)")
    query = typed or st.session_state.pop("pending", None)
    if query:
        ans = answer_question(query, team, today(), rules)
        ai_text = llm.rephrase(query, ans.text, ans.facts) if ans.supported and ans.facts else None
        st.session_state.chat.append({"role": "user", "text": query})
        st.session_state.chat.append({"role": "assistant", "text": ans.text, "tables": ans.tables,
                                      "documents": ans.documents, "ai_text": ai_text})
    for i, msg in enumerate(st.session_state.chat):
        _render_msg(msg, i)
    if st.session_state.chat and st.button("대화 지우기"):
        st.session_state.chat = []
        st.rerun()
