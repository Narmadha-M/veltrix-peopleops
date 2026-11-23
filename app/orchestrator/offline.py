"""Offline (keyword) mode, used only when the LLM is unavailable (no API key, timeout, outage).

It never guesses: policy questions return the strongest relevant passage verbatim with its citation
(same access, region, date and precedence rules as normal retrieval), simple leave lookups call the
read tools directly, and apply/cancel requests are parsed with rules (rule_parser) and then go through the
same validation, proposal and "yes" confirmation as LLM mode (handled in agent._offline).
"""
from __future__ import annotations

import re

from app.config import today
from app.orchestrator.guardrails import is_injection
from app.retrieval.index import tokenize
from app.retrieval.retriever import retrieve
from app.tools import leave_tools as T
from app.tools.leave_tools import RequestContext

OFFLINE_NOTE = "(AI summaries are unavailable right now, so this is a keyword-based answer.)"

_BALANCE = re.compile(r"\bbalances?\b|\bdays (do i have|left|remaining)\b|\bhow many (leave )?days (do )?i have\b", re.I)
_REQUESTS = re.compile(r"\b(my|show|list)\b.*\b(leave )?requests?\b|\bmy leaves?\b", re.I)
_HOLIDAYS = re.compile(r"\b(public |restricted |optional |bank )?holidays?\b|\bholiday (list|calendar)\b", re.I)
_UPCOMING = re.compile(r"\b(left|remaining|upcoming|next|coming|rest of)\b", re.I)

# Questions about other people: an employee ID ("E2004") or a full name from the employees table.
_EMP_ID = re.compile(r"\b([A-Za-z]\d{3,5})\b")
_REQUEST_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# Manager questions about their direct reports.
_TEAM = re.compile(r"\b(my team|team'?s|team members?|my (direct )?reports|direct reports|my staff)\b", re.I)
_WHO_OFF = re.compile(r"\bwho\b.*\b(on leave|off|away|out of office|absent)\b", re.I)
_ABSENT = re.compile(r"\b(on leave|off|away|out of office|absent|leave (calendar|schedule|plan))\b", re.I)
_SHOW = re.compile(r"\b(show|list|see|view|check)\b", re.I)
_LEAVE_INFO = re.compile(r"\b(leaves?|plans?|planned|booked|off|away|absen\w*|vacations?|time off|pto|"
                         r"schedule|calendar|holidays?|trips?)\b", re.I)
_POLICY_WORDS = re.compile(r"\b(polic(y|ies)|rules?|approv\w*|process|how (do|can|should|to))\b", re.I)


def is_team_question(message: str) -> bool:
    """A lookup of who is on leave in the manager's team, not a policy question that mentions teams."""
    if _POLICY_WORDS.search(message):
        return False
    if _WHO_OFF.search(message):
        return True
    return bool(_TEAM.search(message) and (_ABSENT.search(message) or _SHOW.search(message)))


def other_employees(ctx: RequestContext, message: str) -> list[str]:
    """Employee IDs the message refers to, other than the requester (IDs, or full names from the database)."""
    from app.data.pii import PII

    text = _REQUEST_ID.sub(" ", message)            # a request ID like '...-e123-...' is not an employee ID
    found: list[str] = []
    for m in _EMP_ID.findall(text):
        found.append(m.upper())
    PII.ensure()
    low = f" {text.lower()} "
    for p in PII.people:
        name = (p.get("full_name") or "").lower().strip()
        if name and re.search(rf"\b{re.escape(name)}\b", low):
            found.append(p["employee_id"])
    out: list[str] = []
    for e in found:
        if e != ctx.employee_id and e not in out:
            out.append(e)
    return out


def _team_range(message: str, now) -> tuple[str, str, bool]:
    """(start, end, defaulted) for a team-leave question. Defaults to the next 30 days."""
    import calendar
    from datetime import timedelta

    from app.orchestrator.rule_parser import parse_date_range

    low = message.lower()
    if re.search(r"\btoday\b", low):
        return now.isoformat(), now.isoformat(), False
    if re.search(r"\btomorrow\b", low):
        d = now + timedelta(days=1)
        return d.isoformat(), d.isoformat(), False
    if "this month" in low:
        last = now.replace(day=calendar.monthrange(now.year, now.month)[1])
        return now.isoformat(), last.isoformat(), False
    if "next month" in low:
        first = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        return first.isoformat(), last.isoformat(), False
    rng = parse_date_range(message, now)
    if rng:
        return rng[0], rng[1], False
    return now.isoformat(), (now + timedelta(days=30)).isoformat(), True


def _refuse(text: str) -> dict:
    return {"answer": text, "route": "refuse", "citations": [], "tool_calls": []}


def _team_answer(ctx: RequestContext, message: str) -> dict:
    """'Who in my team is on leave next week?' -> get_team_leave (managers only; others get PermissionDenied)."""
    from app.orchestrator.agent import _format_tool_results

    start, end, defaulted = _team_range(message, today())
    out = T.get_team_leave(ctx, start, end)
    answer = _format_tool_results(ctx, [{"tool": "get_team_leave", "result": out}])
    if defaulted:
        answer += "\n\n(No dates were given, so this shows the next 30 days.)"
    return {"answer": answer, "route": "tool", "citations": [],
            "tool_calls": [{"name": "get_team_leave", "args": {"start_date": start, "end_date": end},
                            "status": "success"}]}


