"""main.py — نقطة تشغيل النظام"""
import logging
import os
import uvicorn
from api import app
from config import PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info(f"🚀 بدء التشغيل على المنفذ {PORT}")
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )
