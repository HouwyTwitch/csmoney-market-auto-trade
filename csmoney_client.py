import logging
import time
from typing import Optional

import aiohttp

import config

logger = logging.getLogger(__name__)


def _build_headers(extra: Optional[dict] = None) -> dict:
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-language": "en",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "x-client-app": "CS.Money extension",
        "x-clent-version": config.EXTENSION_VERSION,
    }
    if extra:
        headers.update(extra)
    return headers


def _build_cookies() -> dict:
    return {
        "csgo_ses": config.CSMONEY_SESSION,
    }


class CsMoneyClient:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._base = config.CSMONEY_BASE_URL

    async def get_notifications(self, updated_from: int) -> dict:
        url = (
            f"{self._base}/1.0/market/notifications"
            f"?updatedFrom={updated_from}&limit={config.NOTIFICATIONS_LIMIT}"
        )
        headers = _build_headers({"x-client-app": "web"})
        async with self._session.get(
            url, headers=headers, cookies=_build_cookies()
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def mark_notifications_viewed(self, notification_ids: list[str]) -> None:
        url = f"{self._base}/1.0/market/notifications/mark-viewed"
        headers = _build_headers(
            {
                "content-type": "application/json;charset=UTF-8",
                "origin": config.CSMONEY_BASE_URL,
                "x-client-app": "web",
            }
        )
        async with self._session.post(
            url,
            headers=headers,
            cookies=_build_cookies(),
            json={"notificationsIds": notification_ids},
        ) as resp:
            resp.raise_for_status()

    async def get_active_offers(self) -> dict:
        url = f"{self._base}/3.0/market/active-offers"
        headers = _build_headers(
            {
                "content-type": "application/json",
                "origin": f"chrome-extension://{config.EXTENSION_ID}",
            }
        )
        async with self._session.get(
            url, headers=headers, cookies=_build_cookies()
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_security_key(self) -> dict:
        url = f"{self._base}/1.0/market/secure/key"
        headers = _build_headers(
            {
                "content-type": "application/json",
                "content-length": "0",
                "origin": f"chrome-extension://{config.EXTENSION_ID}",
            }
        )
        async with self._session.post(
            url, headers=headers, cookies=_build_cookies(), data=b""
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def send_session(
        self, session_id: str, session_data: str, correlation_id: str
    ) -> None:
        url = f"{self._base}/4.0/market/offers/session"
        headers = _build_headers(
            {
                "content-type": "application/json",
                "origin": f"chrome-extension://{config.EXTENSION_ID}",
            }
        )
        payload = {
            "sessionId": session_id,
            "sessionData": session_data,
            "correlationId": correlation_id,
        }
        async with self._session.post(
            url,
            headers=headers,
            cookies=_build_cookies(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            logger.info("Session sent successfully (correlationId=%s)", correlation_id)

    async def get_user_store(self) -> dict:
        url = f"{self._base}/1.0/market/user-store"
        headers = _build_headers({"x-client-app": "web"})
        async with self._session.get(
            url, headers=headers, cookies=_build_cookies()
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
