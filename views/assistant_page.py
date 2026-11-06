import streamlit as st

from services import llm, llm_client
from services.agent import run_agent
from services.models import today
from views.common import header


def _render_msg(msg):
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])
        if msg.get("agent_calls"):
            with st.expander(f"VACON 데이터 조회 기록 · {len(msg['agent_calls'])}회"):
                for call in msg["agent_calls"]:
                    st.write(f"- `{call['tool']}` · {call['status']} · {call['seconds']}초"
                             + (f" · {call['error']}" if call.get("error") else ""))
        if msg.get("error"):
            st.caption("ALIRA 안내: " + msg["error"])


def render(team, rules):
    header("💬 ALIRA AI 비서", "모든 질문을 ALIRA가 해석하고 답합니다. VACON 업무 정보는 조회 도구로 확인합니다.")
    agent_ready = llm.llm_available()
    if agent_ready:
        st.caption("질문과 필요한 VACON 조회 결과가 사내 ALIRA API로 전송됩니다. 조회 도구는 읽기 전용입니다.")
        if st.button("ALIRA 연결 확인"):
            try:
                status = llm_client.check_connection()
                model_state = "설정 모델 확인됨" if status["model_available"] else "설정 모델이 서버 목록에 없음"
                st.success(f"ALIRA 연결 성공 · {model_state} · {status['model']}")
            except Exception as exc:
                st.error(f"ALIRA 연결 실패: {exc}")
    else:
        st.warning("ALIRA_API_BASE와 ALIRA_MODEL 환경변수를 설정해야 AI 비서를 사용할 수 있습니다.")

    st.session_state.setdefault("chat", [])
    if st.session_state.get("assistant_mode") != "alira-only":
        st.session_state.chat = []
        st.session_state.assistant_mode = "alira-only"

    query = st.chat_input("무엇이든 질문하세요")
    if query:
        if agent_ready:
            with st.spinner("ALIRA가 질문을 분석하고 필요한 VACON 데이터를 조회합니다..."):
                result = run_agent(query, team, rules, today())
            answer = result.text if result.used else "ALIRA가 답변을 생성하지 못했습니다."
            error = result.error
            calls = result.calls
        else:
            answer = "ALIRA 설정이 없어 답변을 요청하지 못했습니다."
            error = "ALIRA_API_BASE와 ALIRA_MODEL 환경변수를 확인하세요."
            calls = []
        st.session_state.chat.append({"role": "user", "text": query})
        st.session_state.chat.append({"role": "assistant", "text": answer,
                                      "error": error, "agent_calls": calls})
    for msg in st.session_state.chat:
        _render_msg(msg)
    if st.session_state.chat and st.button("대화 지우기"):
        st.session_state.chat = []
        st.rerun()
