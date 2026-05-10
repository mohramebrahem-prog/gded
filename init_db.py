"""
init_db.py — يحذف الجداول القديمة ويعيد إنشاءها بالأعمدة الصحيحة.
شغّله مرة واحدة فقط:
    python init_db.py
"""
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from config import DATABASE_URL

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def reset():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:

        logger.info("🗑️  حذف الجداول القديمة...")
        await conn.execute(text("DROP TABLE IF EXISTS messages  CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS campaigns CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS logs      CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS groups    CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS accounts  CASCADE"))

        logger.info("🔨 إنشاء الجداول الجديدة...")

        await conn.execute(text("""
            CREATE TABLE accounts (
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
            CREATE TABLE groups (
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
            CREATE TABLE campaigns (
                id         SERIAL PRIMARY KEY,
                name       VARCHAR(200) NOT NULL,
                text       TEXT NOT NULL,
                status     VARCHAR(20) DEFAULT 'draft',
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        await conn.execute(text("""
            CREATE TABLE messages (
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
            CREATE TABLE logs (
                id         SERIAL PRIMARY KEY,
                source     VARCHAR(50)  NOT NULL,
                action     VARCHAR(100) NOT NULL,
                detail     TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        logger.info("⚡ إنشاء الفهارس...")
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_account  ON messages(account_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_group    ON messages(group_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_status   ON messages(status)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_sent_at  ON messages(sent_at)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_groups_category   ON groups(category)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_logs_created_at   ON logs(created_at)"))

    await engine.dispose()
    logger.info("✅ تم إنشاء قاعدة البيانات بنجاح — شغّل المشروع الآن")


if __name__ == "__main__":
    asyncio.run(reset())
