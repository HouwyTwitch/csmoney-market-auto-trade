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
import logging
import time

import aiohttp

import config
from csmoney_client import CsMoneyClient
from openid_auth import openid_login
from session_crypto import encrypt_message

logger = logging.getLogger(__name__)

OFFER_BOUGHT = "OFFER_BOUGHT"
CHECK_ACTIVE_OFFERS_INTERVAL = 360  # 6 minutes, same as extension alarm


async def send_steam_session(client: CsMoneyClient) -> None:
    """Encrypt Steam cookies and submit them to CS.Money."""
    logger.info("Sending Steam session to CS.Money...")
    try:
        key_info = await client.get_security_key()
        public_key = key_info["publicKey"]
        correlation_id = key_info["correlationId"]

        encrypted_session_data = encrypt_message(public_key, config.STEAM_LOGIN_SECURE)
        encrypted_session_id = encrypt_message(public_key, config.STEAM_SESSION_ID)

        await client.send_session(
            session_id=encrypted_session_id,
            session_data=encrypted_session_data,
            correlation_id=correlation_id,
        )
        logger.info("Steam session submitted successfully.")
    except Exception as exc:
        logger.error("Failed to send Steam session: %s", exc)


async def process_active_offers(client: CsMoneyClient) -> None:
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
            await send_steam_session(client)
        elif offers:
            creating = [o for o in offers if o.get("status") == "CREATING"]
            if creating:
                logger.info(
                    "%d offer(s) in CREATING state — sending Steam session.", len(creating)
                )
                await send_steam_session(client)
    except Exception as exc:
        logger.error("Error checking active offers: %s", exc)


async def handle_notification(client: CsMoneyClient, notification: dict) -> None:
    ntype = notification.get("type")
    nid = notification.get("id")
    item_name = notification.get("data", {}).get("item", {}).get("name", "unknown item")

    if ntype == OFFER_BOUGHT:
        logger.info("OFFER_BOUGHT notification for '%s' (id=%s)", item_name, nid)
        try:
            await client.mark_notifications_viewed([nid])
        except Exception as exc:
            logger.warning("Could not mark notification %s as viewed: %s", nid, exc)
        await process_active_offers(client)
    else:
        logger.debug("Ignoring notification type=%s id=%s", ntype, nid)


async def run_notification_poller(client: CsMoneyClient, stop_event: asyncio.Event):
    """Poll the notifications endpoint continuously."""
    updated_from = int(time.time() * 1000)
    logger.info("Starting notification poller (updatedFrom=%d)", updated_from)

    while not stop_event.is_set():
        try:
            data = await client.get_notifications(updated_from)
            notifications = data.get("notifications", [])

            for n in notifications:
                updated_from = max(updated_from, n.get("date", 0) + 1)
                await handle_notification(client, n)

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


async def run_active_offers_checker(client: CsMoneyClient, stop_event: asyncio.Event):
    """Periodically check active offers independent of notifications."""
    logger.info(
        "Starting active-offers checker (interval=%ds)", CHECK_ACTIVE_OFFERS_INTERVAL
    )
    while not stop_event.is_set():
        await process_active_offers(client)
        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=CHECK_ACTIVE_OFFERS_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


async def run(stop_event: asyncio.Event):
    config.validate_config()

    csgo_ses = await asyncio.get_event_loop().run_in_executor(None, openid_login)

    connector = aiohttp.TCPConnector(ssl=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        client = CsMoneyClient(session, csgo_ses)

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
        await process_active_offers(client)

        await asyncio.gather(
            run_notification_poller(client, stop_event),
            run_active_offers_checker(client, stop_event),
        )
