"""
Main processing loop for CS.Money auto-sale tool.

Flow (mirrors the CS.Money Chrome extension service worker):
  1. Poll /1.0/market/notifications every POLL_INTERVAL seconds.
  2. When an OFFER_BOUGHT notification appears:
       a. Mark it viewed.
       b. Fetch active offers.
       c. If historyOutdate=true → send encrypted Steam session.
  3. Every CHECK_ACTIVE_OFFERS_INTERVAL seconds also check active offers
     directly (catches cases where notification was missed).
  4. If historyOutdate is signalled, send the session unconditionally.
"""

import asyncio
import functools
import logging
import time

import primp
from aiosteampy import SteamClient
from aiosteampy.models import ConfirmationType

from . import config
from .csmoney_client import CsMoneyClient
from .openid_auth import openid_login
from .session_crypto import encrypt_message

logger = logging.getLogger(__name__)

OFFER_BOUGHT = "OFFER_BOUGHT"
CHECK_ACTIVE_OFFERS_INTERVAL = 360  # 6 minutes, same as extension alarm
CONFIRMATION_INTERVAL = 15  # seconds between confirmation polls


def _steam_login_secure(steam: SteamClient) -> str:
    """Return the full steamLoginSecure cookie value: {steamId}%7C%7C{accessToken}."""
    return f"{steam.steam_id}%7C%7C{steam.access_token}"


async def send_steam_session(client: CsMoneyClient, steam: SteamClient) -> None:
    """Encrypt Steam cookies and submit them to CS.Money."""
    logger.info("Sending Steam session to CS.Money...")
    try:
        if steam.is_access_token_expired:
            logger.info("Steam access token expired — refreshing…")
            await steam.refresh_access_token()

        key_info = await client.get_security_key()
        public_key = key_info["publicKey"]
        correlation_id = key_info["correlationId"]

        encrypted_session_data = encrypt_message(public_key, _steam_login_secure(steam))
        encrypted_session_id = encrypt_message(public_key, steam.session_id)

        await client.send_session(
            session_id=encrypted_session_id,
            session_data=encrypted_session_data,
            correlation_id=correlation_id,
        )
        logger.info("Steam session submitted successfully.")
    except Exception as exc:
        logger.error("Failed to send Steam session: %s", exc)


async def process_active_offers(client: CsMoneyClient, steam: SteamClient) -> None:
    """Check active offers and send session if CS.Money requests it."""
    try:
        data = await client.get_active_offers()
        offers = data.get("activeOffers", [])
        history_outdate = data.get("historyOutdate", False)

        logger.debug(
            "Active offers: %d | historyOutdate: %s", len(offers), history_outdate
        )

        if history_outdate:
            logger.info(
                "historyOutdate=true — CS.Money needs Steam session to process trades."
            )
            await send_steam_session(client, steam)
        elif offers:
            creating = [o for o in offers if o.get("status") == "CREATING"]
            if creating:
                logger.info(
                    "%d offer(s) in CREATING state — sending Steam session.", len(creating)
                )
                await send_steam_session(client, steam)
    except Exception as exc:
        logger.error("Error checking active offers: %s", exc)


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
        await process_active_offers(client, steam)
    else:
        logger.debug("Ignoring notification type=%s id=%s", ntype, nid)


async def run_notification_poller(
    client: CsMoneyClient, steam: SteamClient, stop_event: asyncio.Event
):
    """Poll the notifications endpoint continuously."""
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
        except Exception as exc:
            logger.error("Notification poll error: %s", exc)

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()), timeout=config.POLL_INTERVAL
            )
        except asyncio.TimeoutError:
            pass


async def run_confirmation_poller(steam: SteamClient, stop_event: asyncio.Event):
    """Periodically fetch and auto-confirm pending Steam trade confirmations."""
    logger.info(
        "Starting confirmation poller (interval=%ds)", CONFIRMATION_INTERVAL
    )
    while not stop_event.is_set():
        try:
            confs = await steam.get_confirmations()
            trade_confs = [c for c in confs if c.type is ConfirmationType.TRADE]
            if trade_confs:
                await steam.allow_multiple_confirmations(trade_confs)
                logger.info("Auto-confirmed %d trade confirmation(s).", len(trade_confs))
        except Exception as exc:
            logger.error("Confirmation poll error: %s", exc)

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()), timeout=CONFIRMATION_INTERVAL
            )
        except asyncio.TimeoutError:
            pass


async def run_active_offers_checker(
    client: CsMoneyClient, steam: SteamClient, stop_event: asyncio.Event
):
    """Periodically check active offers independent of notifications."""
    logger.info(
        "Starting active-offers checker (interval=%ds)", CHECK_ACTIVE_OFFERS_INTERVAL
    )
    while not stop_event.is_set():
        await process_active_offers(client, steam)
        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=CHECK_ACTIVE_OFFERS_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


async def run(stop_event: asyncio.Event):
    config.validate_config()

    # 1. Login to Steam via aiosteampy
    steam = SteamClient(
        config.STEAM_USERNAME,
        config.STEAM_PASSWORD,
        shared_secret=config.STEAM_SHARED_SECRET,
        identity_secret=config.STEAM_IDENTITY_SECRET,
        proxy=config.STEAM_PROXY or None,
    )
    logger.info("Logging in to Steam as %s…", config.STEAM_USERNAME)
    await steam.login()
    logger.info("Steam login successful (steamId=%s)", steam.steam_id)

    # 2. OpenID login to CS.Money using the Steam session cookies
    loop = asyncio.get_event_loop()
    csgo_ses = await loop.run_in_executor(
        None,
        functools.partial(
            openid_login,
            _steam_login_secure(steam),
            steam.session_id,
        ),
    )

    # 3. Run the CS.Money processing loop
    proxy = config.CSMONEY_PROXY or None
    async with primp.AsyncClient(
        impersonate="chrome_144",
        impersonate_os="windows",
        proxy=proxy,
    ) as http:
        client = CsMoneyClient(http, csgo_ses)

        # Verify credentials by checking user store on startup
        try:
            store = await client.get_user_store()
            logger.info(
                "Connected to CS.Money. Store status: %s", store.get("status", "?")
            )
        except Exception as exc:
            logger.error("Startup check failed: %s", exc)
            return

        # Run an initial active-offers check immediately
        await process_active_offers(client, steam)

        await asyncio.gather(
            run_notification_poller(client, steam, stop_event),
            run_active_offers_checker(client, steam, stop_event),
            run_confirmation_poller(steam, stop_event),
        )
