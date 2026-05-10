"""userbot.py — إدارة حسابات Pyrogram في الذاكرة"""
import asyncio
import logging
from typing import Optional
from pyrogram import Client
from config import API_ID, API_HASH

logger = logging.getLogger(__name__)

# ─── تخزين العملاء النشطين في الذاكرة ────────────────────────────────────────
_clients: dict[int, Client] = {}   # account_id → Client


async def start_account(account_id: int, phone: str, session_string: str) -> bool:
    """يشغّل حساباً من session string موجود."""
    if account_id in _clients:
        return True
    try:
        client = Client(
            name=f"acc_{account_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True,
        )
        await client.start()
        _clients[account_id] = client
        logger.info(f"✅ حساب {phone} متصل")
        return True
    except Exception as e:
        logger.error(f"❌ فشل تشغيل حساب {phone}: {e}")
        return False


async def stop_account(account_id: int) -> bool:
    """يوقف حساباً."""
    client = _clients.pop(account_id, None)
    if client:
        try:
            await client.stop()
        except Exception:
            pass
    return True


async def get_client(account_id: int) -> Optional[Client]:
    """يرجع Client نشط أو None."""
    return _clients.get(account_id)


async def send_message(account_id: int, chat_id: int, text: str) -> Optional[int]:
    """
    يرسل رسالة ويرجع telegram_msg_id أو None عند الفشل.
    """
    client = _clients.get(account_id)
    if not client:
        logger.warning(f"حساب {account_id} غير متصل")
        return None
    try:
        msg = await client.send_message(chat_id, text)
        return msg.id
    except Exception as e:
        logger.error(f"خطأ في الإرسال: {e}")
        return None


async def join_group(account_id: int, link: str) -> bool:
    """يضيف حساباً لمجموعة عبر رابط أو username."""
    client = _clients.get(account_id)
    if not client:
        return False
    try:
        await client.join_chat(link)
        return True
    except Exception as e:
        logger.error(f"فشل الانضمام لـ {link}: {e}")
        return False


async def get_group_info(account_id: int, link: str) -> Optional[dict]:
    """يجلب معلومات مجموعة."""
    client = _clients.get(account_id)
    if not client:
        return None
    try:
        chat = await client.get_chat(link)
        return {
            "telegram_id": chat.id,
            "title": chat.title or "",
            "username": chat.username or "",
            "member_count": getattr(chat, "members_count", 0) or 0,
        }
    except Exception as e:
        logger.error(f"فشل جلب معلومات {link}: {e}")
        return None


def active_accounts() -> list[int]:
    """يرجع قائمة معرفات الحسابات النشطة."""
    return list(_clients.keys())


async def generate_session(phone: str) -> tuple[str, any]:
    """
    يبدأ جلسة تسجيل دخول جديدة ويرجع (phone_code_hash, client).
    يُستدعى من API لبدء إضافة حساب جديد.
    """
    client = Client(
        name=f"tmp_{phone}",
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )
    await client.connect()
    sent = await client.send_code(phone)
    return sent.phone_code_hash, client


async def complete_session(client: any, phone: str, code: str,
                           phone_code_hash: str, password: str = "") -> Optional[str]:
    """
    يكمل تسجيل الدخول ويرجع session string.
    """
    try:
        await client.sign_in(phone, phone_code_hash, code)
    except Exception as e:
        if "two" in str(e).lower() or "password" in str(e).lower():
            if not password:
                raise ValueError("2FA_REQUIRED")
            await client.check_password(password)
        else:
            raise
    session = await client.export_session_string()
    await client.disconnect()
    return session
