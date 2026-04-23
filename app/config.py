import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# ── Legacy Anthropic key (kept for backward-compat, not used by default) ──────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ── Free-tier LLM provider keys ───────────────────────────────────────────────
GROQ_API_KEY: str   = os.getenv("GROQ_API_KEY", "")
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL: str         = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY: str    = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

# ── App config ────────────────────────────────────────────────────────────────
DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"

# ── LLM selection ─────────────────────────────────────────────────────────────
# This is the globally selected model for all new analyses.
# Can be changed at runtime via POST /api/settings/current-model.
current_model_id: str = os.getenv("DEFAULT_MODEL", "groq-llama-3.3-70b")

MAX_CONCURRENT_ANALYSES: int = 5
MAX_RETRIES: int = 3

# ── Supabase client singleton ─────────────────────────────────────────────────
_supabase_client: Client | None = None


def get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env"
            )
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase_client
