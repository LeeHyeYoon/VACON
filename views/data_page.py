import pandas as pd
import streamlit as st

from data import db
from services.models import IMPORTANCE, MBTI_TYPES, SKILL_LEVELS, STATUSES, isnull
from services.validation import validate_assignees, validate_member, validate_task
from views import sheet_tab
from views.common import errors_box, header, show_df


MBTI_OPTIONS = ["미입력", *MBTI_TYPES]


def render(team, rules):
    header("🗂️ 데이터 관리", "팀원, 역량, 업무, 마감일과 시트 연동을 관리합니다.")
    t1, t2, t3, t4 = st.tabs(["팀원 · 역량", "업무 · 마감일", "시트 연동", "초기화"])
    with t1:
        _members(team)
    with t2:
        _tasks(team)
    with t3:
        sheet_tab.render(team, rules)
    with t4:
        st.warning("모든 데이터(팀원·업무·휴가·설정)를 삭제하고 샘플 데이터로 되돌립니다.")
        if st.checkbox("초기화에 동의합니다") and st.button("샘플 데이터로 초기화"):
            db.reset_sample()
            st.rerun()


def _members(team):
    ids = [int(i) for i in team.members["id"]]
    if ids:
        sk = {i: ", ".join(f"{r.skill}(Lv{r.level})" for r in team.skills[team.skills["member_id"] == i].itertuples())
              for i in ids}
        show_df(pd.DataFrame([{"이름": m["name"], "직무": m["role"], "업무량(%)": m["workload"],
                               "MBTI": m.get("mbti") or "미입력",
                               "비상 연락처": m["contact"] or "미입력", "역량": sk[m["id"]] or "미입력"}
                              for m in team.member_records()]))
    st.subheader("팀원 추가")
    with st.form("add_member", clear_on_submit=True):
        c = st.columns(5)
        name, role = c[0].text_input("이름"), c[1].text_input("직무")
        workload = c[2].number_input("현재 업무량(%)", 0, 150, 50)
        contact = c[3].text_input("비상 연락처(선택)")
        mbti = c[4].selectbox("MBTI(선택)", MBTI_OPTIONS)
        mbti = "" if mbti == "미입력" else mbti
        if st.form_submit_button("추가") and not errors_box(validate_member(name, workload, mbti)):
            db.add_member(name, role, workload, contact, mbti)
            st.rerun()
    if not ids:
        return
    st.subheader("팀원 수정 · 삭제")
    mid = st.selectbox("대상 팀원", ids, format_func=team.name)
    m = next(x for x in team.member_records() if x["id"] == mid)
    with st.form(f"edit_member_{mid}"):
        c = st.columns(5)
        name, role = c[0].text_input("이름", m["name"]), c[1].text_input("직무", m["role"] or "")
        workload = c[2].number_input("현재 업무량(%)", 0, 150, int(m["workload"] or 0))
        contact = c[3].text_input("비상 연락처", m["contact"] or "")
        cur = "미입력" if isnull(m.get("mbti")) or not m.get("mbti") else str(m["mbti"]).upper()
        mbti = c[4].selectbox("MBTI", MBTI_OPTIONS, index=MBTI_OPTIONS.index(cur) if cur in MBTI_OPTIONS else 0)
        mbti = "" if mbti == "미입력" else mbti
        if st.form_submit_button("수정 저장") and not errors_box(validate_member(name, workload, mbti)):
            db.update_member(mid, name, role, workload, contact, mbti)
            st.rerun()
    if st.button("🗑️ 팀원 삭제(휴가·역량 함께 삭제, 담당 업무는 '미지정'으로 변경)", key=f"del_m_{mid}"):
        db.delete_member(mid)
        st.rerun()

    st.subheader("역량 관리")
    with st.form(f"skill_{mid}", clear_on_submit=True):
        c = st.columns([2, 1])
        skill = c[0].text_input("역량명(예: 백엔드, QA)")
        level = c[1].selectbox("수준", list(SKILL_LEVELS), format_func=lambda x: f"Lv{x} {SKILL_LEVELS[x]}", index=1)
        if st.form_submit_button("역량 추가/수정"):
            if skill.strip():
                db.set_skill(mid, skill, level)
                st.rerun()
            else:
                st.error("역량명을 입력하세요.")
    mine = team.skills[team.skills["member_id"] == mid]["skill"].tolist()
    if mine:
        rm = st.selectbox("삭제할 역량", mine)
        if st.button("역량 삭제"):
            db.delete_skill(mid, rm)
            st.rerun()


