"""데이터 관리 > 시트 연동 탭. 구글 시트 탭별 CSV를 읽어 열을 매핑하고, 미리보기 후 반영한다.
역할: 업무 목록 / 일정(마감일) / 작업 로그(동적 검증·요구사양서 작성, 열 문자 기반)."""
import pandas as pd
import streamlit as st

from data import db
from services import sheet_sync as ss
from services import worklog as wl
from services.models import required_skills
from views.common import show_df

NONE = "(사용 안 함)"
ROLE_LABEL = {
    "log_dynamic": "작업 로그 — 동적 검증 (완료 Req / 완료 Func / 실행 TC)",
    "log_req": "작업 로그 — 요구사양서 작성 (완료 일감)",
    "tasks": "업무 목록 (업무 생성·갱신)",
    "schedule": "일정 (기존 업무의 마감일만 갱신)",
}


def _load_text(url, src, uploaded):
    if uploaded is not None:
        return uploaded.read().decode("utf-8-sig", errors="replace")
    return ss.fetch_csv(url, src["gid"])


def _rows(text):
    import csv, io
    return list(csv.reader(io.StringIO(text)))


def _raw_header(text, header_row):
    rows = _rows(text)
    if header_row < 1 or header_row > len(rows):
        raise ss.SheetError(f"헤더 행({header_row})이 데이터 범위를 벗어났습니다(총 {len(rows)}행).")
    return [h.strip() for h in rows[header_row - 1]]


def _preview_df(text, n=12):
    """시트 원본 상단 n행을 열 문자(A, B, …)와 함께 보여주기 위한 표."""
    rows = _rows(text)[:n]
    width = max((len(r) for r in rows), default=0)
    df = pd.DataFrame([r + [""] * (width - len(r)) for r in rows], columns=[wl.col_letter(i) for i in range(width)])
    df.index = range(1, len(df) + 1)
    return df


