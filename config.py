import os
import logging
from dotenv import load_dotenv

load_dotenv()

CSMONEY_BASE_URL = "https://cs.money"

# Steam credentials — used to build the Steam curl_cffi session for OpenID
STEAM_LOGIN_SECURE = os.getenv("STEAM_LOGIN_SECURE", "")
STEAM_SESSION_ID = os.getenv("STEAM_SESSION_ID", "")

# Proxies (format: http://user:pass@host:port  or  http://host:port)
# CSMONEY_PROXY is used for the cs.money session.
# STEAM_PROXY is optional; falls back to CSMONEY_PROXY when not set.
CSMONEY_PROXY = os.getenv("CSMONEY_PROXY", "")
STEAM_PROXY = os.getenv("STEAM_PROXY", "") or CSMONEY_PROXY


POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "10"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

NOTIFICATIONS_LIMIT = 60

EXTENSION_VERSION = "4.0.0"
EXTENSION_ID = "mkjknmlmebnimmkonggecjlccealonel"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)


def validate_config():
    missing = []
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
