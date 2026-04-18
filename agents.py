"""
agents.py – وكلاء CrewAI الخمسة مع أدوات LangChain
يتطلب: crewai, langchain, chromadb, openai
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

from langchain.tools import tool
from crewai import Agent, Task, Crew, Process

try:
    import chromadb
    from chromadb.config import Settings
    from config import CHROMA_PERSIST_DIR
    _chroma_client = chromadb.PersistentClient(
        path=CHROMA_PERSIST_DIR,
        settings=Settings(anonymized_telemetry=False),
    )
    _memory_col = _chroma_client.get_or_create_collection("agent_memory")
    CHROMA_AVAILABLE = True
except Exception as _e:
    CHROMA_AVAILABLE = False
    _memory_col = None
    logging.warning(f"ChromaDB غير متاح: {_e}")

from config import PREFERRED_LLM, OPENAI_API_KEY
from database import get_session
from models import Account, Group, Message, Template, Campaign, AuditLog

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# أدوات LangChain المشتركة
# ══════════════════════════════════════════════════════════════════════════════

def _run_async(coro):
    """تشغيل coroutine من كود متزامن."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@tool("query_groups")
def query_groups(criteria: str) -> str:
    """
    يستعلم عن المجموعات من قاعدة البيانات بناءً على معايير JSON.
    مثال: {"category": "تعليمي", "min_members": 500, "is_joined": true}
    """
    async def _query():
        try:
            params = json.loads(criteria) if isinstance(criteria, str) else criteria
        except Exception:
            params = {}

        db = await get_session()
        try:
            from sqlalchemy import select, and_
            q = select(Group)
            conditions = []
            if params.get("category"):
                conditions.append(Group.category == params["category"])
            if params.get("is_joined") is not None:
                conditions.append(Group.is_joined == params["is_joined"])
            if params.get("min_members"):
                conditions.append(Group.member_count >= params["min_members"])
            if params.get("language"):
                conditions.append(Group.language == params["language"])
            if params.get("protection_bot") is not None:
                if params["protection_bot"] is False:
                    conditions.append(Group.protection_bot == None)
                else:
                    conditions.append(Group.protection_bot == params["protection_bot"])
            if conditions:
                q = q.where(and_(*conditions))
            result = await db.execute(q.limit(100))
            groups = result.scalars().all()
            return json.dumps([g.to_dict() for g in groups], ensure_ascii=False)
        finally:
            await db.close()

    return _run_async(_query())


@tool("send_to_group")
def send_to_group(payload: str) -> str:
    """
    يرسل رسالة لمجموعة. payload: {"account_id":1,"group_id":1,"message":"نص"}
    """
    async def _send():
        import userbot_manager as um
        try:
            data = json.loads(payload)
        except Exception:
            return json.dumps({"status": "error", "detail": "payload غير صالح"})

        account_id = data.get("account_id")
        group_id   = data.get("group_id")
        text       = data.get("message", "")

        client = await um.get_client(account_id)
        if not client:
            return json.dumps({"status": "error", "detail": "العميل غير متاح"})

        # جلب telegram_id المجموعة
        db = await get_session()
        try:
            from sqlalchemy import select
            res = await db.execute(select(Group).where(Group.id == group_id))
            grp = res.scalar_one_or_none()
            if not grp:
                return json.dumps({"status": "error", "detail": "مجموعة غير موجودة"})
            chat_id = int(grp.telegram_id)
        finally:
            await db.close()

        msg = await um.send_message(client, chat_id, text)
        if not msg:
            return json.dumps({"status": "error", "detail": "فشل الإرسال"})

        # تسجيل الرسالة
        db = await get_session()
        try:
            db_msg = Message(
                account_id=account_id,
                group_id=group_id,
                content=text,
                telegram_msg_id=msg.id,
                sent_at=datetime.utcnow(),
                status="sent",
            )
            db.add(db_msg)
            await db.commit()
            await db.refresh(db_msg)
            msg_db_id = db_msg.id
        finally:
            await db.close()

        # تشغيل مراقبة الحذف في الخلفية
        asyncio.create_task(
            um.monitor_deletion(client, msg.id, chat_id, msg_db_id)
        )
        return json.dumps({"status": "ok", "message_id": msg_db_id})

    return _run_async(_send())


