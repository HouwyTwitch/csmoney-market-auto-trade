"""
Main processing loop for CS.Money auto-sale tool.

The real flow (reverse-engineered from the CS.Money Chrome extension):

  Notification path (every ~10 s):
    Poll /1.0/market/notifications.  No trade creation happens here — the
    extension only shows desktop notifications from this alarm.

  Active-offers path (every CHECK_ACTIVE_OFFERS_INTERVAL seconds):
    1. GET  /3.0/market/active-offers
    2a. If historyOutdate=true → POST /4.0/market/offers/session (re-sync)
    2b. For each offer with status CREATING (no steamOfferId yet):
          POST /3.0/market/offers/tradeoffer   ← ask CS.Money for trade data
          POST steamcommunity.com/tradeoffer/new/send  ← send trade directly
          POST /1.0/market/secure/key          ← get encryption key
          PATCH /4.0/market/offers/tradeoffer  ← report tradeOfferId + session
          confirm_trade_offer                  ← approve via mobile auth
    2c. For existing offers whose Steam status diverges → re-send session

  Confirmation path (every CONFIRMATION_INTERVAL seconds):
    Catch any confirmations the active-offers path may have missed.

Cookie persistence:
  Cookies written to csmoney_cookies.json after each login.
  Saved cookies tried first on startup; re-login only if expired.

Steam session persistence:
  The refresh token (valid ~2 years) is saved to steam_session.json after
  first login.  Subsequent startups restore the token and call
  refresh_access_token() — no password/2FA round-trip needed.
"""

import asyncio
import functools
import json
import logging
import secrets
import time
from pathlib import Path

import primp
from aiosteampy import SteamClient
from aiosteampy.constants import STEAM_URL
from aiosteampy.models import ConfirmationType

from . import config
from .csmoney_client import CsMoneyClient, SessionExpiredError
from .openid_auth import openid_login
from .session_crypto import encrypt_message
from .steam_trade import send_steam_trade_offer

logger = logging.getLogger(__name__)

OFFER_BOUGHT = "OFFER_BOUGHT"
CHECK_ACTIVE_OFFERS_INTERVAL = 30   # seconds; extension uses 6 min alarms but we poll faster
CONFIRMATION_INTERVAL = 15          # seconds between confirmation polls

_COOKIE_FILE = Path("csmoney_cookies.json")
_STEAM_SESSION_FILE = Path("steam_session.json")

# ── Steam session persistence ─────────────────────────────────────────────────

def _load_steam_session() -> dict:
    if _STEAM_SESSION_FILE.exists():
        try:
            return json.loads(_STEAM_SESSION_FILE.read_text())
        except Exception as exc:
            logger.warning("Could not read %s: %s", _STEAM_SESSION_FILE, exc)
    return {}


def _save_steam_session(steam: SteamClient) -> None:
    try:
        data = {
            "refresh_token": steam.get_refresh_token(),
            "session_id": steam.session_id,
        }
        _STEAM_SESSION_FILE.write_text(json.dumps(data))
        logger.debug("Steam session saved to %s", _STEAM_SESSION_FILE)
    except Exception as exc:
        logger.warning("Could not save Steam session: %s", exc)


async def _steam_login(steam: SteamClient) -> None:
    """
    Login to Steam, reusing a saved refresh token if available.
    Falls back to full username/password login when the saved token is
    missing, expired, or fails to refresh.
    """
    saved = _load_steam_session()
    refresh_token = saved.get("refresh_token")
    session_id = saved.get("session_id")

    if refresh_token:
        try:
            steam.set_refresh_token(refresh_token)
            if steam.is_refresh_token_expired:
                raise ValueError("Refresh token expired")
            logger.info("Restoring Steam session from saved refresh token…")
            await steam.refresh_access_token()
            # Restore (or generate) a session ID
            if session_id:
                steam.set_session_id(STEAM_URL.COMMUNITY, session_id)
            else:
                steam.set_session_id(STEAM_URL.COMMUNITY, secrets.token_hex(12))
            logger.info("Steam session restored (steamId=%s)", steam.steam_id)
            _save_steam_session(steam)
            return
        except Exception as exc:
            logger.warning("Could not restore Steam session (%s) — doing full login.", exc)

    logger.info("Logging in to Steam as %s…", config.STEAM_USERNAME)
    await steam.login()
    logger.info("Steam login successful (steamId=%s)", steam.steam_id)
    _save_steam_session(steam)


# ── CS.Money cookie persistence ───────────────────────────────────────────────

