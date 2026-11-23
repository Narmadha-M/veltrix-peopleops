"""SQLite data access. Existing tables are never altered; new tables are added only."""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.config import get_settings

ACTIVE_STATUSES = ("pending", "approved")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or get_settings().db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, rolled back on any exception."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def ensure_extra_tables() -> None:
    """Tables added by this service. Rebuilt at every ingestion."""
    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS extracted_holidays (
                doc_id TEXT NOT NULL,
                holiday_date TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,          -- 'public' (office closed) | 'restricted' (optional)
                PRIMARY KEY (doc_id, holiday_date, name)
            );
            CREATE INDEX IF NOT EXISTS idx_requests_emp ON leave_requests(employee_id, status);
            CREATE INDEX IF NOT EXISTS idx_employees_mgr ON employees(manager_id);
            """
        )
    finally:
        conn.close()


# ---------------------------------------------------------------- employees
def get_employee(employee_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_employee_by_email(email: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM employees WHERE lower(email) = lower(?)", (email.strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def direct_report_ids(manager_id: str) -> list[str]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT employee_id FROM employees WHERE manager_id = ? ORDER BY employee_id", (manager_id,)
        ).fetchall()
        return [r["employee_id"] for r in rows]
    finally:
        conn.close()


def all_people_pii() -> list[dict]:
    """Used only by the PII redactor (never returned to a user or the LLM)."""
    conn = connect()
    try:
        rows = conn.execute("SELECT employee_id, full_name, email, phone FROM employees").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def find_employees_by_name(fragment: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT employee_id, full_name FROM employees WHERE lower(full_name) LIKE ? ORDER BY employee_id",
            (f"%{fragment.lower().strip()}%",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_employee_directory() -> list[dict]:
    """Non-personal columns only (for the demo UI's employee picker)."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT employee_id, region, role, level, manager_id FROM employees ORDER BY employee_id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- leave reads
def get_balances(employee_id: str, leave_type: str | None = None) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT leave_type, balance_days FROM leave_balances WHERE employee_id = ?"
        args: list = [employee_id]
        if leave_type:
            sql += " AND leave_type = ?"
            args.append(leave_type)
        return [dict(r) for r in conn.execute(sql + " ORDER BY leave_type", args).fetchall()]
    finally:
        conn.close()


def list_requests(employee_id: str, status: str | None = None) -> list[dict]:
    conn = connect()
    try:
        sql = (
            "SELECT request_id, employee_id, leave_type, start_date, end_date, working_days, status, created_at "
            "FROM leave_requests WHERE employee_id = ?"
        )
        args: list = [employee_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        return [dict(r) for r in conn.execute(sql + " ORDER BY start_date", args).fetchall()]
    finally:
        conn.close()


def get_request(request_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM leave_requests WHERE request_id = ?", (request_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def team_leave(manager_id: str, start: str, end: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT r.request_id, r.employee_id, r.leave_type, r.start_date, r.end_date, r.working_days, r.status
            FROM leave_requests r JOIN employees e ON e.employee_id = r.employee_id
            WHERE e.manager_id = ? AND r.status IN ('pending','approved')
              AND r.start_date <= ? AND r.end_date >= ?
            ORDER BY r.start_date, r.employee_id
            """,
            (manager_id, end, start),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def overlapping_active_requests(conn: sqlite3.Connection, employee_id: str, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        "SELECT request_id, start_date, end_date, status FROM leave_requests "
        "WHERE employee_id = ? AND status IN ('pending','approved') AND start_date <= ? AND end_date >= ?",
        (employee_id, end, start),
    ).fetchall()
    return [dict(r) for r in rows]


def balance_in_tx(conn: sqlite3.Connection, employee_id: str, leave_type: str) -> float | None:
    row = conn.execute(
        "SELECT balance_days FROM leave_balances WHERE employee_id = ? AND leave_type = ?",
        (employee_id, leave_type),
    ).fetchone()
    return None if row is None else float(row["balance_days"])


# ---------------------------------------------------------------- leave writes
def insert_request_and_debit(
    conn: sqlite3.Connection, employee_id: str, leave_type: str, start: str, end: str, working_days: int
) -> str:
    request_id = str(uuid.uuid4())
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO leave_requests (request_id, employee_id, leave_type, start_date, end_date, working_days, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (request_id, employee_id, leave_type, start, end, working_days, created),
    )
    cur = conn.execute(
        "UPDATE leave_balances SET balance_days = balance_days - ? WHERE employee_id = ? AND leave_type = ?",
        (working_days, employee_id, leave_type),
    )
    if cur.rowcount != 1:
        raise RuntimeError("balance row missing")
    return request_id


def cancel_and_credit(conn: sqlite3.Connection, request: dict) -> None:
    cur = conn.execute(
        "UPDATE leave_requests SET status = 'cancelled' WHERE request_id = ? AND status IN ('pending','approved')",
        (request["request_id"],),
    )
    if cur.rowcount != 1:
        raise RuntimeError("request no longer cancellable")
    conn.execute(
        "UPDATE leave_balances SET balance_days = balance_days + ? WHERE employee_id = ? AND leave_type = ?",
        (request["working_days"], request["employee_id"], request["leave_type"]),
    )


# ---------------------------------------------------------------- holidays
def replace_holidays(rows: list[tuple[str, str, str, str]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM extracted_holidays")
        conn.executemany(
            "INSERT OR IGNORE INTO extracted_holidays (doc_id, holiday_date, name, kind) VALUES (?, ?, ?, ?)", rows
        )


def holidays_for_doc(doc_id: str, kind: str | None = None) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT holiday_date, name, kind FROM extracted_holidays WHERE doc_id = ?"
        args: list = [doc_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return [dict(r) for r in conn.execute(sql + " ORDER BY holiday_date", args).fetchall()]
    finally:
        conn.close()
