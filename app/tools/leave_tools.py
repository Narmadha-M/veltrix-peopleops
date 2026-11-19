"""The five leave tools. Every permission check runs here, using the requester from the
X-Employee-Id header (RequestContext), never from the prompt or from LLM-chosen arguments.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from app.config import get_settings, today
from app.data import db
from app.logging_setup import log_event

log = logging.getLogger("veltrix.tools")

FIXED_REFUSAL = "I can't share information about that employee."
LEAVE_TYPES = ("annual", "sick")
STATUSES = ("pending", "approved", "cancelled")


@dataclass
class RequestContext:
    employee_id: str
    role: str
    region: str
    join_date: str
    level: str
    manager_id: str | None
    direct_reports: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, employee_id: str) -> "RequestContext | None":
        emp = db.get_employee(employee_id)
        if not emp:
            return None
        reports = db.direct_report_ids(employee_id) if emp["role"] == "manager" else []
        return cls(emp["employee_id"], emp["role"], emp["region"], emp["join_date"], emp["level"],
                   emp["manager_id"], reports)


class PermissionDenied(Exception):
    """Requester may not see this data. Message is fixed so it reveals nothing about existence."""

    def __init__(self, message: str = FIXED_REFUSAL) -> None:
        super().__init__(message)
        self.message = message


class ToolRejected(Exception):
    """A business rule rejected the action. The message is the specific reason shown to the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- permission helpers
def can_view_employee(ctx: RequestContext, target_id: str) -> bool:
    if target_id == ctx.employee_id:
        return True
    if db.get_employee(target_id) is None:
        return False
    if ctx.role == "hr":
        return True
    if ctx.role == "manager":
        return target_id in ctx.direct_reports
    return False


def visible_employee_ids(ctx: RequestContext) -> set[str]:
    ids = {ctx.employee_id}
    if ctx.role == "manager":
        ids.update(ctx.direct_reports)
    return ids


def _require_view(ctx: RequestContext, employee_id: str) -> str:
    target = (employee_id or "").strip().upper() or ctx.employee_id
    if not can_view_employee(ctx, target):
        log_event(log, "permission_denied", requester=ctx.employee_id, target="[withheld]")
        raise PermissionDenied()
    return target


