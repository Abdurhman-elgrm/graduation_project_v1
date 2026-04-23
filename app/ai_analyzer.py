"""
ai_analyzer.py — Async AI analysis engine.

Uses the unified llm_manager to support any configured free model.
For each log entry:
  1. Calls the currently selected LLM asynchronously
  2. Parses the JSON response
  3. Inserts into analysis_results in Supabase
  4. Auto-triggers playbook generation for HIGH / CRITICAL findings
  5. Broadcasts analysis_ready event to WebSocket clients
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING

import app.config as config
from app.config import MAX_CONCURRENT_ANALYSES, MAX_RETRIES, get_supabase
from app.llm_manager import call_llm, clean_json_response

if TYPE_CHECKING:
    from app.websocket.manager import ConnectionManager

# Semaphore caps concurrent LLM API calls
_sem = asyncio.Semaphore(MAX_CONCURRENT_ANALYSES)

SYSTEM_PROMPT = """You are an expert SOC analyst. Analyze the following system log entry and respond ONLY with a valid JSON object, no markdown, no explanation outside the JSON.

Return this exact structure:
{
  "criticality": "CRITICAL|HIGH|MEDIUM|LOW|INFO",
  "threat_type": "string (e.g. Brute Force, Privilege Escalation, Data Exfiltration, Malware, Reconnaissance, Normal Activity)",
  "summary": "2-3 sentence threat assessment",
  "ioc_data": {
    "ip_addresses": [],
    "usernames": [],
    "file_paths": [],
    "processes": []
  },
  "recommended_actions": [
    "action 1",
    "action 2"
  ]
}"""


async def _call_with_retry(model_id: str, user_message: str) -> dict:
    """Call the LLM with exponential backoff retry. Returns parsed JSON dict."""
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            raw_text = await call_llm(model_id, SYSTEM_PROMPT, user_message)
            clean = clean_json_response(raw_text)
            return json.loads(clean)

        except json.JSONDecodeError as exc:
            last_exc = exc
            print(
                f"[ai_analyzer] JSON parse error (attempt {attempt+1}/{MAX_RETRIES}): {exc}",
                file=sys.stderr,
            )
            # JSON errors won't be fixed by retry — break immediately
            break

        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            print(
                f"[ai_analyzer] LLM error (attempt {attempt+1}/{MAX_RETRIES}): {exc}"
                f" — retrying in {wait}s",
                file=sys.stderr,
            )
            await asyncio.sleep(wait)

    raise RuntimeError(
        f"LLM analysis failed after {MAX_RETRIES} attempts: {last_exc}"
    )


async def analyze_log_entry(
    log_id: str,
    entry: dict,
    ws_manager: "ConnectionManager",
    generate_playbook_fn,
) -> None:
    """
    Main analysis coroutine. Called as a fire-and-forget asyncio task.
    Reads current_model_id from config at call-time so model switches take
    effect immediately without restarting the server.
    """
    async with _sem:
        # Snapshot the model at the moment this analysis starts
        model_id = config.current_model_id

        try:
            user_message = (
                f"Log source: {entry.get('source_file', 'unknown')}\n"
                f"Timestamp: {entry.get('timestamp', 'unknown')}\n"
                f"Raw message: {entry.get('raw_message', '')}\n"
                f"Parsed fields: {json.dumps(entry.get('parsed_fields', {}))}"
            )

            analysis = await _call_with_retry(model_id, user_message)

            analysis_row = {
                "log_entry_id":       log_id,
                "criticality":        analysis.get("criticality", "INFO"),
                "threat_type":        analysis.get("threat_type", "Unknown"),
                "summary":            analysis.get("summary", ""),
                "ioc_data":           analysis.get("ioc_data", {}),
                "recommended_actions": analysis.get("recommended_actions", []),
                "llm_model_used":     model_id,
            }

            loop = asyncio.get_event_loop()
            supabase = get_supabase()
            result = await loop.run_in_executor(
                None,
                lambda: supabase.table("analysis_results").insert(analysis_row).execute(),
            )

            if not result.data:
                print(
                    f"[ai_analyzer] No data returned after insert for log {log_id}",
                    file=sys.stderr,
                )
                return

            analysis_id = result.data[0]["id"]
            analysis_row["id"] = analysis_id
            analysis_row["log_id"] = log_id

            await ws_manager.broadcast({
                "type": "analysis_ready",
                "data": {
                    **analysis_row,
                    "ioc_data": analysis.get("ioc_data", {}),
                    "recommended_actions": analysis.get("recommended_actions", []),
                },
            })

            if analysis_row["criticality"] in ("HIGH", "CRITICAL"):
                asyncio.create_task(
                    generate_playbook_fn(analysis_id, analysis_row, entry, ws_manager)
                )

        except Exception as exc:
            print(
                f"[ai_analyzer] Failed to analyse log {log_id}: {exc}",
                file=sys.stderr,
            )
