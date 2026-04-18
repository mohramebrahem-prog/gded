"""
api.py – تطبيق FastAPI الرئيسي
- REST API للوحة التحكم
- WebSocket للتحديثات الحية
- خدمة الملفات الثابتة (React)
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    FastAPI, Depends, HTTPException, WebSocket,
    WebSocketDisconnect, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc

from config import SECRET_KEY
from database import get_db, init_db
from models import Account, Group, Message, Campaign, Template, AuditLog
import userbot_manager as um

logger = logging.getLogger(__name__)

# ─── إنشاء التطبيق ───────────────────────────────────────────────────────────
app = FastAPI(title="Telegram AI System", version="1.0.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── إدارة اتصالات WebSocket ──────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = ConnectionManager()


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ══════════════════════════════════════════════════════════════════════════════

class AddAccountReq(BaseModel):
    phone: str
    proxy: Optional[dict] = None

class VerifyCodeReq(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None

class GroupActionReq(BaseModel):
    action: str           # join / leave / update_tags
    group_id: Optional[int] = None
    group_link: Optional[str] = None
    account_id: Optional[int] = None
    data: Optional[dict] = None

class CampaignCreate(BaseModel):
    name: str
    target_criteria: dict = {}
    schedule: dict = {}
    template_ids: list[int] = []

class TemplateCreate(BaseModel):
    name: str
    base_content: str
    variations: list[str] = []

class CopilotReq(BaseModel):
    command: str

class SendMessageReq(BaseModel):
    account_id: int
    group_id: int
    message: str


# ══════════════════════════════════════════════════════════════════════════════
# Startup / Shutdown
# ══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(um.start_clients())
    logger.info("🚀 النظام بدأ")

@app.on_event("shutdown")
async def shutdown():
    await um.stop_clients()


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket
# ══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ══════════════════════════════════════════════════════════════════════════════
# /api/accounts
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).order_by(desc(Account.created_at)))
    return [a.to_dict() for a in result.scalars().all()]


@app.post("/api/accounts/add")
async def add_account(req: AddAccountReq):
    return await um.add_account(req.phone, req.proxy)


@app.post("/api/accounts/verify")
async def verify_code(req: VerifyCodeReq):
    result = await um.verify_code(req.phone, req.code, req.password)
    if result.get("status") == "ok":
        await ws_manager.broadcast({"type": "account_added", "phone": req.phone})
    return result


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "حساب غير موجود")
    await db.delete(acc)
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# /api/groups
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/groups")
async def list_groups(
    category: Optional[str] = None,
    is_joined: Optional[bool] = None,
    min_members: Optional[int] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Group)
    conditions = []
    if category:
        conditions.append(Group.category == category)
    if is_joined is not None:
        conditions.append(Group.is_joined == is_joined)
    if min_members:
        conditions.append(Group.member_count >= min_members)
    if search:
        conditions.append(Group.title.ilike(f"%{search}%"))
    if conditions:
        q = q.where(and_(*conditions))
    result = await db.execute(q.order_by(desc(Group.member_count)).limit(500))
    return [g.to_dict() for g in result.scalars().all()]


@app.post("/api/groups/action")
async def group_action(req: GroupActionReq, background: BackgroundTasks,
                       db: AsyncSession = Depends(get_db)):
    if req.action == "join":
        client = await um.get_client(req.account_id)
        if not client:
            raise HTTPException(400, "العميل غير متاح")
        result = await um.join_group(client, req.group_link)
        if result["status"] == "ok":
            # تسجيل المجموعة
            grp = Group(
                telegram_id=str(result["chat_id"]),
                title=result["title"],
                account_id=req.account_id,
                is_joined=True,
                joined_at=datetime.utcnow(),
            )
            db.add(grp)
            await ws_manager.broadcast({"type": "group_joined", "title": result["title"]})
        return result

    if req.action == "sync_dialogs":
        client = await um.get_client(req.account_id)
        if not client:
            raise HTTPException(400, "العميل غير متاح")
        dialogs = await um.get_dialogs(client)
        added = 0
        for d in dialogs:
            res2 = await db.execute(select(Group).where(Group.telegram_id == d["id"]))
            if not res2.scalar_one_or_none():
                grp = Group(
                    telegram_id=d["id"], title=d["title"],
                    username=d.get("username"), account_id=req.account_id,
                    member_count=d.get("members", 0),
                    is_joined=True, joined_at=datetime.utcnow(),
                )
                db.add(grp)
                added += 1
        return {"status": "ok", "synced": len(dialogs), "added": added}

    if req.action == "update":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp:
            raise HTTPException(404, "مجموعة غير موجودة")
        for k, v in (req.data or {}).items():
            if hasattr(grp, k):
                setattr(grp, k, v)
        return grp.to_dict()

    raise HTTPException(400, f"إجراء غير معروف: {req.action}")


@app.post("/api/groups/import")
async def import_groups(groups: list[dict], db: AsyncSession = Depends(get_db)):
    added = 0
    for g in groups:
        res = await db.execute(select(Group).where(Group.telegram_id == str(g.get("telegram_id", ""))))
        if not res.scalar_one_or_none():
            grp = Group(**{k: v for k, v in g.items() if hasattr(Group, k)})
            db.add(grp)
            added += 1
    return {"status": "ok", "added": added}


# ══════════════════════════════════════════════════════════════════════════════
# /api/campaigns
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).order_by(desc(Campaign.created_at)))
    return [c.to_dict() for c in result.scalars().all()]


@app.post("/api/campaigns")
async def create_campaign(req: CampaignCreate, db: AsyncSession = Depends(get_db)):
    camp = Campaign(
        name=req.name, target_criteria=req.target_criteria,
        schedule=req.schedule, template_ids=req.template_ids,
    )
    db.add(camp)
    await db.flush()
    await db.refresh(camp)
    return camp.to_dict()


@app.patch("/api/campaigns/{campaign_id}")
async def update_campaign(campaign_id: int, data: dict,
                          db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp:
        raise HTTPException(404)
    for k, v in data.items():
        if hasattr(camp, k):
            setattr(camp, k, v)
    return camp.to_dict()


@app.delete("/api/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp:
        raise HTTPException(404)
    await db.delete(camp)
    return {"status": "deleted"}


@app.post("/api/campaigns/{campaign_id}/run")
async def run_campaign(campaign_id: int, background: BackgroundTasks,
                       db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp:
        raise HTTPException(404)
    camp.status = "running"

    async def _run():
        from agents import run_copilot
        criteria_str = json.dumps(camp.target_criteria, ensure_ascii=False)
        cmd = f"شغّل الحملة '{camp.name}' وأرسل القوالب {camp.template_ids} للمجموعات التي تطابق: {criteria_str}"
        result = await run_copilot(cmd)
        # تحديث الحالة
        from database import get_session
        db2 = await get_session()
        try:
            res2 = await db2.execute(select(Campaign).where(Campaign.id == campaign_id))
            c = res2.scalar_one_or_none()
            if c:
                c.status = "done"
                await db2.commit()
        finally:
            await db2.close()
        await ws_manager.broadcast({"type": "campaign_done", "id": campaign_id, "result": str(result)[:200]})

    background.add_task(_run)
    return {"status": "started"}


# ══════════════════════════════════════════════════════════════════════════════
# /api/templates
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/templates")
async def list_templates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Template).order_by(desc(Template.created_at)))
    return [t.to_dict() for t in result.scalars().all()]


@app.post("/api/templates")
async def create_template(req: TemplateCreate, db: AsyncSession = Depends(get_db)):
    tmpl = Template(name=req.name, base_content=req.base_content, variations=req.variations)
    db.add(tmpl)
    await db.flush()
    await db.refresh(tmpl)
    return tmpl.to_dict()


@app.patch("/api/templates/{template_id}")
async def update_template(template_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Template).where(Template.id == template_id))
    tmpl = res.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404)
    for k, v in data.items():
        if hasattr(tmpl, k):
            setattr(tmpl, k, v)
    return tmpl.to_dict()


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Template).where(Template.id == template_id))
    tmpl = res.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404)
    await db.delete(tmpl)
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# /api/stats
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats/overview")
async def stats_overview(db: AsyncSession = Depends(get_db)):
    accounts_total   = (await db.execute(select(func.count(Account.id)))).scalar() or 0
    accounts_active  = (await db.execute(
        select(func.count(Account.id)).where(Account.status == "active"))).scalar() or 0
    groups_total     = (await db.execute(select(func.count(Group.id)))).scalar() or 0
    groups_joined    = (await db.execute(
        select(func.count(Group.id)).where(Group.is_joined == True))).scalar() or 0
    messages_total   = (await db.execute(select(func.count(Message.id)))).scalar() or 0
    messages_deleted = (await db.execute(
        select(func.count(Message.id)).where(Message.status == "deleted"))).scalar() or 0
    campaigns_total  = (await db.execute(select(func.count(Campaign.id)))).scalar() or 0

    return {
        "accounts": {"total": accounts_total, "active": accounts_active},
        "groups":   {"total": groups_total,   "joined": groups_joined},
        "messages": {
            "total": messages_total, "deleted": messages_deleted,
            "success_rate": round(
                (messages_total - messages_deleted) / messages_total * 100, 1
            ) if messages_total else 0,
        },
        "campaigns": {"total": campaigns_total},
    }


@app.get("/api/stats/messages_timeline")
async def messages_timeline(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            func.date(Message.sent_at).label("date"),
            func.count(Message.id).label("count"),
        ).group_by(func.date(Message.sent_at))
        .order_by(func.date(Message.sent_at))
        .limit(30)
    )
    return [{"date": str(r.date), "count": r.count} for r in result.all()]


@app.get("/api/stats/groups_by_category")
async def groups_by_category(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.category, func.count(Group.id).label("count"))
        .group_by(Group.category)
        .order_by(desc("count"))
    )
    return [{"category": r.category or "غير مصنف", "count": r.count} for r in result.all()]


@app.get("/api/stats/protection_bots")
async def protection_bots(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.protection_bot, func.count(Group.id).label("count"))
        .where(Group.protection_bot != None)
        .group_by(Group.protection_bot)
    )
    return [{"bot": r.protection_bot, "count": r.count} for r in result.all()]


# ══════════════════════════════════════════════════════════════════════════════
# /api/audit-log
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/audit-log")
async def get_audit_log(limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
    )
    return [l.to_dict() for l in result.scalars().all()]


# ══════════════════════════════════════════════════════════════════════════════
# /api/messages
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/messages")
async def list_messages(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message).order_by(desc(Message.sent_at)).limit(limit)
    )
    return [m.to_dict() for m in result.scalars().all()]


@app.post("/api/messages/send")
async def send_message_direct(req: SendMessageReq, background: BackgroundTasks):
    client = await um.get_client(req.account_id)
    if not client:
        raise HTTPException(400, "العميل غير متاح")

    async def _send():
        from database import get_session
        db = await get_session()
        try:
            res = await db.execute(select(Group).where(Group.id == req.group_id))
            grp = res.scalar_one_or_none()
            if not grp:
                return
            msg = await um.send_message(client, int(grp.telegram_id), req.message)
            if msg:
                db_msg = Message(
                    account_id=req.account_id, group_id=req.group_id,
                    content=req.message, telegram_msg_id=msg.id,
                )
                db.add(db_msg)
                await db.commit()
                await db.refresh(db_msg)
                asyncio.create_task(
                    um.monitor_deletion(client, msg.id, int(grp.telegram_id), db_msg.id)
                )
                await ws_manager.broadcast({"type": "message_sent", "group": grp.title})
        finally:
            await db.close()

    background.add_task(_send)
    return {"status": "queued"}


# ══════════════════════════════════════════════════════════════════════════════
# /api/copilot
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/copilot")
async def copilot(req: CopilotReq, background: BackgroundTasks):
    from agents import run_copilot
    background.add_task(_copilot_task, req.command)
    return {"status": "processing", "command": req.command}


async def _copilot_task(command: str):
    from agents import run_copilot
    result = await run_copilot(command)
    await ws_manager.broadcast({"type": "copilot_result", "data": result})


# ══════════════════════════════════════════════════════════════════════════════
# خدمة الواجهة الأمامية
# ══════════════════════════════════════════════════════════════════════════════

STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    @app.get("/")
    async def serve_root():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = STATIC_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(STATIC_DIR / "index.html")
