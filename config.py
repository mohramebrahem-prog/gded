"""
config.py – إعدادات النظام
يقرأ جميع المتغيرات من ملف .env تلقائياً عبر python-dotenv.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# تحميل .env من جذر المشروع
_BASE_DIR = Path(__file__).parent
load_dotenv(_BASE_DIR / ".env")


# ─────────────────────────────────────────────────────────────────────────────
# قاعدة البيانات – SQLite محلي افتراضياً
# ─────────────────────────────────────────────────────────────────────────────
_raw_db_url = os.getenv("DATABASE_URL", "sqlite:///./system.db")

# تحويل sqlite:// → sqlite+aiosqlite:// للدعم async
if _raw_db_url.startswith("sqlite:///") and "aiosqlite" not in _raw_db_url:
    DATABASE_URL = _raw_db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
elif _raw_db_url.startswith("postgresql://"):
    DATABASE_URL = _raw_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    DATABASE_URL = _raw_db_url

# مسار ملف SQLite المحلي
SQLITE_FILE = str(_BASE_DIR / "system.db")


# ─────────────────────────────────────────────────────────────────────────────
# تيليجرام API  (my.telegram.org)
# ─────────────────────────────────────────────────────────────────────────────
API_ID   = int(os.getenv("TELEGRAM_API_ID",  os.getenv("API_ID",   "0")))
API_HASH = os.getenv("TELEGRAM_API_HASH", os.getenv("API_HASH", ""))


# ─────────────────────────────────────────────────────────────────────────────
# مفاتيح الذكاء الاصطناعي
# ─────────────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY   = os.getenv("GOOGLE_API_KEY",   "")   # Gemini  – القائد / المخطط
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")   # DeepSeek – المحلل
GROQ_API_KEY     = os.getenv("GROQ_API_KEY",     "")   # Groq     – المنفذ / المراقب
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY",   "")   # OpenAI   – احتياطي
ANTHROPIC_API_KEY= os.getenv("ANTHROPIC_API_KEY","")   # Anthropic – احتياطي

PREFERRED_LLM    = os.getenv("PREFERRED_LLM", "gemini/gemini-1.5-flash")


# ─────────────────────────────────────────────────────────────────────────────
# إعدادات الخادم
# ─────────────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
HOST       = os.getenv("HOST",  "0.0.0.0")
PORT       = int(os.getenv("PORT", "8000"))
DEBUG      = os.getenv("DEBUG", "false").lower() == "true"


# ─────────────────────────────────────────────────────────────────────────────
# إعدادات الحسابات والتخفي
# ─────────────────────────────────────────────────────────────────────────────
MAX_ACCOUNTS       = int(os.getenv("MAX_ACCOUNTS",       "50"))
MIN_DELAY_MINUTES  = int(os.getenv("MIN_DELAY_MINUTES",  "3"))
MAX_DELAY_MINUTES  = int(os.getenv("MAX_DELAY_MINUTES",  "15"))
DELETION_CHECK_SEC = int(os.getenv("DELETION_CHECK_SEC", "120"))


# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB – الذاكرة المتجهية للوكلاء
# ─────────────────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", str(_BASE_DIR / "chroma_db"))


# ─────────────────────────────────────────────────────────────────────────────
# البروكسي الافتراضي (اختياري – None = بدون بروكسي)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_PROXY: dict | None = None
# مثال: DEFAULT_PROXY = {"scheme": "socks5", "hostname": "127.0.0.1", "port": 1080}


# ─────────────────────────────────────────────────────────────────────────────
# بوتات الحماية المعروفة
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_PROTECTION_BOTS = {
    "جبل":    ["@Jabal_bot",  "@jabalbot"],
    "الماسة": ["@AlmasaBot",  "@almasa_bot"],
    "شيلد":   ["@GroupShield"],
}


# ─────────────────────────────────────────────────────────────────────────────
# فحص الإعداد عند التشغيل (تحذيرات فقط، لا يوقف التشغيل)
# ─────────────────────────────────────────────────────────────────────────────
def validate_config() -> list[str]:
    warnings = []
    if not API_ID or API_ID == 0:
        warnings.append("⚠️  TELEGRAM_API_ID غير محدد")
    if not API_HASH:
        warnings.append("⚠️  TELEGRAM_API_HASH غير محدد")
    if not any([GOOGLE_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY, OPENAI_API_KEY]):
        warnings.append("⚠️  لم يتم تحديد أي مفتاح AI – الوكلاء لن يعملوا")
    if SECRET_KEY == "change-me-in-production":
        warnings.append("⚠️  SECRET_KEY يجب تغييره في الإنتاج")
    return warnings
