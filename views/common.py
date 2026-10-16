"""화면 공통 헬퍼."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from services.risk import HIGH, INSUFFICIENT, LOW, MEDIUM, format_reasons

LEVEL_COLORS = {HIGH: "#E5484D", MEDIUM: "#F5A524", LOW: "#30A46C", INSUFFICIENT: "#8B8D98"}
BADGE = {HIGH: "🔴 High", MEDIUM: "🟠 Medium", LOW: "🟢 Low", INSUFFICIENT: "⚪ 데이터 부족"}
WEEKDAY = "월화수목금토일"


def header(title: str, caption: str = "") -> None:
    st.title(title)
    if caption:
        st.caption(caption)


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
    st.dataframe(df, hide_index=True)


def errors_box(errors: list[str]) -> bool:
    for e in errors:
        st.error(e)
    return bool(errors)
