import hashlib
import json

import pandas as pd
import streamlit as st

from services import llm
from services import mail_ai, groupware_mail
from services import mail_digest as md
from services.models import today
from views.common import header, show_df

LEVEL_BADGE = {"높음": "🔴 높음", "보통": "🟠 보통", "낮음": "🟢 낮음"}


def render(team, rules):
    header("📧 메일 요약", "대상 기간을 선택하고 그룹웨어에 로그인해 받은 메일을 요약합니다.")
    st.info("그룹웨어 메일은 읽기 전용으로 가져옵니다. 원문은 VACON DB에 저장하지 않고 현재 브라우저 세션에만 유지합니다. ALIRA 요약 시 메일 내용은 사내 ALIRA 서버로 전송됩니다.")
    st.caption("ALIRA 요청에는 메일당 본문 최대 3,000자까지 포함합니다. 메일이 많으면 여러 묶음으로 나눠 요약합니다.")
    rng = st.date_input("대상 기간", value=(today(), today()), key="mail_target_range")
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.info("시작일과 종료일을 모두 선택하세요.")
        return
    s, e = rng
    account_name = (st.session_state.get("gw_username") or "").strip() or "그룹웨어 계정"
    st.session_state.setdefault("mails", [])
    if not groupware_mail.configured():
        st.warning("회사 그룹웨어 연결은 Windows 10/11 PC에서 사용할 수 있습니다.")
    else:
        if st.button("저장된 로그인 정보 불러오기", disabled=not groupware_mail.has_saved_credentials(), key="gw_load_credentials"):
            try:
                saved = groupware_mail.load_credentials() or {}
                st.session_state.gw_username = saved.get("username", "")
                st.session_state.gw_password = saved.get("password", "")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        username = st.text_input("그룹웨어 아이디", key="gw_username")
        password = st.text_input("그룹웨어 비밀번호", type="password", key="gw_password")
        account_name = username.strip() or "그룹웨어 계정"
        remember = st.checkbox("이 PC에 로그인 정보 저장 (Windows 계정으로 암호화)", key="gw_remember_credentials")
        status = st.session_state.get("gw_login_status")
        if status and status.get("account") == username.strip():
            (st.success if status.get("ok") else st.error)(status["message"])
        check_col, fetch_col = st.columns(2)
        if check_col.button("그룹웨어 로그인 확인", disabled=not (username and password), key="gw_check_login"):
            try:
                with st.spinner("그룹웨어 로그인과 받은메일함 접근을 확인하고 있습니다…"):
                    message = groupware_mail.check_login(username, password)
                st.session_state.gw_login_status = {"ok": True, "account": username.strip(), "message": message}
                st.rerun()
            except Exception as exc:
                st.session_state.gw_login_status = {"ok": False, "account": username.strip(), "message": f"로그인 확인 실패: {exc}"}
                st.rerun()
        if fetch_col.button("대상 기간 메일 가져오기", disabled=not (username and password), key=f"gw_fetch_{s}_{e}"):
            try:
                with st.spinner("그룹웨어에서 대상 기간의 받은 메일을 읽고 있습니다…"):
                    fetched, truncated = groupware_mail.fetch_received_mail(s, e, username, password)
                if remember:
                    groupware_mail.save_credentials(username, password)
                st.session_state.gw_login_status = {"ok": True, "account": username.strip(), "message": "로그인 확인됨 · 받은메일함 접근 가능"}
                st.session_state.mails = fetched
                st.session_state.mail_source = "회사 그룹웨어"
                st.session_state.mail_ai_digest = None
                st.session_state.mail_fetch_truncated = truncated
                st.session_state.gw_fetch_notice = f"그룹웨어 로그인 확인됨 · 메일 {len(fetched)}건 조회"
                st.rerun()
            except Exception as exc:
                st.session_state.gw_login_status = {"ok": False, "account": username.strip(), "message": f"그룹웨어 연결 실패: {exc}"}
                st.error(st.session_state.gw_login_status["message"])
        if groupware_mail.has_saved_credentials() and st.button("저장된 로그인 정보 삭제", key="gw_delete_credentials"):
            groupware_mail.delete_credentials()
            st.session_state.pop("gw_login_status", None)
            st.success("저장된 로그인 정보를 삭제했습니다.")
        st.caption("로그인 확인은 새 브라우저 세션으로 인증한 뒤 받은메일함 접근 여부를 확인합니다. 로그인 정보는 기본 저장하지 않으며, 저장을 선택하면 이 Windows 계정으로만 복호화할 수 있게 암호화합니다.")
        if st.session_state.get("gw_fetch_notice"):
            st.success(st.session_state.pop("gw_fetch_notice"))

    mails = st.session_state.mails
    c1, c2 = st.columns([3, 1])
    source = st.session_state.get("mail_source")
    c1.write(f"현재 불러온 메일: **{len(mails)}건**" + (f" · 출처 {source}" if source else ""))
    if st.session_state.get("mail_fetch_truncated"):
        st.warning(f"가져오기 한도 {groupware_mail.MAX_MESSAGES}건에 도달했습니다. 선택한 기간의 메일이 더 있으면 날짜 범위를 나눠 조회하세요.")
    if mails and c2.button("메일 모두 지우기"):
        st.session_state.mails = []
        st.session_state.mail_source = ""
        st.session_state.mail_ai_digest = None
        st.rerun()
    if not mails:
        if st.session_state.get("mail_source") == "회사 그룹웨어":
            st.info("선택한 기간에 받은 그룹웨어 메일이 없습니다.")
        return

    res = md.summarize_mails(mails, s, e, team, None, today())
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
        if res["undated"]:
            st.error(f"그룹웨어 메일 제목 {len(res['undated'])}건은 찾았지만 목록에서 수신 날짜를 읽지 못했습니다. 날짜 형식 확인이 필요합니다.")
        else:
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

    digest = md.digest_markdown(res, account_name, today())
    st.subheader("ALIRA 메일 브리핑")
    if llm.llm_available():
        digest_input = [{"date": str(item.get("date") or ""), "sender": item.get("sender", ""),
                         "subject": item.get("subject", ""), "body": item.get("body", "")}
                        for item in res["items"]]
        fingerprint = hashlib.sha256(json.dumps(
            {"period": [str(s), str(e)], "employee": account_name, "mails": digest_input},
            ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        saved = st.session_state.get("mail_ai_digest") or {}
        if saved.get("fingerprint") != fingerprint:
            try:
                with st.spinner("ALIRA가 선택한 기간의 메일을 분석하고 있습니다…"):
                    ai_digest = mail_ai.summarize_with_alira(res["items"], s, e, account_name)
                saved = {"fingerprint": fingerprint, "text": ai_digest, "error": None}
                st.session_state.mail_ai_digest = saved
            except Exception as exc:
                saved = {"fingerprint": fingerprint, "text": None, "error": str(exc)}
                st.session_state.mail_ai_digest = saved
        if saved.get("text"):
            digest = saved["text"]
            st.markdown(digest)
        else:
            st.error(f"ALIRA 메일 요약에 실패했습니다: {saved.get('error') or '응답을 받지 못했습니다.'}")
            st.caption("아래는 ALIRA를 사용할 수 없을 때 확인할 수 있는 규칙 기반 참고 요약입니다.")
            st.markdown(digest)
    else:
        st.warning("ALIRA_API_BASE와 ALIRA_MODEL이 설정되지 않아 AI 요약을 만들 수 없습니다.")
        st.markdown(digest)
    st.download_button("⬇️ Markdown 저장", digest, file_name=f"mail_digest_{s}.md", mime="text/markdown")
