"""
userbot_manager.py – إدارة حسابات تيليجرام عبر Pyrogram
- تخزين الجلسات كـ StringSession في قاعدة البيانات
- استراتيجيات التخفي: تأخير عشوائي، محاكاة القراءة
- اكتشاف حذف الرسائل خلال نافذة 10 ثوانٍ
"""

import asyncio
import random
import logging
from datetime import datetime
from typing import Optional

from pyrogram import Client
from pyrogram.errors import (
    FloodWait, SessionPasswordNeeded, PhoneCodeInvalid,
    AuthKeyUnregistered, UserDeactivated
)
from pyrogram.types import Message as PyroMessage

from config import (
    API_ID, API_HASH, DEFAULT_PROXY,
    MIN_DELAY_MINUTES, MAX_DELAY_MINUTES, DELETION_CHECK_SEC
)
from database import get_session
from models import Account, Message, Group

logger = logging.getLogger(__name__)

# تخزين العملاء في الذاكرة: {account_id: Client}
_clients: dict[int, Client] = {}

# تخزين مؤقت للأكواد: {phone: {"client": Client, "phone_code_hash": str}}
_pending_auth: dict[str, dict] = {}


# ─── مساعدات ─────────────────────────────────────────────────────────────────
async def human_delay():
    """تأخير عشوائي يحاكي السلوك البشري (3-15 دقيقة افتراضياً)."""
    seconds = random.randint(MIN_DELAY_MINUTES * 60, MAX_DELAY_MINUTES * 60)
    logger.debug(f"⏳ تأخير بشري: {seconds // 60}د {seconds % 60}ث")
    await asyncio.sleep(seconds)


async def short_delay():
    """تأخير قصير بين الإجراءات (2-8 ثوانٍ)."""
    await asyncio.sleep(random.uniform(2, 8))


def _build_client(phone: str, session_string: Optional[str] = None,
                  proxy: Optional[dict] = None) -> Client:
    kwargs = dict(
        name=phone,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )
    if session_string:
        kwargs["session_string"] = session_string
    if proxy or DEFAULT_PROXY:
        kwargs["proxy"] = proxy or DEFAULT_PROXY
    return Client(**kwargs)


