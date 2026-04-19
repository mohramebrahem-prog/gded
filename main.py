"""
main.py – نقطة الدخول (Railway-ready)
"""
import logging
import os

import uvicorn

from config import validate_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# طباعة التحذيرات عند البدء
for w in validate_config():
    logger.warning(w)

# استيراد التطبيق (يسجل كل الـ routes)
from api import app  # noqa: F401, E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"🚀 بدء التشغيل على المنفذ {port}")
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
        access_log=True,
    )