def _parse_iso(value: str, label: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise ToolRejected(f"The {label} '{value}' is not a valid date (expected YYYY-MM-DD).") from None


# ---------------------------------------------------------------- read tools
def get_leave_balance(ctx: RequestContext, employee_id: str, leave_type: str | None = None) -> dict:
    target = _require_view(ctx, employee_id)
    if leave_type and leave_type not in LEAVE_TYPES:
        raise ToolRejected("Leave type must be 'annual' or 'sick'.")
    rows = db.get_balances(target, leave_type or None)
    return {"employee_id": target, "balances": {r["leave_type"]: r["balance_days"] for r in rows}}


def list_leave_requests(ctx: RequestContext, employee_id: str, status: str | None = None) -> dict:
    target = _require_view(ctx, employee_id)
    if status and status not in STATUSES:
        raise ToolRejected("Status must be one of pending, approved or cancelled.")
    rows = db.list_requests(target, status or None)
    return {"employee_id": target, "requests": [
        {k: r[k] for k in ("request_id", "leave_type", "start_date", "end_date", "working_days", "status")}
        for r in rows]}


def get_team_leave(ctx: RequestContext, start_date: str, end_date: str) -> dict:
    if ctx.role != "manager":
        raise PermissionDenied("Team leave is only available to managers, for their own direct reports.")
    start, end = _parse_iso(start_date, "start date"), _parse_iso(end_date, "end date")
    if end < start:
        raise ToolRejected("The end date is before the start date.")
    rows = db.team_leave(ctx.employee_id, start.isoformat(), end.isoformat())
    return {"start_date": start.isoformat(), "end_date": end.isoformat(), "direct_reports": len(ctx.direct_reports),
            "leave": [{k: r[k] for k in ("employee_id", "leave_type", "start_date", "end_date", "working_days", "status")}
                      for r in rows]}


# ---------------------------------------------------------------- apply_leave
def validate_apply(ctx: RequestContext, docs: dict, leave_type: str, start_date: str, end_date: str) -> dict:
    """All Section 6.2 checks without writing. Returns the computed proposal or raises ToolRejected."""
    from app.tools.working_days import count_working_days, public_holidays

    if leave_type not in LEAVE_TYPES:
        raise ToolRejected("Leave type must be 'annual' or 'sick'.")
    start, end = _parse_iso(start_date, "start date"), _parse_iso(end_date, "end date")
    year, now = get_settings().leave_year, today()
    if start.year != year or end.year != year:
        raise ToolRejected(f"Leave can only be requested for dates in {year}.")
    if start < now:
        raise ToolRejected(f"The start date {start.isoformat()} is in the past (today is {now.isoformat()}).")
    if end < start:
        raise ToolRejected("The end date is before the start date.")
    holidays, calendar = public_holidays(docs, ctx.role, ctx.region, year, now)
    wd, skipped = count_working_days(start, end, holidays)
    if wd == 0:
        raise ToolRejected(
            f"{start.isoformat()} to {end.isoformat()} has no working days (only weekends or public holidays).")
    conn = db.connect()
    try:
        balance = db.balance_in_tx(conn, ctx.employee_id, leave_type)
        overlaps = db.overlapping_active_requests(conn, ctx.employee_id, start.isoformat(), end.isoformat())
    finally:
        conn.close()
    if balance is None:
        raise ToolRejected(f"You have no {leave_type} leave balance on record.")
    if wd > balance:
        raise ToolRejected(
            f"This request needs {wd} working days but your {leave_type} leave balance is {balance:g} days.")
    if overlaps:
        o = overlaps[0]
        raise ToolRejected(
            f"These dates overlap your existing {o['status']} request from {o['start_date']} to {o['end_date']}.")
    return {"leave_type": leave_type, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "working_days": wd, "balance_before": balance,
            "holidays_excluded": [{"date": d.isoformat(), "name": n} for d, n in skipped],
            "calendar_doc_id": calendar.doc_id if calendar else None}


def apply_leave(ctx: RequestContext, docs: dict, leave_type: str, start_date: str, end_date: str) -> dict:
    proposal = validate_apply(ctx, docs, leave_type, start_date, end_date)
    with db.transaction() as conn:
        # re-check inside the transaction: data may have changed since the proposal
        balance = db.balance_in_tx(conn, ctx.employee_id, leave_type)
        if balance is None or proposal["working_days"] > balance:
            raise ToolRejected(
                f"This request needs {proposal['working_days']} working days but your {leave_type} leave "
                f"balance is {(balance or 0):g} days.")
        if db.overlapping_active_requests(conn, ctx.employee_id, proposal["start_date"], proposal["end_date"]):
            raise ToolRejected("These dates now overlap one of your existing pending or approved requests.")
        request_id = db.insert_request_and_debit(conn, ctx.employee_id, leave_type, proposal["start_date"],
                                                 proposal["end_date"], proposal["working_days"])
    log_event(log, "leave_applied", requester=ctx.employee_id, request_id=request_id,
              working_days=proposal["working_days"])
    return {**proposal, "request_id": request_id, "status": "pending",
            "balance_after": proposal["balance_before"] - proposal["working_days"]}


# ---------------------------------------------------------------- cancel_leave
def validate_cancel(ctx: RequestContext, request_id: str) -> dict:
    req = db.get_request((request_id or "").strip())
    if req is None or req["employee_id"] != ctx.employee_id:
        # same message whether the request does not exist or belongs to someone else
        raise ToolRejected("I couldn't find that request among your leave requests.")
    if req["status"] not in ("pending", "approved"):
        raise ToolRejected(f"That request is already {req['status']}.")
    if date.fromisoformat(req["start_date"]) <= today():
        raise ToolRejected(
            f"That request started on {req['start_date']}; only requests starting after today can be cancelled.")
    return {k: req[k] for k in ("request_id", "leave_type", "start_date", "end_date", "working_days", "status")}


def cancel_leave(ctx: RequestContext, request_id: str) -> dict:
    req = validate_cancel(ctx, request_id)
    with db.transaction() as conn:
        fresh = conn.execute("SELECT * FROM leave_requests WHERE request_id = ?", (req["request_id"],)).fetchone()
        if fresh is None or fresh["status"] not in ("pending", "approved"):
            raise ToolRejected("That request can no longer be cancelled.")
        db.cancel_and_credit(conn, dict(fresh))
    log_event(log, "leave_cancelled", requester=ctx.employee_id, request_id=req["request_id"])
    return {**req, "status": "cancelled", "days_returned": req["working_days"]}


def cancellable_requests(ctx: RequestContext) -> list[dict]:
    now = today().isoformat()
    return [r for r in list_leave_requests(ctx, ctx.employee_id)["requests"]
            if r["status"] in ("pending", "approved") and r["start_date"] > now]
