import logging
from typing import Optional

import primp

from . import config

logger = logging.getLogger(__name__)


def _build_headers(extra: Optional[dict] = None) -> dict:
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-language": "en",
        "user-agent": config.USER_AGENT,
        "x-client-app": "CS.Money extension",
        "x-clent-version": config.EXTENSION_VERSION,
    }
    if extra:
        headers.update(extra)
    return headers


class CsMoneyClient:
    def __init__(self, http: primp.AsyncClient, csgo_ses: str):
        self._http = http
        self._base = config.CSMONEY_BASE_URL
        self._cookies = {"csgo_ses": csgo_ses}

    async def get_notifications(self, updated_from: int) -> dict:
        url = (
            f"{self._base}/1.0/market/notifications"
            f"?updatedFrom={updated_from}&limit={config.NOTIFICATIONS_LIMIT}"
        )
        resp = await self._http.get(
            url,
            headers=_build_headers({"x-client-app": "web"}),
            cookies=self._cookies,
        )
        resp.raise_for_status()
        return resp.json()

    async def mark_notifications_viewed(self, notification_ids: list[str]) -> None:
        url = f"{self._base}/1.0/market/notifications/mark-viewed"
        resp = await self._http.post(
            url,
            headers=_build_headers(
                {
                    "content-type": "application/json;charset=UTF-8",
                    "origin": config.CSMONEY_BASE_URL,
                    "x-client-app": "web",
                }
            ),
            cookies=self._cookies,
            json={"notificationsIds": notification_ids},
        )
        resp.raise_for_status()

    async def get_active_offers(self) -> dict:
        url = f"{self._base}/3.0/market/active-offers"
        resp = await self._http.get(
            url,
            headers=_build_headers(
                {
                    "content-type": "application/json",
                    "origin": f"chrome-extension://{config.EXTENSION_ID}",
                }
            ),
            cookies=self._cookies,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_security_key(self) -> dict:
        url = f"{self._base}/1.0/market/secure/key"
        resp = await self._http.post(
            url,
            headers=_build_headers(
                {
                    "content-type": "application/json",
                    "content-length": "0",
                    "origin": f"chrome-extension://{config.EXTENSION_ID}",
                }
            ),
            cookies=self._cookies,
            content=b"",
        )
        resp.raise_for_status()
        return resp.json()

    async def send_session(
        self, session_id: str, session_data: str, correlation_id: str
    ) -> None:
        url = f"{self._base}/4.0/market/offers/session"
        resp = await self._http.post(
            url,
            headers=_build_headers(
                {
                    "content-type": "application/json",
                    "origin": f"chrome-extension://{config.EXTENSION_ID}",
                }
            ),
            cookies=self._cookies,
            json={
                "sessionId": session_id,
                "sessionData": session_data,
                "correlationId": correlation_id,
            },
        )
        resp.raise_for_status()
        logger.info("Session sent successfully (correlationId=%s)", correlation_id)

    async def get_user_store(self) -> dict:
        url = f"{self._base}/1.0/market/user-store"
        resp = await self._http.get(
            url,
            headers=_build_headers({"x-client-app": "web"}),
            cookies=self._cookies,
        )
        resp.raise_for_status()
        return resp.json()
