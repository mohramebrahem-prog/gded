"""database.py — اتصال PostgreSQL عبر SQLAlchemy async"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

async def init_db():
    """ينشئ الجداول إذا لم تكن موجودة."""
    from models import Account, Group, Message, Campaign, Log
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ قاعدة البيانات جاهزة")

async def get_db():
    async with SessionLocal() as session:
        yield session
