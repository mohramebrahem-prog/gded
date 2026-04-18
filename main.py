"""
main.py – نقطة الدخول الرئيسية
يشغّل FastAPI + Uvicorn مع مدير حسابات Pyrogram في نفس event loop.
"""

import asyncio
import logging
import uvicorn
from api import app          # noqa: F401 – يجب استيراده لتسجيل routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if __name__ == "__main__":
    from config import HOST, PORT, DEBUG

    uvicorn.run(
        "api:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="info",
    )
