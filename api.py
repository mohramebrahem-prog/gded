"""
api.py – FastAPI REST API + WebSocket + Static Files
"""
import asyncio, json, logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc, or_, case

from database import get_db, init_db
from models import Account, Group, Message, Campaign, Template, AuditLog, Proxy
import userbot_manager as um

logger = logging.getLogger(__name__)

app = FastAPI(title="TG AI System", version="2.0.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ─── WebSocket Manager ────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept(); self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active: self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try: await ws.send_json(data)
            except: dead.append(ws)
        for ws in dead: self.disconnect(ws)

ws_manager = ConnectionManager()


# ─── Schemas ──────────────────────────────────────────────────────────────────
class AddAccountReq(BaseModel):
    phone: str
    proxy_id: Optional[int] = None
    proxy: Optional[dict] = None

class VerifyCodeReq(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None

class GroupActionReq(BaseModel):
    action: str
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

class ProxyCreate(BaseModel):
    label: str
    scheme: str = "socks5"
    hostname: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None


# ─── Startup / Shutdown ───────────────────────────────────────────────────────
async def _safe_start_clients():
    """تشغيل العملاء بأمان — لا يوقف السيرفر إن فشل."""
    try:
        await um.start_clients()
    except Exception as e:
        logger.warning(f"⚠️ start_clients فشل (غير مؤثر على السيرفر): {e}")

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(_safe_start_clients())
    logger.info("🚀 النظام بدأ")

@app.on_event("shutdown")
async def shutdown():
    try:
        await um.stop_clients()
    except Exception as e:
        logger.warning(f"⚠️ stop_clients فشل: {e}")


# ─── WebSocket ────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await asyncio.sleep(25)
            await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).order_by(desc(Account.created_at)))
    return [a.to_dict() for a in result.scalars().all()]

@app.post("/api/accounts/add")
async def add_account(req: AddAccountReq, db: AsyncSession = Depends(get_db)):
    proxy = req.proxy
    if req.proxy_id and not proxy:
        res = await db.execute(select(Proxy).where(Proxy.id == req.proxy_id))
        p = res.scalar_one_or_none()
        if p:
            proxy = {"scheme": p.scheme, "hostname": p.hostname, "port": p.port,
                     "username": p.username, "password": p.password}
    return await um.add_account(req.phone, proxy)

@app.post("/api/accounts/verify")
async def verify_code(req: VerifyCodeReq):
    result = await um.verify_code(req.phone, req.code, req.password)
    if result.get("status") == "ok":
        await ws_manager.broadcast({"type": "account_added", "phone": req.phone})
    return result

@app.patch("/api/accounts/{account_id}")
async def update_account(account_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id == account_id))
    acc = res.scalar_one_or_none()
    if not acc: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(acc, k): setattr(acc, k, v)
    await db.commit()
    return acc.to_dict()

@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id == account_id))
    acc = res.scalar_one_or_none()
    if not acc: raise HTTPException(404)
    await db.delete(acc)
    await db.commit()
    return {"status": "deleted"}

@app.post("/api/accounts/{account_id}/check")
async def check_account(account_id: int):
    client = await um.get_client(account_id)
    if client:
        return {"status": "ok", "connected": True}
    return {"status": "error", "connected": False}

@app.post("/api/accounts/{account_id}/sync")
async def sync_account_groups(account_id: int, db: AsyncSession = Depends(get_db)):
    client = await um.get_client(account_id)
    if not client: raise HTTPException(400, "العميل غير متاح")
    dialogs = await um.get_dialogs(client)
    added = 0
    for d in dialogs:
        res2 = await db.execute(select(Group).where(Group.telegram_id == d["id"]))
        if not res2.scalar_one_or_none():
            grp = Group(
                telegram_id=d["id"], title=d["title"],
                username=d.get("username"), account_id=account_id,
                member_count=d.get("members", 0),
                is_joined=True, joined_at=datetime.utcnow(),
            )
            db.add(grp)
            added += 1
    await db.commit()
    await ws_manager.broadcast({"type": "groups_synced", "account_id": account_id, "added": added})
    return {"status": "ok", "synced": len(dialogs), "added": added}


