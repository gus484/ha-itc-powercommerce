"""Async client for ITC PowerCommerce customer portals.

The portal path pattern is ``<host>/powercommerce/<tenant>/fo/portal/<view>``.
For Stadtwerke Elbtal the tenant is ``swet``.

Observed flow (from Phase 0 recon):
    GET  /start          -> establishes JSESSIONID
    POST /loginProcess   -> 302 to /home on success
    GET  /meterWidget.json  -> meters + latest reading (JSON)
    GET  /meterDetails      -> reading history (HTML table)

Login is a plain form POST of exactly three fields; the captures show no
Spring Web Flow ``execution`` token, no CSRF field, and no hidden inputs on
the login form at all.

This module deliberately knows nothing about Home Assistant. The caller owns
the :class:`aiohttp.ClientSession` and passes it in — inside Home Assistant
that is ``async_create_clientsession(hass)``, in the CLI a session of its own.
Authentication is a session cookie, so the session should not be one whose
cookie jar is shared with unrelated code.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import aiohttp
from yarl import URL

from .exceptions import AuthError, ParseError, PortalError, SessionExpired
from .models import Meter, Reading
from .parsing import (
    is_login_page,
    parse_latest_readings,
    parse_meter_widget,
    parse_reading_history,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "https://onlineservice.stadtwerke-elbtal.de"
DEFAULT_TENANT = "swet"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# meterWidget.json is requested with the same query the portal's own frontend
# uses; the cache-busting `_` param it also sends is not needed.
METER_WIDGET_VIEW = "meterWidget.json?responsiveDesign=true"


class ITCPowerCommerceClient:
    """Reads meters and readings from an ITC PowerCommerce portal.

    The session is supplied and owned by the caller::

        client = ITCPowerCommerceClient(user, password, session)
        await client.login()
        meters = await client.get_meters()
    """

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        *,
        host: str = DEFAULT_HOST,
        tenant: str = DEFAULT_TENANT,
    ) -> None:
        self._username = username
        self._password = password
        self._host = host.rstrip("/")
        self._cookie_domain = URL(self._host).host or ""
        self._tenant = tenant
        self._base = f"{self._host}/powercommerce/{tenant}/fo/portal"
        self._session = session
        self._logged_in = False

    # -- introspection ------------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def tenant(self) -> str:
        return self._tenant

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    # -- low-level helpers --------------------------------------------------

    def _url(self, view: str) -> str:
        return f"{self._base}/{view}"

    async def _get_text(self, view: str) -> str:
        async with self._session.get(
            self._url(view), headers={"User-Agent": USER_AGENT}
        ) as resp:
            return await resp.text()

    async def _get_json(self, view: str) -> dict:
        async with self._session.get(
            self._url(view),
            headers={
                "User-Agent": USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
            },
        ) as resp:
            text = await resp.text()
        if is_login_page(text):
            raise SessionExpired(f"{view} returned the login page")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ParseError(f"{view} did not return JSON") from exc

    # -- authentication -----------------------------------------------------

    async def login(self) -> None:
        """Authenticate. Raises :class:`AuthError` on failure."""
        # 0. Start from an empty jar for the portal. A login on top of an old
        #    JSESSIONID is not a fresh login: after a config entry reload the
        #    portal accepted it, then served a meterWidget.json without meters,
        #    and rejected the very next attempt as bad credentials.
        self._session.cookie_jar.clear_domain(self._cookie_domain)
        self._logged_in = False

        # 1. GET /start to obtain a JSESSIONID.
        await self._get_text("start")

        # 2. POST the three fields the portal's login form actually submits.
        payload = {
            "login": self._username,
            "password": self._password,
            "twoFactorAuthenticationCode": "",
        }
        async with self._session.post(
            self._url("loginProcess"),
            data=payload,
            headers={"User-Agent": USER_AGENT},
        ) as resp:
            landing = await resp.text()

        # On success the portal 302-redirects to /home (aiohttp follows it).
        # On failure it re-renders the login page.
        if is_login_page(landing):
            raise AuthError("login failed: check credentials or 2FA requirement")
        self._logged_in = True

    async def async_ensure_login(self) -> None:
        """Log in unless this client already holds a session."""
        if not self._logged_in:
            await self.login()

    async def _ensure_session(self, coro_factory):
        """Run a request; on :class:`SessionExpired`, re-login once and retry."""
        try:
            return await coro_factory()
        except SessionExpired:
            _LOGGER.info("session expired, re-logging in once")
            self._logged_in = False
            await self.login()
            try:
                return await coro_factory()
            except SessionExpired as exc:
                raise AuthError("session could not be recovered") from exc

    # -- data ---------------------------------------------------------------

    async def get_meters(self) -> list[Meter]:
        """Return the meters on the account (from ``meterWidget.json``)."""
        self._require_login()
        payload = await self._ensure_session(lambda: self._get_json(METER_WIDGET_VIEW))
        return parse_meter_widget(payload)

    async def get_latest_readings(self) -> list[Reading]:
        """Return the most recent reading per meter/tariff (JSON, fast path)."""
        self._require_login()
        payload = await self._ensure_session(lambda: self._get_json(METER_WIDGET_VIEW))
        return parse_latest_readings(payload)

    async def get_meters_and_readings(self) -> tuple[list[Meter], list[Reading]]:
        """Return meters and their latest readings from a single request.

        The coordinator needs both on every poll; calling the two methods
        above would fetch the same payload twice.
        """
        self._require_login()
        payload = await self._ensure_session(lambda: self._get_json(METER_WIDGET_VIEW))
        return parse_meter_widget(payload), parse_latest_readings(payload)

    async def get_readings(
        self,
        meter_no: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Reading]:
        """Return the reading history for ``meter_no`` from ``meterDetails``.

        ``start``/``end`` filter the returned rows inclusively; the portal page
        itself returns the full available history.
        """
        self._require_login()
        html = await self._ensure_session(lambda: self._get_meter_details())
        readings = parse_reading_history(html, meter_no)
        if start is not None:
            readings = [r for r in readings if r.reading_date >= start]
        if end is not None:
            readings = [r for r in readings if r.reading_date <= end]
        return readings

    async def _get_meter_details(self) -> str:
        html = await self._get_text("meterDetails")
        if is_login_page(html):
            raise SessionExpired("meterDetails returned the login page")
        return html

    def _require_login(self) -> None:
        if not self._logged_in:
            raise PortalError("not logged in; call login() first")
