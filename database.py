"""database.py — اتصال PostgreSQL مع إعادة بناء الجداول تلقائياً"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

async def init_db():
    """يتحقق من الأعمدة ويضيف الناقصة — بدون حذف البيانات."""
    async with engine.begin() as conn:

        # إنشاء الجداول إن لم تكن موجودة
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS accounts (
                id         SERIAL PRIMARY KEY,
                phone      VARCHAR(30) UNIQUE NOT NULL,
                session    TEXT,
                is_active  BOOLEAN DEFAULT FALSE,
                is_online  BOOLEAN DEFAULT FALSE,
                note       VARCHAR(200),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS groups (
                id             SERIAL PRIMARY KEY,
                telegram_id    BIGINT UNIQUE NOT NULL,
                username       VARCHAR(100),
                title          VARCHAR(200) NOT NULL,
                category       VARCHAR(50),
                member_count   INTEGER DEFAULT 0,
                language       VARCHAR(10),
                is_joined      BOOLEAN DEFAULT FALSE,
                protection_bot VARCHAR(100),
                last_sent_at   TIMESTAMP,
                created_at     TIMESTAMP DEFAULT NOW()
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id         SERIAL PRIMARY KEY,
                name       VARCHAR(200) NOT NULL,
                text       TEXT NOT NULL,
                status     VARCHAR(20) DEFAULT 'draft',
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id              SERIAL PRIMARY KEY,
                account_id      INTEGER NOT NULL REFERENCES accounts(id)  ON DELETE CASCADE,
                group_id        INTEGER NOT NULL REFERENCES groups(id)    ON DELETE CASCADE,
                campaign_id     INTEGER          REFERENCES campaigns(id) ON DELETE SET NULL,
                telegram_msg_id BIGINT,
                content         TEXT NOT NULL,
                status          VARCHAR(20) DEFAULT 'sent',
                sent_at         TIMESTAMP DEFAULT NOW(),
                deleted_at      TIMESTAMP
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS logs (
                id         SERIAL PRIMARY KEY,
                source     VARCHAR(50)  NOT NULL,
                action     VARCHAR(100) NOT NULL,
                detail     TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        # إضافة الأعمدة الناقصة إن وجدت جداول قديمة
        missing_cols = [
            ("accounts", "session",    "TEXT"),
            ("accounts", "is_online",  "BOOLEAN DEFAULT FALSE"),
            ("accounts", "note",       "VARCHAR(200)"),
            ("groups",   "username",       "VARCHAR(100)"),
            ("groups",   "category",       "VARCHAR(50)"),
            ("groups",   "language",       "VARCHAR(10)"),
            ("groups",   "protection_bot", "VARCHAR(100)"),
            ("groups",   "last_sent_at",   "TIMESTAMP"),
            ("messages", "telegram_msg_id","BIGINT"),
            ("messages", "deleted_at",     "TIMESTAMP"),
            ("messages", "campaign_id",    "INTEGER"),
        ]
        for table, col, col_type in missing_cols:
            await conn.execute(text(f"""
                ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}
            """))

        # فهارس
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_messages_account  ON messages(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_group    ON messages(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_status   ON messages(status)",
            "CREATE INDEX IF NOT EXISTS idx_messages_sent_at  ON messages(sent_at)",
            "CREATE INDEX IF NOT EXISTS idx_groups_category   ON groups(category)",
            "CREATE INDEX IF NOT EXISTS idx_logs_created_at   ON logs(created_at)",
        ]:
            await conn.execute(text(idx_sql))

    logger.info("✅ قاعدة البيانات جاهزة")

async def get_db():
    async with SessionLocal() as session:
        yield session
