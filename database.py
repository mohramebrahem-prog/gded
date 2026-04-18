"""
database.py – إدارة اتصال قاعدة البيانات (async SQLAlchemy)
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from config import DATABASE_URL
from models import Base
import logging

logger = logging.getLogger(__name__)

# ─── محرك قاعدة البيانات ──────────────────────────────────────────────────────
_connect_args = {}
_poolclass    = None

if "sqlite" in DATABASE_URL:
    _connect_args = {"check_same_thread": False}
    _poolclass    = StaticPool

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    **({"poolclass": _poolclass} if _poolclass else {}),
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ─── توفير جلسة (dependency injection لـ FastAPI) ────────────────────────────
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ─── تهيئة الجداول عند التشغيل الأول ────────────────────────────────────────
async def init_db():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ قاعدة البيانات جاهزة")
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        raise


# ─── مساعد: تنفيذ بدون FastAPI (للوكلاء والمهام الخلفية) ─────────────────────
async def get_session() -> AsyncSession:
    """ترجع جلسة غير مرتبطة بـ DI – يجب إغلاقها يدوياً."""
    return AsyncSessionLocal()
