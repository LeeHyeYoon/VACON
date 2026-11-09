"""VACON 진입점: streamlit run app.py"""
import streamlit as st

from data import db
from services import llm
from services.models import today
from views import assistant_page, backup_page, calendar_page, dashboard, data_page, handover_page, internal_work_page, mail_page, worklog_page, performance_page

st.set_page_config(page_title="VACON · 검증 운영", page_icon=":material/fact_check:", layout="wide")
st.markdown("""
<style>
.stApp, h1, h2, h3, [data-testid="stMarkdownContainer"] p, label, input, textarea { font-family: Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; }
h1, h2, h3 { color: #1E2D45; letter-spacing: -0.03em; }
[data-testid="stAppViewContainer"] > .main .block-container{max-width:1500px;}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #1E2D45; }
[data-testid="stSidebar"]{min-width:19rem!important;max-width:19rem!important;}
[data-testid="stSidebarNav"] a{display:flex;flex-direction:row;align-items:center;justify-content:flex-start;min-height:42px;box-sizing:border-box;gap:.42rem;border-radius:8px;margin:3px 8px;padding:0 .72rem;transition:background-color .16s ease,color .16s ease;}
[data-testid="stSidebarNav"] a > div{display:flex;flex-direction:row;align-items:center;min-height:42px;gap:.42rem;}
[data-testid="stSidebarNav"] a p{display:flex;align-items:center;margin:0;height:42px;font-size:.88rem;line-height:1.2rem!important;font-weight:500;}
[data-testid="stSidebarNav"] li:nth-child(odd) > a{background-color:#F7F9FC;}
[data-testid="stSidebarNav"] li:nth-child(even) > a{background-color:#F0F4FA;}
[data-testid="stSidebar"] a:hover{background-color:#E8F0FF;color:#1D55B5;}
[data-testid="stSidebar"] [aria-current="page"]{background-color:#E8F0FF;color:#1D55B5;font-weight:650;box-shadow:none;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p{line-height:1.45;}
[data-testid="stMetric"]{background:#FFFFFF;border:1px solid #E4EAF2;border-radius:10px;padding:1rem 1.1rem;box-shadow:0 1px 2px rgba(28,47,77,.035);}
[data-testid="stDataFrame"]{border-radius:9px;overflow:hidden;border:1px solid #E4EAF2;}
[data-testid="stTabs"] [role="tablist"]{gap:.75rem;border-bottom:1px solid #E4EAF2;}
[data-testid="stTabs"] button[role="tab"]{flex:0 0 auto;white-space:nowrap;min-height:44px;padding:.7rem 1.1rem;background:#F3F6FB;border:1px solid #E1E8F1;border-radius:8px 8px 0 0;color:#637188;}
[data-testid="stTabs"] button[role="tab"]:hover{background:#EAF0F8;color:#34445D;}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"]{background:#FFFFFF;border-color:#CBD8EB;border-bottom-color:#FFFFFF;color:#245FC8;font-weight:650;}
[data-testid="stTabs"] [role="tabpanel"]{padding-top:.6rem;}
[data-testid="stSegmentedControl"]{padding:.24rem;background:#EEF2F8;border:1px solid #E4EAF2;border-radius:9px;}
[data-testid="stSegmentedControl"] button{border-radius:7px;min-height:2.45rem;font-weight:550;}
[data-testid="stSegmentedControl"] button[aria-pressed="true"]{background:#FFFFFF;color:#1E2D45;box-shadow:0 1px 3px rgba(28,47,77,.12);}
[data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button{min-height:2.55rem;border-radius:8px;font-weight:600;transition:background-color .16s ease,border-color .16s ease,box-shadow .16s ease,transform .16s ease;}
[data-testid="stFormSubmitButton"] button{background:#2F6FED;border:1px solid #2F6FED;color:#FFFFFF;box-shadow:0 2px 5px rgba(47,111,237,.16);}
[data-testid="stFormSubmitButton"] button:hover{background:#245FC8;border-color:#245FC8;box-shadow:0 3px 8px rgba(47,111,237,.20);}
[data-testid="stButton"] button[kind="secondary"]{background:#FFFFFF;border:1px solid #DCE4EF;color:#34445D;box-shadow:0 1px 1px rgba(28,47,77,.025);}
[data-testid="stButton"] button[kind="tertiary"]{background:transparent;border:1px solid transparent;color:#52627A;}
[data-testid="stButton"] button[kind="tertiary"]:hover{background:#EEF3FA;color:#24344C;}
[data-testid="stButton"] button[kind="secondary"]:hover{background:#F4F7FC;border-color:#B9C9DF;color:#1E2D45;}
[data-testid="stButton"] button[kind="primary"],[data-testid="stFormSubmitButton"] button[kind="primary"]{background:#2F6FED;border:1px solid #2F6FED;color:#FFFFFF;box-shadow:0 2px 5px rgba(47,111,237,.18);}
[data-testid="stButton"] button[kind="primary"]:hover,[data-testid="stFormSubmitButton"] button[kind="primary"]:hover{background:#245FC8;border-color:#245FC8;box-shadow:0 3px 8px rgba(47,111,237,.22);}
[data-testid="stTextInput"] input,[data-testid="stNumberInput"] input,[data-testid="stDateInput"] input,[data-testid="stTextArea"] textarea{border-radius:8px;}
[data-testid="stVerticalBlockBorderWrapper"]{border-color:#E4EAF2!important;border-radius:10px!important;box-shadow:0 1px 2px rgba(28,47,77,.025);}
.st-key-planning_area_shell{background:#FFFFFF;border:1px solid #C7D3E3;border-radius:12px;padding:.9rem 1rem;box-shadow:0 2px 7px rgba(28,47,77,.045);}
.st-key-planning_area_shell h4{font-size:1.08rem;font-weight:650;margin-top:.8rem;letter-spacing:-.02em;}
.st-key-planning_area_shell hr{border-color:#E8EDF4;margin:1.2rem 0;}
.st-key-performance_work_record_shell{background:#FFFFFF;border:1px solid #D7E0EB;border-radius:12px;padding:1rem 1.1rem;box-shadow:0 2px 7px rgba(28,47,77,.04);}
</style>""", unsafe_allow_html=True)