# ══════════════════════════════════════════════════════════════════════════════
# PROXIES
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/proxies")
async def list_proxies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Proxy).order_by(Proxy.id))
    return [p.to_dict() for p in result.scalars().all()]

@app.post("/api/proxies")
async def create_proxy(req: ProxyCreate, db: AsyncSession = Depends(get_db)):
    p = Proxy(**req.dict())
    db.add(p)
    await db.flush(); await db.refresh(p)
    await db.commit()
    return p.to_dict()

@app.patch("/api/proxies/{proxy_id}")
async def update_proxy(proxy_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    p = res.scalar_one_or_none()
    if not p: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(p, k): setattr(p, k, v)
    await db.commit()
    return p.to_dict()

@app.delete("/api/proxies/{proxy_id}")
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    p = res.scalar_one_or_none()
    if not p: raise HTTPException(404)
    await db.delete(p)
    await db.commit()
    return {"status": "deleted"}

@app.post("/api/proxies/{proxy_id}/check")
async def check_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    import time, httpx
    res = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    p = res.scalar_one_or_none()
    if not p: raise HTTPException(404)
    try:
        proxy_url = f"{p.scheme}://"
        if p.username: proxy_url += f"{p.username}:{p.password}@"
        proxy_url += f"{p.hostname}:{p.port}"
        t0 = time.time()
        async with httpx.AsyncClient(proxy=proxy_url, timeout=8) as c:
            await c.get("https://api.telegram.org")
        ms = int((time.time() - t0) * 1000)
        p.latency_ms = ms; p.last_check = datetime.utcnow(); p.is_active = True
        await db.commit()
        return {"status": "ok", "latency_ms": ms}
    except Exception as e:
        p.is_active = False; p.last_check = datetime.utcnow()
        await db.commit()
        return {"status": "error", "detail": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# GROUPS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/groups")
async def list_groups(
    category: Optional[str] = None,
    is_joined: Optional[bool] = None,
    is_blacklisted: Optional[bool] = None,
    min_members: Optional[int] = None,
    protection_bot: Optional[str] = None,
    activity_level: Optional[str] = None,
    account_id: Optional[int] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Group)
    conds = []
    if category:        conds.append(Group.category == category)
    if is_joined is not None: conds.append(Group.is_joined == is_joined)
    if is_blacklisted is not None: conds.append(Group.is_blacklisted == is_blacklisted)
    if min_members:     conds.append(Group.member_count >= min_members)
    if account_id:      conds.append(Group.account_id == account_id)
    if activity_level:  conds.append(Group.activity_level == activity_level)
    if protection_bot == "none":
        conds.append(Group.protection_bot == None)
    elif protection_bot:
        conds.append(Group.protection_bot == protection_bot)
    if search:          conds.append(Group.title.ilike(f"%{search}%"))
    if conds: q = q.where(and_(*conds))
    result = await db.execute(q.order_by(desc(Group.member_count)).limit(500))
    return [g.to_dict() for g in result.scalars().all()]

@app.post("/api/groups/action")
async def group_action(req: GroupActionReq, db: AsyncSession = Depends(get_db)):
    if req.action == "join":
        client = await um.get_client(req.account_id)
        if not client: raise HTTPException(400, "العميل غير متاح")
        result = await um.join_group(client, req.group_link)
        if result["status"] == "ok":
            res2 = await db.execute(select(Group).where(Group.telegram_id == str(result["chat_id"])))
            if not res2.scalar_one_or_none():
                grp = Group(
                    telegram_id=str(result["chat_id"]), title=result["title"],
                    account_id=req.account_id, is_joined=True, joined_at=datetime.utcnow(),
                )
                db.add(grp)
                await db.commit()
            await ws_manager.broadcast({"type": "group_joined", "title": result["title"]})
        return result

    if req.action == "leave":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp: raise HTTPException(404)
        client = await um.get_client(grp.account_id)
        if client:
            try: await client.leave_chat(int(grp.telegram_id))
            except: pass
        grp.is_joined = False; grp.left_at = datetime.utcnow()
        await db.commit()
        return {"status": "ok"}

    if req.action == "blacklist":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp: raise HTTPException(404)
        grp.is_blacklisted = True
        await db.commit()
        return {"status": "ok"}

    if req.action == "unblacklist":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp: raise HTTPException(404)
        grp.is_blacklisted = False
        await db.commit()
        return {"status": "ok"}

    if req.action == "favorite":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp: raise HTTPException(404)
        grp.is_favorite = not grp.is_favorite
        await db.commit()
        return {"status": "ok", "is_favorite": grp.is_favorite}

    if req.action == "update":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp: raise HTTPException(404)
        for k, v in (req.data or {}).items():
            if hasattr(grp, k): setattr(grp, k, v)
        await db.commit()
        return grp.to_dict()

    raise HTTPException(400, f"إجراء غير معروف: {req.action}")

@app.post("/api/groups/import")
async def import_groups(groups: list[dict], db: AsyncSession = Depends(get_db)):
    added = 0
    for g in groups:
        res = await db.execute(select(Group).where(Group.telegram_id == str(g.get("telegram_id", ""))))
        if not res.scalar_one_or_none():
            grp = Group(**{k: v for k, v in g.items() if hasattr(Group, k) and k != "id"})
            db.add(grp); added += 1
    await db.commit()
    return {"status": "ok", "added": added}

@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Group).where(Group.id == group_id))
    grp = res.scalar_one_or_none()
    if not grp: raise HTTPException(404)
    await db.delete(grp)
    await db.commit()
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/templates")
async def list_templates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Template).order_by(desc(Template.created_at)))
    return [t.to_dict() for t in result.scalars().all()]

