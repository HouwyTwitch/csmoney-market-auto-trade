import json
import logging
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"

_raw: dict = {}
if _CONFIG_PATH.exists():
    with _CONFIG_PATH.open() as _f:
        _raw = json.load(_f)


def _get(key: str, default=None):
    return _raw.get(key, default)


CSMONEY_BASE_URL = "https://cs.money"

# Steam account credentials — used to log in via aiosteampy
STEAM_USERNAME: str = _get("steam_username", "")
STEAM_PASSWORD: str = _get("steam_password", "")
STEAM_SHARED_SECRET: str = _get("steam_shared_secret", "")
STEAM_IDENTITY_SECRET: str = _get("steam_identity_secret", "")

# Proxies (format: http://user:pass@host:port  or  http://host:port)
# csmoney_proxy is used for the cs.money session.
# steam_proxy is optional; falls back to csmoney_proxy when not set.
CSMONEY_PROXY: str = _get("csmoney_proxy", "")
STEAM_PROXY: str = _get("steam_proxy", "") or CSMONEY_PROXY

POLL_INTERVAL: float = float(_get("poll_interval", 10))

LOG_LEVEL: str = str(_get("log_level", "INFO")).upper()

NOTIFICATIONS_LIMIT = 60

EXTENSION_VERSION = "4.0.0"
EXTENSION_ID = "mkjknmlmebnimmkonggecjlccealonel"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)


def validate_config():
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"config.json not found at {_CONFIG_PATH}. "
            "Copy config.example.json to config.json and fill in your credentials."
        )
    missing = [
        key
        for key, val in [
            ("steam_username", STEAM_USERNAME),
            ("steam_password", STEAM_PASSWORD),
            ("steam_shared_secret", STEAM_SHARED_SECRET),
            ("steam_identity_secret", STEAM_IDENTITY_SECRET),
        ]
        if not val
    ]
    if missing:
        raise ValueError(f"Missing required config.json fields: {', '.join(missing)}")


def setup_logging():
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
