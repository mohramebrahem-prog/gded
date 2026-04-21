"""
agents.py – وكلاء CrewAI الخمسة مع أدوات LangChain
يتطلب: crewai, langchain, chromadb, openai
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─── ChromaDB (اختياري) ───────────────────────────────────────────────────────
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
    logger.warning(f"ChromaDB غير متاح (غير مؤثر على التشغيل): {_e}")

# ─── CrewAI (اختياري) — لا يُستورد عند بدء التشغيل ───────────────────────────
CREWAI_AVAILABLE = False
try:
    from crewai.tools import tool as crewai_tool
    from crewai import Agent, Task, Crew, Process, LLM
    CREWAI_AVAILABLE = True
except Exception as _e:
    logger.warning(f"CrewAI غير متاح (غير مؤثر على التشغيل): {_e}")
    def crewai_tool(name):
        def decorator(fn):
            return fn
        return decorator

from config import PREFERRED_LLM, OPENAI_API_KEY, GOOGLE_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY
from database import get_session
from models import Account, Group, Message, Template, Campaign, AuditLog


def _run_async(coro):
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


@crewai_tool("query_groups")
def query_groups(criteria: str) -> str:
    """يستعلم عن المجموعات من قاعدة البيانات بناءً على معايير JSON."""
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


@crewai_tool("send_to_group")
def send_to_group(payload: str) -> str:
    """يرسل رسالة لمجموعة. payload: {"account_id":1,"group_id":1,"message":"نص"}"""
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
        db = await get_session()
        try:
            db_msg = Message(
                account_id=account_id, group_id=group_id,
                content=text, telegram_msg_id=msg.id,
                sent_at=datetime.utcnow(), status="sent",
            )
            db.add(db_msg)
            await db.commit()
            await db.refresh(db_msg)
            msg_db_id = db_msg.id
        finally:
            await db.close()
        asyncio.create_task(um.monitor_deletion(client, msg.id, chat_id, msg_db_id))
        return json.dumps({"status": "ok", "message_id": msg_db_id})
    return _run_async(_send())


@crewai_tool("check_deletion_status")
def check_deletion_status(message_id: str) -> str:
    """يتحقق من حالة رسالة."""
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


@crewai_tool("store_memory")
def store_memory(payload: str) -> str:
    """يخزن ذاكرة في ChromaDB."""
    if not CHROMA_AVAILABLE:
        return "ChromaDB غير متاح"
    try:
        data = json.loads(payload)
        _memory_col.upsert(ids=[data["id"]], documents=[data["text"]], metadatas=[data.get("metadata", {})])
        return "تم حفظ الذاكرة"
    except Exception as e:
        return f"خطأ: {e}"


@crewai_tool("recall_memory")
def recall_memory(query: str) -> str:
    """يسترجع ذاكرة من ChromaDB."""
    if not CHROMA_AVAILABLE:
        return "ChromaDB غير متاح"
    try:
        results = _memory_col.query(query_texts=[query], n_results=5)
        docs  = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        return json.dumps([{"text": d, "meta": m} for d, m in zip(docs, metas)], ensure_ascii=False)
    except Exception as e:
        return f"خطأ: {e}"


@crewai_tool("get_campaign_stats")
def get_campaign_stats(campaign_id: str) -> str:
    """يجلب إحصائيات حملة."""
    async def _stats():
        db = await get_session()
        try:
            from sqlalchemy import select, func
            res = await db.execute(select(Campaign).where(Campaign.id == int(campaign_id)))
            camp = res.scalar_one_or_none()
            if not camp:
                return json.dumps({"error": "حملة غير موجودة"})
            total_q   = select(func.count(Message.id)).where(Message.campaign_id == int(campaign_id))
            deleted_q = select(func.count(Message.id)).where(
                Message.campaign_id == int(campaign_id), Message.status == "deleted")
            total   = (await db.execute(total_q)).scalar() or 0
            deleted = (await db.execute(deleted_q)).scalar() or 0
            return json.dumps({
                "campaign": camp.to_dict(), "total_messages": total, "deleted": deleted,
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


def _get_key_for_model(model_str: str) -> str:
    """
    يرجع مفتاح API المناسب ويضبط متغيرات البيئة المطلوبة.
    يقرأ دائماً من os.environ أولاً ثم من config كاحتياطي.
    """
    import os
    m = model_str.lower()

    if "gemini" in m:
        key = os.environ.get("GOOGLE_API_KEY", "") or GOOGLE_API_KEY
        if not key:
            raise RuntimeError("GOOGLE_API_KEY غير محدد")
        os.environ["GEMINI_API_KEY"] = key
        os.environ["GOOGLE_API_KEY"] = key
        return key

    elif "groq" in m or "llama" in m or "mixtral" in m:
        key = os.environ.get("GROQ_API_KEY", "") or GROQ_API_KEY
        if not key:
            raise RuntimeError("GROQ_API_KEY غير محدد")
        os.environ["GROQ_API_KEY"] = key
        return key

    elif "deepseek" in m:
        key = os.environ.get("DEEPSEEK_API_KEY", "") or DEEPSEEK_API_KEY
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY غير محدد")
        os.environ["DEEPSEEK_API_KEY"] = key
        return key

    elif "anthropic" in m or "claude" in m:
        from config import ANTHROPIC_API_KEY as _ANT
        key = os.environ.get("ANTHROPIC_API_KEY", "") or _ANT
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY غير محدد")
        os.environ["ANTHROPIC_API_KEY"] = key
        return key

    else:
        key = os.environ.get("OPENAI_API_KEY", "") or OPENAI_API_KEY
        if not key:
            raise RuntimeError("OPENAI_API_KEY غير محدد")
        os.environ["OPENAI_API_KEY"] = key
        return key


def _get_candidates() -> list:
    """
    يبني قائمة الموديلات مرتبة:
    الأساسي (PREFERRED_LLM من os.environ) أولاً ثم الاحتياطية.
    يقرأ PREFERRED_LLM من os.environ مباشرة حتى يعكس أي تغيير
    تم عبر الواجهة أو api/set-preferred بدون إعادة تشغيل السيرفر.
    """
    import os
    preferred = os.environ.get("PREFERRED_LLM", "") or PREFERRED_LLM or "groq/llama-3.3-70b-versatile"

    candidates = [preferred]

    _groq = os.environ.get("GROQ_API_KEY", "") or GROQ_API_KEY
    _deep = os.environ.get("DEEPSEEK_API_KEY", "") or DEEPSEEK_API_KEY
    _gem  = os.environ.get("GOOGLE_API_KEY", "") or GOOGLE_API_KEY

    # أضف الاحتياطيات بترتيب الأسرع → الأبطأ
    if _groq and "groq" not in preferred.lower() and "llama" not in preferred.lower():
        candidates.append("groq/llama-3.3-70b-versatile")
    if _deep and "deepseek" not in preferred.lower():
        candidates.append("deepseek/deepseek-chat")
    if _gem and "gemini" not in preferred.lower():
        candidates.append("gemini/gemini-2.0-flash")

    return candidates


def _build_llm_for(model_str: str):
    """ينشئ LLM لموديل محدد."""
    if not CREWAI_AVAILABLE:
        raise RuntimeError("CrewAI غير مثبت")
    api_key = _get_key_for_model(model_str)
    return LLM(model=model_str, api_key=api_key)


def _build_llm():
    """
    ينشئ LLM للموديل الأساسي فقط.
    ملاحظة: خطأ 429 يحدث عند crew.kickoff وليس هنا،
    لذا الـ fallback الحقيقي موجود في run_copilot_with_fallback.
    """
    if not CREWAI_AVAILABLE:
        raise RuntimeError("CrewAI غير مثبت")
    candidates = _get_candidates()
    return _build_llm_for(candidates[0])


def build_orchestrator(llm):
    return Agent(
        role="قائد الفريق",
        goal="فهم أمر المستخدم وتوزيع المهام على الوكلاء المناسبين.",
        backstory="مدير ذكي يحوّل الأوامر الطبيعية إلى خطط قابلة للتنفيذ.",
        tools=[recall_memory], llm=llm, verbose=False,
    )

def build_planner(llm):
    return Agent(
        role="المخطط الاستراتيجي",
        goal="تحليل الأمر واستعلام قاعدة البيانات لاقتراح قائمة مجموعات مستهدفة.",
        backstory="خبير في تحليل البيانات وتخطيط حملات تيليجرام.",
        tools=[query_groups, recall_memory], llm=llm, verbose=False,
    )

def build_executor(llm):
    return Agent(
        role="المنفذ الميداني",
        goal="تنفيذ خطة الإرسال بإرسال الرسائل عبر الحسابات المناسبة.",
        backstory="متخصص في تشغيل عمليات تيليجرام.",
        tools=[send_to_group, query_groups], llm=llm, verbose=False,
    )

def build_monitor(llm):
    return Agent(
        role="المراقب الذكي",
        goal="مراقبة الرسائل المرسلة والكشف عن حذفها.",
        backstory="عين ساهرة ترصد كل رسالة وتسجل وقت الحذف وسببه.",
        tools=[check_deletion_status, store_memory], llm=llm, verbose=False,
    )

def build_analyzer(llm):
    return Agent(
        role="المحلل والمحسّن",
        goal="تحليل نتائج الحملات واقتراح تحسينات.",
        backstory="عالم بيانات يحوّل الأرقام إلى رؤى قابلة للتطبيق.",
        tools=[get_campaign_stats, store_memory, recall_memory, query_groups], llm=llm, verbose=False,
    )


def _build_crew(llm):
    """ينشئ طاقم الوكلاء بـ LLM محدد."""
    orchestrator = build_orchestrator(llm)
    planner      = build_planner(llm)
    executor     = build_executor(llm)
    monitor      = build_monitor(llm)
    analyzer     = build_analyzer(llm)

    task_orchestrate = Task(
        description="الأمر المستلم سيُزوَّد في kickoff\nحدّد نوع العملية وضع خطة مختصرة.",
        expected_output="خطة مختصرة: نوع العملية، المجموعات المستهدفة، الصيغة.",
        agent=orchestrator,
    )
    task_plan = Task(
        description="استعلم عن المجموعات المناسبة وحدد قائمة الإرسال والجدول الزمني.",
        expected_output="قائمة JSON بمعرفات المجموعات والحسابات والصيغ.",
        agent=planner, context=[task_orchestrate],
    )
    task_execute = Task(
        description="نفّذ خطة الإرسال باستخدام أداة send_to_group. الزم بالتأخيرات البشرية.",
        expected_output="تقرير بعدد الرسائل المرسلة وأي أخطاء.",
        agent=executor, context=[task_plan],
    )
    task_monitor = Task(
        description="راقب الرسائل المرسلة وتحقق من حالة حذفها. سجّل أي حذف مع تصنيف بوت الحماية.",
        expected_output="تقرير بعدد الرسائل المحذوفة وتصنيف المجموعات.",
        agent=monitor, context=[task_execute],
    )
    task_analyze = Task(
        description="حلّل النتائج وقدّم توصيات لتحسين الحملة القادمة. خزّن الرؤى في ChromaDB.",
        expected_output="ملخص تحليلي مع توصيات قابلة للتطبيق.",
        agent=analyzer, context=[task_monitor],
    )
    return Crew(
        agents=[orchestrator, planner, executor, monitor, analyzer],
        tasks=[task_orchestrate, task_plan, task_execute, task_monitor, task_analyze],
        process=Process.sequential, verbose=False,
    )


async def run_copilot(command: str) -> dict:
    """
    يستقبل أمراً نصياً ويشغّل طاقم الوكلاء.

    آلية الـ Fallback:
    - LLM() لا يتحقق من الاتصال عند البناء، الخطأ 429 يحدث عند crew.kickoff
    - لذا نجرب كل موديل احتياطي كاملاً (بناء crew + kickoff) عند الفشل
    - الأولوية: PREFERRED_LLM → Groq → DeepSeek → Gemini
    """
    await _log_audit("Orchestrator", "copilot_command", {"command": command})

    if not CREWAI_AVAILABLE:
        msg = "⚠️ CrewAI غير مثبت — الأمر سُجِّل لكن لم يُنفَّذ."
        await _log_audit("Orchestrator", "copilot_error", {"error": msg})
        return {"status": "error", "detail": msg}

    candidates = _get_candidates()
    last_err = None

    for model_str in candidates:
        try:
            llm = _build_llm_for(model_str)
        except Exception as e:
            last_err = e
            logger.warning(f"⚠️ فشل بناء LLM للموديل {model_str}: {e}")
            continue

        crew = _build_crew(llm)
        # أضف الأمر في وصف المهمة الأولى
        crew.tasks[0].description = f"الأمر المستلم: «{command}»\nحدّد نوع العملية وضع خطة مختصرة."

        try:
            result = await asyncio.to_thread(crew.kickoff)
            result_str = str(result)
            used_model = model_str
            if model_str != candidates[0]:
                prefix = "[تم التحويل تلقائياً لـ " + model_str + "]\n\n"
                result_str = prefix + result_str
                logger.warning("✅ نجح الاحتياطي: " + model_str)
            await _log_audit("Analyzer", "copilot_done", {
                "result": result_str[:1000],
                "model_used": used_model,
            })
            return {"status": "ok", "result": result_str, "model_used": model_str}

        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            is_quota_err = any(x in err_str for x in [
                "429", "quota", "resource_exhausted",
                "rate_limit", "rate limit", "exceeded",
                "too many requests",
            ])
            if is_quota_err:
                logger.warning(f"⚠️ {model_str} رفض بـ quota/429، أجرب الاحتياطي التالي...")
                continue
            # خطأ غير متعلق بـ quota → توقف فوراً
            logger.error(f"❌ خطأ غير متوقع من {model_str}: {e}")
            await _log_audit("Orchestrator", "copilot_error", {
                "error": str(e), "model": model_str,
            })
            return {"status": "error", "detail": str(e)}

    # جميع الموديلات فشلت
    final_msg = f"❌ جميع الموديلات فشلت (quota منتهية). آخر خطأ: {last_err}"
    logger.error(final_msg)
    await _log_audit("Orchestrator", "copilot_error", {"error": final_msg})
    return {"status": "error", "detail": final_msg}
