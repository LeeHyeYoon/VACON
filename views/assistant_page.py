import streamlit as st

from services import llm
from services.agent import run_agent
from services.models import today
from views.common import header


def _render_msg(msg):
    avatar = ":material/person:" if msg["role"] == "user" else ":material/smart_toy:"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["text"])
        if msg.get("agent_calls"):
            with st.expander(f"VACON 데이터 조회 기록 · {len(msg['agent_calls'])}회"):
                for call in msg["agent_calls"]:
                    st.write(f"- `{call['tool']}` · {call['status']} · {call['seconds']}초"
                             + (f" · {call['error']}" if call.get("error") else ""))
        if msg.get("error"):
            st.caption("ALIRA 안내: " + msg["error"])


def render(team, rules):
    header("💬 ALIRA 비서", "모든 질문을 ALIRA가 해석하고 답합니다. VACON 업무 정보는 조회 도구로 확인합니다.")
    agent_ready = llm.llm_available()
    if agent_ready:
        st.caption("ALIRA가 VACON 업무 데이터를 조회해 답변합니다. 질문과 답변은 이 화면에 누적됩니다.")
    else:
        st.warning("ALIRA_API_BASE와 ALIRA_MODEL 환경변수를 설정해야 ALIRA 비서를 사용할 수 있습니다.")

    st.session_state.setdefault("chat", [])
    if st.session_state.get("assistant_mode") != "alira-only":
        st.session_state.chat = []
        st.session_state.assistant_mode = "alira-only"

    if st.session_state.chat:
        _, clear_col = st.columns([6, 1], vertical_alignment="center")
        if clear_col.button("기록 지우기", icon=":material/delete_sweep:", type="tertiary", width="stretch"):
            st.session_state.chat = []
            st.rerun()

    st.html("""
    <style>
      .st-key-assistant_chat_history {
        background: #ffffff !important;
        border: 1px solid #e7ebf0 !important;
        border-radius: 12px;
        padding-bottom: 0.5rem;
        margin-bottom: 1rem;
      }
    </style>
    """)
    st.space("small")
    history_slot = st.empty()
    query = st.chat_input("VACON 업무에 대해 질문하세요", key="assistant_chat_input")
    with history_slot.container(height=700, border=True, key="assistant_chat_history"):
        if query:
            st.session_state.chat.append({"role": "user", "text": query})
        if st.session_state.chat:
            for msg in st.session_state.chat:
                _render_msg(msg)
        else:
            with st.chat_message("assistant", avatar=":material/smart_toy:"):
                st.markdown("안녕하세요. VACON의 업무 현황과 휴가·대체 업무에 관해 질문해 주세요.")

        if query:
            if agent_ready:
                with st.spinner("ALIRA가 질문을 분석하고 필요한 VACON 데이터를 조회합니다..."):
                    result = run_agent(query, team, rules, today())
                answer = result.text if result.used else "ALIRA가 답변을 생성하지 못했습니다."
                assistant_msg = {"role": "assistant", "text": answer,
                                 "error": result.error, "agent_calls": result.calls}
            else:
                assistant_msg = {"role": "assistant", "text": "ALIRA 설정이 없어 답변을 요청하지 못했습니다.",
                                 "error": "ALIRA_API_BASE와 ALIRA_MODEL 환경변수를 확인하세요.",
                                 "agent_calls": []}
            st.session_state.chat.append(assistant_msg)
            _render_msg(assistant_msg)