def render(team, rules):
    cfg = db.load_sync_config()
    st.caption("구글 시트의 탭을 읽어 업무 로그·업무·마감일을 갱신합니다. 시트가 '링크가 있는 모든 사용자 - 뷰어'로 공유되어 있어야 직접 연결됩니다. "
               "연결이 막히면 탭을 CSV로 내려받아 업로드해도 같은 방식으로 처리됩니다.")
    url = st.text_input("구글 시트 주소", value=cfg["sheet"], placeholder="https://docs.google.com/spreadsheets/d/…")
    if st.button("주소 저장"):
        try:
            ss.extract_sheet_id(url)
            db.save_sync_config({**cfg, "sheet": url})
            st.success("저장했습니다.")
            st.rerun()
        except ss.SheetError as e:
            st.error(str(e))

    st.subheader("① 연동할 탭 설정")
    labels = [s["label"] for s in cfg["sources"]]
    pick = st.selectbox("설정 선택", [*labels, "+ 새 탭 추가"])
    cur = next((s for s in cfg["sources"] if s["label"] == pick), {}) if pick != "+ 새 탭 추가" else {}
    k = pick
    c = st.columns(3)
    label = c[0].text_input("탭 이름(표시용)", cur.get("label", ""), key=f"lb_{k}", help="예: 2. Dynamic_Verification")
    gid = c[1].text_input("탭 gid", str(cur.get("gid", "")), key=f"gid_{k}", help="시트에서 해당 탭을 열었을 때 주소 끝 '#gid=' 뒤 숫자")
    roles = list(ROLE_LABEL)
    role = c[2].selectbox("역할", roles, index=roles.index(cur.get("role", "log_dynamic")), format_func=ROLE_LABEL.get, key=f"role_{k}")
    st.caption("헤더 행은 파일에서 열 이름을 찾기 위해 쓰는 설정입니다. 보통 첫 행에 열 이름이 있으므로 자동으로 읽습니다. 위에 설명이나 빈 줄이 있을 때만 조정하세요.")
    manual_header = st.checkbox("파일 앞부분에 설명/빈 줄이 있어 헤더 행을 직접 지정", value=False,
                                key=f"manual_hr_{k}", help="예: 1행은 제목 설명, 2행에 업무명·담당자 같은 열 이름이 있으면 2를 선택합니다.")
    header_row = (st.number_input("열 이름이 있는 행 번호 (첫 행부터 1)", 1, 50,
                                  int(cur.get("header_row", 1)), key=f"hr_{k}")
                  if manual_header else int(cur.get("header_row", 1)))
    with st.expander("헤더 행이 무엇인가요?"):
        st.write("헤더 행은 실제 열 제목이 들어 있는 행입니다. 예를 들어 첫 번째 행에 담당자, 시작일, 종료일이 있다면 1을 선택합니다. 제목 위에 설명이나 빈 행이 있다면 실제 열 제목이 있는 행을 선택하세요.")
    is_log = role in wl.LOG_ROLES
    skills = sorted(set(team.skills["skill"])) if len(team.skills) else []
    req = ""
    imp = "(미입력)"
    if role == "tasks":
        c = st.columns(2)
        sel = c[0].multiselect("새 업무의 필수 역량(여러 개 가능)", skills,
                               default=[s for s in required_skills(cur.get("required_skill")) if s in skills], key=f"rq_{k}")
        req = ", ".join(sel)
        imp = c[1].selectbox("새 업무의 기본 중요도", ["(미입력)", "상", "중", "하"],
                             index=(["(미입력)", "상", "중", "하"].index(cur["default_importance"]) if cur.get("default_importance") in ("상", "중", "하") else 0),
                             key=f"imp_{k}", help="시트에 중요도가 없으면 '미입력'을 권장합니다(위험도가 '데이터 부족'으로 표시되어 입력을 유도).")
    uploaded = st.file_uploader("직접 연결이 안 될 때: 이 탭의 CSV 업로드(선택)", type=["csv"], key=f"up_{k}")

    draft = {"label": label.strip(), "gid": gid.strip(), "role": role, "header_row": int(header_row)}
    if st.button("열 목록 불러오기", key=f"hdr_{k}"):
        try:
            text = _load_text(url or cfg["sheet"], draft, uploaded)
            rows = _rows(text)
            guess = wl.guess_header_row(rows, cur.get("headers")) if is_log else ss.guess_csv_header_row(rows)
            effective_row = int(draft["header_row"]) if manual_header else (guess or 1)
            draft["header_row"] = effective_row
            if not manual_header and not guess:
                st.session_state["sync_headers"] = {"key": k, "needs_header": True, "preview": _preview_df(text), "guess": None}
            else:
                if is_log:
                    raw = _raw_header(text, effective_row)
                    st.session_state["sync_headers"] = {"key": k, "raw": raw, "headers": raw, "n": len(rows) - effective_row,
                        "preview": _preview_df(text), "guess": guess, "header_row": effective_row}
                else:
                    headers, recs = ss.read_table(text, effective_row)
                    raw = _raw_header(text, effective_row)
                    st.session_state["sync_headers"] = {"key": k, "headers": headers, "raw": raw, "n": len(recs),
                        "preview": _preview_df(text), "guess": guess, "header_row": effective_row}
        except ss.SheetError as e:
            st.error(str(e))
    hdr = st.session_state.get("sync_headers")
    header_uncertain = bool(hdr and hdr["key"] == k and hdr.get("needs_header"))
    loaded = bool(hdr and hdr["key"] == k and not header_uncertain)
    if loaded or header_uncertain:
        if header_uncertain:
            st.warning("열 제목 행을 자동으로 찾지 못했습니다. 미리보기에서 열 이름이 있는 행을 확인한 뒤 '파일 앞부분에 설명/빈 줄이 있어 헤더 행을 직접 지정'을 켜고 다시 불러오세요.")
        else:
            st.success(f"열 {len(hdr['headers'])}개, 데이터 약 {hdr['n']}행을 읽었습니다.")
        if hdr.get("preview") is not None:
            st.write("**시트 원본 상단(앱이 실제로 읽은 내용)** — 행 번호는 왼쪽, 열 문자는 위쪽입니다.")
            show_df_idx(hdr["preview"])
            st.caption(f"헤더로 사용 중인 행: {hdr.get('header_row', 1)}행. 이 행에서 열 이름을 읽어 아래 매핑 항목을 채웁니다.")
            raw = hdr.get("raw", [])
            blank = [i + 1 for i, name in enumerate(raw) if not name.strip()]
            duplicates = sorted({name for name in raw if name.strip() and raw.count(name) > 1})
            if blank or duplicates:
                st.warning("선택한 제목 행에 " + (f"빈 제목 열 {blank}번" if blank else "")
                           + (" · " if blank and duplicates else "")
                           + (f"중복 제목 {', '.join(duplicates)}" if duplicates else "")
                           + "이 있습니다. 실제 제목 행인지 확인하고 필요하면 원본 CSV/시트를 정리하세요.")

    if is_log and (loaded or not hdr or hdr.get("key") != k or not hdr.get("needs_header")):
        track = wl.LOG_ROLES[role]
        st.write("**② 열 매핑(열 문자)** — 시트의 열 문자(A, B, …)를 입력합니다. 제목을 불러오면 확인용으로 옆에 표시됩니다.")
        cols, grid = {}, st.columns(3)
        raw = hdr["raw"] if loaded else []
        guessed_func = wl.guess_func_column(raw) if track == "dynamic" and raw else None
        guessed_subsystem = wl.guess_subsystem_column(raw) if raw else None
        defaults = {**db.DEFAULT_SYNC["sources"][0 if track == "dynamic" else 1]["columns"], **cur.get("columns", {})}
        if track == "dynamic" and not defaults.get("func") and guessed_func:
            defaults["func"] = guessed_func
            marker_key = f"func_guess_applied_{k}"
            marker = tuple(raw)
            if st.session_state.get(marker_key) != marker:
                st.session_state[f"let_{k}_func"] = guessed_func
                st.session_state[marker_key] = marker
        if not defaults.get("subsystem") and guessed_subsystem:
            marker_key = f"subsystem_guess_applied_{k}"
            marker = tuple(raw)
            if st.session_state.get(marker_key) != marker:
                st.session_state[f"let_{k}_subsystem"] = guessed_subsystem
                st.session_state[marker_key] = marker
        for i, (f, lab) in enumerate(wl.LOG_FIELDS[track]):
            letter = grid[i % 3].text_input(f"{lab} 열", defaults.get(f, ""), key=f"let_{k}_{f}", max_chars=3).strip().upper()
            if letter:
                cols[f] = letter
                if raw and wl.valid_letter(letter):
                    idx = wl.col_index(letter)
                    grid[i % 3].caption("→ 시트 제목: " + (raw[idx] if idx < len(raw) and raw[idx] else "(비어 있음)"))
        if "subsystem" not in cols:
            st.caption("서브시스템 열은 선택 항목입니다. 매핑하지 않으면 가져온 로그는 ‘서브시스템 미입력’으로 표시됩니다.")
        o = st.columns(2)
        fill = o[0].checkbox("병합 셀 대응: 빈 날짜·담당자는 위 행 값 사용", value=bool(cur.get("fill_down")), key=f"fill_{k}",
                             help="날짜나 담당자 칸이 병합되어 첫 행에만 값이 있는 시트에서 켜세요.")
        yr = o[1].number_input("연도(날짜에 연도가 없을 때만 사용, 0=자동 추정)", 0, 2100, int(cur.get("year") or 0), key=f"yr_{k}")
        if st.button("이 탭 설정 저장", key=f"save_{k}"):
            if not draft["label"] or not draft["gid"].isdigit():
                st.error("탭 이름과 숫자 gid를 입력하세요.")
            elif any(not wl.valid_letter(c_) for c_ in cols.values()):
                st.error("열 문자는 A, B, …, AA 형식으로 입력하세요.")
            elif "date" not in cols or "member" not in cols or not any(m in cols for m in wl.METRIC_COLS[track]):
                st.error("날짜·담당자 열과 집계할 숫자 열을 최소 하나 입력하세요.")
            elif track == "dynamic" and "func" not in cols:
                st.error("버전별 검증 완료 수에 연결할 '완료 Func' 열을 지정하세요. 헤더를 찾지 못하면 시트에서 Func 열 문자를 확인해 입력합니다.")
            else:
                snap = ({f: (raw[wl.col_index(c_)] if wl.col_index(c_) < len(raw) else "") for f, c_ in cols.items()} if raw
                        else (cur.get("headers", {}) if cur.get("columns") == cols else {}))
                new = {**draft, "columns": cols, "headers": snap, "fill_down": fill, "year": int(yr) or None}
                srcs = [s for s in cfg["sources"] if s["label"] not in (pick, draft["label"])] + [new]
                db.save_sync_config({**db.load_sync_config(), "sources": srcs})
                st.success("저장했습니다." + ("" if snap else " (열 제목을 불러온 뒤 저장하면 시트 구조 변경을 감지할 수 있습니다.)"))
                st.rerun()
    elif (loaded and not hdr.get("needs_header")) or cur.get("columns"):
        headers = hdr["headers"] if loaded else list(dict.fromkeys(cur.get("columns", {}).values()))
        st.write("**② 열 매핑** — 시트의 어느 열이 VACON의 어떤 항목인지 지정합니다.")
        fields = ss.TASK_FIELDS if role == "tasks" else ss.SCHEDULE_FIELDS
        cols, grid = {}, st.columns(3)
        for i, (f, lab) in enumerate(fields):
            opts = [NONE, *headers]
            prev = cur.get("columns", {}).get(f)
            sel = grid[i % 3].selectbox(lab, opts, index=opts.index(prev) if prev in opts else 0, key=f"map_{k}_{f}")
            if sel != NONE:
                cols[f] = sel
        if st.button("이 탭 설정 저장", key=f"save_{k}"):
            if not draft["label"] or not draft["gid"].isdigit():
                st.error("탭 이름과 숫자 gid를 입력하세요.")
            elif "name" not in cols or (role == "schedule" and "due" not in cols):
                st.error("필수 항목(업무명" + (", 마감일" if role == "schedule" else "") + ")을 매핑하세요.")
            else:
                new = {**draft, "columns": cols, "required_skill": req, "default_importance": None if imp == "(미입력)" else imp}
                srcs = [s for s in cfg["sources"] if s["label"] not in (pick, draft["label"])] + [new]
                db.save_sync_config({**db.load_sync_config(), "sources": srcs})
                st.success("저장했습니다.")
                st.rerun()
    else:
        st.info("'열 목록 불러오기'를 눌러 시트의 열을 확인한 뒤 매핑하세요.")
    if cur and st.button("🗑️ 이 탭 설정 삭제", key=f"del_{k}"):
        db.save_sync_config({**cfg, "sources": [s for s in cfg["sources"] if s["label"] != pick]})
        st.rerun()

    st.divider()
    st.subheader("③ 동기화 (미리보기 → 반영)")
    ready = [s for s in cfg["sources"] if str(s.get("gid", "")).isdigit() and s.get("columns")]
    if not ready:
        st.write("동기화할 수 있는 탭이 없습니다. 위에서 탭의 gid와 열 매핑을 저장하세요.")
        return
    src_label = st.selectbox("동기화할 탭", [s["label"] for s in ready], key="sync_pick")
    src = next(s for s in ready if s["label"] == src_label)
    up2 = st.file_uploader("직접 연결이 안 될 때: CSV 업로드(선택)", type=["csv"], key="sync_upload")
    if st.button("🔄 시트에서 새로고침 (미리보기)"):
        try:
            text = _load_text(cfg["sheet"] or url, src, up2)
            if src["role"] in wl.LOG_ROLES:
                if src["role"] == "log_dynamic" and not src.get("columns", {}).get("func"):
                    st.error("동적검증 진행률을 계산하려면 '완료 Func' 시트 열을 먼저 매핑하고 설정을 저장하세요.")
                    return
                st.session_state["sync_plan"] = {"label": src_label, "kind": "log", "res": wl.parse_logs(text, src, team)}
            else:
                _, recs = ss.read_table(text, int(src.get("header_row", 1)))
                missing = [v for v in src["columns"].values() if recs and v not in recs[0]]
                if missing:
                    st.error("시트의 열 이름이 바뀌었거나 사라졌습니다: " + ", ".join(missing) + " → 위에서 열 매핑을 다시 설정하세요.")
                else:
                    st.session_state["sync_plan"] = {"label": src_label, "kind": "tasks", "plan": ss.plan_sync(team, recs, src)}
        except ss.SheetError as e:
            st.error(str(e))

    sp = st.session_state.get("sync_plan")
    if not (sp and sp["label"] == src_label):
        return
    if sp["kind"] == "log":
        _show_log_plan(team, sp, src_label)
    else:
        _show_task_plan(team, sp["plan"], src_label)


