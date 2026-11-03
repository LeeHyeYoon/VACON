"""SQLite 저장소. 화면/업무 로직과 분리된 데이터 접근 계층."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from services.config import merge_rules
from services.models import TeamData, isnull, make_team, today

from .sample_data import build_sample

DEFAULT_DB = Path(__file__).parent / "vacon.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    role TEXT DEFAULT '', workload INTEGER DEFAULT 0, contact TEXT DEFAULT '', mbti TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS skills(member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    skill TEXT NOT NULL, level INTEGER DEFAULT 1, PRIMARY KEY(member_id, skill));
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    owner_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    backup_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    importance TEXT, status TEXT, progress INTEGER DEFAULT 0, due_date TEXT, required_skill TEXT DEFAULT '',
    remaining TEXT DEFAULT '', notes TEXT DEFAULT '', load_pct INTEGER, source TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS leaves(id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, leave_type TEXT NOT NULL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS work_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, track TEXT NOT NULL,
    log_date TEXT NOT NULL, member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    version TEXT DEFAULT '', subsystem TEXT DEFAULT '', category TEXT DEFAULT '', req_done REAL DEFAULT 0, tc_done REAL DEFAULT 0,
    func_done REAL DEFAULT 0, issue_done REAL DEFAULT 0, new_req_done REAL, modified_req_done REAL, deleted_req_done REAL,
    defect_done REAL, tc_created_done REAL, tc_modified_done REAL, tc_deleted_done REAL, notes TEXT DEFAULT '', src_row INTEGER);
CREATE INDEX IF NOT EXISTS idx_logs_member_date ON work_logs(member_id, log_date);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS version_projects(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL COLLATE NOCASE UNIQUE,
    status TEXT NOT NULL DEFAULT '준비',
    spec_start TEXT, spec_due TEXT, verify_start TEXT, verify_due TEXT,
    total_items INTEGER NOT NULL DEFAULT 0, spec_total_items INTEGER NOT NULL DEFAULT 0,
    verify_spec_total_items INTEGER NOT NULL DEFAULT 0,
    verify_total_items INTEGER NOT NULL DEFAULT 0, spec_done INTEGER NOT NULL DEFAULT 0,
    verify_done INTEGER NOT NULL DEFAULT 0,
    spec_registered INTEGER NOT NULL DEFAULT 0, verify_registered INTEGER NOT NULL DEFAULT 0,
    spec_owner_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    verify_owner_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    spec_owner_ids TEXT NOT NULL DEFAULT '[]', verify_owner_ids TEXT NOT NULL DEFAULT '[]',
    source_info TEXT DEFAULT '', notes TEXT DEFAULT '', holidays TEXT DEFAULT '',
    daily_spec_capacity INTEGER, daily_verify_capacity INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS version_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES version_projects(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    field TEXT NOT NULL, old_value TEXT, new_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_version_history_project ON version_history(project_id, changed_at);
CREATE TABLE IF NOT EXISTS version_subsystems(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES version_projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, name)
);
CREATE TABLE IF NOT EXISTS spec_workloads(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES version_projects(id) ON DELETE CASCADE,
    subsystem_id INTEGER NOT NULL REFERENCES version_subsystems(id) ON DELETE CASCADE,
    total_tasks INTEGER NOT NULL DEFAULT 0 CHECK(total_tasks >= 0),
    difficulty INTEGER NOT NULL DEFAULT 1 CHECK(difficulty BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, subsystem_id)
);
CREATE TABLE IF NOT EXISTS verification_workloads(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES version_projects(id) ON DELETE CASCADE,
    subsystem_id INTEGER NOT NULL REFERENCES version_subsystems(id) ON DELETE CASCADE,
    total_functions INTEGER NOT NULL DEFAULT 0 CHECK(total_functions >= 0),
    total_requirements INTEGER NOT NULL DEFAULT 0 CHECK(total_requirements >= 0),
    difficulty INTEGER NOT NULL DEFAULT 1 CHECK(difficulty BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, subsystem_id)
);
CREATE TABLE IF NOT EXISTS spec_work_logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES version_projects(id) ON DELETE CASCADE,
    subsystem_id INTEGER NOT NULL REFERENCES version_subsystems(id) ON DELETE CASCADE,
    log_date TEXT NOT NULL,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    completed_tasks INTEGER NOT NULL DEFAULT 0 CHECK(completed_tasks >= 0),
    new_requirements INTEGER NOT NULL DEFAULT 0 CHECK(new_requirements >= 0),
    modified_requirements INTEGER NOT NULL DEFAULT 0 CHECK(modified_requirements >= 0),
    deleted_requirements INTEGER NOT NULL DEFAULT 0 CHECK(deleted_requirements >= 0),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_spec_logs_project_date ON spec_work_logs(project_id, log_date);
CREATE TABLE IF NOT EXISTS version_dynamic_work_logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES version_projects(id) ON DELETE CASCADE,
    subsystem_id INTEGER NOT NULL REFERENCES version_subsystems(id) ON DELETE CASCADE,
    log_date TEXT NOT NULL,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    completed_requirements INTEGER NOT NULL DEFAULT 0 CHECK(completed_requirements >= 0),
    completed_functions INTEGER NOT NULL DEFAULT 0 CHECK(completed_functions >= 0),
    defects INTEGER NOT NULL DEFAULT 0 CHECK(defects >= 0),
    tc_executed INTEGER NOT NULL DEFAULT 0 CHECK(tc_executed >= 0),
    tc_created INTEGER NOT NULL DEFAULT 0 CHECK(tc_created >= 0),
    tc_modified INTEGER NOT NULL DEFAULT 0 CHECK(tc_modified >= 0),
    tc_deleted INTEGER NOT NULL DEFAULT 0 CHECK(tc_deleted >= 0),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_version_dynamic_logs_project_date ON version_dynamic_work_logs(project_id, log_date);
CREATE TABLE IF NOT EXISTS version_assignments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES version_projects(id) ON DELETE CASCADE,
    phase TEXT NOT NULL CHECK(phase IN ('spec', 'verify')),
    subsystem_id INTEGER NOT NULL REFERENCES version_subsystems(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    allocated_tasks INTEGER NOT NULL DEFAULT 0 CHECK(allocated_tasks >= 0),
    allocated_functions INTEGER NOT NULL DEFAULT 0 CHECK(allocated_functions >= 0),
    allocated_requirements INTEGER NOT NULL DEFAULT 0 CHECK(allocated_requirements >= 0),
    effort_score REAL NOT NULL DEFAULT 0,
    feasibility TEXT NOT NULL DEFAULT '판단 자료 부족',
    rationale TEXT NOT NULL DEFAULT '',
    approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, phase, subsystem_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_assignments_project_phase ON version_assignments(project_id, phase);
"""


