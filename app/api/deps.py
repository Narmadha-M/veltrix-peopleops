"""Shared request handling for the chat endpoints: identity (401), body (400), session ownership (403)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.ingestion.pipeline import KB
from app.logging_setup import log_event
from app.orchestrator.sessions import SESSIONS, Session, SessionOwnershipError
from app.tools.leave_tools import RequestContext

log = logging.getLogger("veltrix.api")
MAX_MESSAGE_CHARS = 4000


def error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


@dataclass
class ChatRequest:
    ctx: RequestContext
    session: Session
    message: str


async def requester(request: Request) -> RequestContext | JSONResponse:
    """The X-Employee-Id header and its database row are the only source of identity."""
    employee_id = (request.headers.get("X-Employee-Id") or "").strip()
    if not employee_id:
        return error(401, "X-Employee-Id header is required")
    ctx = await run_in_threadpool(RequestContext.load, employee_id)
    return ctx if ctx is not None else error(401, "Unknown employee")


async def parse_chat_request(request: Request) -> ChatRequest | JSONResponse:
    ctx = await requester(request)
    if isinstance(ctx, JSONResponse):
        return ctx
    try:
        body = await request.json()
    except Exception:
        return error(400, "Body must be valid JSON")
    if not isinstance(body, dict):
        return error(400, "Body must be a JSON object")
    session_id, message = body.get("session_id"), body.get("message")
    if not isinstance(session_id, str) or not session_id.strip():
        return error(400, "session_id must be a non-empty string")
    if not isinstance(message, str) or not message.strip():
        return error(400, "message must be a non-empty string")
    if len(message) > MAX_MESSAGE_CHARS:
        return error(400, "message is too long")
    try:
        session = SESSIONS.get_or_create(session_id, ctx.employee_id)
    except SessionOwnershipError:
        log_event(log, "session_forbidden", requester=ctx.employee_id)
        return error(403, "This session belongs to another employee")
    if not KB.ready:
        return error(503, "Service is still starting")
    return ChatRequest(ctx, session, message.strip())