def show_df_idx(df):
    st.dataframe(df)


def _show_log_plan(team, sp, src_label):
    res = sp["res"]
    dg = res.get("diag", {})
    if dg.get("bad_date_rows") or dg.get("blank_date_rows"):
        st.error(f"날짜를 읽지 못한 행이 있습니다(날짜 열 {dg['date_col']}). "
                 + (f"읽지 못한 값 예시: " + ", ".join(f"'{v}'" for v, _ in dg.get("bad_dates", [])[:6]) if dg.get("bad_dates") else ""))
        if dg.get("header_hint"):
            st.warning(dg["header_hint"])
    ents = res["entries"]
    cur = int((team.logs["source"] == src_label).sum()) if len(team.logs) else 0
    m = st.columns(4)
    m[0].metric("읽은 기록", f"{len(ents)}건")
    m[1].metric("현재 저장된 기록", f"{cur}건", help="반영하면 이 탭의 기존 기록은 시트 내용으로 교체됩니다.")
    m[2].metric("건너뛴 행", res["skipped"])
    m[3].metric("기간", f"{res['date_range'][0]} ~ {res['date_range'][1]}" if res["date_range"] else "-")
    if ents:
        df = pd.DataFrame(ents)
        cols = list(wl.LOG_METRICS[res["track"]])
        agg = df.groupby("member_id")[cols].sum(min_count=1).reset_index()
        agg.insert(0, "팀원", agg["member_id"].map(team.name))
        agg = agg.drop(columns="member_id").rename(columns={c: wl.METRIC_LABELS[c] + " 합계" for c in cols})
        st.write("**팀원별 합계(미리보기)** — 값이 시트와 맞는지 확인하세요.")
        show_df(agg)
        by_version = df.assign(버전=df["version"].fillna("").astype(str).str.strip().replace("", "(버전 미입력)"))
        st.write("**버전별 원본 행 수(미리보기)**")
        show_df(by_version.groupby("버전", as_index=False).size().rename(columns={"size": "기록 행 수"}))
        non_pool = [team.name(i) for i in df["member_id"].unique() if i not in team.unit_pool()]
        if non_pool:
            st.warning("단위 테스트 인원이 아닌 팀원의 기록이 포함되어 있습니다: " + ", ".join(non_pool) + " (집계·예측은 단위 테스트 인원만 대상으로 합니다)")
    if res["unmatched_members"]:
        st.warning("팀원 목록에 없는 이름이라 건너뜀: " + ", ".join(res["unmatched_members"]) + " — 이름 표기를 확인하거나 데이터 관리에 팀원을 추가하세요.")
    if res["text_cells"]:
        st.info(f"숫자가 아닌 텍스트 셀 {res['text_cells']}개를 '항목 개수'로 세었습니다(쉼표·줄바꿈 기준). 의도와 다르면 알려주세요.")
    if res["warnings"]:
        with st.expander(f"해석 경고 {len(res['warnings'])}건"):
            for w in res["warnings"][:200]:
                st.write("- " + w)
    has_sample = len(team.logs) and (team.logs["source"] == "샘플").any()
    drop_sample = st.checkbox("샘플(가상) 로그 삭제", value=True, key="drop_sample") if has_sample and src_label != "샘플" else False
    if ents and st.button("✅ 미리보기 내용 반영(이 탭의 기존 기록 교체)", key="log_apply"):
        try:
            r = db.replace_logs(src_label, ents)
            if drop_sample:
                db.delete_logs("샘플")
            st.session_state.pop("sync_plan", None)
            suffix = f" · 내부 DB 관리 버전 {r['ignored_internal']}건은 시트에서 제외했습니다." if r.get("ignored_internal") else ""
            st.success(f"기존 {r['removed']}건을 {r['added']}건으로 교체했습니다.{suffix}")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    elif not ents:
        st.error("반영할 기록이 없습니다. 열 매핑·헤더 행 번호·날짜 형식을 확인하세요.")


