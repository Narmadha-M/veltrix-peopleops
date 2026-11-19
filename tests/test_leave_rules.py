"""Rules for apply_leave (6.2) and cancel_leave (6.3)."""
import sqlite3
import uuid

import pytest

from app.data import db
from app.ingestion.pipeline import KB
from app.tools import leave_tools as T
from app.tools.leave_tools import ToolRejected


def reject_reason(fn, *args):
    with pytest.raises(ToolRejected) as exc:
        fn(*args)
    return exc.value.reason


@pytest.mark.parametrize("args, fragment", [
    (("vacation", "2026-12-01", "2026-12-01"), "annual' or 'sick"),
    (("annual", "2026-11-10", "2026-11-12"), "in the past"),
    (("annual", "2027-01-04", "2027-01-05"), "dates in 2026"),
    (("annual", "2026-12-03", "2026-12-01"), "before the start"),
    (("annual", "2026-11-21", "2026-11-22"), "no working days"),
    (("annual", "2026-12-25", "2026-12-27"), "no working days"),       # holiday + weekend
    (("annual", "2026-11-30", "2026-12-31"), "balance is 12"),
    (("annual", "2026-12-15", "2026-12-16"), "overlap"),
    (("annual", "not-a-date", "2026-12-16"), "not a valid date"),
])
def test_apply_rejections(fresh_db, ctx_of, args, fragment):
    assert fragment in reject_reason(T.validate_apply, ctx_of("E201"), KB.docs, *args)


def test_apply_counts_regional_holidays(fresh_db, ctx_of):
    p = T.validate_apply(ctx_of("E201"), KB.docs, "annual", "2026-12-21", "2026-12-25")   # IN: Christmas
    assert p["working_days"] == 4
    p_de = T.validate_apply(ctx_of("E301"), KB.docs, "annual", "2026-12-21", "2026-12-25")  # DE: 24 + 25
    assert p_de["working_days"] == 3


def test_apply_success_is_atomic(fresh_db, ctx_of):
    ctx = ctx_of("E202")
    out = T.apply_leave(ctx, KB.docs, "sick", "2026-12-07", "2026-12-11")
    assert out["working_days"] == 5 and out["status"] == "pending"
    uuid.UUID(out["request_id"])                                       # random, unguessable id
    row = db.get_request(out["request_id"])
    assert row["status"] == "pending" and row["working_days"] == 5 and row["employee_id"] == "E202"
    assert db.get_balances("E202", "sick")[0]["balance_days"] == 5.0


def test_apply_rechecks_balance_at_execution(fresh_db, ctx_of):
    ctx = ctx_of("E202")
    T.validate_apply(ctx, KB.docs, "annual", "2026-12-07", "2026-12-11")
    con = sqlite3.connect(fresh_db)
    con.execute("UPDATE leave_balances SET balance_days = 1 WHERE employee_id='E202' AND leave_type='annual'")
    con.commit()
    con.close()
    assert "balance is 1" in reject_reason(T.apply_leave, ctx, KB.docs, "annual", "2026-12-07", "2026-12-11")
    assert len(db.list_requests("E202")) == 2                         # nothing inserted


def test_cancel_success_returns_days(fresh_db, ctx_of):
    out = T.cancel_leave(ctx_of("E201"), "req-201-future")
    assert out["status"] == "cancelled" and out["days_returned"] == 2
    assert db.get_request("req-201-future")["status"] == "cancelled"
    assert db.get_balances("E201", "annual")[0]["balance_days"] == 14.0


@pytest.mark.parametrize("eid, rid, fragment", [
    ("E201", "req-201-past", "only requests starting after today"),
    ("E202", "req-202-cancelled", "already cancelled"),
    ("E201", "req-202-future", "couldn't find"),
    ("E201", "does-not-exist", "couldn't find"),
])
def test_cancel_rejections(fresh_db, ctx_of, eid, rid, fragment):
    assert fragment in reject_reason(T.cancel_leave, ctx_of(eid), rid)
