"""
ai_copilot.py — كوبايلوت ذكي يفهم الأوامر بالعربي ويستعلم عن DB مباشرة.
يستخدم litellm مباشرة (بدون CrewAI) لتجنب مشاكل schema validation مع Groq.
"""
import json
import logging
import os
from datetime import datetime
from typing import Any

import litellm
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_active_llm, _key_for
import models

logger = logging.getLogger(__name__)
litellm.set_verbose = False


# ─── أدوات الكوبايلوت (Python functions عادية) ────────────────────────────────

async def _tool_query_groups(db: AsyncSession, category: str = "",
                              min_members: int = 0, is_joined: bool = None,
                              language: str = "", limit: int = 20) -> list[dict]:
    q = select(models.Group)
    conds = []
    if category:
        conds.append(models.Group.category == category)
    if min_members > 0:
        conds.append(models.Group.member_count >= min_members)
    if is_joined is not None:
        conds.append(models.Group.is_joined == is_joined)
    if language:
        conds.append(models.Group.language == language)
    if conds:
        q = q.where(and_(*conds))
    q = q.limit(limit)
    result = await db.execute(q)
    return [g.to_dict() for g in result.scalars().all()]


async def _tool_get_stats(db: AsyncSession) -> dict:
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
        "delete_rate": f"{round(deleted/messages*100,1)}%" if messages else "0%",
        "campaigns": campaigns,
    }


async def _tool_list_accounts(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(models.Account))
    return [a.to_dict() for a in result.scalars().all()]


async def _tool_list_campaigns(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(models.Campaign))
    return [c.to_dict() for c in result.scalars().all()]


# ─── خريطة الأدوات ────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": "يجلب إحصائيات عامة: عدد الحسابات، المجموعات، الرسائل، الحملات.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_groups",
            "description": "يستعلم عن المجموعات المسجلة في قاعدة البيانات.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category":    {"type": "string", "description": "تصنيف المجموعة مثل news أو ads"},
                    "min_members": {"type": "integer", "description": "الحد الأدنى لعدد الأعضاء"},
                    "is_joined":   {"type": "boolean", "description": "true إذا أردت المنضم إليها فقط"},
                    "language":    {"type": "string", "description": "لغة المجموعة مثل ar"},
                    "limit":       {"type": "integer", "description": "أقصى عدد نتائج"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_accounts",
            "description": "يعرض قائمة الحسابات المسجلة.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_campaigns",
            "description": "يعرض قائمة الحملات الإعلانية.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


async def _call_tool(name: str, args: dict, db: AsyncSession) -> str:
    """تنفيذ أداة وإرجاع النتيجة كـ string."""
    try:
        if name == "get_stats":
            result = await _tool_get_stats(db)
        elif name == "query_groups":
            result = await _tool_query_groups(
                db,
                category=args.get("category", ""),
                min_members=int(args.get("min_members", 0)),
                is_joined=args.get("is_joined"),
                language=args.get("language", ""),
                limit=int(args.get("limit", 20)),
            )
        elif name == "list_accounts":
            result = await _tool_list_accounts(db)
        elif name == "list_campaigns":
            result = await _tool_list_campaigns(db)
        else:
            result = {"error": f"أداة غير معروفة: {name}"}
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"خطأ في أداة {name}: {e}")
        return json.dumps({"error": str(e)})


# ─── الدالة الرئيسية ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """أنت مساعد ذكي متخصص في إدارة نظام Telegram userbot.
لديك أدوات للاستعلام عن قاعدة البيانات: الحسابات، المجموعات، الحملات، والإحصائيات.
أجب دائماً بالعربي. كن دقيقاً ومختصراً. إذا كنت تحتاج بيانات استخدم الأدوات المتاحة."""

async def run_copilot(command: str, db: AsyncSession) -> dict:
    """
    يستقبل أمراً نصياً، يستعلم عن DB عند الحاجة، ويرجع الرد.
    يجرب النماذج المتاحة تلقائياً عند الفشل.
    """
    model, api_key = get_active_llm()
    if not model or not api_key:
        return {"status": "error", "detail": "لا يوجد مفتاح AI — أضف GROQ_API_KEY أو غيره في المتغيرات"}

    # إعداد مفتاح البيئة للنموذج المختار
    _set_env_key(model, api_key)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": command},
    ]

    # جولة أولى — قد يطلب النموذج أداة
    try:
        response = litellm.completion(
            model=model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            max_tokens=1500,
            timeout=30,
        )
    except Exception as e:
        return {"status": "error", "detail": f"خطأ في الاتصال بـ AI: {e}"}

    msg = response.choices[0].message

    # تنفيذ الأدوات إذا طلبها النموذج
    if msg.tool_calls:
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]})

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            tool_result = await _call_tool(tc.function.name, args, db)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result,
            })

        # جولة ثانية — النموذج يُجيب بناءً على نتائج الأدوات
        try:
            response2 = litellm.completion(
                model=model,
                messages=messages,
                max_tokens=1500,
                timeout=30,
            )
            final_text = response2.choices[0].message.content or ""
        except Exception as e:
            return {"status": "error", "detail": f"خطأ في الجولة الثانية: {e}"}
    else:
        final_text = msg.content or ""

    # تسجيل في قاعدة البيانات
    try:
        log = models.Log(source="copilot", action="command", detail=f"Q: {command[:200]}\nA: {final_text[:500]}")
        db.add(log)
        await db.commit()
    except Exception:
        pass

    return {"status": "ok", "result": final_text, "model": model}


def _set_env_key(model: str, key: str):
    m = model.lower()
    if "groq" in m or "llama" in m:
        os.environ["GROQ_API_KEY"] = key
    elif "gemini" in m:
        os.environ["GOOGLE_API_KEY"] = key
        os.environ["GEMINI_API_KEY"] = key
    elif "deepseek" in m:
        os.environ["DEEPSEEK_API_KEY"] = key
    elif "gpt" in m or "openai" in m:
        os.environ["OPENAI_API_KEY"] = key
    elif "claude" in m or "anthropic" in m:
        os.environ["ANTHROPIC_API_KEY"] = key