@app.post("/api/templates")
async def create_template(req: TemplateCreate, db: AsyncSession = Depends(get_db)):
    tmpl = Template(name=req.name, base_content=req.base_content, variations=req.variations)
    db.add(tmpl); await db.flush(); await db.refresh(tmpl); await db.commit()
    return tmpl.to_dict()

@app.patch("/api/templates/{template_id}")
async def update_template(template_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Template).where(Template.id == template_id))
    tmpl = res.scalar_one_or_none()
    if not tmpl: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(tmpl, k): setattr(tmpl, k, v)
    await db.commit()
    return tmpl.to_dict()

@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Template).where(Template.id == template_id))
    tmpl = res.scalar_one_or_none()
    if not tmpl: raise HTTPException(404)
    await db.delete(tmpl); await db.commit()
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).order_by(desc(Campaign.created_at)))
    return [c.to_dict() for c in result.scalars().all()]

@app.post("/api/campaigns")
async def create_campaign(req: CampaignCreate, db: AsyncSession = Depends(get_db)):
    camp = Campaign(name=req.name, target_criteria=req.target_criteria,
                    schedule=req.schedule, template_ids=req.template_ids)
    db.add(camp); await db.flush(); await db.refresh(camp); await db.commit()
    return camp.to_dict()

@app.patch("/api/campaigns/{campaign_id}")
async def update_campaign(campaign_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(camp, k): setattr(camp, k, v)
    await db.commit()
    return camp.to_dict()

@app.delete("/api/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    await db.delete(camp); await db.commit()
    return {"status": "deleted"}

@app.post("/api/campaigns/{campaign_id}/run")
async def run_campaign(campaign_id: int, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    camp.status = "running"; camp.started_at = datetime.utcnow()
    await db.commit()

    async def _run():
        from agents import run_copilot
        from database import get_session
        criteria_str = json.dumps(camp.target_criteria or {}, ensure_ascii=False)
        cmd = f"شغّل الحملة '{camp.name}' وأرسل القوالب {camp.template_ids} للمجموعات: {criteria_str}"
        await run_copilot(cmd)
        db2 = await get_session()
        try:
            r2 = await db2.execute(select(Campaign).where(Campaign.id == campaign_id))
            c = r2.scalar_one_or_none()
            if c: c.status = "done"; c.finished_at = datetime.utcnow(); await db2.commit()
        finally: await db2.close()
        await ws_manager.broadcast({"type": "campaign_done", "id": campaign_id})

    background.add_task(_run)
    return {"status": "started"}

@app.post("/api/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    camp.status = "paused"; await db.commit()
    return {"status": "paused"}

@app.get("/api/campaigns/{campaign_id}/stats")
async def campaign_stats(campaign_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    total = (await db.execute(
        select(func.count(Message.id)).where(Message.campaign_id == campaign_id)
    )).scalar() or 0
    deleted = (await db.execute(
        select(func.count(Message.id)).where(
            Message.campaign_id == campaign_id, Message.status == "deleted")
    )).scalar() or 0
    return {
        "campaign": camp.to_dict(),
        "total": total, "deleted": deleted,
        "success": total - deleted,
        "success_rate": round((total - deleted) / total * 100, 1) if total else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGES
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/messages")
async def list_messages(limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Message).order_by(desc(Message.sent_at)).limit(limit))
    return [m.to_dict() for m in result.scalars().all()]

@app.post("/api/messages/send")
async def send_message_direct(req: SendMessageReq, background: BackgroundTasks):
    client = await um.get_client(req.account_id)
    if not client: raise HTTPException(400, "العميل غير متاح")

    async def _send():
        from database import get_session
        db = await get_session()
        try:
            res = await db.execute(select(Group).where(Group.id == req.group_id))
            grp = res.scalar_one_or_none()
            if not grp: return
            msg = await um.send_message(client, int(grp.telegram_id), req.message)
            if msg:
                db_msg = Message(account_id=req.account_id, group_id=req.group_id,
                                 content=req.message, telegram_msg_id=msg.id)
                db.add(db_msg); await db.commit(); await db.refresh(db_msg)
                grp.last_ad_at = datetime.utcnow(); await db.commit()
                asyncio.create_task(um.monitor_deletion(client, msg.id, int(grp.telegram_id), db_msg.id))
                await ws_manager.broadcast({"type": "message_sent", "group": grp.title})
        finally: await db.close()

    background.add_task(_send)
    return {"status": "queued"}


# ══════════════════════════════════════════════════════════════════════════════
# STATS / ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/stats/overview")
async def stats_overview(db: AsyncSession = Depends(get_db)):
    acc_total   = (await db.execute(select(func.count(Account.id)))).scalar() or 0
    acc_active  = (await db.execute(select(func.count(Account.id)).where(Account.status == "active"))).scalar() or 0
    acc_banned  = (await db.execute(select(func.count(Account.id)).where(Account.status == "banned"))).scalar() or 0
    acc_flood   = (await db.execute(select(func.count(Account.id)).where(Account.status == "flood"))).scalar() or 0
    grp_total   = (await db.execute(select(func.count(Group.id)))).scalar() or 0
    grp_joined  = (await db.execute(select(func.count(Group.id)).where(Group.is_joined == True))).scalar() or 0
    grp_banned  = (await db.execute(select(func.count(Group.id)).where(Group.is_banned == True))).scalar() or 0
    grp_black   = (await db.execute(select(func.count(Group.id)).where(Group.is_blacklisted == True))).scalar() or 0
    msg_total   = (await db.execute(select(func.count(Message.id)))).scalar() or 0
    msg_deleted = (await db.execute(select(func.count(Message.id)).where(Message.status == "deleted"))).scalar() or 0
    camp_total  = (await db.execute(select(func.count(Campaign.id)))).scalar() or 0
    camp_run    = (await db.execute(select(func.count(Campaign.id)).where(Campaign.status == "running"))).scalar() or 0
    tmpl_total  = (await db.execute(select(func.count(Template.id)))).scalar() or 0
    proxy_total = (await db.execute(select(func.count(Proxy.id)))).scalar() or 0
    proxy_ok    = (await db.execute(select(func.count(Proxy.id)).where(Proxy.is_active == True))).scalar() or 0

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    msg_today = (await db.execute(
        select(func.count(Message.id)).where(Message.sent_at >= today)
    )).scalar() or 0

    return {
        "accounts": {"total": acc_total, "active": acc_active, "banned": acc_banned, "flood": acc_flood},
        "groups":   {"total": grp_total, "joined": grp_joined, "banned": grp_banned, "blacklisted": grp_black},
        "messages": {
            "total": msg_total, "deleted": msg_deleted, "today": msg_today,
            "success_rate": round((msg_total - msg_deleted) / msg_total * 100, 1) if msg_total else 0,
        },
        "campaigns": {"total": camp_total, "running": camp_run},
        "templates": {"total": tmpl_total},
        "proxies":   {"total": proxy_total, "active": proxy_ok},
    }

@app.get("/api/stats/messages_timeline")
async def messages_timeline(days: int = 14, db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(func.date(Message.sent_at).label("date"), func.count(Message.id).label("total"),
               func.sum(case((Message.status == "deleted", 1), else_=0)).label("deleted"))
        .where(Message.sent_at >= since)
        .group_by(func.date(Message.sent_at))
        .order_by(func.date(Message.sent_at))
    )
    return [{"date": str(r.date), "total": r.total, "deleted": r.deleted or 0} for r in result.all()]

@app.get("/api/stats/groups_by_category")
async def groups_by_category(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.category, func.count(Group.id).label("count"))
        .group_by(Group.category).order_by(desc("count"))
    )
    return [{"category": r.category or "غير مصنف", "count": r.count} for r in result.all()]

@app.get("/api/stats/groups_by_activity")
async def groups_by_activity(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.activity_level, func.count(Group.id).label("count"))
        .group_by(Group.activity_level).order_by(desc("count"))
    )
    return [{"level": r.activity_level or "unknown", "count": r.count} for r in result.all()]

@app.get("/api/stats/protection_bots")
async def protection_bots(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.protection_bot, func.count(Group.id).label("count"))
        .group_by(Group.protection_bot).order_by(desc("count"))
    )
    return [{"bot": r.protection_bot or "لا يوجد", "count": r.count} for r in result.all()]

@app.get("/api/stats/top_groups")
async def top_groups(limit: int = 20, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group).where(Group.is_joined == True)
        .order_by(desc(Group.response_rate)).limit(limit)
    )
    return [g.to_dict() for g in result.scalars().all()]

@app.get("/api/stats/accounts_performance")
async def accounts_performance(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account))
    accounts = result.scalars().all()
    out = []
    for acc in accounts:
        total = (await db.execute(
            select(func.count(Message.id)).where(Message.account_id == acc.id)
        )).scalar() or 0
        deleted = (await db.execute(
            select(func.count(Message.id)).where(
                Message.account_id == acc.id, Message.status == "deleted")
        )).scalar() or 0
        groups_count = (await db.execute(
            select(func.count(Group.id)).where(Group.account_id == acc.id, Group.is_joined == True)
        )).scalar() or 0
        d = acc.to_dict()
        d.update({"messages_total": total, "messages_deleted": deleted,
                  "groups_count": groups_count,
                  "delete_rate": round(deleted / total * 100, 1) if total else 0})
        out.append(d)
    return out

