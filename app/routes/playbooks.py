"""
routes/playbooks.py — REST API for incident response playbooks.

Endpoints:
  GET   /api/playbooks              — list playbooks with optional status filter
  GET   /api/playbooks/{id}         — single playbook with all steps
  POST  /api/playbooks/trigger/{analysis_id} — manually trigger playbook generation
  PATCH /api/playbooks/{id}/status  — update playbook status
  PATCH /api/playbooks/{id}/steps/{step_id}  — toggle step completion
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import get_supabase
from app.websocket.manager import manager

router = APIRouter(prefix="/api/playbooks", tags=["playbooks"])


class StatusUpdate(BaseModel):
    status: str  # open | in_progress | closed


class StepUpdate(BaseModel):
    is_completed: bool
    completed_by: str | None = None


@router.get("")
async def list_playbooks(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Return playbooks with step progress counts."""
    loop = asyncio.get_event_loop()
    supabase = get_supabase()
    offset = (page - 1) * limit

    def _query():
        q = (
            supabase.table("playbooks")
            .select(
                "*, analysis_results(criticality, threat_type, summary),"
                "playbook_steps(id, is_completed)"
            )
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if status:
            q = q.eq("status", status)
        return q.execute()

    try:
        result = await loop.run_in_executor(None, _query)
        # Compute progress for each playbook
        enriched = []
        for pb in result.data:
            steps = pb.get("playbook_steps", [])
            total = len(steps)
            completed = sum(1 for s in steps if s.get("is_completed"))
            pb["total_steps"] = total
            pb["completed_steps"] = completed
            enriched.append(pb)
        return {"data": enriched, "page": page, "limit": limit}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/trigger/{analysis_id}")
async def trigger_playbook(analysis_id: str):
    """Manually trigger playbook generation for any analysis result."""
    from app.playbook_generator import generate_playbook

    loop = asyncio.get_event_loop()
    supabase = get_supabase()

    def _fetch_analysis():
        return (
            supabase.table("analysis_results")
            .select(
                "*, log_entries(id, timestamp, source_file, raw_message, parsed_fields)"
            )
            .eq("id", analysis_id)
            .maybe_single()
            .execute()
        )

    def _check_existing():
        return (
            supabase.table("playbooks")
            .select("id")
            .eq("analysis_result_id", analysis_id)
            .execute()
        )

    try:
        result = await loop.run_in_executor(None, _fetch_analysis)
        if not result.data:
            raise HTTPException(status_code=404, detail="Analysis not found")

        analysis = dict(result.data)
        entry = analysis.pop("log_entries", None) or {}

        existing = await loop.run_in_executor(None, _check_existing)
        if existing.data:
            raise HTTPException(status_code=409, detail="Playbook already exists for this analysis")

        asyncio.create_task(generate_playbook(analysis_id, analysis, entry, manager))
        return {"status": "generating"}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{playbook_id}")
async def get_playbook(playbook_id: str):
    """Return a single playbook with all ordered steps."""
    loop = asyncio.get_event_loop()
    supabase = get_supabase()

    def _query():
        return (
            supabase.table("playbooks")
            .select(
                "*, "
                "analysis_results(criticality, threat_type, summary, ioc_data, recommended_actions,"
                "  log_entries(timestamp, source_file, raw_message)),"
                "playbook_steps(id, step_order, category, description, is_completed, completed_at, completed_by)"
            )
            .eq("id", playbook_id)
            .maybe_single()
            .execute()
        )

    try:
        result = await loop.run_in_executor(None, _query)
        if not result.data:
            raise HTTPException(status_code=404, detail="Playbook not found")

        pb = result.data
        # Sort steps by order
        if pb.get("playbook_steps"):
            pb["playbook_steps"] = sorted(pb["playbook_steps"], key=lambda s: s["step_order"])
        return pb
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/{playbook_id}/status")
async def update_playbook_status(playbook_id: str, body: StatusUpdate):
    """Update the lifecycle status of a playbook."""
    valid_statuses = {"open", "in_progress", "closed"}
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {valid_statuses}",
        )

    loop = asyncio.get_event_loop()
    supabase = get_supabase()

    def _update():
        return (
            supabase.table("playbooks")
            .update({"status": body.status})
            .eq("id", playbook_id)
            .execute()
        )

    try:
        result = await loop.run_in_executor(None, _update)
        if not result.data:
            raise HTTPException(status_code=404, detail="Playbook not found")

        await manager.broadcast({
            "type": "playbook_updated",
            "data": {"id": playbook_id, "status": body.status},
        })
        return result.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/{playbook_id}/steps/{step_id}")
async def update_step(playbook_id: str, step_id: str, body: StepUpdate):
    """Toggle a step's completion state."""
    loop = asyncio.get_event_loop()
    supabase = get_supabase()

    update_payload: dict = {"is_completed": body.is_completed}
    if body.is_completed:
        update_payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        if body.completed_by:
            update_payload["completed_by"] = body.completed_by
    else:
        update_payload["completed_at"] = None
        update_payload["completed_by"] = None

    def _update():
        return (
            supabase.table("playbook_steps")
            .update(update_payload)
            .eq("id", step_id)
            .eq("playbook_id", playbook_id)
            .execute()
        )

    try:
        result = await loop.run_in_executor(None, _update)
        if not result.data:
            raise HTTPException(status_code=404, detail="Step not found")

        await manager.broadcast({
            "type": "step_completed",
            "data": {
                "playbook_id": playbook_id,
                "step_id": step_id,
                "is_completed": body.is_completed,
                "completed_by": body.completed_by,
            },
        })
        return result.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
