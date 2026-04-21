"""
api.py – FastAPI REST API + WebSocket + Static Files
الإصلاحات المُطبَّقة:
  1. حماية API بمفتاح سري (API Key)
  2. تتبع مهام الخلفية لمنع تسرب الذاكرة
  3. إصلاح N+1 queries في إحصائيات الحسابات والقوالب
  4. إلغاء الحسابات المعلقة تلقائياً بعد 10 دقائق
  5. استبدال on_event القديم بـ lifespan الحديث
  6. نقل imports للأعلى
"""
import asyncio
import json
import logging
import time
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc, or_, case

from database import get_db, init_db
from models import Account, Group, Message, Campaign, Template, AuditLog, Proxy
import userbot_manager as um
from config import SECRET_KEY

logger = logging.getLogger(__name__)

# ─── الحماية بمفتاح API ────────────────────────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(api_key: Optional[str] = Security(_api_key_header)):
    if not api_key or api_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="مفتاح API غير صحيح أو مفقود")
    return api_key

# ─── تتبع مهام الخلفية ────────────────────────────────────────────────────────
_background_tasks: set[asyncio.Task] = set()

def create_monitored_task(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task

# ─── تنظيف الحسابات المعلقة ───────────────────────────────────────────────────
async def _cleanup_pending_auth():
    while True:
        await asyncio.sleep(60)
        now = datetime.utcnow()
        expired = [
            phone for phone, data in list(um._pending_auth.items())
            if (now - data.get("created_at", now)).total_seconds() > 600
        ]
        for phone in expired:
            pending = um._pending_auth.pop(phone, None)
            if pending:
                try:
                    await pending["client"].disconnect()
                except Exception:
                    pass
                logger.info(f"تم إلغاء جلسة منتهية: {phone}")

# ─── Lifespan الحديث ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    create_monitored_task(_safe_start_clients())
    create_monitored_task(_cleanup_pending_auth())
    logger.info("النظام بدأ")
    yield
    try:
        await um.stop_clients()
    except Exception as e:
        logger.warning(f"stop_clients فشل: {e}")

app = FastAPI(title="TG AI System", version="2.0.0", docs_url="/docs", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


async def _safe_start_clients():
    try:
        await um.start_clients()
    except Exception as e:
        logger.warning(f"start_clients فشل: {e}")


@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(func.count(Account.id)))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "active_clients": len(um._clients),
        "pending_auth": len(um._pending_auth),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await asyncio.sleep(25)
            await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


@app.get("/api/accounts", dependencies=[Depends(require_api_key)])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).order_by(desc(Account.created_at)))
    return [a.to_dict() for a in result.scalars().all()]

@app.post("/api/accounts/add", dependencies=[Depends(require_api_key)])
async def add_account(req: AddAccountReq, db: AsyncSession = Depends(get_db)):
    proxy = req.proxy
    if req.proxy_id and not proxy:
        res = await db.execute(select(Proxy).where(Proxy.id == req.proxy_id))
        p = res.scalar_one_or_none()
        if p:
            proxy = {"scheme": p.scheme, "hostname": p.hostname, "port": p.port,
                     "username": p.username, "password": p.password}
    return await um.add_account(req.phone, proxy)

@app.post("/api/accounts/verify", dependencies=[Depends(require_api_key)])
async def verify_code(req: VerifyCodeReq):
    result = await um.verify_code(req.phone, req.code, req.password)
    if result.get("status") == "ok":
        await ws_manager.broadcast({"type": "account_added", "phone": req.phone})
    return result

@app.patch("/api/accounts/{account_id}", dependencies=[Depends(require_api_key)])
async def update_account(account_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id == account_id))
    acc = res.scalar_one_or_none()
    if not acc: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(acc, k): setattr(acc, k, v)
    await db.commit()
    return acc.to_dict()

@app.delete("/api/accounts/{account_id}", dependencies=[Depends(require_api_key)])
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id == account_id))
    acc = res.scalar_one_or_none()
    if not acc: raise HTTPException(404)
    await db.delete(acc)
    await db.commit()
    return {"status": "deleted"}

@app.post("/api/accounts/{account_id}/check", dependencies=[Depends(require_api_key)])
async def check_account(account_id: int):
    client = await um.get_client(account_id)
    if client: return {"status": "ok", "connected": True}
    return {"status": "error", "connected": False}