@app.get("/api/stats/templates_performance")
async def templates_performance(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Template))
    templates = result.scalars().all()
    out = []
    for t in templates:
        total = (await db.execute(
            select(func.count(Message.id)).where(Message.template_id == t.id)
        )).scalar() or 0
        deleted = (await db.execute(
            select(func.count(Message.id)).where(
                Message.template_id == t.id, Message.status == "deleted")
        )).scalar() or 0
        d = t.to_dict()
        d.update({"uses": total, "deleted": deleted,
                  "delete_rate": round(deleted / total * 100, 1) if total else 0})
        out.append(d)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/audit-log")
async def get_audit_log(limit: int = 200, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit))
    return [l.to_dict() for l in result.scalars().all()]


# ══════════════════════════════════════════════════════════════════════════════
# COPILOT
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/api/copilot")
async def copilot(req: CopilotReq, background: BackgroundTasks):
    background.add_task(_copilot_task, req.command)
    return {"status": "processing", "command": req.command}

async def _copilot_task(command: str):
    from agents import run_copilot
    try:
        result = await run_copilot(command)
    except Exception as e:
        result = {"status": "error", "detail": str(e)}
    await ws_manager.broadcast({"type": "copilot_result", "data": result})


# ══════════════════════════════════════════════════════════════════════════════
# AI CONFIG
# ══════════════════════════════════════════════════════════════════════════════

