"""
routes/logs.py — REST API for log entries.

Endpoints:
  GET  /api/logs                 — paginated list with optional filters
  GET  /api/logs/{id}            — single log entry with its analysis
  POST /api/logs/{id}/reanalyze  — re-queue AI analysis for a log entry
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.config import get_supabase

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def list_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    criticality: str | None = Query(None),
    source: str | None = Query(None),
):
    """Return a paginated list of log entries, newest first."""
    loop = asyncio.get_event_loop()
    supabase = get_supabase()
    offset = (page - 1) * limit

    async def _query():
        # Base query: log entries joined with latest analysis
        q = (
            supabase.table("log_entries")
            .select(
                "id, timestamp, source_file, raw_message, parsed_fields, ingested_at,"
                "analysis_results(id, criticality, threat_type, summary, analyzed_at)"
            )
            .order("ingested_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if source:
            q = q.eq("source_file", source)
        if criticality:
            # Filter via analysis_results
            q = q.eq("analysis_results.criticality", criticality)
        return q.execute()

    try:
        result = await loop.run_in_executor(None, _query)
        return {"data": result.data, "page": page, "limit": limit}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{log_id}")
async def get_log(log_id: str):
    """Return a single log entry with its full analysis result."""
    loop = asyncio.get_event_loop()
    supabase = get_supabase()

    def _query():
        return (
            supabase.table("log_entries")
            .select(
                "*, analysis_results("
                "id, criticality, threat_type, summary, ioc_data,"
                "recommended_actions, llm_model_used, analyzed_at,"
                "playbooks(id, incident_id, title, status, created_at)"
                ")"
            )
            .eq("id", log_id)
            .maybe_single()
            .execute()
        )

    try:
        result = await loop.run_in_executor(None, _query)
        if not result.data:
            raise HTTPException(status_code=404, detail="Log entry not found")
        return result.data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{log_id}/reanalyze")
async def reanalyze_log(log_id: str):
    """
    Delete the existing analysis for this log entry and re-queue AI analysis.
    The analysis runs asynchronously; this endpoint returns immediately.
    """
    from app.ai_analyzer import analyze_log_entry
    from app.playbook_generator import generate_playbook
    from app.websocket.manager import manager

    loop = asyncio.get_event_loop()
    supabase = get_supabase()

    # Fetch the log entry
    def _fetch():
        return supabase.table("log_entries").select("*").eq("id", log_id).maybe_single().execute()

    try:
        result = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not result.data:
        raise HTTPException(status_code=404, detail="Log entry not found")

    entry = result.data

    # Delete existing analysis (cascades to playbooks via FK)
    def _delete():
        return supabase.table("analysis_results").delete().eq("log_entry_id", log_id).execute()

    await loop.run_in_executor(None, _delete)

    # Kick off new analysis as a background task
    asyncio.create_task(
        analyze_log_entry(
            log_id,
            entry,
            manager,
            generate_playbook,
        )
    )

    return {"status": "reanalysis_queued", "log_id": log_id}
