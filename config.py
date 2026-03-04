import os
import logging
from dotenv import load_dotenv

load_dotenv()

CSMONEY_BASE_URL = "https://cs.money"

CSMONEY_SESSION = os.getenv("CSMONEY_SESSION", "")
STEAM_LOGIN_SECURE = os.getenv("STEAM_LOGIN_SECURE", "")
STEAM_SESSION_ID = os.getenv("STEAM_SESSION_ID", "")

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "10"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

NOTIFICATIONS_LIMIT = 60

EXTENSION_VERSION = "4.0.0"
EXTENSION_ID = "mkjknmlmebnimmkonggecjlccealonel"


def validate_config():
    missing = []
    if not CSMONEY_SESSION:
        missing.append("CSMONEY_SESSION")
    if not STEAM_LOGIN_SECURE:
        missing.append("STEAM_LOGIN_SECURE")
    if not STEAM_SESSION_ID:
        missing.append("STEAM_SESSION_ID")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def setup_logging():
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