@tool("check_deletion_status")
def check_deletion_status(message_id: str) -> str:
    """يتحقق من حالة رسالة (مرسلة/محذوفة). message_id: معرف قاعدة البيانات."""
    async def _check():
        db = await get_session()
        try:
            from sqlalchemy import select
            res = await db.execute(select(Message).where(Message.id == int(message_id)))
            msg = res.scalar_one_or_none()
            if not msg:
                return json.dumps({"error": "رسالة غير موجودة"})
            return json.dumps(msg.to_dict(), ensure_ascii=False)
        finally:
            await db.close()
    return _run_async(_check())


@tool("store_memory")
def store_memory(payload: str) -> str:
    """
    يخزن ذاكرة في ChromaDB. payload: {"id":"unique_id","text":"نص","metadata":{}}
    """
    if not CHROMA_AVAILABLE:
        return "ChromaDB غير متاح"
    try:
        data = json.loads(payload)
        _memory_col.upsert(
            ids=[data["id"]],
            documents=[data["text"]],
            metadatas=[data.get("metadata", {})],
        )
        return "تم حفظ الذاكرة"
    except Exception as e:
        return f"خطأ: {e}"


@tool("recall_memory")
def recall_memory(query: str) -> str:
    """يسترجع ذاكرة ذات صلة من ChromaDB بناءً على النص."""
    if not CHROMA_AVAILABLE:
        return "ChromaDB غير متاح"
    try:
        results = _memory_col.query(query_texts=[query], n_results=5)
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        combined = [{"text": d, "meta": m} for d, m in zip(docs, metas)]
        return json.dumps(combined, ensure_ascii=False)
    except Exception as e:
        return f"خطأ: {e}"


@tool("get_campaign_stats")
def get_campaign_stats(campaign_id: str) -> str:
    """يجلب إحصائيات حملة معينة."""
    async def _stats():
        db = await get_session()
        try:
            from sqlalchemy import select, func
            res = await db.execute(select(Campaign).where(Campaign.id == int(campaign_id)))
            camp = res.scalar_one_or_none()
            if not camp:
                return json.dumps({"error": "حملة غير موجودة"})

            # إحصائيات الرسائل
            total_q  = select(func.count(Message.id)).where(Message.campaign_id == int(campaign_id))
            deleted_q = select(func.count(Message.id)).where(
                Message.campaign_id == int(campaign_id),
                Message.status == "deleted"
            )
            total   = (await db.execute(total_q)).scalar() or 0
            deleted = (await db.execute(deleted_q)).scalar() or 0
            return json.dumps({
                "campaign": camp.to_dict(),
                "total_messages": total,
                "deleted": deleted,
                "success_rate": round((total - deleted) / total * 100, 1) if total else 0,
            }, ensure_ascii=False)
        finally:
            await db.close()
    return _run_async(_stats())


async def _log_audit(agent: str, action: str, details: dict):
    db = await get_session()
    try:
        log = AuditLog(agent=agent, action=action, details=details)
        db.add(log)
        await db.commit()
    finally:
        await db.close()


# ══════════════════════════════════════════════════════════════════════════════
# تعريف الوكلاء
# ══════════════════════════════════════════════════════════════════════════════

_llm_config = {"model": PREFERRED_LLM}
if OPENAI_API_KEY:
    _llm_config["api_key"] = OPENAI_API_KEY


def build_orchestrator() -> Agent:
    return Agent(
        role="قائد الفريق",
        goal="فهم أمر المستخدم وتوزيع المهام على الوكلاء المناسبين وضمان تنفيذها بنجاح.",
        backstory=(
            "أنت مدير ذكي يفهم سياق الأعمال ويحوّل الأوامر الطبيعية إلى خطط قابلة للتنفيذ. "
            "تتميز بالإيجاز والدقة."
        ),
        tools=[recall_memory],
        llm=_llm_config,
        verbose=False,
    )


def build_planner() -> Agent:
    return Agent(
        role="المخطط الاستراتيجي",
        goal=(
            "تحليل الأمر واستعلام قاعدة البيانات لاقتراح قائمة مجموعات مستهدفة، "
            "اختيار الصيغة الأنسب، وتحديد جدول الإرسال."
        ),
        backstory=(
            "خبير في تحليل البيانات وتخطيط حملات التسويق عبر تيليجرام. "
            "يراعي معدلات النجاح السابقة ومخاطر الحظر."
        ),
        tools=[query_groups, recall_memory],
        llm=_llm_config,
        verbose=False,
    )