def _mask_key(key: str) -> str:
    """يخفي معظم المفتاح ويظهر أول 4 أحرف فقط."""
    if not key or len(key) < 5:
        return ""
    return key[:4] + "••••••••••••••••••••"


def _detect_provider(model_str: str) -> dict:
    """يكتشف المزود من اسم الموديل."""
    m = model_str.lower()
    if "gemini" in m:
        return {"name": "Google", "avatar": "✨", "bg": "linear-gradient(135deg,#1a73e8,#0f9d58)"}
    if "groq" in m or "llama" in m or "mixtral" in m:
        return {"name": "Groq", "avatar": "⚡", "bg": "linear-gradient(135deg,#f97316,#dc2626)"}
    if "deepseek" in m:
        return {"name": "DeepSeek", "avatar": "🔵", "bg": "linear-gradient(135deg,#4f46e5,#7c3aed)"}
    if "gpt" in m or "openai" in m:
        return {"name": "OpenAI", "avatar": "🤖", "bg": "linear-gradient(135deg,#10b981,#059669)"}
    if "claude" in m or "anthropic" in m:
        return {"name": "Anthropic", "avatar": "🧡", "bg": "linear-gradient(135deg,#f97316,#ef4444)"}
    return {"name": "Custom", "avatar": "🔧", "bg": "linear-gradient(135deg,#64748b,#475569)"}


