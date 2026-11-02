import streamlit as st

from services import llm
from services.agent import run_agent
from services.assistant import SUPPORTED, answer_question
from services.models import today
from views.common import header, show_df


def _render_msg(msg, idx):
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])
        if msg.get("ai_text"):
            st.info("🤖 AI 요약: " + msg["ai_text"])
        if msg.get("agent_calls"):
            with st.expander(f"Agent 실행 기록 · {len(msg['agent_calls'])}회"):
                for call in msg["agent_calls"]:
                    st.write(f"- `{call['tool']}` · {call['status']} · {call['seconds']}초"
                             + (f" · {call['error']}" if call.get("error") else ""))
        if msg.get("agent_error"):
            st.caption("Agent 안내: " + msg["agent_error"])
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
    agent_enabled = st.checkbox("다단계 AI Agent 사용 (Anthropic API로 질문과 필요한 VACON 조회 결과를 전송)",
                                value=False, disabled=not llm.llm_available(),
                                help="켜면 VACON 조회 도구를 최대 6회 사용해 복합 질문을 분석합니다. 도구는 조회 전용이며 실제 일정/담당자 변경은 하지 않습니다.")
    if not llm.llm_available():
        st.caption("Agent는 ANTHROPIC_API_KEY와 anthropic 패키지가 설정되면 사용할 수 있습니다.")
    st.session_state.setdefault("chat", [])
    st.write("예시 질문")
    cols = st.columns(len(SUPPORTED[:4]))
    for col, q in zip(cols, SUPPORTED[:4]):
        col.button(q, key=f"ex_{q}", on_click=lambda q=q: st.session_state.update(pending=q))

    typed = st.chat_input("질문을 입력하세요 (예: 다음 주에 업무 공백 위험이 높은 날짜는?)")
    query = typed or st.session_state.pop("pending", None)
    if query:
        ans = answer_question(query, team, today(), rules)
        agent_calls, agent_error, ai_text = [], "", None
        if agent_enabled and llm.llm_available():
            result = run_agent(query, team, rules, today())
            agent_calls = result.calls
            if result.used:
                ans.text = result.text
                ans.tables, ans.documents = [], []
                ans.supported = True
            else:
                agent_error = result.error or "Agent 결과를 받지 못해 규칙 기반 답변으로 전환했습니다."
                if not ans.supported:
                    ans.text += "\n\n" + agent_error
                ai_text = llm.rephrase(query, ans.text, ans.facts) if ans.supported and ans.facts else None
        else:
            if not ans.supported and llm.llm_available():
                agent_error = "버전 분석이나 여러 도구를 조합하는 질문은 Agent를 켜면 처리할 수 있습니다."
            ai_text = llm.rephrase(query, ans.text, ans.facts) if ans.supported and ans.facts else None
        st.session_state.chat.append({"role": "user", "text": query})
        st.session_state.chat.append({"role": "assistant", "text": ans.text, "tables": ans.tables,
                                      "documents": ans.documents, "ai_text": ai_text,
                                      "agent_calls": agent_calls, "agent_error": agent_error})
    for i, msg in enumerate(st.session_state.chat):
        _render_msg(msg, i)
    if st.session_state.chat and st.button("대화 지우기"):
        st.session_state.chat = []
        st.rerun()