def _holiday_answer(ctx: RequestContext, message: str) -> dict | None:
    """'Which public holidays are there?' -> the full list from the calendar that applies to this user."""
    from datetime import date as _date

    from app.data import db
    from app.ingestion.pipeline import KB
    from app.tools.working_days import applicable_calendar

    now = today()
    year_m = re.search(r"\b(20\d{2})\b", message)
    year = int(year_m[1]) if year_m else now.year
    cal = applicable_calendar(KB.docs, ctx.role, ctx.region, year, now)
    if cal is None:
        return None
    rows = [h for h in db.holidays_for_doc(cal.doc_id) if h["holiday_date"].startswith(f"{year}-")]
    upcoming = bool(_UPCOMING.search(message))
    if upcoming:
        rows = [h for h in rows if h["holiday_date"] >= now.isoformat()]
    want_restricted = bool(re.search(r"\b(restricted|optional)\b", message, re.I))
    want_public = bool(re.search(r"\bpublic\b", message, re.I)) or not want_restricted

    def fmt(h: dict) -> str:
        d = _date.fromisoformat(h["holiday_date"])
        return f"- **{d.strftime('%a, %d %b %Y')}**: {h['name']}"

    parts = [f"Here are the {'remaining ' if upcoming else ''}holidays from **{cal.title}**:"]
    public = sorted((h for h in rows if h["kind"] == "public"), key=lambda h: h["holiday_date"])
    restricted = sorted((h for h in rows if h["kind"] != "public"), key=lambda h: h["holiday_date"])
    if want_public:
        parts.append(f"**Public holidays (office closed)** ({len(public)})\n" +
                     ("\n".join(fmt(h) for h in public) or "- none"))
    if restricted and (want_restricted or want_public):
        parts.append(f"**Restricted / optional holidays** ({len(restricted)})\n" + "\n".join(fmt(h) for h in restricted))
    locator = next((c.locator for c in KB.chunks if c.doc_id == cal.doc_id), "p. 1")
    return {"answer": "\n\n".join(parts) + f"\n\n{OFFLINE_NOTE}", "route": "rag", "tool_calls": [],
            "citations": [{"doc_id": cal.doc_id, "locator": locator}]}


def _pointwise(body: str) -> str:
    """Show a passage as bullet points: keep existing list lines, split prose into one point per sentence."""
    out: list[str] = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^([-*•]|\d+[.)])\s", line):
            out.append("- " + re.sub(r"^([-*•]|\d+[.)])\s+", "", line).replace(" | ", " · "))
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line) if s.strip()]
        out.extend(f"- {s}" for s in sentences)
    return "\n".join(out)


def _coverage(terms: set[str], text: str) -> float:
    if not terms:
        return 0.0
    return len(terms & set(tokenize(text))) / len(terms)


