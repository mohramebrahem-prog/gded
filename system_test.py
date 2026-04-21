"""
system_test.py – اختبار شامل لكل مكونات النظام
يُشغَّل عبر: GET /api/system-test
يكتب كل نتيجة في AuditLog تحت action="system_test"
"""

import asyncio
import importlib
import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# أداة مساعدة لتسجيل النتائج
# ══════════════════════════════════════════════════════════════════════════════

class TestReport:
    def __init__(self):
        self.results: list[dict] = []
        self.start_time = time.time()

    def add(self, category: str, name: str, status: str, detail: str = "", duration_ms: float = 0):
        icon = "✅" if status == "pass" else ("⚠️" if status == "warn" else "❌")
        entry = {
            "category": category,
            "name": name,
            "status": status,
            "icon": icon,
            "detail": detail,
            "duration_ms": round(duration_ms, 1),
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.results.append(entry)
        log_fn = logger.info if status == "pass" else (logger.warning if status == "warn" else logger.error)
        log_fn(f"[SYSTEM_TEST] {icon} [{category}] {name}: {detail[:200]}")
        return entry

    def summary(self) -> dict:
        total    = len(self.results)
        passed   = sum(1 for r in self.results if r["status"] == "pass")
        warnings = sum(1 for r in self.results if r["status"] == "warn")
        failed   = sum(1 for r in self.results if r["status"] == "fail")
        duration = round(time.time() - self.start_time, 2)

        if failed > 0:
            overall = "❌ يوجد أخطاء"
        elif warnings > 0:
            overall = "⚠️ يوجد تحذيرات"
        else:
            overall = "✅ كل شيء يعمل"

        return {
            "overall": overall,
            "total": total,
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "duration_sec": duration,
            "results": self.results,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 1. فحص متغيرات البيئة
# ══════════════════════════════════════════════════════════════════════════════

async def test_env(report: TestReport):
    required = {
        "TELEGRAM_API_ID":   "تيليجرام",
        "TELEGRAM_API_HASH": "تيليجرام",
        "SECRET_KEY":        "الأمان",
        "DATABASE_URL":      "قاعدة البيانات",
    }
    ai_keys = {
        "GOOGLE_API_KEY":    "Gemini",
        "GROQ_API_KEY":      "Groq",
        "DEEPSEEK_API_KEY":  "DeepSeek",
        "OPENAI_API_KEY":    "OpenAI",
        "ANTHROPIC_API_KEY": "Anthropic",
    }

    for var, label in required.items():
        val = os.environ.get(var, "")
        if val:
            report.add("البيئة", var, "pass", f"{label} ✓")
        else:
            report.add("البيئة", var, "fail", f"❌ متغير {label} غير موجود في Railway Variables")

    # على الأقل مفتاح AI واحد
    found_ai = [k for k in ai_keys if os.environ.get(k)]
    if found_ai:
        report.add("البيئة", "AI Keys", "pass", f"مفاتيح موجودة: {', '.join(found_ai)}")
    else:
        report.add("البيئة", "AI Keys", "fail", "لا يوجد أي مفتاح AI — أضف من واجهة النظام")

    preferred = os.environ.get("PREFERRED_LLM", "")
    if preferred:
        report.add("البيئة", "PREFERRED_LLM", "pass", preferred)
    else:
        report.add("البيئة", "PREFERRED_LLM", "warn", "غير محدد — سيُضبط عند إضافة موديل من الواجهة")


# ══════════════════════════════════════════════════════════════════════════════
# 2. فحص المكتبات
# ══════════════════════════════════════════════════════════════════════════════

async def test_imports(report: TestReport):
    libs = [
        ("fastapi",           "FastAPI"),
        ("uvicorn",           "Uvicorn"),
        ("sqlalchemy",        "SQLAlchemy"),
        ("pyrogram",          "Pyrogram"),
        ("crewai",            "CrewAI"),
        ("litellm",           "LiteLLM"),
        ("chromadb",          "ChromaDB"),
        ("langchain",         "LangChain"),
        ("google.generativeai","Google Generative AI"),
    ]
    for module, label in libs:
        t0 = time.time()
        try:
            importlib.import_module(module)
            ms = (time.time() - t0) * 1000
            report.add("المكتبات", label, "pass", "مثبت", ms)
        except ImportError as e:
            ms = (time.time() - t0) * 1000
            is_critical = module in ("fastapi", "uvicorn", "sqlalchemy", "pyrogram")
            status = "fail" if is_critical else "warn"
            report.add("المكتبات", label, status, f"غير مثبت: {e}", ms)


# ══════════════════════════════════════════════════════════════════════════════
# 3. فحص قاعدة البيانات
# ══════════════════════════════════════════════════════════════════════════════

async def test_database(report: TestReport):
    t0 = time.time()
    try:
        from database import get_session
        from models import Account, Group, Message, Campaign, AuditLog
        from sqlalchemy import select, func, text

        db = await get_session()
        try:
            # اختبار اتصال بسيط
            await db.execute(text("SELECT 1"))
            ms = (time.time() - t0) * 1000
            report.add("قاعدة البيانات", "الاتصال", "pass", "متصل", ms)

            # إحصاء الجداول
            tables = {
                "حسابات":  Account,
                "مجموعات": Group,
                "رسائل":   Message,
                "حملات":   Campaign,
                "سجلات":   AuditLog,
            }
            for label, model in tables.items():
                t1 = time.time()
                try:
                    result = await db.execute(select(func.count(model.id)))
                    count = result.scalar() or 0
                    ms2 = (time.time() - t1) * 1000
                    report.add("قاعدة البيانات", f"جدول {label}", "pass", f"{count} سجل", ms2)
                except Exception as e:
                    report.add("قاعدة البيانات", f"جدول {label}", "fail", str(e))
        finally:
            await db.close()
    except Exception as e:
        ms = (time.time() - t0) * 1000
        report.add("قاعدة البيانات", "الاتصال", "fail", f"فشل الاتصال: {e}", ms)


# ══════════════════════════════════════════════════════════════════════════════
# 4. فحص نظام الذكاء الاصطناعي
# ══════════════════════════════════════════════════════════════════════════════

async def test_ai(report: TestReport):
    # CrewAI
    try:
        from agents import CREWAI_AVAILABLE, CHROMA_AVAILABLE, _get_candidates
        report.add("الذكاء الاصطناعي", "CrewAI", "pass" if CREWAI_AVAILABLE else "fail",
                   "مثبت ✓" if CREWAI_AVAILABLE else "❌ غير مثبت — pip install crewai")
        report.add("الذكاء الاصطناعي", "ChromaDB", "pass" if CHROMA_AVAILABLE else "warn",
                   "متاح ✓" if CHROMA_AVAILABLE else "غير متاح (الذاكرة معطلة)")

        candidates = _get_candidates()
        if candidates:
            report.add("الذكاء الاصطناعي", "قائمة الموديلات", "pass",
                       f"{len(candidates)} موديل: {', '.join(candidates)}")
        else:
            report.add("الذكاء الاصطناعي", "قائمة الموديلات", "fail",
                       "لا توجد موديلات — أضف مفتاح AI من الواجهة")
    except Exception as e:
        report.add("الذكاء الاصطناعي", "agents.py", "fail", str(e))
        return

    # اختبار استدعاء حقيقي لكل موديل
    if not CREWAI_AVAILABLE:
        return

    try:
        import litellm
        litellm.set_verbose = False

        for model_str in candidates[:3]:
            t0 = time.time()
            try:
                resp = await asyncio.to_thread(
                    litellm.completion,
                    model=model_str,
                    messages=[{"role": "user", "content": "قل: نعم (كلمة واحدة فقط)"}],
                    max_tokens=5,
                    timeout=15,
                )
                text = resp.choices[0].message.content.strip()
                ms = (time.time() - t0) * 1000
                report.add("الذكاء الاصطناعي", f"استدعاء {model_str}", "pass",
                           f"رد: {text}", ms)
            except Exception as e:
                ms = (time.time() - t0) * 1000
                err = str(e)
                is_quota = any(x in err.lower() for x in ["429", "quota", "rate_limit", "resource_exhausted", "exceeded"])
                status = "warn" if is_quota else "fail"
                detail = "quota منتهية — جدد المفتاح" if is_quota else err[:200]
                report.add("الذكاء الاصطناعي", f"استدعاء {model_str}", status, detail, ms)
    except ImportError:
        report.add("الذكاء الاصطناعي", "LiteLLM", "fail", "غير مثبت")


# ══════════════════════════════════════════════════════════════════════════════
# 5. فحص تيليجرام
# ══════════════════════════════════════════════════════════════════════════════

async def test_telegram(report: TestReport):
    api_id   = os.environ.get("TELEGRAM_API_ID", "")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")

    if not api_id or api_id == "0":
        report.add("تيليجرام", "API_ID", "fail", "غير محدد في المتغيرات")
        return
    if not api_hash:
        report.add("تيليجرام", "API_HASH", "fail", "غير محدد في المتغيرات")
        return

    report.add("تيليجرام", "بيانات API", "pass", f"ID={api_id[:4]}**** HASH={api_hash[:4]}****")

    # فحص الحسابات النشطة
    try:
        import userbot_manager as um
        active = len(um._clients) if hasattr(um, "_clients") else 0
        status = "pass" if active > 0 else "warn"
        report.add("تيليجرام", "الحسابات النشطة", status,
                   f"{active} حساب نشط" if active > 0 else "لا توجد حسابات — أضف من الواجهة")
    except Exception as e:
        report.add("تيليجرام", "userbot_manager", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 6. فحص الـ API endpoints
# ══════════════════════════════════════════════════════════════════════════════

async def test_api_endpoints(report: TestReport):
    try:
        from httpx import AsyncClient
        from api import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            endpoints = [
                ("GET",  "/api/stats/overview",           "إحصائيات عامة"),
                ("GET",  "/api/accounts",                  "قائمة الحسابات"),
                ("GET",  "/api/groups",                    "قائمة المجموعات"),
                ("GET",  "/api/ai/config",                 "إعدادات AI"),
                ("GET",  "/api/audit-log?limit=5",         "سجل التدقيق"),
            ]
            for method, path, label in endpoints:
                t0 = time.time()
                try:
                    r = await client.request(method, path)
                    ms = (time.time() - t0) * 1000
                    if r.status_code < 400:
                        report.add("API", label, "pass", f"HTTP {r.status_code}", ms)
                    else:
                        report.add("API", label, "fail", f"HTTP {r.status_code}: {r.text[:100]}", ms)
                except Exception as e:
                    ms = (time.time() - t0) * 1000
                    report.add("API", label, "fail", str(e)[:150], ms)
    except ImportError:
        report.add("API", "httpx", "warn", "httpx غير مثبت — تخطي اختبار الـ endpoints")
    except Exception as e:
        report.add("API", "فحص Endpoints", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 7. فحص WebSocket
# ══════════════════════════════════════════════════════════════════════════════

async def test_websocket(report: TestReport):
    try:
        from api import ws_manager
        active_connections = len(ws_manager.active_connections)
        report.add("WebSocket", "المدير", "pass", f"{active_connections} اتصال نشط حالياً")
    except Exception as e:
        report.add("WebSocket", "ws_manager", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# المدخل الرئيسي
# ══════════════════════════════════════════════════════════════════════════════

async def run_all_tests() -> dict:
    """يشغل جميع الاختبارات ويرجع تقريراً شاملاً."""
    report = TestReport()
    logger.info("=" * 60)
    logger.info("[SYSTEM_TEST] 🚀 بدء الاختبار الشامل للنظام")
    logger.info("=" * 60)

    tests = [
        ("متغيرات البيئة",    test_env),
        ("المكتبات",          test_imports),
        ("قاعدة البيانات",    test_database),
        ("الذكاء الاصطناعي", test_ai),
        ("تيليجرام",          test_telegram),
        ("API Endpoints",     test_api_endpoints),
        ("WebSocket",         test_websocket),
    ]

    for name, fn in tests:
        logger.info(f"[SYSTEM_TEST] ── فحص: {name}")
        try:
            await fn(report)
        except Exception as e:
            report.add(name, "خطأ غير متوقع", "fail", str(e))

    summary = report.summary()

    # حفظ في AuditLog
    try:
        from database import get_session
        from models import AuditLog
        db = await get_session()
        try:
            log = AuditLog(
                agent="SystemTest",
                action="system_test",
                details={
                    "overall": summary["overall"],
                    "passed":  summary["passed"],
                    "failed":  summary["failed"],
                    "warnings":summary["warnings"],
                    "duration":summary["duration_sec"],
                },
            )
            db.add(log)
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.warning(f"[SYSTEM_TEST] تعذر الحفظ في AuditLog: {e}")

    logger.info("=" * 60)
    logger.info(f"[SYSTEM_TEST] {summary['overall']} | ✅{summary['passed']} ⚠️{summary['warnings']} ❌{summary['failed']} | {summary['duration_sec']}s")
    logger.info("=" * 60)

    return summary
