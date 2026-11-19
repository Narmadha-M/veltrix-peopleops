"""Section 5 rules, including the worked example from the task (5.2)."""
from datetime import date

from app.ingestion.models import DocMeta
from app.retrieval.policy import compare, deciding_test, is_eligible, precedence_ranks


def doc(doc_id, dtype, regions, eff, access="all"):
    return DocMeta(doc_id, f"{doc_id}.pdf", doc_id, dtype, regions, eff, access)


V1 = doc("v1", "policy", ["GLOBAL"], "2025-01-01")
V2 = doc("v2", "policy", ["GLOBAL"], "2026-04-01")
FAQ = doc("faq", "faq", ["GLOBAL"], "2026-05-01")
DE = doc("de", "addendum", ["DE"], "2025-06-01")
V3 = doc("v3", "policy", ["GLOBAL"], "2027-01-01")
ALL = [V1, V2, FAQ, DE, V3]
TODAY = date(2026, 9, 15)


def winner(role, region, ref):
    eligible = [d for d in ALL if is_eligible(d, role, region, ref)]
    ranks = precedence_ranks(eligible)
    best = min(ranks.values())
    return sorted(d.doc_id for d in eligible if ranks[d.doc_id] == best), eligible


def test_india_employee_today_v2_wins_and_v3_is_upcoming():
    win, eligible = winner("employee", "IN", TODAY)
    assert win == ["v2"]
    assert {d.doc_id for d in eligible} == {"v1", "v2", "faq"}       # DE not their region, v3 not yet
    assert deciding_test(V2, FAQ) == "authority" and deciding_test(V2, V1) == "recency"
    assert compare(V3, V2) == -1          # v3 would win on its effective date -> mention as upcoming


def test_germany_employee_addendum_wins_and_v3_not_mentioned():
    win, _ = winner("employee", "DE", TODAY)
    assert win == ["de"]
    assert compare(DE, V3) == -1 and deciding_test(DE, V3) == "scope"   # addendum still wins in 2027


def test_india_employee_past_date_only_v1():
    win, eligible = winner("employee", "IN", date(2025, 8, 1))
    assert win == ["v1"] and [d.doc_id for d in eligible] == ["v1"]


def test_unresolved_tie_shares_rank():
    a = doc("expense", "policy", ["GLOBAL"], "2026-01-01")
    b = doc("finance", "handbook", ["GLOBAL"], "2026-01-01")
    assert compare(a, b) == 0 and deciding_test(a, b) == "tie"
    assert len(set(precedence_ranks([a, b]).values())) == 1


def test_authority_beats_scope_and_recency():
    regional_wiki = doc("wiki", "wiki", ["IN"], "2026-10-01")
    old_global_policy = doc("pol", "policy", ["GLOBAL"], "2024-01-01")
    assert compare(old_global_policy, regional_wiki) == -1


def test_eligibility_by_access_level():
    mgr = doc("m", "handbook", ["GLOBAL"], "2026-01-01", "managers")
    hr = doc("h", "policy", ["GLOBAL"], "2026-01-01", "hr")
    assert not is_eligible(mgr, "employee", "IN", TODAY) and not is_eligible(hr, "employee", "IN", TODAY)
    assert is_eligible(mgr, "manager", "IN", TODAY) and not is_eligible(hr, "manager", "IN", TODAY)
    assert is_eligible(mgr, "hr", "IN", TODAY) and is_eligible(hr, "hr", "IN", TODAY)


def test_multi_region_document():
    memo = doc("memo", "memo", ["IN", "US"], "2026-07-01")
    assert is_eligible(memo, "employee", "US", TODAY) and not is_eligible(memo, "employee", "DE", TODAY)
    assert not is_eligible(memo, "employee", "IN", date(2026, 6, 30))
