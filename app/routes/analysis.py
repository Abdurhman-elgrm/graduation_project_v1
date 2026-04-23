"""
routes/analysis.py — REST API for analysis results.

Endpoints:
  GET /api/analysis        — filtered list of analysis results
  GET /api/analysis/stats  — summary statistics for the dashboard
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.config import get_supabase

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/stats")
async def get_stats():
    """
    Return counts grouped by criticality for today's logs,
    plus total logs, total analyses, and open playbook count.
    """
    loop = asyncio.get_event_loop()
    supabase = get_supabase()

    def _query():
        from datetime import datetime, timezone
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()

        # Today's log count
        logs_today = (
            supabase.table("log_entries")
            .select("id", count="exact")
            .gte("ingested_at", today_start)
            .execute()
        )

        # Criticality breakdown (all time for the dashboard widgets)
        critical = (
            supabase.table("analysis_results")
            .select("id", count="exact")
            .eq("criticality", "CRITICAL")
            .execute()
        )
        high = (
            supabase.table("analysis_results")
            .select("id", count="exact")
            .eq("criticality", "HIGH")
            .execute()
        )
        medium = (
            supabase.table("analysis_results")
            .select("id", count="exact")
            .eq("criticality", "MEDIUM")
            .execute()
        )
        low = (
            supabase.table("analysis_results")
            .select("id", count="exact")
            .eq("criticality", "LOW")
            .execute()
        )
        info = (
            supabase.table("analysis_results")
            .select("id", count="exact")
            .eq("criticality", "INFO")
            .execute()
        )

        # Open playbooks
        open_pb = (
            supabase.table("playbooks")
            .select("id", count="exact")
            .in_("status", ["open", "in_progress"])
            .execute()
        )

        return {
            "logs_today": logs_today.count or 0,
            "critical": critical.count or 0,
            "high": high.count or 0,
            "medium": medium.count or 0,
            "low": low.count or 0,
            "info": info.count or 0,
            "open_playbooks": open_pb.count or 0,
        }

    try:
        return await loop.run_in_executor(None, _query)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("")
async def list_analysis(
    criticality: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    """Return analysis results, newest first, with optional criticality filter."""
    loop = asyncio.get_event_loop()
    supabase = get_supabase()
    offset = (page - 1) * limit

    def _query():
        q = (
            supabase.table("analysis_results")
            .select(
                "*, log_entries(id, timestamp, source_file, raw_message, parsed_fields)"
            )
            .order("analyzed_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if criticality:
            q = q.eq("criticality", criticality)
        return q.execute()

    try:
        result = await loop.run_in_executor(None, _query)
        return {"data": result.data, "page": page, "limit": limit}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