def _show_task_plan(team, p, src_label):
    m = st.columns(4)
    m[0].metric("추가", len(p["add"])); m[1].metric("변경", len(p["update"]))
    m[2].metric("변경 없음", p["unchanged"]); m[3].metric("시트에서 사라짐", len(p["missing"]))
    if p["add"]:
        st.write("**추가될 업무**")
        show_df(pd.DataFrame([{"업무": t["name"], "담당자": team.name(t["owner_id"]) if t["owner_id"] else "미지정",
                               "상태": t["status"] or "대기", "마감일": t["due_date"] or "미입력",
                               "필수 역량": t["required_skill"] or "미입력", "중요도": t["importance"] or "미입력"} for t in p["add"]]))
    if p["update"]:
        st.write("**변경될 업무**")
        names = {"owner_id": "담당자", "status": "상태", "progress": "진행률", "due_date": "마감일", "remaining": "남은 작업", "notes": "주의사항"}
        fmt = lambda f, v: team.name(v) if f == "owner_id" and v is not None else ("미입력" if v is None else str(v))
        show_df(pd.DataFrame([{"업무": u["name"], "변경 내용": " / ".join(f"{names[f]}: {fmt(f, o)} → {fmt(f, n)}" for f, (o, n) in u["changes"].items())} for u in p["update"]]))
    if p["unmatched_owners"]:
        st.warning("팀원 목록에 없는 담당자(담당자 미지정으로 처리됨): " + ", ".join(p["unmatched_owners"]))
    if p["unmatched_names"]:
        st.warning("일정에는 있지만 VACON 업무와 이름이 일치하지 않아 건너뜀: " + ", ".join(p["unmatched_names"]))
    if p["missing"]:
        st.info("이전에 이 탭에서 가져왔지만 이번 시트에는 없는 업무(자동 삭제하지 않음): " + ", ".join(p["missing"]))
    if p["warnings"]:
        with st.expander(f"해석 경고 {len(p['warnings'])}건"):
            for w in p["warnings"]:
                st.write("- " + w)
    if p["add"] or p["update"]:
        if st.button("✅ 미리보기 내용 반영", key="sync_apply"):
            r = db.apply_sync_plan(p, src_label)
            st.session_state.pop("sync_plan", None)
            st.success(f"추가 {r['added']}건, 변경 {r['updated']}건을 반영했습니다.")
            st.rerun()
    else:
        st.success("반영할 변경 사항이 없습니다.")