def offline_answer(ctx: RequestContext, message: str) -> dict:
    """Returns dict(answer, route, citations, tool_calls).

    Raises PermissionDenied when the requester may not see the employee (or team) asked about;
    agent._offline turns that into a 'refuse' answer with a fixed message.
    """
    from app.orchestrator import prompts
    from app.orchestrator.agent import _format_tool_results

    wants_balance, wants_requests = bool(_BALANCE.search(message)), bool(_REQUESTS.search(message))
    others = other_employees(ctx, message)
    mentions_team = bool(_TEAM.search(message)) and not _POLICY_WORDS.search(message)
    if not others and (mentions_team or is_team_question(message)):
        if wants_balance:                 # "my team's balances" = data about many employees at once
            return _refuse(prompts.BULK_TEXT)
        if mentions_team or not wants_requests:
            return _team_answer(ctx, message)

    if wants_balance or wants_requests:
        if len(others) > 1:
            return _refuse(prompts.BULK_TEXT)
        target = others[0] if others else ctx.employee_id   # permission is checked inside the tool
        if wants_balance:
            leave_type = "sick" if re.search(r"\bsick\b", message, re.I) else (
                "annual" if re.search(r"\bannual|vacation\b", message, re.I) else None)
            out = T.get_leave_balance(ctx, target, leave_type)
            tool, args = "get_leave_balance", {"employee_id": target,
                                               **({"leave_type": leave_type} if leave_type else {})}
        else:
            out = T.list_leave_requests(ctx, target)
            tool, args = "list_leave_requests", {"employee_id": target}
        return {"answer": _format_tool_results(ctx, [{"tool": tool, "result": out}]), "route": "tool",
                "citations": [], "tool_calls": [{"name": tool, "args": args, "status": "success"}]}

    # A question that names an employee ("E3001 leave plans") is about that person, not a document search.
    mentions_self = bool(re.search(rf"\b{re.escape(ctx.employee_id)}\b", message, re.I))
    if others or mentions_self:
        if len(others) > 1:
            return _refuse(prompts.BULK_TEXT)
        target = others[0] if others else ctx.employee_id
        if not T.can_view_employee(ctx, target):
            raise T.PermissionDenied()        # same fixed message whether or not the employee exists
        if _LEAVE_INFO.search(message):
            out = T.list_leave_requests(ctx, target)
            return {"answer": _format_tool_results(ctx, [{"tool": "list_leave_requests", "result": out}]),
                    "route": "tool", "citations": [],
                    "tool_calls": [{"name": "list_leave_requests", "args": {"employee_id": target},
                                    "status": "success"}]}
        return {"answer": f"What would you like to know about {target}? I can show their leave balance or their "
                          "leave requests.", "route": "clarify", "citations": [], "tool_calls": []}

    if _HOLIDAYS.search(message) and not re.search(r"\b(policy|rule|work on|compensat|swap)\b", message, re.I):
        hol = _holiday_answer(ctx, message)
        if hol:
            return hol

    evidence = retrieve(message, ctx.role, ctx.region, today())
    terms = set(tokenize(message))
    # keep passages that (after removing instruction-like text) contain most of the question's key words
    current = []
    for e in evidence.items:
        body = _clean_body(e.chunk.text)
        if not e.upcoming and body and _coverage(terms, body) >= 0.6:
            current.append((e, body))
    if not current:
        return {"answer": _not_found_text(message, evidence), "route": "no_answer", "citations": [],
                "tool_calls": []}
    # Section 5 precedence: strongest rank wins; equal-rank passages from different documents are shown together
    best_rank = min(e.rank for e, _ in current)
    winners, seen_docs = [], set()
    for e, body in sorted(current, key=lambda x: -_coverage(terms, x[1])):
        if e.rank == best_rank and e.doc.doc_id not in seen_docs:
            winners.append((e, body))
            seen_docs.add(e.doc.doc_id)
    winners = [(e, _pointwise(_with_next_section(e, body))) for e, body in winners[:2]]
    parts = [f"**{e.doc.title}** ({e.chunk.locator}):\n\n{body}" for e, body in winners]
    if len(winners) == 1:
        e, body = winners[0]
        answer = f"Here is what **{e.doc.title}** ({e.chunk.locator}) says:\n\n{body}"
    else:
        answer = ("These two documents have equal standing. If they give different values, they conflict and "
                  "neither one overrides the other:\n\n" + "\n\n".join(parts))
    return {"answer": f"{answer}\n\n{OFFLINE_NOTE}", "route": "rag", "tool_calls": [],
            "citations": [{"doc_id": e.doc.doc_id, "locator": e.chunk.locator} for e, _ in winners]}


def _with_next_section(e, body: str) -> str:
    """A very short passage (e.g. an intro line above a table) is completed with the next part of the same document."""
    if len(body) >= 250:
        return body
    from app.ingestion.pipeline import KB
    doc_chunks = [c for c in KB.chunks if c.doc_id == e.doc.doc_id]
    ids = [c.chunk_id for c in doc_chunks]
    if e.chunk.chunk_id not in ids:
        return body
    i = ids.index(e.chunk.chunk_id)
    if i + 1 < len(doc_chunks):
        nxt = doc_chunks[i + 1]
        heading = nxt.locator if not nxt.locator.startswith("p.") else ""
        extra = _clean_body(nxt.text)
        if extra:
            return f"{body}\n\n**{heading}**\n{extra}" if heading else f"{body}\n{extra}"
    return body


_SUGGESTIONS = ["How many sick days do I get?", "What is my leave balance?", "Which public holidays are there?",
                 "Apply annual leave from 14 Dec to 18 Dec", "Show my leave requests"]


def _not_found_text(message: str, evidence) -> str:
    """No passage answers the question: say so, and point to the closest documents this user may read."""
    question = " ".join(message.split())
    question = question if len(question) <= 80 else question[:77] + "..."
    titles: list[str] = []
    for e in evidence.items:
        if e.doc.title not in titles:
            titles.append(e.doc.title)
    lines = [f"I couldn't find a clear answer to \"{question}\" in the HR documents available to you."]
    if titles:
        lines.append("The closest documents I found are: " + ", ".join(f"**{t}**" for t in titles[:3])
                     + ". Try asking with the words those documents use, or contact the HR team.")
    else:
        lines.append("It may be outside HR topics, or not covered by any policy. Please contact the HR team "
                     "if you need more help.")
    lines.append("You could also try:\n" + "\n".join(f"- {s}" for s in _SUGGESTIONS[:3]))
    return "\n\n".join(lines)


_AI_DIRECTED = re.compile(r"\b(ai|assistant|chatbot|llm|language model)\b", re.I)


def _clean_body(chunk_text: str) -> str:
    """Drop the chunk's title line and any sentence addressed to an AI (document text is data, not instructions)."""
    body = chunk_text.split("\n", 1)[1] if "\n" in chunk_text else chunk_text
    kept = []
    for line in body.split("\n"):
        sentences = re.split(r"(?<=[.!?])\s+", line)
        line = " ".join(s for s in sentences if not (_AI_DIRECTED.search(s) or is_injection(s)))
        if line.strip():
            kept.append(line)
    return "\n".join(kept).strip()
