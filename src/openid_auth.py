"""
CS.Money OpenID authentication via Steam.

Flow:
  1. GET auth.dota.trade/login  → redirects to Steam OpenID URL
  2. GET Steam OpenID page (allow_redirects=True to absorb CDN/Akamai hops)
       a. Final URL still on steamcommunity.com → extract hidden form fields,
          POST to /openid/login (multipart) → 302 to cs.money callback
       b. Final URL off Steam → Steam auto-approved, use it directly as callback
  3. GET cs.money callback (via csmoney_session) → sets csgo_ses cookie
"""

import logging

from curl_cffi import CurlMime
from curl_cffi.requests import Session

from . import config

logger = logging.getLogger(__name__)

_LOGIN_URL = (
    "https://auth.dota.trade/login"
    "?redirectUrl=https://cs.money/"
    "&callbackUrl=https://cs.money/login"
)
_STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
_OPENID_FIELDS = ["openidparams", "openid.mode", "action", "nonce"]


def _make_steam_session(steam_login_secure: str, session_id: str) -> Session:
    session = Session(impersonate="chrome136", http_version=2, verify=False)
    session.headers.update({"user-agent": config.USER_AGENT})
    session.cookies.update(
        {
            "steamLoginSecure": steam_login_secure,
            "sessionid": session_id,
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
        fields[name] = html.split(marker)[-1].split('"')[0] if marker in html else ""
    return fields


def _build_multipart(fields: dict) -> CurlMime:
    m = CurlMime()
    for name, value in fields.items():
        m.addpart(name=name, data=value.encode())
    return m


def _submit_openid(steam_session: Session, auth_link: str) -> str:
    # Follow redirects so Akamai CDN hops are transparent; land on the actual form.
    response = steam_session.get(auth_link)
    final_url = response.url
    logger.debug("Steam OpenID GET ended at: %s (status: %d)", final_url, response.status_code)

    # If Steam auto-approved and redirected off-domain, that IS the callback URL.
    if "steamcommunity.com" not in final_url:
        logger.debug("Steam auto-approved, callback: %s", final_url)
        return final_url

    # We landed on the approval form — extract hidden fields and POST.
    content = response.content.decode("utf-8", errors="ignore")
    fields = _extract_openid_fields(content)
    logger.debug(
        "OpenID form fields: %s",
        {k: (v[:30] + "…" if len(v) > 30 else v) for k, v in fields.items()},
    )

    steam_session.headers.update(
        {
            "Origin": "https://steamcommunity.com",
            "Referer": final_url,
        }
    )
    response = steam_session.post(
        _STEAM_OPENID_URL,
        multipart=_build_multipart(fields),
        allow_redirects=False,
    )
    logger.debug("Steam OpenID POST status: %d", response.status_code)
    location = response.headers.get("Location", "")
    if not location:
        raise RuntimeError(
            f"Steam OpenID did not redirect — HTTP {response.status_code}"
        )
    return location


def openid_login(
    steam_login_secure: str, session_id: str, login_url: str = _LOGIN_URL
) -> dict:
    """
    Run the full OpenID flow and return all CS.Money cookies as a dict.
    The dict is guaranteed to contain the 'csgo_ses' key.
    """
    logger.info("Starting CS.Money OpenID login…")

    steam_session = _make_steam_session(steam_login_secure, session_id)
    csmoney_session = _make_csmoney_session()

    auth_link = _get_auth_link(csmoney_session, login_url)
    logger.debug("Steam OpenID URL: %s", auth_link)

    csmoney_callback = _submit_openid(steam_session, auth_link)
    logger.debug("CS.Money callback URL obtained.")

    csmoney_session.get(csmoney_callback)

    cookies = dict(csmoney_session.cookies)
    logger.debug("Cookies after callback: %s", list(cookies.keys()))

    if not cookies.get("csgo_ses"):
        raise RuntimeError(
            "OpenID login failed — 'csgo_ses' cookie not present after callback. "
            "Set LOG_LEVEL=DEBUG for full diagnostic output."
        )

    logger.info("OpenID login successful.")
    return cookies