@app.post("/api/accounts/{account_id}/sync", dependencies=[Depends(require_api_key)])
async def sync_account_groups(account_id: int, db: AsyncSession = Depends(get_db)):
    client = await um.get_client(account_id)
    if not client: raise HTTPException(400, "العميل غير متاح")
    dialogs = await um.get_dialogs(client)
    added = 0
    for d in dialogs:
        res2 = await db.execute(select(Group).where(Group.telegram_id == d["id"]))
        if not res2.scalar_one_or_none():
            grp = Group(telegram_id=d["id"], title=d["title"], username=d.get("username"),
                        account_id=account_id, member_count=d.get("members", 0),
                        is_joined=True, joined_at=datetime.utcnow())
            db.add(grp)
            added += 1
    await db.commit()
    await ws_manager.broadcast({"type": "groups_synced", "account_id": account_id, "added": added})
    return {"status": "ok", "synced": len(dialogs), "added": added}


@app.get("/api/proxies", dependencies=[Depends(require_api_key)])
async def list_proxies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Proxy).order_by(Proxy.id))
    return [p.to_dict() for p in result.scalars().all()]

@app.post("/api/proxies", dependencies=[Depends(require_api_key)])
async def create_proxy(req: ProxyCreate, db: AsyncSession = Depends(get_db)):
    p = Proxy(**req.dict())
    db.add(p)
    await db.flush(); await db.refresh(p); await db.commit()
    return p.to_dict()

@app.patch("/api/proxies/{proxy_id}", dependencies=[Depends(require_api_key)])
async def update_proxy(proxy_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    p = res.scalar_one_or_none()
    if not p: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(p, k): setattr(p, k, v)
    await db.commit()
    return p.to_dict()

@app.delete("/api/proxies/{proxy_id}", dependencies=[Depends(require_api_key)])
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    p = res.scalar_one_or_none()
    if not p: raise HTTPException(404)
    await db.delete(p); await db.commit()
    return {"status": "deleted"}

@app.post("/api/proxies/{proxy_id}/check", dependencies=[Depends(require_api_key)])
async def check_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
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


@app.get("/api/groups", dependencies=[Depends(require_api_key)])
async def list_groups(
    category: Optional[str] = None, is_joined: Optional[bool] = None,
    is_blacklisted: Optional[bool] = None, min_members: Optional[int] = None,
    protection_bot: Optional[str] = None, activity_level: Optional[str] = None,
    account_id: Optional[int] = None, search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Group)
    conds = []
    if category:              conds.append(Group.category == category)
    if is_joined is not None: conds.append(Group.is_joined == is_joined)
    if is_blacklisted is not None: conds.append(Group.is_blacklisted == is_blacklisted)
    if min_members:           conds.append(Group.member_count >= min_members)
    if account_id:            conds.append(Group.account_id == account_id)
    if activity_level:        conds.append(Group.activity_level == activity_level)
    if protection_bot == "none": conds.append(Group.protection_bot == None)
    elif protection_bot:      conds.append(Group.protection_bot == protection_bot)
    if search:                conds.append(Group.title.ilike(f"%{search}%"))
    if conds: q = q.where(and_(*conds))
    result = await db.execute(q.order_by(desc(Group.member_count)).limit(500))
    return [g.to_dict() for g in result.scalars().all()]

@app.post("/api/groups/action", dependencies=[Depends(require_api_key)])
async def group_action(req: GroupActionReq, db: AsyncSession = Depends(get_db)):
    if req.action == "join":
        client = await um.get_client(req.account_id)
        if not client: raise HTTPException(400, "العميل غير متاح")
        result = await um.join_group(client, req.group_link)
        if result["status"] == "ok":
            res2 = await db.execute(select(Group).where(Group.telegram_id == str(result["chat_id"])))
            if not res2.scalar_one_or_none():
                grp = Group(telegram_id=str(result["chat_id"]), title=result["title"],
                            account_id=req.account_id, is_joined=True, joined_at=datetime.utcnow())
                db.add(grp); await db.commit()
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
        grp.is_blacklisted = True; await db.commit()
        return {"status": "ok"}

    if req.action == "unblacklist":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp: raise HTTPException(404)
        grp.is_blacklisted = False; await db.commit()
        return {"status": "ok"}

    if req.action == "favorite":
        res = await db.execute(select(Group).where(Group.id == req.group_id))
        grp = res.scalar_one_or_none()
        if not grp: raise HTTPException(404)
        grp.is_favorite = not grp.is_favorite; await db.commit()
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

@app.post("/api/groups/import", dependencies=[Depends(require_api_key)])
async def import_groups(groups: list[dict], db: AsyncSession = Depends(get_db)):
    added = 0
    for g in groups:
        res = await db.execute(select(Group).where(Group.telegram_id == str(g.get("telegram_id", ""))))
        if not res.scalar_one_or_none():
            grp = Group(**{k: v for k, v in g.items() if hasattr(Group, k) and k != "id"})
            db.add(grp); added += 1
    await db.commit()
    return {"status": "ok", "added": added}

@app.delete("/api/groups/{group_id}", dependencies=[Depends(require_api_key)])
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Group).where(Group.id == group_id))
    grp = res.scalar_one_or_none()
    if not grp: raise HTTPException(404)
    await db.delete(grp); await db.commit()
    return {"status": "deleted"}