def _task_form(team, key, init=None):
    ids = [int(i) for i in team.members["id"]]
    opts = [None] + ids
    nm = lambda x: "미지정" if x is None else team.name(x)
    init = init or {}
    g = lambda k, d=None: d if isnull(init.get(k)) else init.get(k)
    with st.form(key):
        c = st.columns(3)
        name = c[0].text_input("업무명", g("name", ""))
        owner = c[1].selectbox("담당자", opts, index=opts.index(g("owner_id")) if g("owner_id") in opts else 0, format_func=nm)
        backup = c[2].selectbox("대체 담당자", opts, index=opts.index(g("backup_id")) if g("backup_id") in opts else 0, format_func=nm)
        c = st.columns(4)
        imp = c[0].selectbox("중요도", IMPORTANCE, index=IMPORTANCE.index(g("importance", "중")))
        status = c[1].selectbox("상태", STATUSES, index=STATUSES.index(g("status", "대기")))
        progress = c[2].number_input("진행률(%)", 0, 100, int(g("progress", 0)))
        load = c[3].number_input("예상 업무 부담(%p)", 0, 100, int(g("load_pct", 10)))
        c = st.columns(2)
        has_due = c[0].checkbox("마감일 있음", value=g("due_date") is not None, key=f"{key}_hd")
        due = c[0].date_input("마감일", value=g("due_date") or pd.Timestamp.today().date(), key=f"{key}_due")
        req = c[1].text_input("필수 역량(여러 개는 쉼표로)", g("required_skill", ""))
        remaining = st.text_area("미완료 작업", g("remaining", ""))
        notes = st.text_area("업무별 주의사항", g("notes", ""))
        submitted = st.form_submit_button("저장")
    fields = {"name": name, "owner_id": owner, "backup_id": backup, "importance": imp, "status": status,
              "progress": progress, "due_date": due if has_due else None, "required_skill": req,
              "remaining": remaining, "notes": notes, "load_pct": load}
    return submitted, fields


def _tasks(team):
    if len(team.tasks):
        show_df(pd.DataFrame([{"업무": t["name"], "담당자": team.name(t["owner_id"]), "대체": team.name(t["backup_id"]),
                               "중요도": t["importance"], "상태": t["status"], "진행률": t["progress"],
                               "마감일": t["due_date"] or "미입력", "필수 역량": t["required_skill"] or "미입력"}
                              for t in team.task_records()]))
    st.subheader("업무 추가")
    ok, f = _task_form(team, "add_task")
    if ok and not errors_box(validate_task(f["name"], f["importance"], f["status"], f["progress"], f["load_pct"]) + validate_assignees(team, f["required_skill"], f["owner_id"], f["backup_id"])):
        db.add_task(f)
        st.rerun()
    if len(team.tasks) == 0:
        return
    st.subheader("업무 수정 · 삭제")
    by_id = {t["id"]: t for t in team.task_records()}
    tid = st.selectbox("대상 업무", list(by_id), format_func=lambda i: by_id[i]["name"])
    ok, f = _task_form(team, f"edit_task_{tid}", by_id[tid])
    if ok and not errors_box(validate_task(f["name"], f["importance"], f["status"], f["progress"], f["load_pct"]) + validate_assignees(team, f["required_skill"], f["owner_id"], f["backup_id"])):
        db.update_task(tid, f)
        st.rerun()
    if st.button("🗑️ 업무 삭제", key=f"del_t_{tid}"):
        db.delete_task(tid)
        st.rerun()


