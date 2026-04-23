"""
playbook_generator.py — Incident response playbook auto-generation.

Triggered automatically when AI analysis returns HIGH or CRITICAL criticality.
Uses the unified llm_manager so it honours whatever model is currently selected.
"""

from __future__ import annotations

import asyncio
import json
import random
import string
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import app.config as config
from app.config import MAX_RETRIES, get_supabase
from app.llm_manager import call_llm, clean_json_response

if TYPE_CHECKING:
    from app.websocket.manager import ConnectionManager

PLAYBOOK_SYSTEM_PROMPT = """You are a senior SOC analyst writing an incident response playbook.
Based on this threat analysis, generate a detailed SOC playbook.
Respond ONLY with a valid JSON object in this exact structure:
{
  "title": "Incident title",
  "steps": [
    {
      "order": 1,
      "category": "Investigation|Containment|Eradication|Recovery|Evidence Collection|Escalation",
      "description": "Detailed step description"
    }
  ]
}
Include at least 8-12 steps covering: initial investigation, evidence collection, containment, eradication, recovery, and escalation path."""


def _generate_incident_id() -> str:
    now = datetime.now(timezone.utc)
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"INC-{now.strftime('%Y%m%d')}-{suffix}"


async def _call_with_retry(model_id: str, analysis: dict, entry: dict) -> dict:
    """Call the LLM for playbook generation with retry logic."""
    user_content = (
        f"Threat Analysis:\n"
        f"  Criticality: {analysis.get('criticality')}\n"
        f"  Threat Type: {analysis.get('threat_type')}\n"
        f"  Summary: {analysis.get('summary')}\n"
        f"  IOC Data: {json.dumps(analysis.get('ioc_data', {}))}\n"
        f"  Recommended Actions: {json.dumps(analysis.get('recommended_actions', []))}\n\n"
        f"Original Log:\n"
        f"  Source: {entry.get('source_file')}\n"
        f"  Timestamp: {entry.get('timestamp')}\n"
        f"  Message: {entry.get('raw_message', '')[:500]}"
    )

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            raw_text = await call_llm(model_id, PLAYBOOK_SYSTEM_PROMPT, user_content)
            clean = clean_json_response(raw_text)
            return json.loads(clean)

        except json.JSONDecodeError as exc:
            last_exc = exc
            print(
                f"[playbook_gen] JSON parse error (attempt {attempt+1}/{MAX_RETRIES}): {exc}",
                file=sys.stderr,
            )
            break

        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            print(
                f"[playbook_gen] LLM error (attempt {attempt+1}/{MAX_RETRIES}): {exc}"
                f" — retrying in {wait}s",
                file=sys.stderr,
            )
            await asyncio.sleep(wait)

    raise RuntimeError(
        f"Playbook LLM call failed after {MAX_RETRIES} attempts: {last_exc}"
    )


async def generate_playbook(
    analysis_id: str,
    analysis: dict,
    entry: dict,
    ws_manager: "ConnectionManager",
) -> None:
    """
    Generate an incident response playbook for a HIGH/CRITICAL finding.
    Uses the model that was active when the analysis was performed
    (stored in analysis['llm_model_used']), falling back to current_model_id.
    """
    # Use the same model that performed the analysis for consistency
    model_id = analysis.get("llm_model_used") or config.current_model_id

    try:
        playbook_data = await _call_with_retry(model_id, analysis, entry)

        incident_id = _generate_incident_id()
        loop = asyncio.get_event_loop()
        supabase = get_supabase()

        playbook_row = {
            "analysis_result_id": analysis_id,
            "title":              playbook_data.get("title", "Untitled Incident"),
            "incident_id":        incident_id,
            "status":             "open",
        }
        pb_result = await loop.run_in_executor(
            None,
            lambda: supabase.table("playbooks").insert(playbook_row).execute(),
        )

        if not pb_result.data:
            print(
                f"[playbook_gen] Playbook insert returned no data for analysis {analysis_id}",
                file=sys.stderr,
            )
            return

        playbook_id = pb_result.data[0]["id"]

        steps = playbook_data.get("steps", [])
        if steps:
            step_rows = [
                {
                    "playbook_id": playbook_id,
                    "step_order":  s.get("order", idx + 1),
                    "category":    s.get("category", "Investigation"),
                    "description": s.get("description", ""),
                    "is_completed": False,
                }
                for idx, s in enumerate(steps)
            ]
            await loop.run_in_executor(
                None,
                lambda: supabase.table("playbook_steps").insert(step_rows).execute(),
            )

        await ws_manager.broadcast({
            "type": "playbook_created",
            "data": {
                "id":          playbook_id,
                "incident_id": incident_id,
                "title":       playbook_row["title"],
                "criticality": analysis.get("criticality"),
                "threat_type": analysis.get("threat_type"),
                "step_count":  len(steps),
                "status":      "open",
                "model_used":  model_id,
            },
        })

        print(f"[playbook_gen] Created playbook {incident_id} ({model_id}) with {len(steps)} steps.")

    except Exception as exc:
        print(
            f"[playbook_gen] Failed to generate playbook for analysis {analysis_id}: {exc}",
            file=sys.stderr,
        )
