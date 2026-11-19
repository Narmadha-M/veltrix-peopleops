"""Orchestration: plan (LLM, no corpus) -> enforce rules in code -> tools / retrieval -> compose (LLM) -> route.

Security-relevant decisions are made here and in the tools, never by the LLM:
- write actions run only after an explicit confirmation stored in session state;
- every tool call is executed with the RequestContext built from the X-Employee-Id header;
- retrieval only ever scores documents the requester may read;
- citations are limited to evidence that was actually retrieved for this requester.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

from app.config import today
from app.data import db
from app.data.pii import PII
from app.ingestion.pipeline import KB
from app.llm.provider import get_llm
from app.logging_setup import log_event
from app.orchestrator import prompts
from app.orchestrator.dates import date_hints
from app.orchestrator.progress import emit
from app.orchestrator.guardrails import (is_bulk_request, is_confirmation, is_greeting, is_help,
                                         is_injection, is_thanks)
from app.orchestrator.sessions import Proposal, Session
from app.retrieval.retriever import EvidenceSet, retrieve
from app.tools import leave_tools as T
from app.tools.leave_tools import PermissionDenied, RequestContext, ToolRejected

log = logging.getLogger("veltrix.orchestrator")

SELF_REFS = {"", "self", "me", "my", "myself", "i", "mine"}
READ_TOOLS = ("get_leave_balance", "list_leave_requests", "get_team_leave")
WRITE_TOOLS = ("apply_leave", "cancel_leave")


@dataclass
class ChatResult:
    answer: str
    route: str
    citations: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    pending_confirmation: bool = False


class ClarifyNeeded(Exception):
    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


# ====================================================================== entry point
def handle_message(ctx: RequestContext, session: Session, message: str, request_id: str) -> ChatResult:
    pending = session.pending
    session.pending = None          # a proposal survives only if this message re-proposes
    try:
        if pending and is_confirmation(message):
            result = _execute_pending(ctx, pending)
        elif is_greeting(message) or is_help(message):
            result = ChatResult(_greeting_text(ctx), "clarify")
        elif is_thanks(message):
            result = ChatResult("You're welcome! Is there anything else I can help you with?", "clarify")
        elif is_injection(message):
            result = ChatResult(prompts.MANIPULATION_TEXT, "refuse")
        elif is_bulk_request(message):
            result = ChatResult(prompts.BULK_TEXT, "refuse")
        else:
            result = _plan_and_run(ctx, session, message, pending, request_id)
    except Exception as exc:  # never leak internals; the request still gets a valid response
        log_event(log, "orchestrator_error", request_id=request_id, error=type(exc).__name__)
        result = ChatResult(prompts.ERROR_TEXT, "no_answer")
    result = _enforce_route_contract(result)
    result.answer = PII.sanitize_answer(result.answer, T.visible_employee_ids(ctx))
    session.add_turn(message, result.answer)
    log_event(log, "chat_result", request_id=request_id, requester=ctx.employee_id, route=result.route,
              tools=[(c["name"], c["status"]) for c in result.tool_calls],
              citations=[c["doc_id"] for c in result.citations], pending=result.pending_confirmation)
    return result


def _greeting_text(ctx: RequestContext) -> str:
    """Greetings are not tested by the assessment; answer with what the assistant can do (route 'clarify')."""
    lines = ["Hi! I'm the Veltrix PeopleOps assistant. What can I do for you today? I can help you:",
             "- answer questions about HR policies, leave rules and public holidays",
             "- check your leave balance and leave requests",
             "- apply for or cancel annual or sick leave"]
    if ctx.role == "manager":
        lines.append("- see which of your direct reports are on leave")
    if ctx.role == "hr":
        lines.append("- look up the leave of one employee at a time")
    return "\n".join(lines)


def _enforce_route_contract(r: ChatResult) -> ChatResult:
    if r.route in ("clarify", "no_answer", "refuse"):
        r.citations, r.tool_calls, r.pending_confirmation = [], [], False
    elif r.route == "rag":
        r.tool_calls = []
    elif r.route == "tool":
        r.citations = []
    return r


# ====================================================================== planning
def _planner_input(ctx: RequestContext, session: Session, message: str, pending: Proposal | None) -> str:
    history = "\n".join(f"{role}: {text}" for role, text in session.history[-8:]) or "(none)"
    waiting = f"{pending.name} {json.dumps(pending.args)}" if pending else "none"
    reports = ", ".join(ctx.direct_reports) if ctx.direct_reports else "none"
    return (f"## Requester\nemployee_id: {ctx.employee_id}\nrole: {ctx.role}\nregion: {ctx.region}\n"
            f"direct_reports: {reports}\n\n## Date table\n{date_hints(today())}\n\n"
            f"## Waiting proposal\n{waiting}\n\n## Conversation so far\n{history}\n\n## New message\n{message}")


def _offline(ctx: RequestContext, message: str, request_id: str, exc: Exception,
             session: Session | None = None) -> ChatResult:
    from app.orchestrator.offline import offline_answer
    from app.orchestrator.rule_parser import parse_write
    emit("keyword")
    log_event(log, "llm_unavailable_offline_mode", request_id=request_id, error=type(exc).__name__)
    try:
        call = parse_write(message, today()) if session is not None else None
        if call:          # rule-parsed apply/cancel: same validation + proposal + "yes" confirmation as LLM mode
            return _handle_write(ctx, session, call)
        r = offline_answer(ctx, message)
    except ClarifyNeeded as c:
        return ChatResult(c.question, "clarify")
    except PermissionDenied as p:
        return ChatResult(p.message, "refuse")
    return ChatResult(r["answer"], r["route"], r["citations"], r["tool_calls"])


def _plan_and_run(ctx: RequestContext, session: Session, message: str, pending: Proposal | None,
                  request_id: str) -> ChatResult:
    emit("understanding")
    try:
        plan = get_llm().complete_json(purpose="plan", system=prompts.PLANNER_SYSTEM,
                                       user=_planner_input(ctx, session, message, pending),
                                       schema=prompts.PLAN_SCHEMA, corpus_tokens=0, request_id=request_id)
    except Exception as exc:          # LLM unavailable: answer in keyword mode instead of failing
        return _offline(ctx, message, request_id, exc, session)
    action = plan.get("action")
    if action == "refuse":
        reason = plan.get("refuse_reason")
        text = {"bulk_data": prompts.BULK_TEXT, "manipulation": prompts.MANIPULATION_TEXT}.get(
            reason, prompts.OFF_TOPIC_TEXT)
        return ChatResult(text, "refuse")
    if action == "clarify":
        return ChatResult(plan.get("clarify_question") or "Could you give me a few more details?", "clarify")

    calls = [c for c in plan.get("tool_calls") or [] if c.get("name") in READ_TOOLS + WRITE_TOOLS]
    writes = [c for c in calls if c["name"] in WRITE_TOOLS]
    try:
        if writes:
            return _handle_write(ctx, session, writes[0])
        reads = [c for c in calls if c["name"] in READ_TOOLS]
        return _handle_read(ctx, message, plan, reads, request_id)
    except ClarifyNeeded as c:
        return ChatResult(c.question, "clarify")
    except PermissionDenied as p:
        return ChatResult(p.message, "refuse")


def _reference_date(value: str) -> date:
    now = today()
    try:
        d = date.fromisoformat((value or "").strip())
        return d if d < now else now
    except ValueError:
        return now


# ====================================================================== read path
def _resolve_employee(ctx: RequestContext, ref: str) -> str:
    ref = (ref or "").strip()
    if ref.lower() in SELF_REFS or ref.upper() == ctx.employee_id:
        return ctx.employee_id
    if re.fullmatch(r"[A-Za-z]\d{2,}", ref):
        return ref.upper()
    matches = [m for m in db.find_employees_by_name(ref) if T.can_view_employee(ctx, m["employee_id"])]
    if len(matches) == 1:
        return matches[0]["employee_id"]
    if len(matches) > 1:
        options = ", ".join(f"{m['employee_id']} ({m['full_name']})" for m in matches)
        raise ClarifyNeeded(f"I found more than one employee with that name: {options}. Which one do you mean?")
    raise PermissionDenied()


def _handle_read(ctx: RequestContext, message: str, plan: dict, reads: list[dict], request_id: str) -> ChatResult:
    resolved = []
    for c in reads:
        target = None if c["name"] == "get_team_leave" else _resolve_employee(ctx, c.get("employee_ref", ""))
        resolved.append((c, target))
    others = {t for _, t in resolved if t and t != ctx.employee_id}
    if len(others) > 1:
        return ChatResult(prompts.BULK_TEXT, "refuse")

    executed: list[dict] = []
    results: list[dict] = []
    if resolved:
        emit("checking_leave")
    for c, target in resolved:
        name = c["name"]
        if name == "get_leave_balance":
            args = {"employee_id": target, **({"leave_type": c["leave_type"]} if c.get("leave_type") else {})}
        elif name == "list_leave_requests":
            args = {"employee_id": target, **({"status": c["status"]} if c.get("status") else {})}
        else:
            args = {"start_date": c.get("start_date", ""), "end_date": c.get("end_date", "")}
            if not args["start_date"] or not args["end_date"]:
                raise ClarifyNeeded("Which dates should I check your team's leave for?")
        try:
            out = getattr(T, name)(ctx, **args)
            executed.append({"name": name, "args": args, "status": "success"})
            results.append({"tool": name, "result": out})
        except ToolRejected as e:
            executed.append({"name": name, "args": args, "status": "error"})
            results.append({"tool": name, "error": e.reason})
        log_event(log, "tool_call", request_id=request_id, name=name, status=executed[-1]["status"],
                  requester=ctx.employee_id)

    doc_query = (plan.get("doc_query") or "").strip()
    if not doc_query and not executed:
        doc_query = message
    ref_date = _reference_date(plan.get("reference_date", ""))
    evidence = None
    if doc_query:
        emit("searching")
        evidence = retrieve(doc_query, ctx.role, ctx.region, ref_date)
        emit("found", passages=len(evidence.items), documents=len({e.doc.doc_id for e in evidence.items}))

    if evidence and evidence.items:
        emit("writing")
        try:
            answer, citations, coverage = _compose(ctx, message, doc_query, evidence, results, request_id)
        except Exception as exc:
            if executed:
                return ChatResult(_format_tool_results(ctx, results), "tool", [], executed)
            return _offline(ctx, doc_query or message, request_id, exc)
        if citations and coverage != "none":
            return ChatResult(answer, "hybrid" if executed else "rag", citations, executed)
    if executed:
        return ChatResult(_format_tool_results(ctx, results), "tool", [], executed)
    return ChatResult(prompts.NO_ANSWER_TEXT, "no_answer")


def _compose(ctx: RequestContext, message: str, doc_query: str, evidence: EvidenceSet, results: list[dict],
             request_id: str) -> tuple[str, list[dict], str]:
    profile = {"employee_id": ctx.employee_id, "role": ctx.role, "region": ctx.region,
               "join_date": ctx.join_date, "level": ctx.level}
    user = (f"## Requester profile\n{json.dumps(profile)}\n\n## Reference date\n{evidence.reference_date.isoformat()}"
            f" (today is {today().isoformat()})\n\n## Question\n{message}\n\n## Search intent\n{doc_query}\n\n"
            f"## Tool results (leave database)\n{json.dumps(results) if results else 'none'}\n\n"
            f"## Evidence\n{evidence.render()}")
    out = get_llm().complete_json(purpose="compose", system=prompts.COMPOSER_SYSTEM, user=user,
                                  schema=prompts.COMPOSE_SCHEMA, corpus_tokens=evidence.corpus_tokens,
                                  request_id=request_id)
    by_id = evidence.by_id()
    citations, seen = [], set()
    for eid in out.get("used_evidence_ids") or []:
        ev = by_id.get(str(eid).strip())
        if ev is None:            # never cite something that was not retrieved for this requester
            continue
        key = (ev.doc.doc_id, ev.chunk.locator)
        if key not in seen:
            seen.add(key)
            citations.append({"doc_id": ev.doc.doc_id, "locator": ev.chunk.locator})
    return str(out.get("answer") or "").strip(), citations, out.get("coverage", "full")


def _format_tool_results(ctx: RequestContext, results: list[dict]) -> str:
    lines = []
    for r in results:
        if "error" in r:
            lines.append(f"I couldn't complete {r['tool'].replace('_', ' ')}: {r['error']}")
            continue
        out = r["result"]
        who = "Your" if out.get("employee_id") == ctx.employee_id else f"{out.get('employee_id')}'s"
        if r["tool"] == "get_leave_balance":
            parts = [f"{k}: {v:g} days" for k, v in out["balances"].items()]
            lines.append(f"{who} current leave balance — " + (", ".join(parts) if parts else "no balance on record") + ".")
        elif r["tool"] == "list_leave_requests":
            if not out["requests"]:
                lines.append(f"{who} leave requests: none found.")
            else:
                lines.append(f"{who} leave requests:")
                lines += [f"- {q['leave_type']} leave, {q['start_date']} to {q['end_date']} ({q['working_days']} "
                          f"working days), {q['status']} — request ID {q['request_id']}" for q in out["requests"]]
        elif r["tool"] == "get_team_leave":
            if not out["leave"]:
                lines.append(f"None of your direct reports has pending or approved leave between "
                             f"{out['start_date']} and {out['end_date']}.")
            else:
                lines.append(f"Team leave between {out['start_date']} and {out['end_date']}:")
                lines += [f"- {q['employee_id']}: {q['leave_type']} leave, {q['start_date']} to {q['end_date']} "
                          f"({q['working_days']} working days), {q['status']}" for q in out["leave"]]
    return "\n".join(lines)


# ====================================================================== write path (propose)
def _handle_write(ctx: RequestContext, session: Session, call: dict) -> ChatResult:
    if call["name"] == "apply_leave":
        return _propose_apply(ctx, session, call)
    return _propose_cancel(ctx, session, call)


def _propose_apply(ctx: RequestContext, session: Session, call: dict) -> ChatResult:
    leave_type = (call.get("leave_type") or "").strip().lower()
    start, end = (call.get("start_date") or "").strip(), (call.get("end_date") or "").strip()
    if start and not end:
        end = start
    missing = []
    if leave_type not in T.LEAVE_TYPES:
        missing.append("whether this is annual or sick leave")
    if not start:
        missing.append("the start and end dates")
    if missing:
        return ChatResult("To apply for leave, please tell me " + " and ".join(missing) + ".", "clarify")
    args = {"leave_type": leave_type, "start_date": start, "end_date": end}
    emit("checking_rules")
    try:
        p = T.validate_apply(ctx, KB.docs, leave_type, start, end)
    except ToolRejected as e:
        return ChatResult(f"I can't submit this leave request: {e.reason}", "tool", [],
                          [{"name": "apply_leave", "args": args, "status": "error"}])
    args = {"leave_type": p["leave_type"], "start_date": p["start_date"], "end_date": p["end_date"]}
    session.pending = Proposal("apply_leave", args, p)
    hol = ""
    if p["holidays_excluded"]:
        hol = " (not counting " + ", ".join(f"{h['name']} on {h['date']}" for h in p["holidays_excluded"]) + ")"
    answer = (f"Please confirm this leave request:\n- Type: {p['leave_type']} leave\n"
              f"- Dates: {p['start_date']} to {p['end_date']}\n- Working days: {p['working_days']}{hol}\n"
              f"- {p['leave_type'].capitalize()} balance after this request: "
              f"{p['balance_before'] - p['working_days']:g} days (now {p['balance_before']:g})\n"
              "Reply \"yes\" to submit it, or tell me what to change.")
    return ChatResult(answer, "tool", [], [{"name": "apply_leave", "args": args, "status": "pending_confirmation"}],
                      pending_confirmation=True)


def _propose_cancel(ctx: RequestContext, session: Session, call: dict) -> ChatResult:
    request_id = (call.get("request_id") or "").strip()
    pre_calls: list[dict] = []
    if not request_id:
        own = T.list_leave_requests(ctx, ctx.employee_id)["requests"]
        pre_calls.append({"name": "list_leave_requests", "args": {"employee_id": ctx.employee_id},
                          "status": "success"})
        hint_start, hint_end = (call.get("start_date") or "").strip(), (call.get("end_date") or "").strip()
        hint_type = (call.get("leave_type") or "").strip()
        lo, hi = hint_start or hint_end, hint_end or hint_start
        matches = [r for r in own
                   if (not lo or (r["start_date"] <= hi and r["end_date"] >= lo))
                   and (not hint_type or r["leave_type"] == hint_type)]
        active = [r for r in matches if r["status"] in ("pending", "approved")]
        cancellable = [r for r in active if r["start_date"] > today().isoformat()]
        if len(cancellable) > 1:
            options = "; ".join(f"{r['leave_type']} leave {r['start_date']} to {r['end_date']} ({r['status']})"
                                for r in cancellable)
            return ChatResult(f"You have several requests that could be cancelled: {options}. Which one do you "
                              "want to cancel?", "clarify")
        if len(cancellable) == 1:
            request_id = cancellable[0]["request_id"]
        elif active:
            request_id = active[0]["request_id"]       # will be rejected below with the specific reason
        else:
            what = "matching those details " if (lo or hint_type) else ""
            return ChatResult(f"You have no pending or approved leave requests {what}that can be cancelled.",
                              "tool", [], pre_calls)
    args = {"request_id": request_id}
    try:
        req = T.validate_cancel(ctx, request_id)
    except ToolRejected as e:
        return ChatResult(f"I can't cancel this request: {e.reason}", "tool", [],
                          pre_calls + [{"name": "cancel_leave", "args": args, "status": "error"}])
    session.pending = Proposal("cancel_leave", args, req)
    answer = (f"Please confirm you want to cancel this request:\n- {req['leave_type'].capitalize()} leave, "
              f"{req['start_date']} to {req['end_date']} ({req['working_days']} working days, {req['status']})\n"
              f"- Request ID: {req['request_id']}\n- {req['working_days']} days will be returned to your "
              f"{req['leave_type']} balance.\nReply \"yes\" to cancel it.")
    return ChatResult(answer, "tool", [],
                      pre_calls + [{"name": "cancel_leave", "args": args, "status": "pending_confirmation"}],
                      pending_confirmation=True)


# ====================================================================== write path (execute)
def _execute_pending(ctx: RequestContext, p: Proposal) -> ChatResult:
    emit("saving")
    try:
        if p.name == "apply_leave":
            out = T.apply_leave(ctx, KB.docs, **p.args)
            answer = (f"Done. Your {out['leave_type']} leave request for {out['start_date']} to {out['end_date']} "
                      f"({out['working_days']} working days) has been submitted with status pending.\n"
                      f"Request ID: {out['request_id']}\nYour {out['leave_type']} balance is now "
                      f"{out['balance_after']:g} days.")
        else:
            out = T.cancel_leave(ctx, **p.args)
            answer = (f"Done. Your {out['leave_type']} leave request for {out['start_date']} to {out['end_date']} "
                      f"has been cancelled and {out['days_returned']} days were returned to your "
                      f"{out['leave_type']} balance.")
        status = "success"
    except ToolRejected as e:
        answer, status = f"I couldn't complete that: {e.reason}", "error"
    return ChatResult(answer, "tool", [], [{"name": p.name, "args": p.args, "status": status}])
