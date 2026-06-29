"""
CS.Money OpenID authentication via Steam.

Flow:
  1. GET the cs.money / auth.dota.trade login URL and follow the redirect
     chain until it lands on the Steam OpenID approval page.
  2. GET the Steam OpenID page with the Steam session:
       a. If Steam redirected off-domain it auto-approved — that URL IS the
          auth.dota.trade callback, use it directly.
       b. Otherwise extract the hidden form fields, read the nonce from the
          `sessionidSecureOpenIDNonce` cookie Steam set on this GET (Steam no
          longer embeds the nonce in the form body), and POST the form to
          /openid/login (multipart) → 302 to the auth.dota.trade callback.
  3. Follow the auth.dota.trade callback through cs.money/login?token → cs.money
     with the cs.money session, which sets the `csgo_ses` cookie.
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
    "&redirectQuery="
)
_STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
# Hidden fields submitted back to Steam. The nonce is NOT in this list — Steam
# now delivers it via the `sessionidSecureOpenIDNonce` cookie instead of the form.
_OPENID_FIELDS = ["openidparams", "openid.mode", "action"]
# Maximum redirect hops to follow while resolving the Steam OpenID page.
_MAX_AUTH_HOPS = 5


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
    """
    Follow the login redirect chain (cs.money → auth.dota.trade → Steam) until
    it lands on the Steam OpenID page, and return that URL.
    """
    url = login_url
    for hop in range(_MAX_AUTH_HOPS):
        response = csmoney_session.get(url, allow_redirects=False)
        location = response.headers.get("Location", "")
        logger.debug("auth hop %d: %s → %s (HTTP %d)", hop, url, location, response.status_code)
        if not location:
            if hop == 0:
                raise RuntimeError(
                    f"No redirect from login URL — HTTP {response.status_code}"
                )
            # No further redirect; the last resolved URL is the OpenID page.
            return url
        if "steamcommunity.com/openid" in location:
            return location
        url = location
    raise RuntimeError("Exceeded redirect hops without reaching the Steam OpenID page")


def _extract_openid_fields(html: str) -> dict:
    fields = {}
    for name in _OPENID_FIELDS:
        marker = f'name="{name}" value="'
        fields[name] = html.split(marker)[-1].split('"')[0] if marker in html else ""
    return fields


def _build_multipart(fields: dict) -> CurlMime:
    m = CurlMime()
    for name, value in fields.items():
        m.addpart(name=name, data=(value or "").encode())
    return m


def _get_openid_fields(steam_session: Session, auth_link: str) -> tuple[dict, str]:
    """
    GET the Steam OpenID approval page and return (hidden form fields, final URL).

    The nonce comes from the `sessionidSecureOpenIDNonce` cookie Steam sets on
    this response, not from the form body (with a fallback to the legacy form
    field for older Steam page layouts).
    """
    response = steam_session.get(auth_link)
    final_url = response.url
    content = response.content.decode("utf-8", errors="ignore")
    fields = _extract_openid_fields(content)

    nonce = steam_session.cookies.get("sessionidSecureOpenIDNonce", "")
    if not nonce:
        marker = 'name="nonce" value="'
        nonce = content.split(marker)[-1].split('"')[0] if marker in content else ""
    fields["nonce"] = nonce

    return fields, final_url


def _submit_openid(steam_session: Session, auth_link: str) -> str:
    """
    Approve the Steam OpenID request and return the auth.dota.trade callback URL.
    """
    fields, final_url = _get_openid_fields(steam_session, auth_link)
    logger.debug("Steam OpenID GET ended at: %s", final_url)

    # If Steam auto-approved and redirected off-domain, that IS the callback URL.
    if "steamcommunity.com" not in final_url:
        logger.debug("Steam auto-approved, callback: %s", final_url)
        return final_url

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
            f"Steam OpenID did not redirect after POST — HTTP {response.status_code}"
        )
    return location


def _follow_callback(csmoney_session: Session, csmoney_callback: str) -> None:
    """
    Follow the auth.dota.trade callback through cs.money/login?token → cs.money,
    letting the cs.money session collect the `csgo_ses` cookie.
    """
    csmoney_session.headers.update({"Referer": "https://steamcommunity.com/"})

    r1 = csmoney_session.get(csmoney_callback, allow_redirects=False)
    token_url = r1.headers.get("Location", "")
    logger.debug("auth.dota.trade → %s (HTTP %d)", token_url, r1.status_code)
    if not token_url:
        logger.debug("No Location from auth.dota.trade; assuming chain already followed.")
        return

    r2 = csmoney_session.get(token_url, allow_redirects=False)
    logger.debug("cs.money/login?token → HTTP %d", r2.status_code)

    final_url = r2.headers.get("Location", "https://cs.money/")
    csmoney_session.get(final_url)


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

    _follow_callback(csmoney_session, csmoney_callback)

    cookies = dict(csmoney_session.cookies)
    logger.debug("Cookies after callback: %s", list(cookies.keys()))

    if not cookies.get("csgo_ses"):
        raise RuntimeError(
            "OpenID login failed — 'csgo_ses' cookie not present after callback. "
            "Set LOG_LEVEL=DEBUG for full diagnostic output."
        )

    logger.info("OpenID login successful.")
    return cookies
