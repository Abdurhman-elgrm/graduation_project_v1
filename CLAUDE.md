# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Optional: real Windows Event Log support (instead of DEMO MODE)
pip install pywin32

# Start the server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Access at `http://localhost:8000`. Health check: `GET /health`.

There are no tests, no Makefile, no Docker setup, and no CI pipeline.

## Environment Configuration

Copy `.env.example` to `.env` and fill in:
- `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` — required for all DB operations
- `GROQ_API_KEY` or `GOOGLE_API_KEY` — at least one required for LLM analysis
- `DEMO_MODE=true` — generates synthetic log events; set to `false` for real OS logs

`ANTHROPIC_API_KEY` is present in the config but not currently wired to any LLM call.

## Architecture

**Runtime pipeline** (fully async, runs on startup):

```
log_tailer.py  →  Supabase log_entries  →  ai_analyzer.py  →  analysis_results
    ↓                                            ↓
  WS broadcast                        if HIGH/CRITICAL → playbook_generator.py
                                                            → playbooks + playbook_steps
                                                            → WS broadcast
```

**Key components:**

| File | Role |
|------|------|
| `main.py` | FastAPI app, lifespan events, HTML page routes, `/ws/live` WebSocket |
| `app/config.py` | Env vars, Supabase client singleton (`get_supabase()`), runtime `current_model_id` global |
| `app/log_tailer.py` | Async log ingestion: Linux syslog tailing, Windows Event Log (pywin32), or DEMO synthetic events |
| `app/ai_analyzer.py` | Calls LLM per log entry; semaphore-limited to 5 concurrent; 3 retries with backoff |
| `app/llm_manager.py` | Routes calls to Groq or Google Gemini based on `current_model_id`; cleans JSON from both |
| `app/playbook_generator.py` | Generates 8–12-step incident response playbooks for HIGH/CRITICAL findings |
| `app/websocket/manager.py` | `ConnectionManager` broadcasts events to all live WebSocket clients; silently drops dead connections |
| `app/routes/` | Four routers: `logs`, `analysis`, `playbooks`, `settings` |
| `templates/` | Vanilla JS + Jinja2 HTML; WebSocket listener drives real-time UI updates |

**Database (Supabase/PostgreSQL):** Four tables — `log_entries`, `analysis_results`, `playbooks`, `playbook_steps` — with cascade deletes and RLS enabled. Schema in `supabase/migrations/001_init.sql`.

## Key Design Decisions

- **All I/O is async via `loop.run_in_executor()`** — Supabase SDK calls and LLM calls are synchronous libraries wrapped in thread pool execution.
- **`current_model_id` is a mutable global** in `config.py`, switched at runtime via `POST /api/settings/current-model` without restart.
- **LLM responses require JSON** — both providers are prompted for structured output; `clean_json_response()` in `llm_manager.py` strips markdown fences and `<think>` blocks before parsing.
- **DEMO MODE** injects realistic synthetic events (SSH failures, port scans, privilege escalation attempts) every 2–3 seconds — useful for UI development without real log sources.
- **Playbook incident IDs** follow the format `INC-YYYYMMDD-XXXX` (e.g., `INC-20260423-A7KM`).

## Available LLM Models

Defined in `llm_manager.py → AVAILABLE_MODELS`. Current providers:
- **Groq**: `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`, `gemma2-9b-it`, `deepseek-r1-distill-llama-70b`
- **Google Gemini**: `gemini-1.5-flash`, `gemini-2.0-flash-exp`

To add a model, extend `AVAILABLE_MODELS` and add routing logic in `call_llm()`.

## WebSocket Event Types

Events broadcast over `/ws/live`:
- `log_ingested` — new log entry stored
- `analysis_ready` — AI analysis complete
- `playbook_created` — new playbook generated
- `step_completed` — playbook step marked done
- `playbook_updated` — playbook status changed