@app.get("/api/templates", dependencies=[Depends(require_api_key)])
async def list_templates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Template).order_by(desc(Template.created_at)))
    return [t.to_dict() for t in result.scalars().all()]

@app.post("/api/templates", dependencies=[Depends(require_api_key)])
async def create_template(req: TemplateCreate, db: AsyncSession = Depends(get_db)):
    tmpl = Template(name=req.name, base_content=req.base_content, variations=req.variations)
    db.add(tmpl); await db.flush(); await db.refresh(tmpl); await db.commit()
    return tmpl.to_dict()

@app.patch("/api/templates/{template_id}", dependencies=[Depends(require_api_key)])
async def update_template(template_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Template).where(Template.id == template_id))
    tmpl = res.scalar_one_or_none()
    if not tmpl: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(tmpl, k): setattr(tmpl, k, v)
    await db.commit()
    return tmpl.to_dict()

@app.delete("/api/templates/{template_id}", dependencies=[Depends(require_api_key)])
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Template).where(Template.id == template_id))
    tmpl = res.scalar_one_or_none()
    if not tmpl: raise HTTPException(404)
    await db.delete(tmpl); await db.commit()
    return {"status": "deleted"}


@app.get("/api/campaigns", dependencies=[Depends(require_api_key)])
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).order_by(desc(Campaign.created_at)))
    return [c.to_dict() for c in result.scalars().all()]

@app.post("/api/campaigns", dependencies=[Depends(require_api_key)])
async def create_campaign(req: CampaignCreate, db: AsyncSession = Depends(get_db)):
    camp = Campaign(name=req.name, target_criteria=req.target_criteria,
                    schedule=req.schedule, template_ids=req.template_ids)
    db.add(camp); await db.flush(); await db.refresh(camp); await db.commit()
    return camp.to_dict()

@app.patch("/api/campaigns/{campaign_id}", dependencies=[Depends(require_api_key)])
async def update_campaign(campaign_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(camp, k): setattr(camp, k, v)
    await db.commit()
    return camp.to_dict()

@app.delete("/api/campaigns/{campaign_id}", dependencies=[Depends(require_api_key)])
async def delete_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    await db.delete(camp); await db.commit()
    return {"status": "deleted"}

@app.post("/api/campaigns/{campaign_id}/run", dependencies=[Depends(require_api_key)])
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

@app.post("/api/campaigns/{campaign_id}/pause", dependencies=[Depends(require_api_key)])
async def pause_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    camp = res.scalar_one_or_none()
    if not camp: raise HTTPException(404)
    camp.status = "paused"; await db.commit()
    return {"status": "paused"}

@app.get("/api/campaigns/{campaign_id}/stats", dependencies=[Depends(require_api_key)])
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
    return {"campaign": camp.to_dict(), "total": total, "deleted": deleted,
            "success": total - deleted,
            "success_rate": round((total - deleted) / total * 100, 1) if total else 0}


@app.get("/api/messages", dependencies=[Depends(require_api_key)])
async def list_messages(limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Message).order_by(desc(Message.sent_at)).limit(limit))
    return [m.to_dict() for m in result.scalars().all()]

@app.post("/api/messages/send", dependencies=[Depends(require_api_key)])
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
                create_monitored_task(um.monitor_deletion(client, msg.id, int(grp.telegram_id), db_msg.id))
                await ws_manager.broadcast({"type": "message_sent", "group": grp.title})
        finally: await db.close()

    background.add_task(_send)
    return {"status": "queued"}


@app.get("/api/stats/overview", dependencies=[Depends(require_api_key)])
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
        "messages": {"total": msg_total, "deleted": msg_deleted, "today": msg_today,
                     "success_rate": round((msg_total - msg_deleted) / msg_total * 100, 1) if msg_total else 0},
        "campaigns": {"total": camp_total, "running": camp_run},
        "templates": {"total": tmpl_total},
        "proxies":   {"total": proxy_total, "active": proxy_ok},
    }