db.init_db()

# (제목, url_path, 모듈) — 아이콘을 메뉴 글자와 같은 텍스트 줄에 표시
MENU = [
    ("📊 대시보드", "dashboard", dashboard),
    ("🗓️ 휴가 캘린더", "calendar", calendar_page),
    ("📈 검증 업무 로그", "worklog", worklog_page),
    ("🧭 (PM) 검증 일정 관리", "work-planning", internal_work_page),
    ("✍️ 검증 업무 기록", "performance", performance_page),
    ("🤝 대체 담당자", "backup", backup_page),
    ("📝 인수인계", "handover", handover_page),
    ("📧 메일 요약", "mail", mail_page),
    ("💬 AI 비서", "assistant", assistant_page),
    ("🗂️ 데이터 관리", "data", data_page),
]


def _runner(module):
    def run():
        module.render(db.load_team_data(), db.load_rules())
    return run


with st.sidebar:  # 메뉴 아래에 표시되는 상태 정보
    st.markdown("## VACON")
    st.caption("업무와 휴가를 잇는 AI 관리 플랫폼")
    st.divider()
    st.caption(f"기준일　{today()}")
    st.caption("ALIRA AI　" + ("연결됨" if llm.llm_available() else "미설정"))

pages = [st.Page(_runner(m), title=t, url_path=u, default=(n == 0))
         for n, (t, u, m) in enumerate(MENU)]
# st.page_link() in dynamic navigation must receive the registered StreamlitPage,
# not a URL string that is normally used by the multipage file convention.
page_by_path = {page.url_path: page for page in pages}
dashboard.VERSION_PAGE = page_by_path["work-planning"]
dashboard.INTERNAL_WORK_PAGE = page_by_path["work-planning"]
internal_work_page.WORKLOG_PAGE = page_by_path["worklog"]
st.navigation(pages, position="sidebar").run()
