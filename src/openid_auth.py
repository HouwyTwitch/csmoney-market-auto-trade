"""
CS.Money OpenID authentication via Steam.

Flow:
  1. GET auth.dota.trade/login  → redirects to Steam OpenID URL
  2. GET Steam OpenID page      → extract hidden form fields
  3. POST steamcommunity.com/openid/login → redirects to cs.money callback
  4. GET cs.money callback       → sets csgo_ses session cookie
"""

import logging

from curl_cffi.requests import Session

import config

logger = logging.getLogger(__name__)

_LOGIN_URL = (
    "https://auth.dota.trade/login"
    "?redirectUrl=https://cs.money/"
    "&callbackUrl=https://cs.money/login"
)
_STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
_OPENID_FIELDS = ["openidparams", "openid.mode", "action", "nonce"]


def _make_steam_session() -> Session:
    session = Session(impersonate="chrome136", http_version=2, verify=False)
    session.headers.update({"user-agent": config.USER_AGENT})
    session.cookies.update(
        {
            "steamLoginSecure": config.STEAM_LOGIN_SECURE,
            "sessionid": config.STEAM_SESSION_ID,
        }
    )
    if config.STEAM_PROXY:
        session.proxies = {
            "http": config.STEAM_PROXY,
            "https": config.STEAM_PROXY,
        }
    return session


def _make_csmoney_session() -> Session:
    session = Session(impersonate="chrome136", http_version=3, verify=False)
    session.headers.update(
        {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "accept-language": "en-US,en;q=0.9",
            "priority": "u=0, i",
            "referer": "https://cs.money/",
            "upgrade-insecure-requests": "1",
            "user-agent": config.USER_AGENT,
        }
    )
    if config.CSMONEY_PROXY:
        session.proxies = {
            "http": config.CSMONEY_PROXY,
            "https": config.CSMONEY_PROXY,
        }
    return session


def _get_auth_link(csmoney_session: Session, login_url: str) -> str:
    response = csmoney_session.get(login_url, allow_redirects=False)
    location = response.headers.get("Location", "")
    if not location:
        raise RuntimeError(
            f"No redirect from login URL — HTTP {response.status_code}"
        )
    return location


def _extract_openid_fields(html: str) -> dict:
    fields = {}
    for name in _OPENID_FIELDS:
        marker = f'name="{name}" value="'
        if marker in html:
            fields[name] = (None, html.split(marker)[-1].split('"')[0])
        else:
            fields[name] = (None, "")
    return fields


def _submit_openid(steam_session: Session, auth_link: str) -> str:
    response = steam_session.get(auth_link, allow_redirects=False)
    content = response.content.decode("utf-8", errors="ignore")
    fields = _extract_openid_fields(content)

    steam_session.headers.update(
        {
            "Origin": "https://steamcommunity.com",
            "Referer": auth_link,
        }
    )
    response = steam_session.post(
        _STEAM_OPENID_URL,
        files=fields,
        allow_redirects=False,
    )
    location = response.headers.get("Location", "")
    if not location:
        raise RuntimeError(
            f"Steam OpenID did not redirect — HTTP {response.status_code}"
        )
    return location


def openid_login(login_url: str = _LOGIN_URL) -> str:
    """
    Run the full OpenID flow and return the csgo_ses cookie value.
    """
    logger.info("Starting CS.Money OpenID login…")

    steam_session = _make_steam_session()
    csmoney_session = _make_csmoney_session()

    auth_link = _get_auth_link(csmoney_session, login_url)
    logger.debug("Steam OpenID URL: %s", auth_link)

    csmoney_callback = _submit_openid(steam_session, auth_link)
    logger.debug("CS.Money callback URL: %s", csmoney_callback)

    csmoney_session.get(csmoney_callback)

    csgo_ses = csmoney_session.cookies.get("csgo_ses")
    if not csgo_ses:
        raise RuntimeError(
            "OpenID login failed — 'csgo_ses' cookie not present after callback"
        )

    logger.info("OpenID login successful.")
    return csgo_ses