_ALL_ROLES = [
    {"key": "leader",   "label": "👑 قائد الفريق", "active": True},
    {"key": "planner",  "label": "🗺️ المخطط",      "active": True},
    {"key": "executor", "label": "⚡ المنفذ",       "active": True},
    {"key": "monitor",  "label": "👁️ المراقب",      "active": True},
    {"key": "analyzer", "label": "📊 المحلل",       "active": True},
    {"key": "engineer", "label": "🛠️ المهندس",      "active": True, "engineer": True},
]

# سجل الموديلات المضافة يدوياً من الواجهة (يُخزن في الذاكرة طوال عمر السيرفر)
_extra_models: list[dict] = []


@app.get("/api/ai/config")
async def get_ai_config(db: AsyncSession = Depends(get_db)):
    """
    يرجع قائمة نماذج AI المضبوطة في .env
    مع إحصائيات حقيقية من AuditLog.
    """
    import os
    from config import (
        GOOGLE_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY,
        OPENAI_API_KEY, ANTHROPIC_API_KEY, PREFERRED_LLM,
    )

    # ── إحصائيات من AuditLog ──────────────────────────────────────────────
    today_str = datetime.utcnow().date().isoformat()

    total_calls_q = await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.action == "copilot_command")
    )
    total_calls: int = total_calls_q.scalar() or 0

    today_calls_q = await db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "copilot_command",
            AuditLog.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        )
    )
    today_calls: int = today_calls_q.scalar() or 0

    done_q = await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.action == "copilot_done")
    )
    done_total: int = done_q.scalar() or 0

    error_q = await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.action == "copilot_error")
    )
    error_total: int = error_q.scalar() or 0

    # التوكن يُحسب فقط على الاستدعاءات الناجحة (copilot_done) وليس الكلية
    successful_calls: int = done_total

    # ── بناء قائمة الموديلات من .env ─────────────────────────────────────
    preferred = PREFERRED_LLM or "gemini/gemini-2.0-flash"
    avg_tokens = 2400

    env_models = []

    if GOOGLE_API_KEY:
        prov = _detect_provider(preferred if "gemini" in preferred else "gemini/gemini-2.0-flash")
        model_str = preferred if "gemini" in preferred else "gemini/gemini-2.0-flash"
        is_leader = "gemini" in preferred.lower()
        env_models.append({
            "id": "gemini",
            "name": "Gemini 2.0 Flash" if "2.0" in model_str else "Gemini Flash",
            "provider": f"{prov['name']} · {model_str}",
            "avatar": prov["avatar"],
            "avatarBg": prov["bg"],
            "roles": _ALL_ROLES,
            "apiKey": GOOGLE_API_KEY,
            "apiKeyMasked": _mask_key(GOOGLE_API_KEY),
            "contextWindow": 1500000,
            "tokensUsed": successful_calls * avg_tokens if is_leader else 0,
            "callsTotal": total_calls if is_leader else 0,
            "callsToday": today_calls if is_leader else 0,
            "avgTokensPerCall": avg_tokens,
            "status": "active",
            "modelString": model_str,
            "isDefault": is_leader,
            "isLeader": is_leader,
            "badge": "leader" if is_leader else "",
            "errorMsg": "",
        })

    if GROQ_API_KEY:
        prov = _detect_provider("groq/llama-3.3-70b-versatile")
        model_str = preferred if "groq" in preferred.lower() or "llama" in preferred.lower() else "groq/llama-3.3-70b-versatile"
        is_leader = "groq" in preferred.lower() or "llama" in preferred.lower()
        env_models.append({
            "id": "groq",
            "name": "Llama 3.3 70B",
            "provider": f"Groq · {model_str}",
            "avatar": prov["avatar"],
            "avatarBg": prov["bg"],
            "roles": [
                {"key": "executor", "label": "⚡ المنفذ",  "active": True},
                {"key": "monitor",  "label": "👁️ المراقب", "active": True},
            ],
            "apiKey": GROQ_API_KEY,
            "apiKeyMasked": _mask_key(GROQ_API_KEY),
            "contextWindow": 500000,
            "tokensUsed": successful_calls * avg_tokens if is_leader else 0,
            "callsTotal": total_calls if is_leader else 0,
            "callsToday": today_calls if is_leader else 0,
            "avgTokensPerCall": 1000,
            "status": "active",
            "modelString": model_str,
            "isDefault": is_leader,
            "isLeader": is_leader,
            "badge": "leader" if is_leader else "backup",
            "errorMsg": "",
        })

    if DEEPSEEK_API_KEY:
        prov = _detect_provider("deepseek/deepseek-r1")
        model_str = preferred if "deepseek" in preferred.lower() else "deepseek/deepseek-r1"
        is_leader = "deepseek" in preferred.lower()
        env_models.append({
            "id": "deepseek",
            "name": "DeepSeek R1",
            "provider": f"DeepSeek · {model_str}",
            "avatar": prov["avatar"],
            "avatarBg": prov["bg"],
            "roles": [
                {"key": "analyzer", "label": "📊 المحلل", "active": True},
            ],
            "apiKey": DEEPSEEK_API_KEY,
            "apiKeyMasked": _mask_key(DEEPSEEK_API_KEY),
            "contextWindow": 64000,
            "tokensUsed": total_calls * 1000 if is_leader else 0,
            "callsTotal": total_calls if is_leader else 0,
            "callsToday": today_calls if is_leader else 0,
            "avgTokensPerCall": 1000,
            "status": "active",
            "modelString": model_str,
            "isDefault": is_leader,
            "isLeader": is_leader,
            "badge": "leader" if is_leader else "backup",
            "errorMsg": "",
        })

    if OPENAI_API_KEY:
        prov = _detect_provider("openai/gpt-4o")
        model_str = preferred if "gpt" in preferred.lower() or "openai" in preferred.lower() else "openai/gpt-4o"
        is_leader = "gpt" in preferred.lower() or "openai" in preferred.lower()
        env_models.append({
            "id": "openai",
            "name": "GPT-4o",
            "provider": f"OpenAI · {model_str}",
            "avatar": prov["avatar"],
            "avatarBg": prov["bg"],
            "roles": _ALL_ROLES,
            "apiKey": OPENAI_API_KEY,
            "apiKeyMasked": _mask_key(OPENAI_API_KEY),
            "contextWindow": 128000,
            "tokensUsed": successful_calls * avg_tokens if is_leader else 0,
            "callsTotal": total_calls if is_leader else 0,
            "callsToday": today_calls if is_leader else 0,
            "avgTokensPerCall": avg_tokens,
            "status": "active",
            "modelString": model_str,
            "isDefault": is_leader,
            "isLeader": is_leader,
            "badge": "leader" if is_leader else "",
            "errorMsg": "",
        })

    if ANTHROPIC_API_KEY:
        prov = _detect_provider("claude")
        model_str = preferred if "claude" in preferred.lower() or "anthropic" in preferred.lower() else "anthropic/claude-3-5-sonnet-20241022"
        is_leader = "claude" in preferred.lower() or "anthropic" in preferred.lower()
        env_models.append({
            "id": "anthropic",
            "name": "Claude 3.5 Sonnet",
            "provider": f"Anthropic · {model_str}",
            "avatar": prov["avatar"],
            "avatarBg": prov["bg"],
            "roles": _ALL_ROLES,
            "apiKey": ANTHROPIC_API_KEY,
            "apiKeyMasked": _mask_key(ANTHROPIC_API_KEY),
            "contextWindow": 200000,
            "tokensUsed": successful_calls * avg_tokens if is_leader else 0,
            "callsTotal": total_calls if is_leader else 0,
            "callsToday": today_calls if is_leader else 0,
            "avgTokensPerCall": avg_tokens,
            "status": "active",
            "modelString": model_str,
            "isDefault": is_leader,
            "isLeader": is_leader,
            "badge": "leader" if is_leader else "",
            "errorMsg": "",
        })

    # إذا لم يكن أي موديل هو القائد (PREFERRED_LLM غريب)، اجعل الأول قائداً
    if env_models and not any(m["isLeader"] for m in env_models):
        env_models[0]["isLeader"] = True
        env_models[0]["isDefault"] = True
        env_models[0]["badge"] = "leader"

    all_models = env_models + _extra_models

    return {
        "models": all_models,
        "preferred_llm": preferred,
        "stats": {
            "total_calls": total_calls,
            "today_calls": today_calls,
            "done_total": done_total,
            "error_total": error_total,
            "success_rate": round(done_total / total_calls * 100, 1) if total_calls else 0,
        },
    }


