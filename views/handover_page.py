import hashlib

import streamlit as st

from services import llm
from services.handover import generate_ai_handover, generate_handover
from services.models import today
from views.common import fmt_leave, header


def render(team, rules):
    header("📝 인수인계", "현재 진행 버전의 본인 실적과 팀 진행 현황, 휴가 기간 예정 일정을 바탕으로 인계 문서를 작성합니다.")
    current_date = today()
    leaves = team.leave_records()
    if not leaves:
        st.info("등록된 휴가가 없습니다. '휴가 캘린더'에서 먼저 등록하세요.")
        return
    # 진행 중인 휴가는 계속 선택할 수 있고, 종료일이 오늘보다 이전인 휴가만 숨긴다.
    leaves = sorted((lv for lv in leaves if lv["end"] >= current_date), key=lambda lv: lv["start"])
    if not leaves:
        st.info("오늘 이후 선택할 수 있는 휴가가 없습니다.")
        return
    by_id = {lv["id"]: lv for lv in leaves}
    lid = st.selectbox("휴가 선택", list(by_id), format_func=lambda i: fmt_leave(team, by_id[i]))
    lv = by_id[lid]
    template = generate_handover(team, lv["member_id"], lv["start"], lv["end"], lv["type"], current_date)
    use_ai = llm.llm_available()
    if use_ai:
        if lv["member_id"] in team.unit_pool():
            st.caption("ALIRA 작성 — 선택한 휴가자의 현재 진행 버전 계획·배정·실적과 휴가 기간 일정을 ALIRA 서버로 전송합니다. "
                       "개인 실적과 팀 전체 진행량, 예정 일정과 실제 실적을 구분해 검토하세요.")
        else:
            st.caption("ALIRA 작성 — 생성 시 담당 업무·추천·위험 데이터가 ALIRA 서버로 전송됩니다. "
                       "추천 대체 담당자는 확정이 아니며, 사용 전 휴가자와 대체 담당자가 검토하세요.")
    else:
        st.caption("ALIRA 미설정 — 템플릿으로 생성합니다. ALIRA_API_BASE와 ALIRA_MODEL을 설정하면 ALIRA가 작성합니다. "
                   "데이터가 없는 항목은 '미입력'으로 표시됩니다.")
    cache = st.session_state.setdefault("ho_ai_cache", {})
    key = (lid, template)  # 데이터가 바뀌면 템플릿이 달라져 다시 생성해야 한다
    label = "🔄 다시 생성" if key in cache else "📝 인수인계 문서 생성"
    if st.button(label, key=f"ho_gen_{lid}", type="secondary" if key in cache else "primary"):
        if use_ai:
            status = st.status("인수인계 문서를 준비하고 있습니다", state="running", expanded=True)
            status.write("현재 진행 버전의 업무 계획과 본인 실적을 정리하고 있습니다.")
            status.write("휴가 기간 중 예정 일정과 인계가 필요한 특이사항을 확인합니다.")
            status.write("ALIRA가 확인된 VACON 데이터만 바탕으로 문서를 작성합니다.")
            cache[key] = generate_ai_handover(team, lv["member_id"], lv["start"], lv["end"], lv["type"],
                                              current_date, rules)
            if cache[key][1]:
                status.update(label="인수인계 문서 작성 완료", state="complete", expanded=False)
            else:
                status.update(label="ALIRA 응답이 없어 기본 문서로 전환했습니다", state="error", expanded=False)
        else:
            cache[key] = (template, False)
        st.rerun()
    if key not in cache:
        st.info("휴가를 선택한 뒤 '인수인계 문서 생성'을 누르세요.")
        return
    md, used = cache[key]
    if use_ai and not used:
        st.warning("ALIRA 호출에 실패해 템플릿 문서를 표시합니다.")
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
