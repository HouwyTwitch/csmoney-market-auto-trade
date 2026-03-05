import logging
from typing import Optional

import primp

from . import config

logger = logging.getLogger(__name__)


class SessionExpiredError(Exception):
    """Raised when CS.Money returns 401/403, indicating the session has expired."""


class RateLimitedError(Exception):
    """Raised when CS.Money returns error code 9999 (rate limited)."""


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


def _ext_headers(extra: Optional[dict] = None) -> dict:
    """Headers for requests that come from the extension origin."""
    return _build_headers(
        {"origin": f"chrome-extension://{config.EXTENSION_ID}", **(extra or {})}
    )


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

    async def _patch(self, url: str, headers: dict, **kwargs) -> primp.Response:
        resp = await self._http.patch(url, headers=headers, cookies=self._cookies, **kwargs)
        if resp.status_code in (401, 403):
            raise SessionExpiredError(f"Session expired — HTTP {resp.status_code} on {url}")
        resp.raise_for_status()
        return resp

    async def _delete(self, url: str, headers: dict, **kwargs) -> primp.Response:
        resp = await self._http.delete(url, headers=headers, cookies=self._cookies, **kwargs)
        if resp.status_code in (401, 403):
            raise SessionExpiredError(f"Session expired — HTTP {resp.status_code} on {url}")
        resp.raise_for_status()
        return resp

    # ── notification endpoints ────────────────────────────────────────────────

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

    # ── active-offers endpoints ───────────────────────────────────────────────

    async def get_active_offers(self) -> dict:
        url = f"{self._base}/3.0/market/active-offers"
        resp = await self._get(url, _ext_headers({"content-type": "application/json"}))
        return resp.json()

    # ── trade-offer draft lifecycle ───────────────────────────────────────────

    async def initiate_trade_offer(self, active_offer_id: int) -> None:
        """
        POST /3.0/market/offers/tradeoffer
        Notify CS.Money that we are starting to create the trade offer.
        Success → HTTP 201 empty body.
        Rate-limited → body contains {"errors": [{"code": 9999}]}.
        """
        url = f"{self._base}/3.0/market/offers/tradeoffer"
        resp = await self._post(
            url,
            _ext_headers({"content-type": "application/json"}),
            json={"activeOfferId": active_offer_id},
        )
        if resp.content:
            body = resp.json()
            errors = body.get("errors", [])
            if any(e.get("code") == 9999 for e in errors):
                raise RateLimitedError(
                    f"CS.Money rate-limited (9999) for activeOfferId={active_offer_id}"
                )

    async def delete_trade_offer_draft(self, active_offer_id: int) -> None:
        """DELETE /3.0/market/offers/tradeoffer — cancel the draft on error."""
        url = f"{self._base}/3.0/market/offers/tradeoffer"
        await self._delete(
            url,
            _ext_headers({"content-type": "application/json"}),
            json={"activeOfferId": active_offer_id},
        )

    async def report_trade_offer(
        self,
        offer_id: int,
        trade_offer_id: str,
        session_id: str,
        session_data: str,
        correlation_id: str,
    ) -> None:
        """PATCH /4.0/market/offers/tradeoffer — report back the Steam trade-offer ID."""
        url = f"{self._base}/4.0/market/offers/tradeoffer"
        await self._patch(
            url,
            _ext_headers({"content-type": "application/json"}),
            json={
                "offerId": offer_id,
                "tradeOfferId": trade_offer_id,
                "sessionId": session_id,
                "sessionData": session_data,
                "correlationId": correlation_id,
            },
        )
        logger.info(
            "Trade offer reported to CS.Money (offerId=%s tradeOfferId=%s correlationId=%s)",
            offer_id,
            trade_offer_id,
            correlation_id,
        )

    # ── session / security-key endpoints ─────────────────────────────────────

    async def get_security_key(self) -> dict:
        url = f"{self._base}/1.0/market/secure/key"
        resp = await self._post(
            url,
            _ext_headers({"content-type": "application/json", "content-length": "0"}),
            content=b"",
        )
        return resp.json()

    async def send_session(
        self, session_id: str, session_data: str, correlation_id: str
    ) -> None:
        """POST /4.0/market/offers/session — used for historyOutdate re-sync."""
        url = f"{self._base}/4.0/market/offers/session"
        await self._post(
            url,
            _ext_headers({"content-type": "application/json"}),
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
