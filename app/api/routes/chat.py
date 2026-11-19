"""Assessment endpoints (POST /chat, GET /health) plus a streaming variant for the UI (POST /chat/stream)."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import ChatRequest, parse_chat_request
from app.ingestion.pipeline import KB
from app.logging_setup import log_event
from app.orchestrator.agent import ChatResult, handle_message
from app.orchestrator.progress import set_listener

log = logging.getLogger("veltrix.api")
router = APIRouter()


def to_payload(request_id: str, r: ChatResult) -> dict:
    """Section 9.1 response body."""
    return {"request_id": request_id, "answer": r.answer, "route": r.route, "citations": r.citations,
            "tool_calls": r.tool_calls, "pending_confirmation": r.pending_confirmation}


def _run(req: ChatRequest, request_id: str) -> ChatResult:
    with req.session.lock:            # one message at a time per session
        return handle_message(req.ctx, req.session, req.message, request_id)


@router.get("/health")
def health():
    if KB.ready:
        return {"status": "ok"}
    return JSONResponse(status_code=503, content={"status": "starting"})


@router.post("/chat")
async def chat(request: Request):
    started = time.time()
    req = await parse_chat_request(request)
    if isinstance(req, JSONResponse):
        return req
    request_id = str(uuid.uuid4())
    result = await run_in_threadpool(_run, req, request_id)
    log_event(log, "chat_request", request_id=request_id, requester=req.ctx.employee_id, route=result.route,
              ms=int((time.time() - started) * 1000))
    return to_payload(request_id, result)


@router.post("/chat/stream", include_in_schema=False)
async def chat_stream(request: Request):
    """Same contract as /chat, delivered as Server-Sent Events: 'progress' events while the assistant
    works, then one 'result' event whose data is exactly the /chat response body."""
    req = await parse_chat_request(request)
    if isinstance(req, JSONResponse):
        return req
    request_id = str(uuid.uuid4())
    events: queue.Queue = queue.Queue()

    def worker():
        set_listener(lambda stage, data: events.put(("progress", {"stage": stage, **data})))
        started = time.time()
        try:
            result = _run(req, request_id)
            events.put(("result", to_payload(request_id, result)))
            log_event(log, "chat_request", request_id=request_id, requester=req.ctx.employee_id,
                      route=result.route, ms=int((time.time() - started) * 1000), streamed=True)
        except Exception as exc:
            log_event(log, "stream_error", request_id=request_id, error=type(exc).__name__)
            events.put(("error", {"detail": "Something went wrong."}))
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        yield f"event: progress\ndata: {json.dumps({'stage': 'received'})}\n\n"
        while True:
            try:
                item = events.get(timeout=30)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            kind, data = item
            yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
