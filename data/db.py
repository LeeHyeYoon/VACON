"""SQLite 저장소. 화면/업무 로직과 분리된 데이터 접근 계층."""
from __future__ import annotations

import json
import os
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
    remaining TEXT DEFAULT '', notes TEXT DEFAULT '', load_pct INTEGER);
CREATE TABLE IF NOT EXISTS leaves(id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, leave_type TEXT NOT NULL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
    if "mbti" not in cols:
        c.execute("ALTER TABLE members ADD COLUMN mbti TEXT DEFAULT ''")


def _insert_sample(c) -> None:
    s = build_sample(today())
    c.executemany("INSERT INTO members(id,name,role,workload,contact) VALUES (?,?,?,?,?)", s["members"])
    c.executemany("INSERT INTO skills(member_id,skill,level) VALUES (?,?,?)", s["skills"])
    c.executemany("""INSERT INTO tasks(id,name,owner_id,backup_id,importance,status,progress,due_date,
                     required_skill,remaining,notes,load_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  [(*t[:7], _iso(t[7]), *t[8:]) for t in s["tasks"]])
    c.executemany("INSERT INTO leaves(id,member_id,start_date,end_date,leave_type,note) VALUES (?,?,?,?,?,?)",
                  [(l[0], l[1], _iso(l[2]), _iso(l[3]), l[4], l[5]) for l in s["leaves"]])


def reset_sample(path=None) -> None:
    with connect(path) as c:
        for t in ("leaves", "tasks", "skills", "members", "settings"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM sqlite_sequence")
        _insert_sample(c)


def load_team_data(path=None) -> TeamData:
    with connect(path) as c:
        members = [dict(r) for r in c.execute("SELECT * FROM members ORDER BY id")]
        skills = [dict(r) for r in c.execute("SELECT * FROM skills ORDER BY member_id, skill")]
        tasks = [dict(r) for r in c.execute("SELECT * FROM tasks ORDER BY id")]
        leaves = [dict(r) for r in c.execute("SELECT * FROM leaves ORDER BY start_date, id")]
    for t in tasks:
        t["due_date"] = _date(t["due_date"])
    for l in leaves:
        l["start"], l["end"], l["type"] = _date(l.pop("start_date")), _date(l.pop("end_date")), l.pop("leave_type")
    return make_team(members, skills, tasks, leaves)


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