def _load_saved_cookies() -> dict:
    if _COOKIE_FILE.exists():
        try:
            cookies = json.loads(_COOKIE_FILE.read_text())
            if cookies.get("csgo_ses"):
                logger.info("Loaded saved CS.Money cookies from %s", _COOKIE_FILE)
                return cookies
        except Exception as exc:
            logger.warning("Could not read %s: %s", _COOKIE_FILE, exc)
    return {}


def _save_cookies(cookies: dict) -> None:
    try:
        _COOKIE_FILE.write_text(json.dumps(cookies))
        logger.debug("CS.Money cookies saved to %s", _COOKIE_FILE)
    except Exception as exc:
        logger.warning("Could not save cookies to %s: %s", _COOKIE_FILE, exc)


# ── Steam helpers ─────────────────────────────────────────────────────────────

def _steam_login_secure(steam: SteamClient) -> str:
    """Return the steamLoginSecure cookie value: {steamId}%7C%7C{accessToken}."""
    return f"{steam.steam_id}%7C%7C{steam.access_token}"


async def _do_openid_login(steam: SteamClient) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(openid_login, _steam_login_secure(steam), steam.session_id),
    )


async def relogin(client: CsMoneyClient, steam: SteamClient) -> None:
    """Re-do the CS.Money OpenID login; refresh the Steam token first if needed."""
    logger.info("Session expired — performing OpenID re-login…")
    if steam.is_access_token_expired:
        await steam.refresh_access_token()
        _save_steam_session(steam)
    cookies = await _do_openid_login(steam)
    client.update_cookies(cookies)
    _save_cookies(cookies)
    logger.info("Re-login successful.")


# ── trade creation ────────────────────────────────────────────────────────────

async def _encrypt_session(steam: SteamClient, public_key: str) -> tuple[str, str]:
    """Return (encrypted_session_id, encrypted_session_data)."""
    if steam.is_access_token_expired:
        logger.info("Steam access token expired — refreshing…")
        await steam.refresh_access_token()
    encrypted_data = encrypt_message(public_key, _steam_login_secure(steam))
    encrypted_id = encrypt_message(public_key, steam.session_id)
    return encrypted_id, encrypted_data


async def create_trade_for_offer(
    client: CsMoneyClient, steam: SteamClient, offer: dict
) -> None:
    """
    Full trade-creation flow for a single CS.Money offer:
      1. Notify CS.Money we are creating the trade (POST /3.0/market/offers/tradeoffer — empty 201)
      2. Send Steam trade offer directly using data from the offer dict
      3. Report trade offer ID + encrypted session back to CS.Money (PATCH)
      4. Immediately confirm the trade offer in Steam
    """
    offer_id = offer.get("id")
    logger.info("Creating trade offer for CS.Money offer: %s", offer)
    try:
        # Step 1 — notify CS.Money (response body is empty, data is in the offer dict)
        await client.initiate_trade_offer(offer_id)

        # Extract partner / item data from the active-offers offer dict
        # Log the full offer so field names are visible if extraction fails
        partner_steam_id = str(
            offer.get("steamId64")
            or offer.get("partnerSteamId")
            or offer.get("partner", {}).get("steamId64", "")
            or offer.get("tradePartner", {}).get("steamId64", "")
        )
        partner_token = (
            offer.get("token")
            or offer.get("tradeToken")
            or offer.get("partner", {}).get("token", "")
            or offer.get("tradePartner", {}).get("token", "")
        )
        # Assets may be a top-level list or nested inside "item"
        assets = offer.get("assets") or offer.get("items") or []
        if not assets and offer.get("item"):
            item = offer["item"]
            assets = [{
                "appid": item.get("appId", 730),
                "contextid": str(item.get("contextId", 2)),
                "assetid": str(item.get("assetId") or item.get("id", "")),
                "amount": 1,
            }]
        message = offer.get("message", "")

        if not partner_steam_id or not partner_token or not assets:
            raise ValueError(
                f"Could not extract trade params from offer. "
                f"partner_steam_id={partner_steam_id!r} "
                f"partner_token={partner_token!r} "
                f"assets={assets!r}"
            )

        # Step 2 — send the Steam trade offer
        loop = asyncio.get_event_loop()
        trade_offer_id = await loop.run_in_executor(
            None,
            functools.partial(
                send_steam_trade_offer,
                steam_login_secure=_steam_login_secure(steam),
                session_id=steam.session_id,
                partner_steam_id=partner_steam_id,
                partner_token=partner_token,
                assets=assets,
                offer_id=offer_id,
                message=message,
                proxy=config.STEAM_PROXY or "",
            ),
        )

        # Step 3 — report the trade offer ID + encrypted session to CS.Money
        key_info = await client.get_security_key()
        enc_id, enc_data = await _encrypt_session(steam, key_info["publicKey"])
        await client.report_trade_offer(
            offer_id=offer_id,
            trade_offer_id=trade_offer_id,
            session_id=enc_id,
            session_data=enc_data,
            correlation_id=key_info["correlationId"],
        )

        # Step 4 — confirm immediately (don't wait for the 15-s poller)
        logger.info("Confirming trade offer %s in Steam…", trade_offer_id)
        await steam.confirm_trade_offer(int(trade_offer_id))
        logger.info(
            "Trade offer %s confirmed. offerId=%s complete.", trade_offer_id, offer_id
        )

    except SessionExpiredError:
        raise
    except Exception as exc:
        logger.error(
            "Failed to create trade for offerId=%s: %s", offer_id, exc, exc_info=True
        )
        try:
            await client.delete_trade_offer_draft(offer_id)
            logger.info("Deleted trade offer draft for offerId=%s", offer_id)
        except Exception as del_exc:
            logger.warning(
                "Could not delete draft for offerId=%s: %s", offer_id, del_exc
            )


