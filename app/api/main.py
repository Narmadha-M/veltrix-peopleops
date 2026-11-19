"""FastAPI application: startup ingestion, routers and static files."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import chat, ui
from app.ingestion.pipeline import ingest_in_background
from app.logging_setup import setup_logging

STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if os.getenv("SKIP_STARTUP_INGEST") != "1":
        ingest_in_background()          # /health returns 503 until this finishes
    yield


app = FastAPI(title="Veltrix PeopleOps Assistant", lifespan=lifespan)
app.include_router(chat.router)         # POST /chat, GET /health (assessment contract), POST /chat/stream
app.include_router(ui.router)           # demo UI: /, /ui/*
app.mount("/static", StaticFiles(directory=STATIC), name="static")
