"""database.py — اتصال PostgreSQL مع migration تلقائي"""
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

async def _col_exists(conn, table: str, col: str) -> bool:
    r = await conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": table, "c": col})
    return r.scalar() is not None

async def _table_exists(conn, table: str) -> bool:
    r = await conn.execute(text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = :t
    """), {"t": table})
    return r.scalar() is not None

async def init_db():
    async with engine.begin() as conn:

        # ── فحص جدول accounts — إذا كان قديماً احذفه ──────────────
        if await _table_exists(conn, "accounts"):
            has_session   = await _col_exists(conn, "accounts", "session")
            has_is_active = await _col_exists(conn, "accounts", "is_active")
            if not has_session or not has_is_active:
                logger.warning("⚠️ جدول accounts قديم — سيُحذف ويُعاد بناؤه")
                await conn.execute(text("DROP TABLE IF EXISTS messages  CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS campaigns CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS accounts  CASCADE"))

        # ── إنشاء الجداول ──────────────────────────────────────────
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

        # ── أعمدة ناقصة للجداول الموجودة ──────────────────────────
        patches = [
            ("groups",   "username",        "VARCHAR(100)"),
            ("groups",   "category",        "VARCHAR(50)"),
            ("groups",   "language",        "VARCHAR(10)"),
            ("groups",   "protection_bot",  "VARCHAR(100)"),
            ("groups",   "last_sent_at",    "TIMESTAMP"),
            ("messages", "telegram_msg_id", "BIGINT"),
            ("messages", "deleted_at",      "TIMESTAMP"),
            ("messages", "campaign_id",     "INTEGER"),
        ]
        for table, col, col_type in patches:
            if await _table_exists(conn, table) and not await _col_exists(conn, table, col):
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                logger.info(f"✅ أضفنا عمود {col} لجدول {table}")

        # ── فهارس ──────────────────────────────────────────────────
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_messages_account ON messages(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_group   ON messages(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_status  ON messages(status)",
            "CREATE INDEX IF NOT EXISTS idx_messages_sent_at ON messages(sent_at)",
            "CREATE INDEX IF NOT EXISTS idx_groups_category  ON groups(category)",
            "CREATE INDEX IF NOT EXISTS idx_logs_created_at  ON logs(created_at)",
        ]:
            await conn.execute(text(sql))

    logger.info("✅ قاعدة البيانات جاهزة")

async def get_db():
    async with SessionLocal() as session:
        yield session
