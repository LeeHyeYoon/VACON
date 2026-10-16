import pandas as pd
import streamlit as st

from services import llm
from services import mail_digest as md
from services.models import today
from views.common import fmt_leave, header, show_df

LEVEL_BADGE = {"높음": "🔴 높음", "보통": "🟠 보통", "낮음": "🟢 낮음"}


def render(team, rules):
    header("📧 메일 요약", "휴가(부재) 기간에 받은 메일을 모아 우선순위·회신 필요 여부·관련 업무별로 요약합니다.")
    st.info("이 앱은 메일 서버에 연결하지 않습니다. 메일 파일(.eml) 업로드, CSV, 붙여넣기 중 하나로 가져오세요. "
            "가져온 메일은 저장되지 않고 이 브라우저 세션에만 유지됩니다.")
    leaves = sorted(team.leave_records(), key=lambda lv: (lv["end"] < today(), lv["start"]))
    if not leaves:
        st.warning("등록된 휴가가 없습니다. '휴가 캘린더'에서 먼저 등록하세요.")
        return
    by_id = {lv["id"]: lv for lv in leaves}
    lid = st.selectbox("부재 기간(휴가 선택)", list(by_id), format_func=lambda i: fmt_leave(team, by_id[i]))
    lv = by_id[lid]
    rng = st.date_input("요약 대상 기간(필요하면 조정)", value=(lv["start"], lv["end"]), key=f"mail_rng_{lid}")
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.info("시작일과 종료일을 모두 선택하세요.")
        return
    s, e = rng

    st.session_state.setdefault("mails", [])
    t_sample, t_file, t_paste = st.tabs(["샘플 메일", "파일 업로드(.eml / .csv)", "붙여넣기"])
    with t_sample:
        st.write("시연용 가상 메일을 선택한 기간에 맞춰 불러옵니다.")
        if st.button("샘플 메일 불러오기"):
            st.session_state.mails = md.sample_mails(s, e)
            st.rerun()
    with t_file:
        st.caption("CSV 컬럼: 받은날짜, 보낸사람, 제목, 본문 (UTF-8 또는 CP949). .eml은 여러 개 선택할 수 있습니다.")
        files = st.file_uploader("메일 파일", type=["eml", "csv", "txt"], accept_multiple_files=True)
        if st.button("업로드한 파일 불러오기") and files:
            new, errs = [], []
            for f in files:
                raw = f.read()
                if f.name.lower().endswith(".eml"):
                    m = md.parse_eml(raw)
                    new.append(m) if m else errs.append(f"{f.name}: 읽을 수 없는 .eml 파일입니다.")
                elif f.name.lower().endswith(".csv"):
                    ms, er = md.parse_csv(raw)
                    new += ms
                    errs += [f"{f.name} {x}" for x in er]
                else:
                    ms, er = md.parse_pasted(raw.decode("utf-8", errors="replace"))
                    new += ms
                    errs += [f"{f.name} {x}" for x in er]
            st.session_state.mails = st.session_state.mails + new
            for x in errs:
                st.warning(x)
            st.success(f"{len(new)}건을 불러왔습니다.")
    with t_paste:
        st.caption("형식 예) 보낸사람: 홍길동 / 받은날짜: 2026-10-20 / 제목: 제목 → 본문. 메일 사이는 --- 한 줄로 구분합니다.")
        text = st.text_area("메일 붙여넣기", height=220, key="mail_paste")
        if st.button("붙여넣은 메일 추가"):
            ms, errs = md.parse_pasted(text)
            st.session_state.mails = st.session_state.mails + ms
            for x in errs:
                st.warning(x)
            st.success(f"{len(ms)}건을 추가했습니다.") if ms else st.error("추가할 메일이 없습니다.")

    mails = st.session_state.mails
    c1, c2 = st.columns([3, 1])
    c1.write(f"현재 불러온 메일: **{len(mails)}건**")
    if mails and c2.button("메일 모두 지우기"):
        st.session_state.mails = []
        st.rerun()
    if not mails:
        return

    res = md.summarize_mails(mails, s, e, team, lv["member_id"], today())
    st_ = res["stats"]
    cols = st.columns(4)
    cols[0].metric("기간 내 메일", f"{st_['total']}건")
    cols[1].metric("우선순위 높음", f"{st_['높음']}건")
    cols[2].metric("회신 필요 추정", f"{st_['need_reply']}건")
    cols[3].metric("기한 경과 포함", f"{st_['past_deadline']}건")
    if res["outside_count"]:
        st.caption(f"기간 밖이라 제외한 메일 {res['outside_count']}건")
    if res["undated"]:
        st.warning(f"날짜가 없어 기간을 판단할 수 없는 메일이 {len(res['undated'])}건 있습니다. 요약에는 포함되지 않았으니 직접 확인하세요.")
    if not res["items"]:
        st.info("선택한 기간에 받은 메일이 없습니다.")
        return

    st.subheader("우선순위별 메일")
    show_df(pd.DataFrame([{"우선순위": LEVEL_BADGE[i["level"]], "날짜": i["date"], "보낸사람": i["sender"] or "-",
                           "제목": i["subject"] or "(제목 없음)", "한 줄 요약": i["summary"],
                           "회신 필요": "예" if i["needs_reply"] else "-", "관련 업무(추정)": i["task"] or "-"}
                          for i in res["items"]]))
    for i in res["items"]:
        if i["level"] == "낮음":
            continue
        with st.expander(f"{LEVEL_BADGE[i['level']]} · {i['subject'] or '(제목 없음)'}"):
            st.write("**판단 근거**")
            for r in i["reasons"] or ["-"]:
                st.write(f"- {r}")
            st.write("**원문**")
            st.text(i["body"] or "(본문 없음)")

    digest = md.digest_markdown(res, team.name(lv["member_id"]), today())
    st.subheader("복귀 브리핑 문서")
    if llm.llm_available() and st.checkbox("AI로 브리핑 다듬기 (메일 제목·요약이 외부 LLM API로 전송됩니다)"):
        ai = llm.summarize_digest(digest)
        st.info("🤖 " + ai) if ai else st.warning("AI 요약에 실패해 규칙 기반 요약만 표시합니다.")
    st.markdown(digest)
    st.download_button("⬇️ Markdown 저장", digest, file_name=f"mail_digest_{team.name(lv['member_id'])}_{s}.md", mime="text/markdown")
