"""VACON 진입점: streamlit run app.py"""
import streamlit as st

from data import db
from services import llm
from services.models import today
from views import assistant_page, backup_page, calendar_page, dashboard, data_page, handover_page, internal_work_page, mail_page, worklog_page, performance_page

st.set_page_config(page_title="VACON · 업무 연속성 비서", page_icon="🗓️", layout="wide")
st.markdown("""
<style>
[data-testid="stMetric"]{background:var(--secondary-background-color);border-radius:12px;padding:14px 16px;}
.block-container{padding-top:2rem;}
/* 메뉴 라벨이 잘리지 않도록 사이드바 폭과 한 줄 표시를 고정 */
[data-testid="stSidebar"]{width:19rem!important;min-width:19rem!important;max-width:19rem!important;}
[data-testid="stSidebar"] > div:first-child{width:19rem!important;}
/* 사이드바 메뉴 위에 서비스 이름 표시 */
[data-testid="stSidebarNav"]::before{content:"🗓️ VACON";display:block;font-size:1.5rem;font-weight:700;
  padding:1.2rem 1rem .2rem;}
[data-testid="stSidebarNav"]::after{content:"AI 기반 팀 휴가 · 업무 연속성 비서";display:block;
  font-size:.8rem;opacity:.65;padding:0 1rem .6rem;}
[data-testid="stSidebarNav"] a{border-radius:8px;margin:2px 8px;padding:.45rem .7rem;white-space:nowrap;}
[data-testid="stSidebarNav"] a p{white-space:nowrap;overflow:visible;text-overflow:clip;}
</style>""", unsafe_allow_html=True)

db.init_db()

# (아이콘, 제목, url_path, 모듈) — 위에서 아래 순서대로 표시
MENU = [
    ("📊", "대시보드", "dashboard", dashboard),
    ("🗓️", "휴가 캘린더", "calendar", calendar_page),
    ("📈", "검증 업무 로그", "worklog", worklog_page),
    ("🗓️", "(PM) 검증 일정 관리", "work-planning", internal_work_page),
    ("✍️", "검증 업무 기록", "performance", performance_page),
    ("🤝", "대체 담당자", "backup", backup_page),
    ("📝", "인수인계", "handover", handover_page),
    ("📧", "메일 요약", "mail", mail_page),
    ("💬", "AI 비서", "assistant", assistant_page),
    ("🗂️", "데이터 관리", "data", data_page),
]


def _runner(module):
    def run():
        module.render(db.load_team_data(), db.load_rules())
    return run


with st.sidebar:  # 메뉴 아래에 표시되는 상태 정보
    st.divider()
    st.caption(f"기준일: {today()}")
    st.caption("LLM: " + ("연결됨" if llm.llm_available() else "미설정(규칙 기반)"))

pages = [st.Page(_runner(m), title=t, icon=i, url_path=u, default=(n == 0))
         for n, (i, t, u, m) in enumerate(MENU)]
# st.page_link() in dynamic navigation must receive the registered StreamlitPage,
# not a URL string that is normally used by the multipage file convention.
page_by_path = {page.url_path: page for page in pages}
dashboard.VERSION_PAGE = page_by_path["work-planning"]
dashboard.INTERNAL_WORK_PAGE = page_by_path["work-planning"]
internal_work_page.WORKLOG_PAGE = page_by_path["worklog"]
st.navigation(pages, position="sidebar").run()
