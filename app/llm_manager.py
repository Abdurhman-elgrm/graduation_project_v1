"""
llm_manager.py — Unified async LLM router for free models.

Supports:
  • Groq   — Llama 3.3 70B, Mixtral 8x7B, Gemma 2 9B, DeepSeek R1
  • Google — Gemini 1.5 Flash, Gemini 2.0 Flash

Usage:
    from app.llm_manager import call_llm
    text = await call_llm("groq-llama-3.3-70b", system_prompt, user_message)
"""

from __future__ import annotations

import json
import re
import sys

AVAILABLE_MODELS: list[dict] = [
    {
        "id": "groq-llama-3.3-70b",
        "name": "Llama 3.3 70B",
        "provider": "Groq",
        "speed": "Fast",
        "description": "Best overall quality, highly capable for security analysis",
    },
    {
        "id": "groq-mixtral-8x7b",
        "name": "Mixtral 8x7B",
        "provider": "Groq",
        "speed": "Fast",
        "description": "Great for structured JSON analysis",
    },
    {
        "id": "groq-gemma2-9b",
        "name": "Gemma 2 9B",
        "provider": "Groq",
        "speed": "Very Fast",
        "description": "Lightweight and very quick responses",
    },
    {
        "id": "groq-deepseek-r1",
        "name": "DeepSeek R1",
        "provider": "Groq",
        "speed": "Medium",
        "description": "Strong reasoning, great for complex threats",
    },
    {
        "id": "google-gemini-flash",
        "name": "Gemini 1.5 Flash",
        "provider": "Google",
        "speed": "Very Fast",
        "description": "Google's fastest free model",
    },
    {
        "id": "google-gemini-flash-2",
        "name": "Gemini 2.0 Flash",
        "provider": "Google",
        "speed": "Very Fast",
        "description": "Google's latest and most capable free model",
    },
]

# Internal name mapping — what the provider APIs actually expect
_GROQ_MODEL_NAMES: dict[str, str] = {
    "groq-llama-3.3-70b": "llama-3.3-70b-versatile",
    "groq-mixtral-8x7b":  "mixtral-8x7b-32768",
    "groq-gemma2-9b":     "gemma2-9b-it",
    "groq-deepseek-r1":   "deepseek-r1-distill-llama-70b",
}

_GOOGLE_MODEL_NAMES: dict[str, str] = {
    "google-gemini-flash":   "gemini-1.5-flash",
    "google-gemini-flash-2": "gemini-2.0-flash",
}

# Quick lookup by id
_MODEL_BY_ID: dict[str, dict] = {m["id"]: m for m in AVAILABLE_MODELS}


def get_model_info(model_id: str) -> dict | None:
    return _MODEL_BY_ID.get(model_id)


def clean_json_response(raw: str) -> str:
    """Strip markdown fences, <think> blocks, and leading/trailing whitespace."""
    # Remove DeepSeek reasoning blocks
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = raw.strip()
    # Strip ```json ... ``` or ``` ... ```
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
    return raw


# ── Groq ─────────────────────────────────────────────────────────────────────

async def _call_groq(model_id: str, system_prompt: str, user_message: str) -> str:
    from app.config import GROQ_API_KEY
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set in .env — required for Groq models."
        )

    try:
        from groq import AsyncGroq
    except ImportError:
        raise RuntimeError("groq package not installed. Run: pip install groq")

    groq_model = _GROQ_MODEL_NAMES[model_id]
    client = AsyncGroq(api_key=GROQ_API_KEY)

    response = await client.chat.completions.create(
        model=groq_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return response.choices[0].message.content


# ── Google Gemini ─────────────────────────────────────────────────────────────

async def _call_google(model_id: str, system_prompt: str, user_message: str) -> str:
    from app.config import GOOGLE_API_KEY
    if not GOOGLE_API_KEY:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set in .env — required for Google Gemini models."
        )

    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError(
            "google-generativeai package not installed. Run: pip install google-generativeai"
        )

    google_model_name = _GOOGLE_MODEL_NAMES[model_id]
    genai.configure(api_key=GOOGLE_API_KEY)

    model = genai.GenerativeModel(
        model_name=google_model_name,
        system_instruction=system_prompt,
    )
    response = await model.generate_content_async(user_message)
    return response.text


# ── Public API ────────────────────────────────────────────────────────────────

async def call_llm(model_id: str, system_prompt: str, user_message: str) -> str:
    """
    Route to the correct provider and return the raw text response.
    Raises RuntimeError if the model is unknown or the API key is missing.
    """
    if model_id not in _MODEL_BY_ID:
        raise RuntimeError(
            f"Unknown model_id '{model_id}'. "
            f"Valid options: {list(_MODEL_BY_ID.keys())}"
        )

    if model_id in _GROQ_MODEL_NAMES:
        return await _call_groq(model_id, system_prompt, user_message)

    if model_id in _GOOGLE_MODEL_NAMES:
        return await _call_google(model_id, system_prompt, user_message)

    raise RuntimeError(f"No provider handler for model_id '{model_id}'")
