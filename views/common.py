"""화면 공통 헬퍼."""
from __future__ import annotations

import pandas as pd
import streamlit as st
import re
from contextlib import contextmanager

from services.risk import HIGH, INSUFFICIENT, LOW, MEDIUM, format_reasons

LEVEL_COLORS = {HIGH: "#E5484D", MEDIUM: "#F5A524", LOW: "#30A46C", INSUFFICIENT: "#8B8D98"}
BADGE = {HIGH: "높음", MEDIUM: "보통", LOW: "낮음", INSUFFICIENT: "평가 불가"}
WEEKDAY = "월화수목금토일"


def header(title: str, caption: str = "") -> None:
    # Older screens passed emoji prefixes; keep the shared page heading typographic.
    clean_title = re.sub(r"^(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?\s*)+", "", title)
    st.caption("VACON · 업무와 휴가를 잇는 AI 관리 플랫폼")
    st.title(clean_title)
    if caption:
        st.caption(caption)


@contextmanager
def page_surface(key: str):
    """Consistent white bordered surface for a page's primary content."""
    surface_key = f"vacon_surface_{key}"
    st.html(f"""
    <style>
      .st-key-{surface_key} {{
        background: #ffffff;
        border: 1px solid #e7ebf0;
        border-radius: 12px;
        padding: 1.15rem 1.25rem;
        margin: 0.25rem 0 1rem;
      }}
    </style>
    """)
    with st.container(border=True, key=surface_key) as container:
        yield container


def fmt_date(d) -> str:
    return f"{d.month:02d}/{d.day:02d}({WEEKDAY[d.weekday()]})"


def fmt_leave(team, lv: dict) -> str:
    return f"{team.name(lv['member_id'])} · {lv['type']} · {lv['start']}~{lv['end']}"


def risk_table(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """위험도 DataFrame을 표시용으로 변환(배지, 근거 문자열화)."""
    view = df.copy()
    if "위험도" in view:
        view["위험도"] = view["위험도"].map(BADGE)
    if "근거" in view:
        view["근거"] = view["근거"].map(format_reasons)
    return view[cols]


def show_df(df: pd.DataFrame) -> None:
    st.dataframe(df, hide_index=True, width="stretch", alt="업무 현황 및 기록 표")


def errors_box(errors: list[str]) -> bool:
    for e in errors:
        st.error(e)
    return bool(errors)
