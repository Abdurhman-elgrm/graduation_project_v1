"""
main.py — FastAPI application entry point for the AI SOC Analyst Platform.

Startup sequence:
  1. Create FastAPI app with CORS, Jinja2 templates
  2. Mount all API routers
  3. Register WebSocket endpoint /ws/live
  4. On startup: launch async log tailer as a background task
  5. Serve HTML pages at / and /playbook/{id}
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request

from app.routes.logs import router as logs_router
from app.routes.analysis import router as analysis_router
from app.routes.playbooks import router as playbooks_router
from app.routes.settings import router as settings_router
from app.websocket.manager import manager

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI SOC Analyst Platform",
    description="Real-time security operations centre powered by Claude AI",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(logs_router)
app.include_router(analysis_router)
app.include_router(playbooks_router)
app.include_router(settings_router)

# ── Background task reference (kept to allow cancellation) ───────────────────

_tailer_task: asyncio.Task | None = None

# ── Startup / Shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event() -> None:
    """Launch the async log tailer as a non-blocking background task."""
    global _tailer_task

    from app.ai_analyzer import analyze_log_entry
    from app.log_tailer import start_log_tailer
    from app.playbook_generator import generate_playbook

    async def _analyze_wrapper(log_id: str, entry: dict) -> None:
        await analyze_log_entry(log_id, entry, manager, generate_playbook)

    _tailer_task = asyncio.create_task(
        start_log_tailer(manager, _analyze_wrapper),
        name="log_tailer",
    )

    def _handle_tailer_exception(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            import sys
            print(f"[startup] Log tailer crashed: {exc}", file=sys.stderr)

    _tailer_task.add_done_callback(_handle_tailer_exception)
    print("[startup] Log tailer started.")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _tailer_task
    if _tailer_task and not _tailer_task.done():
        _tailer_task.cancel()
        try:
            await _tailer_task
        except asyncio.CancelledError:
            pass
    print("[shutdown] Log tailer stopped.")

# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; we only send (broadcast), not receive
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)

# ── HTML page routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/playbook/{playbook_id}", response_class=HTMLResponse)
async def playbook_detail(request: Request, playbook_id: str):
    return templates.TemplateResponse(
        request,
        "playbook_detail.html",
        {"playbook_id": playbook_id},
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html")

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ws_connections": manager.connection_count,
    }
