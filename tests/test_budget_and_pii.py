import logging
from datetime import date

import pytest

from app.data.pii import PII, mask_email
from app.llm.provider import CorpusBudgetError, FakeLLM
from app.retrieval.budget import count_tokens
from app.retrieval.retriever import retrieve


def test_retrieval_respects_budget(base_env):
    for budget in (3000, 200, 80):
        ev = retrieve("sick leave volunteer days holidays claims", "hr", "IN", date(2026, 11, 17), budget=budget)
        assert ev.corpus_tokens <= budget
        assert count_tokens(ev.render()) == ev.corpus_tokens


def test_llm_call_over_budget_is_blocked():
    llm = FakeLLM(lambda *a: {"ok": True})
    with pytest.raises(CorpusBudgetError):
        llm.complete_json(purpose="compose", system="s", user="u", schema={}, corpus_tokens=3001)
    assert llm.calls == []


def test_mask_email():
    assert mask_email("priya.nair@veltrix.example") == "p***@veltrix.example"


def test_answer_sanitizer(base_env):
    text = "Contact priya.nair@veltrix.example or +91 98450 33333. Jonas Weber is away. Write to hr@veltrix.example."
    out = PII.sanitize_answer(text, visible_ids={"E201"})
    assert "p***@veltrix.example" in out and "priya.nair@" not in out
    assert "98450" not in out
    assert "Jonas Weber" not in out                      # not visible to the requester
    assert "hr@veltrix.example" in out                   # generic mailbox, not personal data


def test_logs_are_redacted(base_env, caplog):
    logger = logging.getLogger("veltrix.test")
    from app.data.pii import RedactingFilter
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    record = logging.LogRecord("veltrix.test", logging.INFO, __file__, 1,
                               "user Priya Nair (priya.nair@veltrix.example, +91 98450 33333)", (), None)
    RedactingFilter().filter(record)
    msg = record.getMessage()
    assert "Priya" not in msg and "priya.nair" not in msg and "98450" not in msg
    assert "E201" in msg
