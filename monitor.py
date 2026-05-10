"""
monitor.py — يراقب المجموعات ويكتشف الرسائل المحذوفة تلقائياً.
يعمل في الخلفية كـ asyncio task منفصل.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from pyrogram import Client
from pyrogram.handlers import DeletedMessagesHandler, MessageHandler
from pyrogram import filters
from sqlalchemy import select, update

import models
from database import SessionLocal

logger = logging.getLogger(__name__)

# account_id → set of handler references (لإزالتها عند الإيقاف)
_handlers: dict[int, list] = {}
_running = False


async def _on_deleted(account_id: int, client: Client, messages):
    """يُستدعى عند حذف رسالة في أي مجموعة يراقبها الحساب."""
    deleted_ids = [m.id for m in messages]
    if not deleted_ids:
        return

    async with SessionLocal() as db:
        for tg_msg_id in deleted_ids:
            result = await db.execute(
                select(models.Message).where(
                    models.Message.telegram_msg_id == tg_msg_id,
                    models.Message.account_id == account_id,
                    models.Message.status == "sent",
                )
            )
            msg = result.scalar_one_or_none()
            if msg:
                msg.status = "deleted"
                msg.deleted_at = datetime.utcnow()
                log = models.Log(
                    source="monitor",
                    action="message_deleted",
                    detail=f"رسالة {tg_msg_id} حُذفت من مجموعة {msg.group_id}",
                )
                db.add(log)
        await db.commit()

    logger.info(f"🗑️ حساب {account_id}: {len(deleted_ids)} رسالة محذوفة")

    # إشعار WebSocket
    try:
        from api import broadcast
        await broadcast({
            "event": "messages_deleted",
            "account_id": account_id,
            "count": len(deleted_ids),
        })
    except Exception:
        pass


def attach_monitor(account_id: int, client: Client):
    """يربط مستمع الحذف بعميل Pyrogram."""
    if account_id in _handlers:
        return  # مرفق مسبقاً

    async def handler(client, messages):
        await _on_deleted(account_id, client, messages)

    h = DeletedMessagesHandler(handler)
    client.add_handler(h)
    _handlers[account_id] = [h]
    logger.info(f"👁️ مراقبة الحذف مرفقة للحساب {account_id}")


def detach_monitor(account_id: int, client: Client):
    """يفصل مستمع الحذف."""
    handlers = _handlers.pop(account_id, [])
    for h in handlers:
        try:
            client.remove_handler(h)
        except Exception:
            pass


async def attach_all_active():
    """يربط المراقبة بكل الحسابات النشطة عند بدء التشغيل."""
    import userbot
    await asyncio.sleep(5)  # انتظر حتى يتصل كل الحسابات
    for acc_id in userbot.active_accounts():
        client = await userbot.get_client(acc_id)
        if client:
            attach_monitor(acc_id, client)
    logger.info(f"✅ مراقبة الحذف نشطة على {len(_handlers)} حساب")


async def periodic_sync(interval_minutes: int = 30):
    """
    كل N دقيقة: يتحقق من الرسائل القديمة المُرسَلة ويحاول 
    التحقق إن كانت لا تزال موجودة.
    (احتياطي في حال فات حدث الحذف)
    """
    global _running
    _running = True
    while _running:
        await asyncio.sleep(interval_minutes * 60)
        try:
            await _sync_check()
        except Exception as e:
            logger.error(f"خطأ في periodic_sync: {e}")


async def _sync_check():
    """يتحقق من رسائل أُرسلت منذ أكثر من ساعة ولا تزال 'sent'."""
    import userbot
    cutoff = datetime.utcnow() - timedelta(hours=1)

    async with SessionLocal() as db:
        result = await db.execute(
            select(models.Message).where(
                models.Message.status == "sent",
                models.Message.sent_at < cutoff,
                models.Message.telegram_msg_id != None,
            ).limit(200)
        )
        msgs = result.scalars().all()

    if not msgs:
        return

    logger.info(f"🔍 فحص {len(msgs)} رسالة قديمة...")

    for msg in msgs:
        client = await userbot.get_client(msg.account_id)
        if not client:
            continue

        # جلب معرف المجموعة من DB
        async with SessionLocal() as db:
            grp_r = await db.execute(
                select(models.Group).where(models.Group.id == msg.group_id)
            )
            grp = grp_r.scalar_one_or_none()
            if not grp:
                continue

            try:
                tg_msgs = await client.get_messages(grp.telegram_id, msg.telegram_msg_id)
                # إذا كانت فارغة أو محذوفة
                if not tg_msgs or (hasattr(tg_msgs, 'empty') and tg_msgs.empty):
                    msg_result = await db.execute(
                        select(models.Message).where(models.Message.id == msg.id)
                    )
                    db_msg = msg_result.scalar_one_or_none()
                    if db_msg and db_msg.status == "sent":
                        db_msg.status = "deleted"
                        db_msg.deleted_at = datetime.utcnow()
                        await db.commit()
            except Exception:
                pass  # المجموعة قد لا تسمح بجلب الرسائل

        await asyncio.sleep(0.3)  # تأخير خفيف بين الطلبات


def stop():
    global _running
    _running = False