# ── historyOutdate re-sync ────────────────────────────────────────────────────

async def send_steam_session(client: CsMoneyClient, steam: SteamClient) -> None:
    """
    For historyOutdate=true: send encrypted session credentials to CS.Money
    via POST /4.0/market/offers/session so CS.Money can re-sync trade state.
    """
    logger.info("Sending Steam session to CS.Money (historyOutdate re-sync)…")
    try:
        key_info = await client.get_security_key()
        enc_id, enc_data = await _encrypt_session(steam, key_info["publicKey"])
        await client.send_session(
            session_id=enc_id,
            session_data=enc_data,
            correlation_id=key_info["correlationId"],
        )
        logger.info("Steam session submitted successfully.")
    except SessionExpiredError:
        raise
    except Exception as exc:
        logger.error("Failed to send Steam session: %s", exc)


# ── active-offers checker ─────────────────────────────────────────────────────

async def process_active_offers(client: CsMoneyClient, steam: SteamClient) -> None:
    """
    Check active offers and handle:
      • historyOutdate=true  → send session credentials
      • offer.status CREATING (no steamOfferId yet)  → create trade offer
    """
    try:
        data = await client.get_active_offers()
        offers = data.get("activeOffers", [])
        history_outdate = data.get("historyOutdate", False)

        logger.info(
            "Active offers: count=%d historyOutdate=%s", len(offers), history_outdate
        )
        logger.debug("Active offers full response: %s", data)

        if history_outdate:
            logger.info("historyOutdate=true — sending session re-sync.")
            await send_steam_session(client, steam)

        for offer in offers:
            offer_id = offer.get("id")
            status = offer.get("status")
            steam_offer_id = offer.get("steamOfferId")
            offer_type = offer.get("type", "")

            logger.info(
                "Offer id=%s type=%s status=%s steamOfferId=%s",
                offer_id, offer_type, status, steam_offer_id,
            )

            # Create trade for SELL offers that haven't been sent to Steam yet
            if offer_type == "SELL" and status == "CREATING" and not steam_offer_id:
                await create_trade_for_offer(client, steam, offer)

    except SessionExpiredError:
        raise
    except Exception as exc:
        logger.error("Error checking active offers: %s", exc)


# ── notification poller ───────────────────────────────────────────────────────

async def handle_notification(
    client: CsMoneyClient, steam: SteamClient, notification: dict
) -> None:
    ntype = notification.get("type")
    nid = notification.get("id")
    item_name = notification.get("data", {}).get("item", {}).get("name", "unknown item")

    if ntype == OFFER_BOUGHT:
        logger.info("OFFER_BOUGHT notification for '%s' (id=%s)", item_name, nid)
        try:
            await client.mark_notifications_viewed([nid])
        except Exception as exc:
            logger.warning("Could not mark notification %s as viewed: %s", nid, exc)
        # Immediately check active offers so we don't wait for the periodic interval
        await process_active_offers(client, steam)
    else:
        logger.debug("Ignoring notification type=%s id=%s", ntype, nid)