@app.get("/api/stats/messages_timeline", dependencies=[Depends(require_api_key)])
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

@app.get("/api/stats/groups_by_category", dependencies=[Depends(require_api_key)])
async def groups_by_category(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.category, func.count(Group.id).label("count"))
        .group_by(Group.category).order_by(desc("count"))
    )
    return [{"category": r.category or "غير مصنف", "count": r.count} for r in result.all()]

@app.get("/api/stats/groups_by_activity", dependencies=[Depends(require_api_key)])
async def groups_by_activity(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.activity_level, func.count(Group.id).label("count"))
        .group_by(Group.activity_level).order_by(desc("count"))
    )
    return [{"level": r.activity_level or "unknown", "count": r.count} for r in result.all()]

@app.get("/api/stats/protection_bots", dependencies=[Depends(require_api_key)])
async def protection_bots(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group.protection_bot, func.count(Group.id).label("count"))
        .group_by(Group.protection_bot).order_by(desc("count"))
    )
    return [{"bot": r.protection_bot or "لا يوجد", "count": r.count} for r in result.all()]

@app.get("/api/stats/top_groups", dependencies=[Depends(require_api_key)])
async def top_groups(limit: int = 20, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Group).where(Group.is_joined == True)
        .order_by(desc(Group.response_rate)).limit(limit)
    )
    return [g.to_dict() for g in result.scalars().all()]


# إصلاح #3: إحصائيات الحسابات — query واحد لكل حساب بدل N+1
@app.get("/api/stats/accounts_performance", dependencies=[Depends(require_api_key)])
async def accounts_performance(db: AsyncSession = Depends(get_db)):
    acc_result = await db.execute(select(Account))
    accounts = acc_result.scalars().all()
    if not accounts:
        return []

    totals_q = await db.execute(
        select(Message.account_id, func.count(Message.id).label("total"))
        .group_by(Message.account_id)
    )
    totals = {r.account_id: r.total for r in totals_q.all()}

    deleted_q = await db.execute(
        select(Message.account_id, func.count(Message.id).label("deleted"))
        .where(Message.status == "deleted")
        .group_by(Message.account_id)
    )
    deleteds = {r.account_id: r.deleted for r in deleted_q.all()}

    groups_q = await db.execute(
        select(Group.account_id, func.count(Group.id).label("cnt"))
        .where(Group.is_joined == True)
        .group_by(Group.account_id)
    )
    groups_counts = {r.account_id: r.cnt for r in groups_q.all()}

    out = []
    for acc in accounts:
        total   = totals.get(acc.id, 0)
        deleted = deleteds.get(acc.id, 0)
        d = acc.to_dict()
        d.update({"messages_total": total, "messages_deleted": deleted,
                  "groups_count": groups_counts.get(acc.id, 0),
                  "delete_rate": round(deleted / total * 100, 1) if total else 0})
        out.append(d)
    return out


# إصلاح #3: إحصائيات القوالب — query واحد بدل N+1
@app.get("/api/stats/templates_performance", dependencies=[Depends(require_api_key)])
async def templates_performance(db: AsyncSession = Depends(get_db)):
    tmpl_result = await db.execute(select(Template))
    templates = tmpl_result.scalars().all()
    if not templates:
        return []

    totals_q = await db.execute(
        select(Message.template_id, func.count(Message.id).label("total"))
        .group_by(Message.template_id)
    )
    totals = {r.template_id: r.total for r in totals_q.all()}

    deleted_q = await db.execute(
        select(Message.template_id, func.count(Message.id).label("deleted"))
        .where(Message.status == "deleted")
        .group_by(Message.template_id)
    )
    deleteds = {r.template_id: r.deleted for r in deleted_q.all()}

    out = []
    for t in templates:
        total   = totals.get(t.id, 0)
        deleted = deleteds.get(t.id, 0)
        d = t.to_dict()
        d.update({"uses": total, "deleted": deleted,
                  "delete_rate": round(deleted / total * 100, 1) if total else 0})
        out.append(d)
    return out


@app.get("/api/audit-log", dependencies=[Depends(require_api_key)])
async def get_audit_log(limit: int = 200, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit))
    return [l.to_dict() for l in result.scalars().all()]


@app.post("/api/copilot", dependencies=[Depends(require_api_key)])
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
