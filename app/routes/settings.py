"""
routes/settings.py — LLM model selection and provider configuration API.

Endpoints:
  GET  /api/settings/models          — list all available models
  GET  /api/settings/current-model   — get currently active model id
  POST /api/settings/current-model   — switch active model
  GET  /api/settings/test-model      — test current model, returns latency
  GET  /api/settings/api-keys        — return which provider keys are configured
"""

from __future__ import annotations

import time

import app.config as config
from app.llm_manager import AVAILABLE_MODELS, call_llm, get_model_info

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

_VALID_IDS = {m["id"] for m in AVAILABLE_MODELS}

TEST_SYSTEM_PROMPT = (
    "You are a helpful assistant. Respond ONLY with a valid JSON object."
)
TEST_USER_MESSAGE = (
    'Respond with exactly: {"status": "ok", "message": "LLM connection successful"}'
)


class ModelSelectBody(BaseModel):
    model_id: str


@router.get("/models")
async def list_models():
    """Return all available free models with metadata."""
    return {"models": AVAILABLE_MODELS}


@router.get("/current-model")
async def get_current_model():
    """Return the currently active model id and its metadata."""
    model_id = config.current_model_id
    info = get_model_info(model_id) or {}
    return {"model_id": model_id, "model_info": info}


@router.post("/current-model")
async def set_current_model(body: ModelSelectBody):
    """Switch the globally active model. Takes effect for all new analyses immediately."""
    if body.model_id not in _VALID_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown model_id '{body.model_id}'. "
                   f"Valid options: {sorted(_VALID_IDS)}",
        )
    config.current_model_id = body.model_id
    info = get_model_info(body.model_id) or {}
    print(f"[settings] Model switched to {body.model_id}")
    return {"model_id": body.model_id, "model_info": info, "status": "switched"}


@router.get("/test-model")
async def test_current_model():
    """
    Send a lightweight test prompt to the current model.
    Returns success/failure and round-trip latency in milliseconds.
    """
    model_id = config.current_model_id
    info = get_model_info(model_id) or {}

    start = time.perf_counter()
    try:
        response_text = await call_llm(model_id, TEST_SYSTEM_PROMPT, TEST_USER_MESSAGE)
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        return {
            "status":       "ok",
            "model_id":     model_id,
            "model_info":   info,
            "latency_ms":   elapsed_ms,
            "response":     response_text[:200],
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        return {
            "status":       "error",
            "model_id":     model_id,
            "model_info":   info,
            "latency_ms":   elapsed_ms,
            "error":        str(exc),
        }


@router.get("/test-model/{model_id:path}")
async def test_specific_model(model_id: str):
    """Test a specific model by id (used by the Settings page per-card test buttons)."""
    if model_id not in _VALID_IDS:
        raise HTTPException(status_code=422, detail=f"Unknown model_id '{model_id}'")

    info = get_model_info(model_id) or {}
    start = time.perf_counter()
    try:
        response_text = await call_llm(model_id, TEST_SYSTEM_PROMPT, TEST_USER_MESSAGE)
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        return {
            "status":     "ok",
            "model_id":   model_id,
            "model_info": info,
            "latency_ms": elapsed_ms,
            "response":   response_text[:200],
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        return {
            "status":     "error",
            "model_id":   model_id,
            "model_info": info,
            "latency_ms": elapsed_ms,
            "error":      str(exc),
        }


@router.get("/api-keys")
async def get_api_key_status():
    """Return which provider API keys are configured (never exposes the actual keys)."""
    return {
        "groq":   bool(config.GROQ_API_KEY),
        "google": bool(config.GOOGLE_API_KEY),
    }
