"""config.py — يقرأ جميع المتغيرات من os.environ"""
import os

# Telegram
API_ID   = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Database
_db_raw = os.getenv("DATABASE_URL", "")
if _db_raw.startswith("postgres://"):
    _db_raw = _db_raw.replace("postgres://", "postgresql+asyncpg://", 1)
elif _db_raw.startswith("postgresql://"):
    _db_raw = _db_raw.replace("postgresql://", "postgresql+asyncpg://", 1)
DATABASE_URL = _db_raw or "postgresql+asyncpg://localhost/tgbot"

# AI Keys
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GOOGLE_API_KEY    = os.getenv("GOOGLE_API_KEY", "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PREFERRED_LLM     = os.getenv("PREFERRED_LLM", "groq/llama-3.3-70b-versatile")

# Server
PORT       = int(os.getenv("PORT", "8000"))
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

def get_active_llm() -> tuple[str, str]:
    """يرجع (model_string, api_key) للنموذج المتاح."""
    preferred = os.getenv("PREFERRED_LLM", PREFERRED_LLM)
    candidates = [
        (preferred, _key_for(preferred)),
        ("groq/llama-3.3-70b-versatile", os.getenv("GROQ_API_KEY", GROQ_API_KEY)),
        ("gemini/gemini-2.0-flash",       os.getenv("GOOGLE_API_KEY", GOOGLE_API_KEY)),
        ("deepseek/deepseek-chat",         os.getenv("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)),
        ("gpt-4o-mini",                    os.getenv("OPENAI_API_KEY", OPENAI_API_KEY)),
        ("claude-3-haiku-20240307",        os.getenv("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)),
    ]
    for model, key in candidates:
        if key:
            return model, key
    return "", ""

def _key_for(model: str) -> str:
    m = model.lower()
    if "groq" in m or "llama" in m or "mixtral" in m:
        return os.getenv("GROQ_API_KEY", GROQ_API_KEY)
    if "gemini" in m or "google" in m:
        return os.getenv("GOOGLE_API_KEY", GOOGLE_API_KEY)
    if "deepseek" in m:
        return os.getenv("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)
    if "gpt" in m or "openai" in m:
        return os.getenv("OPENAI_API_KEY", OPENAI_API_KEY)
    if "claude" in m or "anthropic" in m:
        return os.getenv("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
    return ""