def build_executor() -> Agent:
    return Agent(
        role="المنفذ الميداني",
        goal="تنفيذ خطة المخطط بإرسال الرسائل عبر الحسابات المناسبة مع مراعاة التأخيرات.",
        backstory=(
            "متخصص في تشغيل عمليات تيليجرام. يعرف متى يُرسل ومن أي حساب "
            "لتجنب الحظر وتحقيق أعلى وصول."
        ),
        tools=[send_to_group, query_groups],
        llm=_llm_config,
        verbose=False,
    )


def build_monitor() -> Agent:
    return Agent(
        role="المراقب الذكي",
        goal="مراقبة الرسائل المرسلة والكشف الفوري عن حذفها وتصنيف بوتات الحماية.",
        backstory=(
            "عين ساهرة لا تغفل. يرصد كل رسالة ويسجل وقت الحذف وسببه، "
            "ويبني قاعدة معرفة عن بوتات الحماية في كل مجموعة."
        ),
        tools=[check_deletion_status, store_memory],
        llm=_llm_config,
        verbose=False,
    )


def build_analyzer() -> Agent:
    return Agent(
        role="المحلل والمحسّن",
        goal=(
            "تحليل نتائج الحملات، تحديث تصنيف المجموعات، "
            "واقتراح تحسينات للصيغ والجداول الزمنية."
        ),
        backstory=(
            "عالم بيانات يحوّل الأرقام إلى رؤى قابلة للتطبيق. "
            "يتعلم من كل حملة ويحسّن الأداء باستمرار."
        ),
        tools=[get_campaign_stats, store_memory, recall_memory, query_groups],
        llm=_llm_config,
        verbose=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# نقطة الدخول الرئيسية للكوبيلوت
# ══════════════════════════════════════════════════════════════════════════════

async def run_copilot(command: str) -> dict:
    """
    يستقبل أمراً نصياً ويشغّل طاقم الوكلاء لتنفيذه.
    يعيد {"result": "...", "log": [...]}
    """
    await _log_audit("Orchestrator", "copilot_command", {"command": command})

    orchestrator = build_orchestrator()
    planner      = build_planner()
    executor     = build_executor()
    monitor      = build_monitor()
    analyzer     = build_analyzer()

    task_orchestrate = Task(
        description=(
            f"الأمر المستلم: «{command}»\n"
            "حدّد نوع العملية (إرسال/تحليل/تقرير/انضمام) وضع خطة مختصرة."
        ),
        expected_output="خطة مختصرة بنقاط تتضمن: نوع العملية، المجموعات المستهدفة، الصيغة.",
        agent=orchestrator,
    )

    task_plan = Task(
        description=(
            "بناءً على الخطة المحددة، استعلم عن المجموعات المناسبة من قاعدة البيانات "
            "وحدد قائمة الإرسال والجدول الزمني."
        ),
        expected_output="قائمة JSON بمعرفات المجموعات والحسابات والصيغ المختارة.",
        agent=planner,
        context=[task_orchestrate],
    )

    task_execute = Task(
        description=(
            "نفّذ خطة الإرسال: أرسل الرسائل للمجموعات المحددة باستخدام أداة send_to_group. "
            "الزم بالتأخيرات البشرية."
        ),
        expected_output="تقرير بعدد الرسائل المرسلة وأي أخطاء.",
        agent=executor,
        context=[task_plan],
    )

    task_monitor = Task(
        description=(
            "راقب الرسائل المرسلة وتحقق من حالة حذفها. "
            "سجّل أي حذف في الذاكرة مع تصنيف بوت الحماية."
        ),
        expected_output="تقرير بعدد الرسائل المحذوفة وتصنيف المجموعات.",
        agent=monitor,
        context=[task_execute],
    )

    task_analyze = Task(
        description=(
            "بعد اكتمال الدورة، حلّل النتائج وقدّم توصيات لتحسين الحملة القادمة. "
            "خزّن الرؤى في ChromaDB."
        ),
        expected_output="ملخص تحليلي مع توصيات قابلة للتطبيق.",
        agent=analyzer,
        context=[task_monitor],
    )

    crew = Crew(
        agents=[orchestrator, planner, executor, monitor, analyzer],
        tasks=[task_orchestrate, task_plan, task_execute, task_monitor, task_analyze],
        process=Process.sequential,
        verbose=False,
    )

    try:
        result = await asyncio.to_thread(crew.kickoff)
        await _log_audit("Analyzer", "copilot_done", {"result": str(result)[:500]})
        return {"status": "ok", "result": str(result)}
    except Exception as e:
        logger.error(f"خطأ في تشغيل الوكلاء: {e}")
        await _log_audit("Orchestrator", "copilot_error", {"error": str(e)})
        return {"status": "error", "detail": str(e)}
