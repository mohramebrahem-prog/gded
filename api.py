"""api.py — FastAPI endpoints"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

import models
import userbot
import ai_copilot
from database import get_db, init_db
from config import get_active_llm, _key_for
import litellm

logger = logging.getLogger(__name__)

app = FastAPI(title="TG Manager")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── WebSocket للإشعارات الفورية ──────────────────────────────────────────────
_ws_clients: list[WebSocket] = []

async def broadcast(data: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.remove(ws)

# ─── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    await init_db()
    # تشغيل الحسابات النشطة من قاعدة البيانات
    from database import SessionLocal
    async with SessionLocal() as db:
        result = await db.execute(
            select(models.Account).where(
                models.Account.is_active == True,
                models.Account.session != None,
            )
        )
        accounts = result.scalars().all()
        for acc in accounts:
            ok = await userbot.start_account(acc.id, acc.phone, acc.session)
            if ok:
                acc.is_online = True
        await db.commit()
    logger.info(f"✅ النظام جاهز — {len(accounts)} حساب نشط")

    # تشغيل مراقبة الحذف
    import monitor
    for acc in accounts:
        client = await userbot.get_client(acc.id)
        if client:
            monitor.attach_monitor(acc.id, client)
    asyncio.create_task(monitor.periodic_sync(30))
    logger.info("👁️ مراقبة الحذف نشطة")

# ═══════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════
@app.get("/api/health")
async def health():
    model, key = get_active_llm()
    return {
        "status": "ok",
        "active_accounts": len(userbot.active_accounts()),
        "ai_model": model or "غير محدد",
        "ai_ready": bool(key),
    }

# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════
@app.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    accounts  = (await db.execute(select(func.count(models.Account.id)))).scalar() or 0
    active    = (await db.execute(select(func.count(models.Account.id)).where(models.Account.is_active == True))).scalar() or 0
    groups    = (await db.execute(select(func.count(models.Group.id)))).scalar() or 0
    joined    = (await db.execute(select(func.count(models.Group.id)).where(models.Group.is_joined == True))).scalar() or 0
    messages  = (await db.execute(select(func.count(models.Message.id)))).scalar() or 0
    deleted   = (await db.execute(select(func.count(models.Message.id)).where(models.Message.status == "deleted"))).scalar() or 0
    campaigns = (await db.execute(select(func.count(models.Campaign.id)))).scalar() or 0
    return {
        "accounts": accounts, "active_accounts": active,
        "groups": groups, "joined_groups": joined,
        "messages_sent": messages, "messages_deleted": deleted,
        "delete_rate": round(deleted / messages * 100, 1) if messages else 0,
        "campaigns": campaigns,
    }

# ═══════════════════════════════════════════════════════════════
# ACCOUNTS
# ═══════════════════════════════════════════════════════════════

# مؤقت لتخزين حالة تسجيل الدخول الجارية
_pending_logins: dict[str, dict] = {}

class AddAccountReq(BaseModel):
    phone: str
    note: Optional[str] = ""

class VerifyCodeReq(BaseModel):
    phone: str
    code: str
    password: Optional[str] = ""

class SessionStringReq(BaseModel):
    phone: str
    session_string: str
    note: Optional[str] = ""

@app.get("/api/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Account).order_by(desc(models.Account.created_at)))
    accounts = result.scalars().all()
    data = []
    for a in accounts:
        d = a.to_dict()
        d["is_online"] = a.id in userbot.active_accounts()
        data.append(d)
    return data

@app.post("/api/accounts/start-login")
async def start_login(req: AddAccountReq):
    """الخطوة 1: إرسال كود التحقق للهاتف."""
    phone = req.phone.strip()
    try:
        code_hash, client = await userbot.generate_session(phone)
        _pending_logins[phone] = {"hash": code_hash, "client": client}
        return {"status": "ok", "message": f"تم إرسال الكود إلى {phone}"}
    except Exception as e:
        raise HTTPException(400, f"فشل إرسال الكود: {e}")

@app.post("/api/accounts/verify-code")
async def verify_code(req: VerifyCodeReq, db: AsyncSession = Depends(get_db)):
    """الخطوة 2: إدخال الكود وإنشاء الحساب."""
    phone = req.phone.strip()
    pending = _pending_logins.get(phone)
    if not pending:
        raise HTTPException(400, "لا يوجد طلب تسجيل دخول لهذا الرقم")
    try:
        session_str = await userbot.complete_session(
            pending["client"], phone, req.code,
            pending["hash"], req.password or ""
        )
    except ValueError as e:
        if "2FA_REQUIRED" in str(e):
            raise HTTPException(400, "الحساب محمي بـ 2FA — أرسل الكود مع كلمة المرور")
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"فشل التحقق: {e}")

    # حفظ في DB
    result = await db.execute(select(models.Account).where(models.Account.phone == phone))
    acc = result.scalar_one_or_none()
    if not acc:
        acc = models.Account(phone=phone)
        db.add(acc)
    acc.session   = session_str
    acc.is_active = True
    await db.commit()
    await db.refresh(acc)

    # تشغيل الحساب
    await userbot.start_account(acc.id, phone, session_str)
    _pending_logins.pop(phone, None)
    await broadcast({"event": "account_added", "phone": phone})
    return {"status": "ok", "account": acc.to_dict()}

@app.post("/api/accounts/import-session")
async def import_session(req: SessionStringReq, db: AsyncSession = Depends(get_db)):
    """إضافة حساب عبر session string مباشرة."""
    phone = req.phone.strip()
    result = await db.execute(select(models.Account).where(models.Account.phone == phone))
    acc = result.scalar_one_or_none()
    if not acc:
        acc = models.Account(phone=phone)
        db.add(acc)
    acc.session   = req.session_string.strip()
    acc.is_active = True
    acc.note      = req.note or ""
    await db.commit()
    await db.refresh(acc)
    ok = await userbot.start_account(acc.id, phone, acc.session)
    await broadcast({"event": "account_added", "phone": phone})
    return {"status": "ok" if ok else "error", "account": acc.to_dict()}

@app.post("/api/accounts/{account_id}/toggle")
async def toggle_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """تشغيل/إيقاف حساب."""
    result = await db.execute(select(models.Account).where(models.Account.id == account_id))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "حساب غير موجود")
    if account_id in userbot.active_accounts():
        await userbot.stop_account(account_id)
        acc.is_online = False
    else:
        if acc.session:
            ok = await userbot.start_account(account_id, acc.phone, acc.session)
            acc.is_online = ok
    await db.commit()
    return {"status": "ok", "is_online": acc.is_online}

@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    await userbot.stop_account(account_id)
    result = await db.execute(select(models.Account).where(models.Account.id == account_id))
    acc = result.scalar_one_or_none()
    if acc:
        await db.delete(acc)
        await db.commit()
    return {"status": "ok"}

# ═══════════════════════════════════════════════════════════════
# GROUPS
# ═══════════════════════════════════════════════════════════════

class AddGroupReq(BaseModel):
    link: str
    account_id: Optional[int] = None
    category: Optional[str] = ""
    language: Optional[str] = "ar"

class UpdateGroupReq(BaseModel):
    category: Optional[str] = None
    language: Optional[str] = None
    protection_bot: Optional[str] = None

@app.get("/api/groups")
async def list_groups(
    category: str = "", language: str = "", is_joined: str = "",
    db: AsyncSession = Depends(get_db)
):
    q = select(models.Group).order_by(desc(models.Group.created_at))
    if category:
        q = q.where(models.Group.category == category)
    if language:
        q = q.where(models.Group.language == language)
    if is_joined in ("true", "false"):
        q = q.where(models.Group.is_joined == (is_joined == "true"))
    result = await db.execute(q)
    return [g.to_dict() for g in result.scalars().all()]

@app.post("/api/groups")
async def add_group(req: AddGroupReq, db: AsyncSession = Depends(get_db)):
    """إضافة مجموعة وجلب معلوماتها."""
    link = req.link.strip()
    account_id = req.account_id
    if not account_id:
        active = userbot.active_accounts()
        if not active:
            raise HTTPException(400, "لا يوجد حساب نشط — شغّل حساباً أولاً")
        account_id = active[0]

    info = await userbot.get_group_info(account_id, link)
    if not info:
        raise HTTPException(400, "تعذر جلب معلومات المجموعة — تحقق من الرابط والحساب")

    result = await db.execute(select(models.Group).where(models.Group.telegram_id == info["telegram_id"]))
    grp = result.scalar_one_or_none()
    if not grp:
        grp = models.Group(telegram_id=info["telegram_id"])
        db.add(grp)
    grp.title        = info["title"]
    grp.username     = info["username"]
    grp.member_count = info["member_count"]
    grp.category     = req.category or ""
    grp.language     = req.language or "ar"
    grp.is_joined    = True
    await db.commit()
    await db.refresh(grp)
    await broadcast({"event": "group_added", "title": grp.title})
    return {"status": "ok", "group": grp.to_dict()}

@app.put("/api/groups/{group_id}")
async def update_group(group_id: int, req: UpdateGroupReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Group).where(models.Group.id == group_id))
    grp = result.scalar_one_or_none()
    if not grp:
        raise HTTPException(404, "مجموعة غير موجودة")
    if req.category    is not None: grp.category       = req.category
    if req.language    is not None: grp.language        = req.language
    if req.protection_bot is not None: grp.protection_bot = req.protection_bot
    await db.commit()
    return {"status": "ok", "group": grp.to_dict()}

@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Group).where(models.Group.id == group_id))
    grp = result.scalar_one_or_none()
    if grp:
        await db.delete(grp)
        await db.commit()
    return {"status": "ok"}

# ═══════════════════════════════════════════════════════════════
# CAMPAIGNS & SEND
# ═══════════════════════════════════════════════════════════════

class CreateCampaignReq(BaseModel):
    name: str
    text: str
    account_id: int
    group_ids: list[int]

class SendMessageReq(BaseModel):
    account_id: int
    group_id: int
    text: str
    campaign_id: Optional[int] = None

@app.get("/api/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Campaign).order_by(desc(models.Campaign.created_at)))
    camps = result.scalars().all()
    data = []
    for c in camps:
        d = c.to_dict()
        total   = (await db.execute(select(func.count(models.Message.id)).where(models.Message.campaign_id == c.id))).scalar() or 0
        deleted = (await db.execute(select(func.count(models.Message.id)).where(models.Message.campaign_id == c.id, models.Message.status == "deleted"))).scalar() or 0
        d["total_messages"] = total
        d["deleted_messages"] = deleted
        data.append(d)
    return data

@app.post("/api/campaigns")
async def create_campaign(req: CreateCampaignReq, db: AsyncSession = Depends(get_db)):
    """ينشئ حملة ويرسل الرسائل لجميع المجموعات المحددة."""
    camp = models.Campaign(name=req.name, text=req.text, account_id=req.account_id, status="running")
    db.add(camp)
    await db.commit()
    await db.refresh(camp)

    asyncio.create_task(_run_campaign(camp.id, req.account_id, req.group_ids, req.text))
    return {"status": "ok", "campaign": camp.to_dict()}

async def _run_campaign(campaign_id: int, account_id: int, group_ids: list[int], text: str):
    """يرسل الرسائل في الخلفية."""
    from database import SessionLocal
    async with SessionLocal() as db:
        sent = 0
        for gid in group_ids:
            grp_result = await db.execute(select(models.Group).where(models.Group.id == gid))
            grp = grp_result.scalar_one_or_none()
            if not grp:
                continue
            msg_id = await userbot.send_message(account_id, grp.telegram_id, text)
            status = "sent" if msg_id else "failed"
            msg = models.Message(
                account_id=account_id, group_id=gid,
                campaign_id=campaign_id, content=text,
                telegram_msg_id=msg_id, status=status,
            )
            db.add(msg)
            if msg_id:
                grp.last_sent_at = datetime.utcnow()
                sent += 1
            await db.commit()
            await asyncio.sleep(3)  # تأخير بين الرسائل

        # تحديث حالة الحملة
        camp_result = await db.execute(select(models.Campaign).where(models.Campaign.id == campaign_id))
        camp = camp_result.scalar_one_or_none()
        if camp:
            camp.status = "done"
            await db.commit()
        await broadcast({"event": "campaign_done", "campaign_id": campaign_id, "sent": sent})

@app.post("/api/send")
async def send_single(req: SendMessageReq, db: AsyncSession = Depends(get_db)):
    """إرسال رسالة واحدة لمجموعة محددة."""
    grp_result = await db.execute(select(models.Group).where(models.Group.id == req.group_id))
    grp = grp_result.scalar_one_or_none()
    if not grp:
        raise HTTPException(404, "مجموعة غير موجودة")
    msg_id = await userbot.send_message(req.account_id, grp.telegram_id, req.text)
    if msg_id is None:
        raise HTTPException(500, "فشل الإرسال — تحقق أن الحساب متصل")
    msg = models.Message(
        account_id=req.account_id, group_id=req.group_id,
        campaign_id=req.campaign_id, content=req.text,
        telegram_msg_id=msg_id, status="sent",
    )
    db.add(msg)
    grp.last_sent_at = datetime.utcnow()
    await db.commit()
    return {"status": "ok", "telegram_msg_id": msg_id}

# ═══════════════════════════════════════════════════════════════
# MESSAGES
# ═══════════════════════════════════════════════════════════════
@app.get("/api/messages")
async def list_messages(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.Message).order_by(desc(models.Message.sent_at)).limit(limit)
    )
    return [m.to_dict() for m in result.scalars().all()]

# ═══════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════
@app.get("/api/logs")
async def list_logs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.Log).order_by(desc(models.Log.created_at)).limit(limit)
    )
    return [l.to_dict() for l in result.scalars().all()]

# ═══════════════════════════════════════════════════════════════
# AI COPILOT
# ═══════════════════════════════════════════════════════════════
class CopilotReq(BaseModel):
    command: str

class TestAIReq(BaseModel):
    model_string: str
    api_key: str

@app.post("/api/copilot")
async def copilot(req: CopilotReq, db: AsyncSession = Depends(get_db)):
    result = await ai_copilot.run_copilot(req.command, db)
    return result

@app.get("/api/ai/status")
async def ai_status():
    model, key = get_active_llm()
    return {
        "model": model or "غير محدد",
        "ready": bool(key),
        "groq":      bool(os.getenv("GROQ_API_KEY")),
        "gemini":    bool(os.getenv("GOOGLE_API_KEY")),
        "deepseek":  bool(os.getenv("DEEPSEEK_API_KEY")),
        "openai":    bool(os.getenv("OPENAI_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
    }

@app.post("/api/ai/test")
async def test_ai(req: TestAIReq):
    """اختبار نموذج بمفتاح محدد."""
    import time
    ai_copilot._set_env_key(req.model_string, req.api_key)
    try:
        t0 = time.time()
        resp = litellm.completion(
            model=req.model_string,
            messages=[{"role": "user", "content": "قل: مرحبا"}],
            max_tokens=20,
            timeout=15,
        )
        text = resp.choices[0].message.content.strip()
        ms = round((time.time() - t0) * 1000)
        return {"status": "ok", "response": text, "latency_ms": ms}
    except Exception as e:
        err = str(e)
        if any(x in err.lower() for x in ["401", "unauthorized", "invalid api key"]):
            msg = "❌ المفتاح غير صحيح"
        elif any(x in err.lower() for x in ["429", "quota", "rate"]):
            msg = "⚠️ تجاوزت الحد المسموح — حاول لاحقاً"
        else:
            msg = f"❌ خطأ: {err[:150]}"
        return {"status": "error", "message": msg}

# ═══════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════
from sqlalchemy import text as sql_text


@app.get("/api/analytics/messages-timeline")
async def messages_timeline(days: int = 14, db: AsyncSession = Depends(get_db)):
    q = f"""
        SELECT DATE(sent_at) as day,
            COUNT(*) FILTER (WHERE status IN ('sent','deleted')) as sent,
            COUNT(*) FILTER (WHERE status = 'deleted') as deleted
        FROM messages
        WHERE sent_at >= NOW() - INTERVAL '{days} days'
        GROUP BY DATE(sent_at) ORDER BY day ASC
    """
    result = await db.execute(sql_text(q))
    rows = result.fetchall()
    return [{"day": str(r.day), "sent": r.sent, "deleted": r.deleted} for r in rows]


@app.get("/api/analytics/groups-performance")
async def groups_performance(db: AsyncSession = Depends(get_db)):
    q = """
        SELECT g.id, g.title, g.category, g.member_count,
            COUNT(m.id) AS total,
            COUNT(m.id) FILTER (WHERE m.status = 'deleted') AS deleted,
            ROUND(COUNT(m.id) FILTER (WHERE m.status='deleted')::numeric
                  / NULLIF(COUNT(m.id),0)*100, 1) AS delete_pct
        FROM groups g LEFT JOIN messages m ON m.group_id = g.id
        GROUP BY g.id, g.title, g.category, g.member_count
        ORDER BY total DESC LIMIT 30
    """
    result = await db.execute(sql_text(q))
    return [
        {"id": r.id, "title": r.title, "category": r.category,
         "member_count": r.member_count, "total": r.total,
         "deleted": r.deleted, "delete_pct": float(r.delete_pct or 0)}
        for r in result.fetchall()
    ]


@app.get("/api/analytics/accounts-performance")
async def accounts_performance(db: AsyncSession = Depends(get_db)):
    q = """
        SELECT a.id, a.phone,
            COUNT(m.id) AS total,
            COUNT(m.id) FILTER (WHERE m.status='deleted') AS deleted,
            COUNT(m.id) FILTER (WHERE m.status='sent') AS active
        FROM accounts a LEFT JOIN messages m ON m.account_id = a.id
        GROUP BY a.id, a.phone ORDER BY total DESC
    """
    result = await db.execute(sql_text(q))
    return [
        {"id": r.id, "phone": r.phone, "total": r.total,
         "deleted": r.deleted, "active": r.active}
        for r in result.fetchall()
    ]


@app.get("/api/analytics/delete-by-hour")
async def delete_by_hour(db: AsyncSession = Depends(get_db)):
    q = """
        SELECT EXTRACT(HOUR FROM deleted_at)::int AS hour, COUNT(*) AS count
        FROM messages WHERE status='deleted' AND deleted_at IS NOT NULL
        GROUP BY hour ORDER BY hour
    """
    result = await db.execute(sql_text(q))
    data = {r.hour: r.count for r in result.fetchall()}
    return [{"hour": h, "count": data.get(h, 0)} for h in range(24)]


@app.get("/api/analytics/categories-summary")
async def categories_summary(db: AsyncSession = Depends(get_db)):
    q = """
        SELECT COALESCE(g.category, 'غير محدد') AS category,
            COUNT(DISTINCT g.id) AS groups,
            COUNT(m.id) AS messages,
            COUNT(m.id) FILTER (WHERE m.status='deleted') AS deleted,
            ROUND(COUNT(m.id) FILTER (WHERE m.status='deleted')::numeric
                  / NULLIF(COUNT(m.id),0)*100, 1) AS delete_pct
        FROM groups g LEFT JOIN messages m ON m.group_id = g.id
        GROUP BY g.category ORDER BY messages DESC
    """
    result = await db.execute(sql_text(q))
    return [
        {"category": r.category, "groups": r.groups, "messages": r.messages,
         "deleted": r.deleted, "delete_pct": float(r.delete_pct or 0)}
        for r in result.fetchall()
    ]
# MONITOR
@app.get("/api/monitor/deleted")
async def get_deleted_messages(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.Message, models.Group, models.Account)
        .join(models.Group,   models.Message.group_id   == models.Group.id)
        .join(models.Account, models.Message.account_id == models.Account.id)
        .where(models.Message.status == "deleted")
        .order_by(desc(models.Message.deleted_at))
        .limit(limit)
    )
    rows = result.fetchall()
    return [
        {**msg.to_dict(), "group_title": grp.title, "account_phone": acc.phone,
         "survival_seconds": int((msg.deleted_at - msg.sent_at).total_seconds()) if msg.deleted_at and msg.sent_at else None}
        for msg, grp, acc in rows
    ]

@app.get("/api/monitor/status")
async def monitor_status():
    import monitor
    return {"monitored_accounts": list(monitor._handlers.keys()), "count": len(monitor._handlers), "sync_running": monitor._running}

# ═══════════════════════════════════════════════════════════════
# STATIC (SPA)
# ═══════════════════════════════════════════════════════════════
from pathlib import Path
STATIC = Path(__file__).parent / "static"

@app.get("/")
async def root():
    return FileResponse(STATIC / "index.html")

@app.get("/{path:path}")
async def spa(path: str):
    f = STATIC / path
    if f.exists() and f.is_file():
        return FileResponse(f)
    return FileResponse(STATIC / "index.html")