class AIModelSaveReq(BaseModel):
    model_string: str
    api_key: str
    role: str = "backup"
    name: str = ""


@app.post("/api/ai/models")
async def save_ai_model(req: AIModelSaveReq):
    """يضيف موديل جديد من الواجهة (يُخزن في الذاكرة حتى إعادة التشغيل)."""
    import os, re
    if not req.model_string or not req.api_key:
        raise HTTPException(400, "model_string و api_key مطلوبان")

    prov = _detect_provider(req.model_string)
    model_id = "model-" + re.sub(r"[^a-z0-9]", "-", req.model_string.lower())[:20]

    # إزالة إذا كان موجوداً بنفس الـ id
    global _extra_models
    _extra_models = [m for m in _extra_models if m["id"] != model_id]

    _extra_models.append({
        "id": model_id,
        "name": req.name or req.model_string,
        "provider": f"{prov['name']} · {req.model_string}",
        "avatar": prov["avatar"],
        "avatarBg": prov["bg"],
        "roles": [],
        "apiKey": req.api_key,
        "apiKeyMasked": _mask_key(req.api_key),
        "contextWindow": 0,
        "tokensUsed": 0,
        "callsTotal": 0,
        "callsToday": 0,
        "avgTokensPerCall": 1000,
        "status": "active",
        "modelString": req.model_string,
        "isDefault": False,
        "isLeader": False,
        "badge": req.role if req.role in ("leader", "backup") else "",
        "errorMsg": "",
    })
    return {"status": "ok", "id": model_id}


@app.post("/api/ai/set-preferred")
async def set_preferred_model(data: dict):
    """يغير PREFERRED_LLM في ملف .env الحالي."""
    model = data.get("model_string", "").strip()
    if not model:
        raise HTTPException(400, "model_string مطلوب")

    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        raise HTTPException(404, "ملف .env غير موجود")

    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.startswith("PREFERRED_LLM="):
            new_lines.append(f"PREFERRED_LLM={model}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"PREFERRED_LLM={model}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # حدّث os.environ مباشرة حتى يراه agents.py فوراً بدون إعادة تشغيل
    import os as _os
    _os.environ["PREFERRED_LLM"] = model

    return {"status": "ok", "preferred_llm": model}


# ══════════════════════════════════════════════════════════════════════════════
# STATIC (SPA)
# ══════════════════════════════════════════════════════════════════════════════
STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    @app.get("/")
    async def serve_root():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        fp = STATIC_DIR / full_path
        if fp.exists() and fp.is_file():
            return FileResponse(fp)
        return FileResponse(STATIC_DIR / "index.html")
