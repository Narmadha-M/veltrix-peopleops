"""Demo UI support endpoints (optional; not part of the assessment contract)."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from app.api.deps import error, requester
from app.config import get_settings
from app.data import db
from app.data.pii import mask_email
from app.ingestion.pipeline import KB
from app.logging_setup import log_event

log = logging.getLogger("veltrix.api")
router = APIRouter(include_in_schema=False)
STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@router.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@router.post("/ui/login")
async def login(request: Request):
    """Demo sign-in: email -> employee. Not real authentication (the API trusts X-Employee-Id);
    it only tells the UI which header to send. Unknown emails use the demo employee."""
    try:
        body = await request.json()
        email = str(body.get("email", "")).strip()
    except Exception:
        return error(400, "Body must be JSON with an email")
    if "@" not in email or len(email) > 254:
        return error(400, "Please enter a valid work email address")
    emp = await run_in_threadpool(db.get_employee_by_email, email)
    demo = emp is None
    if demo:
        emp = await run_in_threadpool(_demo_employee)
        if emp is None:
            return error(404, "No demo employee is available in the database")
    reports = await run_in_threadpool(db.direct_report_ids, emp["employee_id"]) if emp["role"] == "manager" else []
    log_event(log, "ui_login", employee_id=emp["employee_id"], demo=demo)
    local = email.split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ").title()
    return {"employee_id": emp["employee_id"], "name": local if demo else emp["full_name"],
            "email": mask_email(email if demo else emp["email"]), "role": emp["role"], "region": emp["region"],
            "level": emp["level"], "direct_reports": reports, "demo": demo}


def _demo_employee() -> dict | None:
    wanted = (os.getenv("DEMO_EMPLOYEE_ID") or "").strip()
    if wanted:
        emp = db.get_employee(wanted)
        if emp:
            return emp
    first = next((e for e in db.list_employee_directory() if e["role"] == "employee"), None)
    return db.get_employee(first["employee_id"]) if first else None


@router.get("/ui/status")
def status():
    """Whether an LLM is configured ('AI online' vs 'Keyword mode'). No network call."""
    from app.llm.provider import get_llm
    try:
        get_llm()
        ai = True
    except Exception:
        ai = False
    return {"ai": ai, "provider": get_settings().llm_provider, "ready": KB.ready,
            "documents": len(KB.docs), "chunks": len(KB.chunks)}


@router.post("/ui/feedback")
async def feedback(request: Request):
    """Thumbs up/down on an answer. Only IDs and the rating are logged (no message text)."""
    ctx = await requester(request)
    if isinstance(ctx, JSONResponse):
        return ctx
    try:
        body = await request.json()
    except Exception:
        return error(400, "Body must be JSON")
    rating = body.get("rating")
    if rating not in ("up", "down"):
        return error(400, "rating must be 'up' or 'down'")
    log_event(log, "feedback", requester=ctx.employee_id, request_id=str(body.get("request_id", ""))[:64],
              rating=rating)
    return {"ok": True}


@router.get("/ui/employees")
def employees():
    """Employee IDs, roles and regions only (no names, emails or phones)."""
    return db.list_employee_directory()


# ---------------------------------------------------------------- open a cited source document
# A citation chip opens the original document in a new tab. A plain link cannot send the X-Employee-Id
# header, so the UI first asks for a short-lived signed link (with the header), then opens that link.
# Access is checked twice (when the link is made and when it is opened) with the same role/region rules
# as retrieval. Unknown and forbidden documents both answer 404, so restricted documents stay invisible.
import hashlib
import hmac
import html as _html
import secrets
import time

_LINK_KEY = secrets.token_bytes(32)          # new key per process: links die on restart
_LINK_TTL_S = 300


def _sign(employee_id: str, doc_id: str, expires: int) -> str:
    msg = f"{employee_id}|{doc_id}|{expires}".encode()
    return hmac.new(_LINK_KEY, msg, hashlib.sha256).hexdigest()


def _readable_doc(employee_id: str, doc_id: str):
    from app.retrieval.policy import is_readable
    from app.tools.leave_tools import RequestContext
    ctx = RequestContext.load(employee_id)
    doc = KB.docs.get(doc_id)
    if ctx is None or doc is None or not is_readable(doc, ctx.role, ctx.region):
        return None
    return doc


@router.post("/ui/doc-link")
async def doc_link(request: Request):
    ctx = await requester(request)
    if isinstance(ctx, JSONResponse):
        return ctx
    try:
        doc_id = str((await request.json()).get("doc_id", ""))
    except Exception:
        return error(400, "Body must be JSON with a doc_id")
    doc = await run_in_threadpool(_readable_doc, ctx.employee_id, doc_id)
    if doc is None:
        return error(404, "Document not found")
    expires = int(time.time()) + _LINK_TTL_S
    sig = _sign(ctx.employee_id, doc_id, expires)
    log_event(log, "doc_open", requester=ctx.employee_id, doc_id=doc_id)
    return {"url": f"/ui/doc/{doc_id}?e={ctx.employee_id}&x={expires}&s={sig}"}


@router.get("/ui/doc/{doc_id}")
def open_doc(doc_id: str, e: str = "", x: int = 0, s: str = ""):
    if x < time.time() or not hmac.compare_digest(_sign(e, doc_id, x), s):
        return error(404, "This link has expired. Click the source again.")
    doc = _readable_doc(e, doc_id)
    if doc is None:
        return error(404, "Document not found")
    path = os.path.join(get_settings().corpus_dir, doc.file)
    if not os.path.isfile(path):
        return error(404, "Document not found")
    if doc.file.lower().endswith(".pdf"):
        return FileResponse(path, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{os.path.basename(doc.file)}"'})
    return _render_doc(doc, path)


def _render_doc(doc, path: str):
    """DOCX / HTML / Markdown shown as a clean, script-free page built from the parsed text."""
    from fastapi.responses import HTMLResponse
    from app.ingestion.loaders import load_any

    blocks, _ = load_any(path)
    e = _html.escape
    parts = []
    for b in blocks:
        if b.kind == "heading":
            lvl = min(max(b.level, 1) + 1, 4)
            parts.append(f"<h{lvl}>{e(b.text)}</h{lvl}>")
        elif b.kind == "table" and b.rows:
            head, *rest = b.rows
            parts.append("<table><thead><tr>" + "".join(f"<th>{e(c)}</th>" for c in head) + "</tr></thead><tbody>"
                         + "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in rest)
                         + "</tbody></table>")
        elif b.text:
            parts.append(f"<p>{e(b.text)}</p>")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(doc.title)}</title>
<style>
:root{{color-scheme:light dark;--bg:#faf9f6;--ink:#1f1d1a;--muted:#6b665e;--line:#e5e1d8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1a18;--ink:#ece8e1;--muted:#a39d93;--line:#3a3732}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:820px;margin:0 auto;padding:32px 16px 64px}}
.meta{{color:var(--muted);font-size:13px;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:24px}}
h1{{font-size:28px;line-height:1.25;margin:0 0 6px}} h2,h3,h4{{margin:28px 0 8px}}
table{{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px;display:block;overflow-x:auto}}
th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}} th{{font-weight:600}}
</style></head><body><main>
<h1>{e(doc.title)}</h1>
<div class="meta">{e(doc.doc_id)} · effective {e(doc.effective_date)} · {e(", ".join(doc.regions))}</div>
{"".join(parts)}
</main></body></html>"""
    return HTMLResponse(page, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                                       "X-Content-Type-Options": "nosniff"})
