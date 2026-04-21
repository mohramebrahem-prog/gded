"""
config.py – إعدادات النظام (Railway-ready)
يقرأ جميع المتغيرات من os.environ مباشرة — لا يعتمد على ملف .env
"""
import os
from pathlib import Path

_BASE_DIR = Path(__file__).parent

# ─── Database ─────────────────────────────────────────────────────────────────
_raw = os.getenv("DATABASE_URL", "sqlite:///./system.db")

if _raw.startswith("sqlite:///") and "aiosqlite" not in _raw:
    DATABASE_URL = _raw.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
elif _raw.startswith("postgresql://"):
    DATABASE_URL = _raw.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _raw.startswith("postgres://"):
    DATABASE_URL = _raw.replace("postgres://", "postgresql+asyncpg://", 1)
else:
    DATABASE_URL = _raw

# ─── Telegram ─────────────────────────────────────────────────────────────────
API_ID   = int(os.getenv("TELEGRAM_API_ID", os.getenv("API_ID", "0")))
API_HASH = os.getenv("TELEGRAM_API_HASH", os.getenv("API_HASH", ""))

# ─── AI Keys ──────────────────────────────────────────────────────────────────
GOOGLE_API_KEY    = os.getenv("GOOGLE_API_KEY",    "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY",  "")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY",      "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY",    "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PREFERRED_LLM     = os.getenv("PREFERRED_LLM",     "")

# ─── Server ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
HOST       = os.getenv("HOST", "0.0.0.0")
PORT       = int(os.getenv("PORT", "8000"))
DEBUG      = os.getenv("DEBUG", "false").lower() == "true"

# ─── Stealth Settings ─────────────────────────────────────────────────────────
MAX_ACCOUNTS       = int(os.getenv("MAX_ACCOUNTS",       "50"))
MIN_DELAY_MINUTES  = int(os.getenv("MIN_DELAY_MINUTES",  "3"))
MAX_DELAY_MINUTES  = int(os.getenv("MAX_DELAY_MINUTES",  "15"))
DELETION_CHECK_SEC = int(os.getenv("DELETION_CHECK_SEC", "120"))

# ─── ChromaDB ─────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", str(_BASE_DIR / "chroma_db"))

# ─── Default Proxy ────────────────────────────────────────────────────────────
DEFAULT_PROXY: dict | None = None

# ─── Known Protection Bots ────────────────────────────────────────────────────
KNOWN_PROTECTION_BOTS = {
    "جبل":    ["@Jabal_bot", "@jabalbot"],
    "الماسة": ["@AlmasaBot", "@almasa_bot"],
    "شيلد":   ["@GroupShield"],
}

def validate_config() -> list[str]:
    w = []
    if not API_ID or API_ID == 0:
        w.append("⚠️  TELEGRAM_API_ID غير محدد")
    if not API_HASH:
        w.append("⚠️  TELEGRAM_API_HASH غير محدد")
    if not any([GOOGLE_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY]):
        w.append("⚠️  لم يتم تحديد أي مفتاح AI")
    if SECRET_KEY == "change-me-in-production":
        w.append("⚠️  SECRET_KEY يجب تغييره")
    return w
