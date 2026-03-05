"""
Send Steam trade offers directly via the Steam web API.

This replicates the extension's `sendTradeOffer()` function which builds
a multipart form and posts it to steamcommunity.com/tradeoffer/new/send.
"""

import json
import logging

from curl_cffi.requests import Session

from . import config

logger = logging.getLogger(__name__)

_SEND_URL = "https://steamcommunity.com/tradeoffer/new/send"


def send_steam_trade_offer(
    steam_login_secure: str,
    session_id: str,
    partner_steam_id: str,
    partner_token: str,
    assets: list[dict],
    offer_id: int,
    message: str = "",
    proxy: str = "",
) -> str:
    """
    Send a Steam trade offer and return the ``tradeofferid`` string.

    Parameters mirror the object that the extension passes to sendTradeOffer():
      - steam_login_secure : full steamLoginSecure cookie value
      - session_id         : Steam sessionid cookie value
      - partner_steam_id   : buyer's Steam64 ID (string or int)
      - partner_token      : buyer's trade access token
      - assets             : list of asset dicts as returned by CS.Money's
                             POST /3.0/market/offers/tradeoffer response
                             e.g. [{"appid":730,"contextid":"2","assetid":"...","amount":1}]
      - offer_id           : CS.Money offer ID (used in the trade message)
      - message            : optional override for the trade message
      - proxy              : optional proxy URL
    """
    s = Session(impersonate="chrome136")
    if proxy:
        s.proxies = {"https": proxy, "http": proxy}

    s.cookies.set("steamLoginSecure", steam_login_secure, domain="steamcommunity.com")
    s.cookies.set("sessionid", session_id, domain="steamcommunity.com")

    partner_steam_id = str(partner_steam_id)
    partner_account_id = int(partner_steam_id) & 0xFFFFFFFF  # 32-bit account ID for Referer

    json_tradeoffer = {
        "newversion": True,
        "version": len(assets) + 1,
        "me": {"assets": assets, "currency": [], "ready": False},
        "them": {"assets": [], "currency": [], "ready": False},
    }

    trade_message = (
        message or f"Automatically generated from CS.MONEY Market. OfferID: {offer_id}"
    )

    data = {
        "sessionid": session_id,
        "serverid": "1",
        "partner": partner_steam_id,
        "trade_offer_create_params": json.dumps({"trade_offer_access_token": partner_token}),
        "json_tradeoffer": json.dumps(json_tradeoffer),
        "tradeoffermessage": trade_message,
        "captcha": "",
    }

    referer = (
        f"https://steamcommunity.com/tradeoffer/new/"
        f"?partner={partner_account_id}&token={partner_token}"
    )

    logger.debug(
        "Sending Steam trade offer: partner=%s assets=%s", partner_steam_id, assets
    )

    resp = s.post(
        _SEND_URL,
        data=data,
        headers={
            "Referer": referer,
            "Origin": f"chrome-extension://{config.EXTENSION_ID}",
            "Accept": "application/json",
            "User-Agent": config.USER_AGENT,
        },
    )

    if resp.status_code != 200:
        logger.error(
            "Steam trade offer failed: HTTP %d — %s", resp.status_code, resp.text[:500]
        )
        resp.raise_for_status()

    result = resp.json()
    logger.debug("Steam trade offer response: %s", result)

    if "tradeofferid" not in result:
        raise RuntimeError(
            f"Steam did not return tradeofferid. "
            f"strError={result.get('strError')} Response={result}"
        )

    trade_offer_id = result["tradeofferid"]
    logger.info("Steam trade offer sent: tradeofferid=%s", trade_offer_id)
    return trade_offer_id
