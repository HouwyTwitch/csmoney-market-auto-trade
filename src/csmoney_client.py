import logging
from typing import Optional

import primp

from . import config

logger = logging.getLogger(__name__)


class SessionExpiredError(Exception):
    """Raised when CS.Money returns 401/403, indicating the session has expired."""


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
    def __init__(self, http: primp.AsyncClient, cookies: dict):
        self._http = http
        self._base = config.CSMONEY_BASE_URL
        self._cookies = cookies

    def update_cookies(self, cookies: dict) -> None:
        self._cookies = cookies

    async def _get(self, url: str, headers: dict) -> primp.Response:
        resp = await self._http.get(url, headers=headers, cookies=self._cookies)
        if resp.status_code in (401, 403):
            raise SessionExpiredError(f"Session expired — HTTP {resp.status_code} on {url}")
        resp.raise_for_status()
        return resp

    async def _post(self, url: str, headers: dict, **kwargs) -> primp.Response:
        resp = await self._http.post(url, headers=headers, cookies=self._cookies, **kwargs)
        if resp.status_code in (401, 403):
            raise SessionExpiredError(f"Session expired — HTTP {resp.status_code} on {url}")
        resp.raise_for_status()
        return resp

    async def get_notifications(self, updated_from: int) -> dict:
        url = (
            f"{self._base}/1.0/market/notifications"
            f"?updatedFrom={updated_from}&limit={config.NOTIFICATIONS_LIMIT}"
        )
        resp = await self._get(url, _build_headers({"x-client-app": "web"}))
        return resp.json()

    async def mark_notifications_viewed(self, notification_ids: list[str]) -> None:
        url = f"{self._base}/1.0/market/notifications/mark-viewed"
        await self._post(
            url,
            _build_headers(
                {
                    "content-type": "application/json;charset=UTF-8",
                    "origin": config.CSMONEY_BASE_URL,
                    "x-client-app": "web",
                }
            ),
            json={"notificationsIds": notification_ids},
        )

    async def get_active_offers(self) -> dict:
        url = f"{self._base}/3.0/market/active-offers"
        resp = await self._get(
            url,
            _build_headers(
                {
                    "content-type": "application/json",
                    "origin": f"chrome-extension://{config.EXTENSION_ID}",
                }
            ),
        )
        return resp.json()

    async def get_security_key(self) -> dict:
        url = f"{self._base}/1.0/market/secure/key"
        resp = await self._post(
            url,
            _build_headers(
                {
                    "content-type": "application/json",
                    "content-length": "0",
                    "origin": f"chrome-extension://{config.EXTENSION_ID}",
                }
            ),
            content=b"",
        )
        return resp.json()

    async def send_session(
        self, session_id: str, session_data: str, correlation_id: str
    ) -> None:
        url = f"{self._base}/4.0/market/offers/session"
        await self._post(
            url,
            _build_headers(
                {
                    "content-type": "application/json",
                    "origin": f"chrome-extension://{config.EXTENSION_ID}",
                }
            ),
            json={
                "sessionId": session_id,
                "sessionData": session_data,
                "correlationId": correlation_id,
            },
        )
        logger.info("Session sent successfully (correlationId=%s)", correlation_id)

    async def get_user_store(self) -> dict:
        url = f"{self._base}/1.0/market/user-store"
        resp = await self._get(url, _build_headers({"x-client-app": "web"}))
        return resp.json()
