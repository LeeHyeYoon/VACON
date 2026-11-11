import pandas as pd
import streamlit as st

from data import db
from services.models import MBTI_TYPES, isnull
from services.validation import validate_member
from views import sheet_tab
from views.common import errors_box, header, page_surface, show_df


MBTI_OPTIONS = ["미입력", *MBTI_TYPES]


def render(team, rules):
    header("🗂️ 데이터 관리", "팀원 정보와 시트 연동을 관리합니다.")
    with page_surface("data"):
        t1, t2, t3 = st.tabs(["팀원 · 자격증", "시트 연동", "초기화"])
        with t1:
            _members(team)
        with t2:
            sheet_tab.render(team, rules)
        with t3:
            st.warning("모든 데이터(팀원·업무·휴가·설정)를 삭제하고 샘플 데이터로 되돌립니다.")
            if st.toggle("초기화에 동의합니다") and st.button("샘플 데이터로 초기화"):
                db.reset_sample()
                st.rerun()


def _members(team):
    ids = [int(i) for i in team.members["id"]]
    if ids:
        show_df(pd.DataFrame([{"이름": m["name"], "직무": m["role"],
                               "MBTI": m.get("mbti") or "미입력",
                               "비상 연락처": m["contact"] or "미입력",
                               "보유 자격증": m.get("certifications") or "미입력"}
                              for m in team.member_records()]))
    st.subheader("팀원 추가")
    with st.form("add_member", clear_on_submit=True):
        c = st.columns(2)
        name, role = c[0].text_input("이름"), c[1].text_input("직무")
        c = st.columns(2)
        contact = c[0].text_input("비상 연락처(선택)")
        mbti = c[1].selectbox("MBTI(선택)", MBTI_OPTIONS)
        mbti = "" if mbti == "미입력" else mbti
        certifications = st.text_input("보유 자격증(선택)", placeholder="예: ISTQB, 정보처리기사")
        if st.form_submit_button("추가") and not errors_box(validate_member(name, 0, mbti)):
            db.add_member(name, role, 0, contact, mbti, certifications=certifications)
            st.rerun()
    if not ids:
        return
    st.subheader("팀원 수정 · 삭제")
    mid = st.selectbox("대상 팀원", ids, format_func=team.name)
    m = next(x for x in team.member_records() if x["id"] == mid)
    with st.form(f"edit_member_{mid}"):
        c = st.columns(2)
        name, role = c[0].text_input("이름", m["name"]), c[1].text_input("직무", m["role"] or "")
        c = st.columns(2)
        contact = c[0].text_input("비상 연락처", m["contact"] or "")
        cur = "미입력" if isnull(m.get("mbti")) or not m.get("mbti") else str(m["mbti"]).upper()
        mbti = c[1].selectbox("MBTI", MBTI_OPTIONS, index=MBTI_OPTIONS.index(cur) if cur in MBTI_OPTIONS else 0)
        mbti = "" if mbti == "미입력" else mbti
        certifications = st.text_input("보유 자격증", m.get("certifications") or "",
                                       placeholder="예: ISTQB, 정보처리기사")
        if st.form_submit_button("수정 저장") and not errors_box(validate_member(name, int(m["workload"] or 0), mbti)):
            db.update_member(mid, name, role, int(m["workload"] or 0), contact, mbti,
                             certifications=certifications)
            st.rerun()
    if st.button("🗑️ 팀원 삭제(휴가·기존 역량도 삭제, 담당 업무는 '미지정'으로 변경)", key=f"del_m_{mid}"):
        db.delete_member(mid)
        st.rerun()