async def run_notification_poller(
    client: CsMoneyClient, steam: SteamClient, stop_event: asyncio.Event
) -> None:
    updated_from = int(time.time() * 1000)
    logger.info("Starting notification poller (updatedFrom=%d)", updated_from)

    while not stop_event.is_set():
        try:
            data = await client.get_notifications(updated_from)
            notifications = data.get("notifications", [])

            for n in notifications:
                updated_from = max(updated_from, n.get("date", 0) + 1)
                await handle_notification(client, steam, n)

            if not notifications:
                updated_from = int(time.time() * 1000)

        except asyncio.CancelledError:
            break
        except SessionExpiredError:
            logger.warning("Session expired during notification poll — re-logging in…")
            try:
                await relogin(client, steam)
            except Exception as exc:
                logger.error("Re-login failed: %s", exc)
        except Exception as exc:
            logger.error("Notification poll error: %s", exc)

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()), timeout=config.POLL_INTERVAL
            )
        except asyncio.TimeoutError:
            pass


# ── confirmation poller ───────────────────────────────────────────────────────

async def run_confirmation_poller(
    steam: SteamClient, stop_event: asyncio.Event
) -> None:
    """Catch any trade confirmations that create_trade_for_offer may have missed."""
    logger.info("Starting confirmation poller (interval=%ds)", CONFIRMATION_INTERVAL)
    while not stop_event.is_set():
        try:
            confs = await steam.get_confirmations()
            if confs:
                logger.info(
                    "Pending confirmations: total=%d types=%s",
                    len(confs),
                    [c.type.name for c in confs],
                )
            trade_confs = [c for c in confs if c.type is ConfirmationType.TRADE]
            if trade_confs:
                await steam.allow_multiple_confirmations(trade_confs)
                logger.info("Auto-confirmed %d trade confirmation(s).", len(trade_confs))
        except Exception as exc:
            logger.error("Confirmation poll error: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()), timeout=CONFIRMATION_INTERVAL
            )
        except asyncio.TimeoutError:
            pass


# ── active-offers periodic checker ───────────────────────────────────────────

async def run_active_offers_checker(
    client: CsMoneyClient, steam: SteamClient, stop_event: asyncio.Event
) -> None:
    logger.info(
        "Starting active-offers checker (interval=%ds)", CHECK_ACTIVE_OFFERS_INTERVAL
    )
    while not stop_event.is_set():
        try:
            await process_active_offers(client, steam)
        except SessionExpiredError:
            logger.warning("Session expired during offers check — re-logging in…")
            try:
                await relogin(client, steam)
            except Exception as exc:
                logger.error("Re-login failed: %s", exc)
        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=CHECK_ACTIVE_OFFERS_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


# ── entry point ───────────────────────────────────────────────────────────────

async def run(stop_event: asyncio.Event) -> None:
    config.validate_config()

    # 1. Steam login (fast token restore when possible)
    steam = SteamClient(
        config.STEAM_ID,
        config.STEAM_USERNAME,
        config.STEAM_PASSWORD,
        config.STEAM_SHARED_SECRET,
        identity_secret=config.STEAM_IDENTITY_SECRET,
        proxy=config.STEAM_PROXY or None,
    )
    await _steam_login(steam)

    # 2. CS.Money session (saved or fresh OpenID)
    cookies = _load_saved_cookies()
    proxy = config.CSMONEY_PROXY or None

    async with primp.AsyncClient(
        impersonate="chrome_144",
        impersonate_os="windows",
        proxy=proxy,
    ) as http:
        client = CsMoneyClient(http, cookies)

        if cookies:
            try:
                store = await client.get_user_store()
                logger.info(
                    "Resumed session from saved cookies. Store status: %s",
                    store.get("status", "?"),
                )
            except SessionExpiredError:
                logger.info("Saved cookies expired — performing OpenID login…")
                cookies = await _do_openid_login(steam)
                client.update_cookies(cookies)
                _save_cookies(cookies)
                store = await client.get_user_store()
                logger.info(
                    "Connected to CS.Money. Store status: %s", store.get("status", "?")
                )
            except Exception as exc:
                logger.error("Startup check failed: %s", exc)
                return
        else:
            logger.info("No saved cookies — performing OpenID login…")
            try:
                cookies = await _do_openid_login(steam)
                client.update_cookies(cookies)
                _save_cookies(cookies)
                store = await client.get_user_store()
                logger.info(
                    "Connected to CS.Money. Store status: %s", store.get("status", "?")
                )
            except Exception as exc:
                logger.error("Startup failed: %s", exc)
                return

        # 3. Initial active-offers check
        await process_active_offers(client, steam)

        await asyncio.gather(
            run_notification_poller(client, steam, stop_event),
            run_active_offers_checker(client, steam, stop_event),
            run_confirmation_poller(steam, stop_event),
        )