# ─── إضافة حساب جديد ─────────────────────────────────────────────────────────
async def add_account(phone: str, proxy: Optional[dict] = None) -> dict:
    """
    يبدأ عملية تسجيل الدخول ويرسل كود التحقق.
    يعيد {"status": "code_sent"} أو {"status": "error", "detail": "..."}
    """
    try:
        client = _build_client(phone, proxy=proxy)
        await client.connect()
        sent = await client.send_code(phone)
        _pending_auth[phone] = {
            "client": client,
            "phone_code_hash": sent.phone_code_hash,
            "proxy": proxy,
        }
        return {"status": "code_sent", "phone": phone}
    except FloodWait as e:
        return {"status": "error", "detail": f"FloodWait {e.value}s"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ─── التحقق من الكود ──────────────────────────────────────────────────────────
async def verify_code(phone: str, code: str,
                      password: Optional[str] = None) -> dict:
    """
    يكمل عملية التسجيل ويخزن الجلسة في قاعدة البيانات.
    يعيد {"status": "ok", "account_id": N} أو {"status": "error", ...}
    """
    pending = _pending_auth.get(phone)
    if not pending:
        return {"status": "error", "detail": "لم يتم العثور على جلسة مفتوحة لهذا الرقم"}

    client: Client = pending["client"]
    try:
        await client.sign_in(phone, pending["phone_code_hash"], code)
    except SessionPasswordNeeded:
        if not password:
            return {"status": "2fa_required"}
        await client.check_password(password)
    except PhoneCodeInvalid:
        return {"status": "error", "detail": "الكود غير صحيح"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

    session_string = await client.export_session_string()

    # تخزين في قاعدة البيانات
    db = await get_session()
    try:
        from sqlalchemy import select
        result = await db.execute(select(Account).where(Account.phone == phone))
        account = result.scalar_one_or_none()
        if account:
            account.session_string = session_string
            account.status         = "active"
            account.last_active    = datetime.utcnow()
        else:
            account = Account(
                phone=phone,
                session_string=session_string,
                proxy=pending.get("proxy"),
                status="active",
                last_active=datetime.utcnow(),
            )
            db.add(account)
        await db.commit()
        await db.refresh(account)
        account_id = account.id
    finally:
        await db.close()

    _clients[account_id] = client
    _pending_auth.pop(phone, None)
    logger.info(f"✅ حساب {phone} نشط (ID={account_id})")
    return {"status": "ok", "account_id": account_id}


# ─── الحصول على عميل جاهز ────────────────────────────────────────────────────
async def get_client(account_id: int) -> Optional[Client]:
    if account_id in _clients:
        return _clients[account_id]

    db = await get_session()
    try:
        from sqlalchemy import select
        result = await db.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account or not account.session_string:
            return None

        client = _build_client(account.phone, account.session_string, account.proxy)
        await client.connect()
        _clients[account_id] = client
        return client
    except (AuthKeyUnregistered, UserDeactivated) as e:
        logger.warning(f"⚠️ الحساب {account_id} محظور/منتهي: {e}")
        await _mark_account_status(account_id, "banned")
        return None
    finally:
        await db.close()


# ─── الانضمام لمجموعة ────────────────────────────────────────────────────────
async def join_group(client: Client, group_link: str) -> dict:
    try:
        await short_delay()
        chat = await client.join_chat(group_link)
        return {"status": "ok", "chat_id": chat.id, "title": chat.title}
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return {"status": "flood_wait", "wait": e.value}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ─── إرسال رسالة ─────────────────────────────────────────────────────────────
async def send_message(client: Client, group_id: int | str,
                       text: str) -> Optional[PyroMessage]:
    """
    يحاكي القراءة أولاً ثم يرسل الرسالة.
    """
    try:
        # محاكاة القراءة
        await client.get_chat_history(group_id, limit=5)
        await short_delay()

        msg = await client.send_message(group_id, text)
        logger.info(f"📨 رسالة مرسلة → {group_id} (msg_id={msg.id})")
        return msg
    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s للمجموعة {group_id}")
        await asyncio.sleep(e.value)
        return None
    except Exception as e:
        logger.error(f"خطأ إرسال → {group_id}: {e}")
        return None


# ─── جلب قائمة الحوارات ───────────────────────────────────────────────────────
async def get_dialogs(client: Client) -> list[dict]:
    dialogs = []
    async for dialog in client.get_dialogs():
        if dialog.chat.type.name in ("GROUP", "SUPERGROUP"):
            dialogs.append({
                "id":    str(dialog.chat.id),
                "title": dialog.chat.title,
                "username": dialog.chat.username,
                "members":  getattr(dialog.chat, "members_count", 0),
            })
    return dialogs


# ─── مراقبة حذف الرسالة ──────────────────────────────────────────────────────
async def monitor_deletion(client: Client, telegram_msg_id: int,
                           chat_id: int | str, db_message_id: int) -> None:
    """
    تفحص الرسالة كل ثانيتين لمدة DELETION_CHECK_SEC ثانية.
    إن حُذفت تحدّث السجل وتصنّف بوت الحماية.
    """
    check_interval = 2
    elapsed        = 0

    while elapsed < DELETION_CHECK_SEC:
        await asyncio.sleep(check_interval)
        elapsed += check_interval
        try:
            msgs = await client.get_messages(chat_id, telegram_msg_id)
            if msgs is None or (hasattr(msgs, "empty") and msgs.empty):
                await _record_deletion(db_message_id, chat_id, elapsed)
                return
        except Exception:
            await _record_deletion(db_message_id, chat_id, elapsed)
            return

    logger.debug(f"✅ الرسالة {telegram_msg_id} لم تُحذف خلال النافذة")


async def _record_deletion(db_message_id: int, chat_id, elapsed_sec: int):
    """يسجل الحذف ويحدد بوت الحماية المحتمل."""
    db = await get_session()
    try:
        from sqlalchemy import select
        res = await db.execute(select(Message).where(Message.id == db_message_id))
        msg = res.scalar_one_or_none()
        if msg:
            msg.status          = "deleted"
            msg.deleted_at      = datetime.utcnow()
            msg.deletion_reason = "bot" if elapsed_sec < 30 else "admin"

        # تحديث تصنيف المجموعة
        if msg and msg.group_id:
            res2 = await db.execute(select(Group).where(Group.id == msg.group_id))
            grp  = res2.scalar_one_or_none()
            if grp and elapsed_sec < 30:
                grp.protection_bot = "سريع(<30ث)"
            elif grp:
                grp.protection_bot = "بطيء"

        await db.commit()
        logger.warning(f"🗑 رسالة {db_message_id} حُذفت بعد {elapsed_sec}ث")
    finally:
        await db.close()


# ─── تحديث حالة الحساب ───────────────────────────────────────────────────────
async def _mark_account_status(account_id: int, status: str):
    db = await get_session()
    try:
        from sqlalchemy import select
        res = await db.execute(select(Account).where(Account.id == account_id))
        acc = res.scalar_one_or_none()
        if acc:
            acc.status = status
            await db.commit()
    finally:
        await db.close()


# ─── تشغيل العملاء المحفوظة عند البدء ────────────────────────────────────────
async def start_clients():
    """يُستدعى عند تشغيل التطبيق لاستعادة جلسات الحسابات النشطة."""
    db = await get_session()
    try:
        from sqlalchemy import select
        result = await db.execute(
            select(Account).where(Account.status == "active")
        )
        accounts = result.scalars().all()
        for acc in accounts:
            if acc.session_string:
                try:
                    client = _build_client(acc.phone, acc.session_string, acc.proxy)
                    await client.connect()
                    _clients[acc.id] = client
                    logger.info(f"♻️  استعادة جلسة: {acc.phone}")
                except Exception as e:
                    logger.warning(f"⚠️ فشل استعادة {acc.phone}: {e}")
        logger.info(f"🚀 {len(_clients)} حساب نشط")
    finally:
        await db.close()


async def stop_clients():
    """إيقاف جميع العملاء عند إغلاق التطبيق."""
    for acc_id, client in list(_clients.items()):
        try:
            await client.disconnect()
        except Exception:
            pass
    _clients.clear()
    logger.info("🔌 تم إيقاف جميع العملاء")
