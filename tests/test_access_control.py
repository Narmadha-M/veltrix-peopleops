"""Access control per role, for documents (retrieval) and leave data (tools)."""
from datetime import date

import pytest

from app.retrieval.retriever import retrieve
from app.tools import leave_tools as T
from app.tools.leave_tools import FIXED_REFUSAL, PermissionDenied
from tests.conftest import RESTRICTED_MARKERS

QUERIES = ["approving leave respond to leave requests", "pay bands level L3 units", "ZEBRA secret",
           "sick leave", "volunteer days", "manager guide", "anything at all"]
TODAY = date(2026, 11, 17)


def _docs(role, region, query):
    return {e.doc.doc_id for e in retrieve(query, role, region, TODAY).items}


@pytest.mark.parametrize("query", QUERIES)
def test_employee_never_retrieves_restricted_documents(base_env, query):
    ev = retrieve(query, "employee", "IN", TODAY)
    assert not ({"HB-MGR", "HR-BANDS"} & {e.doc.doc_id for e in ev.items})
    assert not any(m in ev.render() for m in RESTRICTED_MARKERS)


def test_manager_gets_manager_docs_but_not_hr_docs(base_env):
    assert "HB-MGR" in _docs("manager", "IN", "approving leave respond to leave requests")
    assert "HR-BANDS" not in _docs("manager", "IN", "pay bands level L3 units")


def test_hr_gets_all_levels(base_env):
    assert "HR-BANDS" in _docs("hr", "IN", "pay bands level L3 units")
    assert "HB-MGR" in _docs("hr", "IN", "approving leave respond to leave requests")


def test_other_region_documents_are_never_retrieved(base_env):
    assert "ADD-DE" not in _docs("employee", "IN", "volunteer days Germany")
    assert "ADD-DE" in _docs("employee", "DE", "volunteer days Germany")


# ------------------------------------------------------------------ leave data
def test_employee_sees_only_own_data(ctx_of):
    e = ctx_of("E201")
    assert T.get_leave_balance(e, "E201")["balances"]["annual"] == 12
    for other in ("E202", "E200", "E999"):
        with pytest.raises(PermissionDenied) as exc:
            T.get_leave_balance(e, other)
        assert exc.value.message == FIXED_REFUSAL
        with pytest.raises(PermissionDenied):
            T.list_leave_requests(e, other)


def test_unknown_and_forbidden_employee_get_identical_refusal(ctx_of):
    e = ctx_of("E201")
    messages = set()
    for target in ("E202", "E999", "NOPE"):
        with pytest.raises(PermissionDenied) as exc:
            T.list_leave_requests(e, target)
        messages.add(exc.value.message)
    assert messages == {FIXED_REFUSAL}


def test_manager_sees_direct_reports_only(ctx_of):
    m = ctx_of("E200")
    assert T.get_leave_balance(m, "E201")["employee_id"] == "E201"
    assert T.list_leave_requests(m, "E202")["employee_id"] == "E202"
    for not_report in ("E301", "E100", "E400"):
        with pytest.raises(PermissionDenied):
            T.get_leave_balance(m, not_report)


def test_hr_sees_any_single_employee(ctx_of):
    h = ctx_of("E100")
    for target in ("E201", "E301", "E400", "E200"):
        assert T.get_leave_balance(h, target)["employee_id"] == target
    with pytest.raises(PermissionDenied):
        T.get_leave_balance(h, "E999")


def test_team_leave_is_managers_only_and_only_their_reports(ctx_of):
    for eid in ("E201", "E100"):
        with pytest.raises(PermissionDenied):
            T.get_team_leave(ctx_of(eid), "2026-11-01", "2026-12-31")
    out = T.get_team_leave(ctx_of("E200"), "2026-11-01", "2026-12-31")
    ids = {r["employee_id"] for r in out["leave"]}
    assert ids <= {"E201", "E202"} and ids
    assert all(r["status"] in ("pending", "approved") for r in out["leave"])


def test_write_tools_only_affect_the_requester(fresh_db, ctx_of):
    with pytest.raises(T.ToolRejected) as exc:
        T.validate_cancel(ctx_of("E201"), "req-202-future")       # someone else's request
    assert "couldn't find" in exc.value.reason