def _path(path=None) -> str:
    return str(path or os.environ.get("VACON_DB") or DEFAULT_DB)


@contextmanager
def connect(path=None):
    conn = sqlite3.connect(_path(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _iso(d) -> str | None:
    return None if isnull(d) else d.isoformat()


def _date(s) -> date | None:
    return None if isnull(s) or s == "" else date.fromisoformat(s)


def init_db(path=None, seed: bool = True) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)
        _migrate(c)
        if seed and c.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0:
            _insert_sample(c)


def _migrate(c) -> None:
    """기존 DB에 새 컬럼 추가(데이터 유지)."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(members)")}
    tcols = {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
    if "source" not in tcols:
        c.execute("ALTER TABLE tasks ADD COLUMN source TEXT DEFAULT ''")
    if "mbti" not in cols:
        c.execute("ALTER TABLE members ADD COLUMN mbti TEXT DEFAULT ''")
    log_cols = {r["name"] for r in c.execute("PRAGMA table_info(work_logs)")}
    if "subsystem" not in log_cols:
        c.execute("ALTER TABLE work_logs ADD COLUMN subsystem TEXT DEFAULT ''")
        c.execute("UPDATE work_logs SET subsystem='' WHERE subsystem IS NULL")
        log_cols.add("subsystem")
    if "func_done" not in log_cols:
        c.execute("ALTER TABLE work_logs ADD COLUMN func_done REAL DEFAULT 0")
    for name in ("new_req_done", "modified_req_done", "deleted_req_done", "defect_done", "tc_created_done",
                 "tc_modified_done", "tc_deleted_done", "notes"):
        if name not in log_cols:
            c.execute(f"ALTER TABLE work_logs ADD COLUMN {name} TEXT DEFAULT NULL" if name == "notes"
                      else f"ALTER TABLE work_logs ADD COLUMN {name} REAL DEFAULT NULL")
    vcols = {r["name"] for r in c.execute("PRAGMA table_info(version_projects)")}
    if "spec_owner_ids" not in vcols:
        c.execute("ALTER TABLE version_projects ADD COLUMN spec_owner_ids TEXT NOT NULL DEFAULT '[]'")
    if "verify_owner_ids" not in vcols:
        c.execute("ALTER TABLE version_projects ADD COLUMN verify_owner_ids TEXT NOT NULL DEFAULT '[]'")
    if "spec_total_items" not in vcols:
        c.execute("ALTER TABLE version_projects ADD COLUMN spec_total_items INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE version_projects SET spec_total_items=total_items")
        vcols.add("spec_total_items")
    if "verify_spec_total_items" not in vcols:
        c.execute("ALTER TABLE version_projects ADD COLUMN verify_spec_total_items INTEGER NOT NULL DEFAULT 0")
    if "verify_total_items" not in vcols:
        c.execute("ALTER TABLE version_projects ADD COLUMN verify_total_items INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE version_projects SET verify_total_items=total_items")
    if "spec_registered" not in vcols:
        c.execute("ALTER TABLE version_projects ADD COLUMN spec_registered INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE version_projects SET spec_registered=1 WHERE spec_start IS NOT NULL OR spec_due IS NOT NULL OR spec_owner_id IS NOT NULL OR spec_owner_ids!='[]' OR (spec_total_items>0 AND verify_total_items=0 AND verify_start IS NULL AND verify_due IS NULL AND verify_owner_id IS NULL AND verify_owner_ids='[]')")
    if "verify_registered" not in vcols:
        c.execute("ALTER TABLE version_projects ADD COLUMN verify_registered INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE version_projects SET verify_registered=1 WHERE verify_start IS NOT NULL OR verify_due IS NOT NULL OR verify_owner_id IS NOT NULL OR verify_owner_ids!='[]'")
    # Earlier builds copied one shared spec_total_items value into both phases.
    # It was never an independently entered dynamic-verification target. Keep the
    # original value on the spec side and audit the duplicate before clearing it once.
    split_key = "migration_internal_spec_totals_split_v2"
    if not c.execute("SELECT 1 FROM settings WHERE key=?", (split_key,)).fetchone():
        for row in c.execute("SELECT id,version,spec_total_items,verify_spec_total_items FROM version_projects").fetchall():
            match = re.fullmatch(r"T\s*(\d+)", str(row["version"] or "").strip(), re.IGNORECASE)
            internal = int(match.group(1)) >= 80 if match else True
            if internal and int(row["spec_total_items"] or 0) > 0 and int(row["verify_spec_total_items"] or 0) == int(row["spec_total_items"]):
                old = int(row["verify_spec_total_items"])
                c.execute("INSERT INTO version_history(project_id,field,old_value,new_value) VALUES (?,?,?,?)",
                          (row["id"], "verify_spec_total_items", str(old), "0 — 기존 공용값 분리, 동적 검증 목표 재입력 필요"))
                c.execute("UPDATE version_projects SET verify_spec_total_items=0 WHERE id=?", (row["id"],))
        c.execute("INSERT INTO settings(key,value) VALUES (?,?)", (split_key, "completed"))


def _insert_sample(c) -> None:
    s = build_sample(today())
    c.executemany("INSERT INTO members(id,name,role,workload,contact) VALUES (?,?,?,?,?)", s["members"])
    c.executemany("INSERT INTO skills(member_id,skill,level) VALUES (?,?,?)", s["skills"])
    c.executemany("""INSERT INTO tasks(id,name,owner_id,backup_id,importance,status,progress,due_date,
                     required_skill,remaining,notes,load_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  [(*t[:7], _iso(t[7]), *t[8:]) for t in s["tasks"]])
    c.executemany("INSERT INTO leaves(id,member_id,start_date,end_date,leave_type,note) VALUES (?,?,?,?,?,?)",
                  [(l[0], l[1], _iso(l[2]), _iso(l[3]), l[4], l[5]) for l in s["leaves"]])
    c.executemany("INSERT INTO work_logs(source,track,log_date,member_id,version,category,req_done,tc_done,issue_done) "
                  "VALUES ('샘플',?,?,?,?,?,?,?,?)", [(g[0], _iso(g[1]), *g[2:]) for g in s["logs"]])


def reset_sample(path=None) -> None:
    with connect(path) as c:
        for t in ("work_logs", "leaves", "tasks", "skills", "members", "settings"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM sqlite_sequence")
        _insert_sample(c)


def load_team_data(path=None) -> TeamData:
    with connect(path) as c:
        members = [dict(r) for r in c.execute("SELECT * FROM members ORDER BY id")]
        skills = [dict(r) for r in c.execute("SELECT * FROM skills ORDER BY member_id, skill")]
        tasks = [dict(r) for r in c.execute("SELECT * FROM tasks ORDER BY id")]
        leaves = [dict(r) for r in c.execute("SELECT * FROM leaves ORDER BY start_date, id")]
        logs = [dict(r) for r in c.execute("SELECT * FROM work_logs ORDER BY log_date, id")]
    for t in tasks:
        t["due_date"] = _date(t["due_date"])
    for l in leaves:
        l["start"], l["end"], l["type"] = _date(l.pop("start_date")), _date(l.pop("end_date")), l.pop("leave_type")
    for g in logs:
        g["date"], g["req"], g["tc"], g["func"], g["issue"] = (_date(g.pop("log_date")), g.pop("req_done"),
            g.pop("tc_done"), g.pop("func_done", 0), g.pop("issue_done"))
        for field, column in (("new_req", "new_req_done"), ("modified_req", "modified_req_done"),
                              ("deleted_req", "deleted_req_done"), ("defect", "defect_done"),
                              ("tc_created", "tc_created_done"), ("tc_modified", "tc_modified_done"),
                              ("tc_deleted", "tc_deleted_done")):
            g[field] = g.pop(column, None)
    return make_team(members, skills, tasks, leaves, logs)


# --- 설정 ---
def load_rules(path=None) -> dict:
    with connect(path) as c:
        row = c.execute("SELECT value FROM settings WHERE key='rules'").fetchone()
    return merge_rules(json.loads(row["value"]) if row else None)


def save_rules(rules: dict, path=None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO settings(key,value) VALUES('rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(rules, ensure_ascii=False),))


# --- 휴가 ---
def add_leave(member_id, start, end, leave_type, note="", path=None) -> int:
    with connect(path) as c:
        return c.execute("INSERT INTO leaves(member_id,start_date,end_date,leave_type,note) VALUES (?,?,?,?,?)",
                         (member_id, _iso(start), _iso(end), leave_type, note)).lastrowid


def update_leave(leave_id, member_id, start, end, leave_type, note="", path=None) -> None:
    with connect(path) as c:
        c.execute("UPDATE leaves SET member_id=?,start_date=?,end_date=?,leave_type=?,note=? WHERE id=?",
                  (member_id, _iso(start), _iso(end), leave_type, note, leave_id))


def delete_leave(leave_id, path=None) -> None:
    with connect(path) as c:
        c.execute("DELETE FROM leaves WHERE id=?", (leave_id,))


# --- 동적검증 버전 프로젝트 ---
_VERSION_DATE_FIELDS = {"spec_start", "spec_due", "verify_start", "verify_due"}


def list_version_projects(path=None) -> list[dict]:
    with connect(path) as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM version_projects")]
    for row in rows:
        for field in _VERSION_DATE_FIELDS:
            row[field] = _date(row[field])
        row["holidays"] = json.loads(row["holidays"] or "[]")
        row["spec_owner_ids"] = json.loads(row["spec_owner_ids"] or "[]") or ([row["spec_owner_id"]] if row["spec_owner_id"] else [])
        row["verify_owner_ids"] = json.loads(row["verify_owner_ids"] or "[]") or ([row["verify_owner_id"]] if row["verify_owner_id"] else [])
    def version_key(row):
        return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                     for part in re.split(r"(\d+)", row["version"]) if part)
    return sorted(rows, key=lambda row: version_key(row), reverse=True)


def get_version_project(project_id, path=None) -> dict | None:
    with connect(path) as c:
        row = c.execute("SELECT * FROM version_projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    for field in _VERSION_DATE_FIELDS:
        result[field] = _date(result[field])
    result["holidays"] = json.loads(result["holidays"] or "[]")
    result["spec_owner_ids"] = json.loads(result["spec_owner_ids"] or "[]") or ([result["spec_owner_id"]] if result["spec_owner_id"] else [])
    result["verify_owner_ids"] = json.loads(result["verify_owner_ids"] or "[]") or ([result["verify_owner_id"]] if result["verify_owner_id"] else [])
    return result


def save_version_project(fields: dict, path=None, project_id: int | None = None) -> int:
    """버전 생성/수정. 일정 변경 이력은 보존하고 중복 버전을 방지한다."""
    version = str(fields.get("version", "")).strip()
    if not version:
        raise ValueError("버전명을 입력하세요.")
    if fields.get("status", "준비") not in ("준비", "사양서 작성 중", "검증 진행 중", "완료", "지연"):
        raise ValueError("지원하지 않는 프로젝트 상태입니다.")
    for start_field, due_field in (("spec_start", "spec_due"), ("verify_start", "verify_due")):
        if fields.get(start_field) and fields.get(due_field) and fields[start_field] > fields[due_field]:
            raise ValueError("시작일은 해당 마감일보다 늦을 수 없습니다.")
    for field in ("total_items", "spec_total_items", "verify_spec_total_items", "verify_total_items", "spec_done", "verify_done",
                  "daily_spec_capacity", "daily_verify_capacity"):
        if fields.get(field) is not None and int(fields[field]) < 0:
            raise ValueError("대상 수와 일일 처리량은 0 이상이어야 합니다.")
    values = {
        "version": version, "status": fields.get("status", "준비"),
        **{k: _iso(fields.get(k)) for k in _VERSION_DATE_FIELDS},
        "total_items": max(0, int(fields.get("total_items", max(int(fields.get("spec_total_items", 0) or 0),
                                                                  int(fields.get("verify_total_items", 0) or 0))) or 0)),
        "spec_total_items": max(0, int(fields.get("spec_total_items", fields.get("total_items", 0)) or 0)),
        "verify_spec_total_items": max(0, int(fields.get("verify_spec_total_items", 0) or 0)),
        "verify_total_items": max(0, int(fields.get("verify_total_items", fields.get("total_items", 0)) or 0)),
        "spec_done": max(0, int(fields.get("spec_done") or 0)),
        "verify_done": max(0, int(fields.get("verify_done") or 0)),
        "spec_registered": int(bool(fields.get("spec_registered", fields.get("spec_start") or fields.get("spec_due") or fields.get("spec_owner_ids") or fields.get("spec_owner_id") or fields.get("spec_total_items")))),
        "verify_registered": int(bool(fields.get("verify_registered", fields.get("verify_start") or fields.get("verify_due") or fields.get("verify_owner_ids") or fields.get("verify_owner_id") or fields.get("verify_total_items")))),
        "spec_owner_ids": json.dumps(list(dict.fromkeys(int(x) for x in (fields.get("spec_owner_ids") or ([fields.get("spec_owner_id")] if fields.get("spec_owner_id") else []))))),
        "verify_owner_ids": json.dumps(list(dict.fromkeys(int(x) for x in (fields.get("verify_owner_ids") or ([fields.get("verify_owner_id")] if fields.get("verify_owner_id") else []))))),
        "spec_owner_id": next(iter(fields.get("spec_owner_ids") or ([fields.get("spec_owner_id")] if fields.get("spec_owner_id") else [])), None),
        "verify_owner_id": next(iter(fields.get("verify_owner_ids") or ([fields.get("verify_owner_id")] if fields.get("verify_owner_id") else [])), None),
        "source_info": str(fields.get("source_info") or ""), "notes": str(fields.get("notes") or ""),
        "holidays": json.dumps(sorted(set(fields.get("holidays") or []))),
        "daily_spec_capacity": fields.get("daily_spec_capacity") or None,
        "daily_verify_capacity": fields.get("daily_verify_capacity") or None,
    }
    if values["spec_done"] > values["spec_total_items"] or values["verify_done"] > values["verify_total_items"]:
        raise ValueError("완료 수는 해당 업무 유형의 전체 대상 수보다 클 수 없습니다.")
    with connect(path) as c:
        if project_id is None:
            cur = c.execute(f"INSERT INTO version_projects({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            return cur.lastrowid
        old = c.execute("SELECT * FROM version_projects WHERE id=?", (project_id,)).fetchone()
        if not old:
            raise ValueError("버전을 찾을 수 없습니다.")
        for field, new in values.items():
            previous = old[field]
            if field in _VERSION_DATE_FIELDS and previous:
                previous = date.fromisoformat(previous).isoformat()
            if field == "holidays":
                previous = json.dumps(sorted(json.loads(previous or "[]")))
            if previous != new:
                c.execute("INSERT INTO version_history(project_id,field,old_value,new_value) VALUES (?,?,?,?)",
                          (project_id, field, str(previous) if previous is not None else None,
                           str(new) if new is not None else None))
        c.execute(f"UPDATE version_projects SET {','.join(f'{k}=?' for k in values)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                  (*values.values(), project_id))
    return project_id


def version_project_history(project_id, path=None) -> list[dict]:
    with connect(path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM version_history WHERE project_id=? ORDER BY changed_at DESC,id DESC", (project_id,))]


def save_version_phase(fields: dict, phase: str, path=None) -> int:
    """사양서/동적검증 한 단계만 저장하고 같은 버전의 다른 단계 데이터는 보존한다."""
    if phase not in ("spec", "verify"):
        raise ValueError("단계는 spec 또는 verify여야 합니다.")
    version = str(fields.get("version", "")).strip()
    if not version:
        raise ValueError("버전명을 입력하세요.")
    with connect(path) as c:
        row = c.execute("SELECT id FROM version_projects WHERE version=? COLLATE NOCASE", (version,)).fetchone()
    project_id = row["id"] if row else None
    if project_id is None:
        merged = {"version": version, "status": "준비", "spec_total_items": 0,
                  "verify_spec_total_items": 0, "verify_total_items": 0,
                  "spec_done": 0, "verify_done": 0, "spec_owner_ids": [], "verify_owner_ids": [],
                  "daily_spec_capacity": 0, "daily_verify_capacity": 0,
                  "spec_registered": False, "verify_registered": False}
    else:
        old = get_version_project(project_id, path)
        merged = {k: old.get(k) for k in ("version", "status", *_VERSION_DATE_FIELDS, "spec_total_items",
                  "verify_spec_total_items",
                  "verify_total_items", "spec_done", "verify_done", "spec_owner_ids", "verify_owner_ids",
                  "daily_spec_capacity", "daily_verify_capacity", "source_info", "notes", "holidays",
                  "spec_registered", "verify_registered")}
    allowed = ({"spec_start", "spec_due", "spec_total_items", "spec_owner_ids", "daily_spec_capacity"}
               if phase == "spec" else
               {"verify_start", "verify_due", "verify_spec_total_items", "verify_total_items", "verify_owner_ids"})
    merged.update({key: value for key, value in fields.items() if key in allowed})
    merged[f"{phase}_registered"] = True
    return save_version_project(merged, path=path, project_id=project_id)


# --- 팀원/역량 ---
def add_member(name, role, workload, contact="", mbti="", path=None) -> int:
    with connect(path) as c:
        return c.execute("INSERT INTO members(name,role,workload,contact,mbti) VALUES (?,?,?,?,?)",
                         (name.strip(), role, int(workload), contact, (mbti or "").upper())).lastrowid


def update_member(member_id, name, role, workload, contact="", mbti="", path=None) -> None:
    with connect(path) as c:
        c.execute("UPDATE members SET name=?,role=?,workload=?,contact=?,mbti=? WHERE id=?",
                  (name.strip(), role, int(workload), contact, (mbti or "").upper(), member_id))


def delete_member(member_id, path=None) -> None:
    with connect(path) as c:
        for row in c.execute("SELECT id,spec_owner_ids,verify_owner_ids,spec_owner_id,verify_owner_id FROM version_projects").fetchall():
            updates = {}
            for field in ("spec_owner_ids", "verify_owner_ids"):
                owners = json.loads(row[field] or "[]") or ([row[field.replace("_ids", "_id")]] if row[field.replace("_ids", "_id")] else [])
                kept = [owner for owner in owners if int(owner) != int(member_id)]
                if kept != owners:
                    updates[field] = json.dumps(kept)
                    scalar = field.replace("_ids", "_id")
                    if row[scalar] is not None and int(row[scalar]) == int(member_id):
                        updates[scalar] = kept[0] if kept else None
            if updates:
                for field, new_value in updates.items():
                    c.execute("INSERT INTO version_history(project_id,field,old_value,new_value) VALUES (?,?,?,?)",
                              (row["id"], field, row[field] if row[field] is not None else None,
                               str(new_value) if new_value is not None else None))
                c.execute(f"UPDATE version_projects SET {','.join(k+'=?' for k in updates)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (*updates.values(), row["id"]))
        c.execute("DELETE FROM members WHERE id=?", (member_id,))


def set_skill(member_id, skill, level, path=None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO skills(member_id,skill,level) VALUES (?,?,?) "
                  "ON CONFLICT(member_id,skill) DO UPDATE SET level=excluded.level",
                  (member_id, skill.strip(), int(level)))


def delete_skill(member_id, skill, path=None) -> None:
    with connect(path) as c:
        c.execute("DELETE FROM skills WHERE member_id=? AND skill=?", (member_id, skill))


# --- 업무 ---
_TASK_FIELDS = ("name", "owner_id", "backup_id", "importance", "status", "progress", "due_date",
                "required_skill", "remaining", "notes", "load_pct")


def _task_values(f: dict):
    return (f["name"].strip(), f.get("owner_id"), f.get("backup_id"), f.get("importance"), f.get("status"),
            int(f.get("progress") or 0), _iso(f.get("due_date")), f.get("required_skill") or "",
            f.get("remaining") or "", f.get("notes") or "", f.get("load_pct"))


def add_task(fields: dict, path=None) -> int:
    with connect(path) as c:
        return c.execute(f"INSERT INTO tasks({','.join(_TASK_FIELDS)}) VALUES ({','.join('?' * len(_TASK_FIELDS))})",
                         _task_values(fields)).lastrowid


def update_task(task_id, fields: dict, path=None) -> None:
    with connect(path) as c:
        c.execute(f"UPDATE tasks SET {','.join(f + '=?' for f in _TASK_FIELDS)} WHERE id=?",
                  (*_task_values(fields), task_id))


def set_backup(task_id, backup_id, path=None) -> None:
    with connect(path) as c:
        c.execute("UPDATE tasks SET backup_id=? WHERE id=?", (backup_id, task_id))


def delete_task(task_id, path=None) -> None:
    with connect(path) as c:
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))


# --- 구글 시트 연동 설정/적용 ---
# 단위 테스트 업무 로그 기본 설정(열 위치는 시트 양식 기준: 값은 열 문자). gid는 사용자가 입력.
DEFAULT_SYNC = {"sheet": "", "sources": [
    {"label": "2. Dynamic_Verification", "gid": "", "role": "log_dynamic", "header_row": 1,
     "columns": {"date": "A", "member": "B", "version": "C", "subsystem": "", "category": "D", "req": "I", "tc": "O", "func": "L"}, "headers": {}},
    {"label": "3. Req_Creation_WBS", "gid": "", "role": "log_req", "header_row": 1,
     "columns": {"date": "A", "member": "B", "version": "C", "subsystem": "", "category": "D", "issue": "G"}, "headers": {}},
]}


def load_sync_config(path=None) -> dict:
    with connect(path) as c:
        row = c.execute("SELECT value FROM settings WHERE key='sheet_sync'").fetchone()
    cfg = json.loads(row["value"]) if row else {}
    return {**DEFAULT_SYNC, **cfg}


def save_sync_config(cfg: dict, path=None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO settings(key,value) VALUES('sheet_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(cfg, ensure_ascii=False),))


def apply_sync_plan(plan: dict, label: str, path=None) -> dict:
    """미리보기(plan)를 한 트랜잭션으로 반영. 삭제는 하지 않는다."""
    added = updated = 0
    col = {"owner_id": "owner_id", "status": "status", "progress": "progress", "due_date": "due_date",
           "remaining": "remaining", "notes": "notes"}
    with connect(path) as c:
        for t in plan["add"]:
            c.execute("""INSERT INTO tasks(name,owner_id,backup_id,importance,status,progress,due_date,required_skill,
                         remaining,notes,load_pct,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (t["name"], t.get("owner_id"), None, t.get("importance"), t.get("status") or "대기",
                       t.get("progress") or 0, _iso(t.get("due_date")), t.get("required_skill") or "",
                       t.get("remaining") or "", t.get("notes") or "", None, label))
            added += 1
        for u in plan["update"]:
            for field, (_, new) in u["changes"].items():
                c.execute(f"UPDATE tasks SET {col[field]}=? WHERE id=?",
                          (_iso(new) if field == "due_date" else new, u["id"]))
            c.execute("UPDATE tasks SET source=? WHERE id=? AND (source IS NULL OR source='')", (label, u["id"]))
            updated += 1
    return {"added": added, "updated": updated}


# --- 업무 로그 ---
def replace_logs(source: str, entries: list[dict], path=None) -> dict:
    """Replace one sheet source while excluding rows owned by internal versions."""
    is_internal = lambda value: bool(re.fullmatch(r"T\s*\d+", str(value or "").strip(), re.IGNORECASE)
        and int(re.search(r"\d+", str(value)).group()) >= 800)
    retained = [e for e in entries if not is_internal(e.get("version"))]
    ignored_internal = len(entries) - len(retained)
    if entries and not retained:
        # A source sheet can contain only internally managed rows. Do not let
        # that filtered preview erase a previously stored legacy source.
        return {"removed": 0, "added": 0, "ignored_internal": ignored_internal}
    with connect(path) as c:
        removed = c.execute("SELECT COUNT(*) FROM work_logs WHERE source=?", (source,)).fetchone()[0]
        c.execute("DELETE FROM work_logs WHERE source=?", (source,))
        c.executemany("""INSERT INTO work_logs(source,track,log_date,member_id,version,subsystem,category,
                      req_done,tc_done,func_done,issue_done,new_req_done,modified_req_done,deleted_req_done,
                      defect_done,tc_created_done,tc_modified_done,tc_deleted_done,notes,src_row)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      [(source, e["track"], _iso(e["date"]), e["member_id"], e.get("version", ""), e.get("subsystem", ""), e.get("category", ""),
                        e.get("req", 0), e.get("tc", 0), e.get("func", 0), e.get("issue", 0), e.get("new_req"),
                        e.get("modified_req"), e.get("deleted_req"), e.get("defect"), e.get("tc_created"),
                        e.get("tc_modified"), e.get("tc_deleted"), e.get("notes", ""), e.get("row")) for e in retained])
    return {"removed": removed, "added": len(retained), "ignored_internal": ignored_internal}


def delete_logs(source: str | None = None, path=None) -> int:
    with connect(path) as c:
        cur = c.execute("DELETE FROM work_logs WHERE source=?", (source,)) if source else c.execute("DELETE FROM work_logs")
        return cur.rowcount


# --- 내부 버전 업무(요구사양서 작성 / 동적 검증 분리 저장) ---
INTERNAL_LOG_FIELDS = {
    "spec": ("completed_tasks", "new_requirements", "modified_requirements", "deleted_requirements"),
    "verify": ("completed_requirements", "completed_functions", "defects", "tc_executed",
               "tc_created", "tc_modified", "tc_deleted"),
}
_INTERNAL_LOG_TABLES = {"spec": "spec_work_logs", "verify": "version_dynamic_work_logs"}


def _internal_subsystem(c, project_id: int, name: str) -> int:
    name = str(name or "").strip()
    if not name:
        raise ValueError("서브시스템명을 입력하세요.")
    c.execute("INSERT INTO version_subsystems(project_id,name) VALUES (?,?) ON CONFLICT(project_id,name) DO NOTHING",
              (int(project_id), name))
    row = c.execute("SELECT id FROM version_subsystems WHERE project_id=? AND name=? COLLATE NOCASE",
                    (int(project_id), name)).fetchone()
    if not row:
        raise ValueError("버전 또는 서브시스템을 찾을 수 없습니다.")
    return int(row["id"])


def list_version_subsystems(project_id: int, path=None) -> list[dict]:
    with connect(path) as c:
        return [dict(r) for r in c.execute(
            "SELECT id,project_id,name,created_at FROM version_subsystems WHERE project_id=? ORDER BY name COLLATE NOCASE,id",
            (int(project_id),))]


def save_version_workload(project_id: int, phase: str, subsystem: str, *, total_tasks=0,
                          total_functions=0, total_requirements=0, difficulty=1, path=None) -> int:
    if phase not in ("spec", "verify"):
        raise ValueError("업무 유형은 spec 또는 verify여야 합니다.")
    counts = {"total_tasks": int(total_tasks or 0), "total_functions": int(total_functions or 0),
              "total_requirements": int(total_requirements or 0)}
    if any(v < 0 for v in counts.values()):
        raise ValueError("업무량은 0 이상이어야 합니다.")
    difficulty = int(difficulty)
    if not 1 <= difficulty <= 5:
        raise ValueError("난이도는 1~5 사이여야 합니다.")
    with connect(path) as c:
        subsystem_id = _internal_subsystem(c, project_id, subsystem)
        if phase == "spec":
            c.execute("""INSERT INTO spec_workloads(project_id,subsystem_id,total_tasks,difficulty)
                         VALUES (?,?,?,?) ON CONFLICT(project_id,subsystem_id) DO UPDATE SET
                         total_tasks=excluded.total_tasks,difficulty=excluded.difficulty,updated_at=CURRENT_TIMESTAMP""",
                      (int(project_id), subsystem_id, counts["total_tasks"], difficulty))
            return int(c.execute("SELECT id FROM spec_workloads WHERE project_id=? AND subsystem_id=?",
                                 (int(project_id), subsystem_id)).fetchone()["id"])
        c.execute("""INSERT INTO verification_workloads(project_id,subsystem_id,total_functions,total_requirements,difficulty)
                     VALUES (?,?,?,?,?) ON CONFLICT(project_id,subsystem_id) DO UPDATE SET
                     total_functions=excluded.total_functions,total_requirements=excluded.total_requirements,
                     difficulty=excluded.difficulty,updated_at=CURRENT_TIMESTAMP""",
                  (int(project_id), subsystem_id, counts["total_functions"], counts["total_requirements"], difficulty))
        return int(c.execute("SELECT id FROM verification_workloads WHERE project_id=? AND subsystem_id=?",
                             (int(project_id), subsystem_id)).fetchone()["id"])


def list_version_workloads(project_id: int, phase: str, path=None) -> list[dict]:
    if phase == "spec":
        query = """SELECT w.*,s.name AS subsystem FROM spec_workloads w
                   JOIN version_subsystems s ON s.id=w.subsystem_id
                   WHERE w.project_id=? ORDER BY s.name COLLATE NOCASE"""
    elif phase == "verify":
        query = """SELECT w.*,s.name AS subsystem FROM verification_workloads w
                   JOIN version_subsystems s ON s.id=w.subsystem_id
                   WHERE w.project_id=? ORDER BY s.name COLLATE NOCASE"""
    else:
        raise ValueError("업무 유형은 spec 또는 verify여야 합니다.")
    with connect(path) as c:
        return [dict(r) for r in c.execute(query, (int(project_id),))]


def save_version_worklog(project_id: int, phase: str, fields: dict, log_id: int | None = None, path=None) -> int:
    if phase not in INTERNAL_LOG_FIELDS:
        raise ValueError("업무 유형은 spec 또는 verify여야 합니다.")
    table = _INTERNAL_LOG_TABLES[phase]
    metrics = INTERNAL_LOG_FIELDS[phase]
    try:
        log_date = _iso(fields.get("date"))
        member_id = int(fields.get("member_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("날짜와 담당자를 선택하세요.") from exc
    if not log_date:
        raise ValueError("업무 날짜를 입력하세요.")
    values = []
    for key in metrics:
        value = int(fields.get(key, 0) or 0)
        if value < 0:
            raise ValueError("업무 기록 수량은 0 이상이어야 합니다.")
        values.append(value)
    with connect(path) as c:
        subsystem_id = _internal_subsystem(c, project_id, fields.get("subsystem", ""))
        workload_table = "spec_workloads" if phase == "spec" else "verification_workloads"
        registered_workload = c.execute(
            f"SELECT 1 FROM {workload_table} WHERE project_id=? AND subsystem_id=?",
            (int(project_id), subsystem_id)).fetchone()
        if not registered_workload:
            raise ValueError("먼저 해당 서브시스템의 사전 물량을 등록하세요.")
        if log_id is not None:
            old = c.execute(f"SELECT id FROM {table} WHERE id=? AND project_id=?", (int(log_id), int(project_id))).fetchone()
            if not old:
                raise ValueError("수정할 업무 기록이 없습니다.")
            assignments = ["subsystem_id=?", "log_date=?", "member_id=?", *[f"{key}=?" for key in metrics],
                           "notes=?", "updated_at=strftime('%Y-%m-%d %H:%M:%f','now')"]
            c.execute(f"UPDATE {table} SET {','.join(assignments)} WHERE id=? AND project_id=?",
                      (subsystem_id, log_date, member_id, *values, str(fields.get("notes") or "").strip(), int(log_id), int(project_id)))
            return int(log_id)
        cols = ["project_id", "subsystem_id", "log_date", "member_id", *metrics, "notes"]
        placeholders = ",".join("?" for _ in cols)
        cur = c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES ({placeholders})",
                        (int(project_id), subsystem_id, log_date, member_id, *values, str(fields.get("notes") or "").strip()))
        return int(cur.lastrowid)


def list_version_worklogs(project_id: int, phase: str, path=None) -> list[dict]:
    if phase not in _INTERNAL_LOG_TABLES:
        raise ValueError("업무 유형은 spec 또는 verify여야 합니다.")
    table = _INTERNAL_LOG_TABLES[phase]
    with connect(path) as c:
        rows = [dict(r) for r in c.execute(
            f"""SELECT l.*,s.name AS subsystem,m.name AS member_name FROM {table} l
                JOIN version_subsystems s ON s.id=l.subsystem_id
                JOIN members m ON m.id=l.member_id WHERE l.project_id=?
                ORDER BY l.log_date DESC,l.id DESC""", (int(project_id),))]
    for row in rows:
        row["date"] = _date(row.pop("log_date"))
    return rows


def list_all_internal_worklogs(phase: str, path=None) -> list[dict]:
    if phase not in _INTERNAL_LOG_TABLES:
        raise ValueError("업무 유형은 spec 또는 verify여야 합니다.")
    table = _INTERNAL_LOG_TABLES[phase]
    with connect(path) as c:
        rows = [dict(r) for r in c.execute(
            f"""SELECT l.*,s.name AS subsystem,m.name AS member_name,p.version FROM {table} l
                JOIN version_subsystems s ON s.id=l.subsystem_id
                JOIN members m ON m.id=l.member_id JOIN version_projects p ON p.id=l.project_id
                ORDER BY l.log_date,l.id""")]
    for row in rows:
        row["date"] = _date(row.pop("log_date"))
    return rows


def delete_version_worklog(project_id: int, phase: str, log_id: int, path=None) -> bool:
    if phase not in _INTERNAL_LOG_TABLES:
        raise ValueError("업무 유형은 spec 또는 verify여야 합니다.")
    table = _INTERNAL_LOG_TABLES[phase]
    with connect(path) as c:
        cur = c.execute(f"DELETE FROM {table} WHERE project_id=? AND id=?", (int(project_id), int(log_id)))
        return cur.rowcount > 0


def replace_version_assignments(project_id: int, phase: str, assignments: list[dict], path=None) -> int:
    if phase not in ("spec", "verify"):
        raise ValueError("업무 유형은 spec 또는 verify여야 합니다.")
    with connect(path) as c:
        c.execute("DELETE FROM version_assignments WHERE project_id=? AND phase=?", (int(project_id), phase))
        for item in assignments:
            subsystem_id = _internal_subsystem(c, project_id, item["subsystem"])
            c.execute("""INSERT INTO version_assignments(project_id,phase,subsystem_id,member_id,
                         allocated_tasks,allocated_functions,allocated_requirements,effort_score,feasibility,rationale)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""",
                      (int(project_id), phase, subsystem_id, int(item["member_id"]), int(item.get("tasks", 0)),
                       int(item.get("functions", 0)), int(item.get("requirements", 0)), float(item.get("effort_score", 0)),
                       str(item.get("feasibility") or "판단 자료 부족"), str(item.get("rationale") or "")))
    return len(assignments)


def list_version_assignments(project_id: int, phase: str, path=None) -> list[dict]:
    with connect(path) as c:
        return [dict(r) for r in c.execute(
            """SELECT a.*,s.name AS subsystem,m.name AS member_name FROM version_assignments a
               JOIN version_subsystems s ON s.id=a.subsystem_id JOIN members m ON m.id=a.member_id
               WHERE a.project_id=? AND a.phase=? ORDER BY s.name COLLATE NOCASE,m.name COLLATE NOCASE""",
            (int(project_id), phase))]
