"""Async client for ITC PowerCommerce customer portals.

The portal path pattern is ``<host>/powercommerce/<tenant>/fo/portal/<view>``.
For Stadtwerke Elbtal the tenant is ``swet``.

Observed flow (from Phase 0 recon):
    GET  /start          -> establishes JSESSIONID
    POST /loginProcess   -> 302 to /home on success
    GET  /meterWidget.json  -> meters + latest reading (JSON)
    GET  /meterDetails      -> reading history (HTML table)

Login is a plain form POST; no Spring Web Flow ``execution`` token or CSRF
field was observed. Hidden fields on the login form are still collected and
echoed back generically, so the client survives a portal update that adds one.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from types import TracebackType

import aiohttp
from bs4 import BeautifulSoup

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


class ITCPowerCommerceClient:
    """Reads meters and readings from an ITC PowerCommerce portal.

    Use as an async context manager so the underlying session is closed::

        async with ITCPowerCommerceClient(user, password) as client:
            await client.login()
            meters = await client.get_meters()
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        host: str = DEFAULT_HOST,
        tenant: str = DEFAULT_TENANT,
        session: aiohttp.ClientSession | None = None,
        debug_dir: str | Path | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._base = f"{host.rstrip('/')}/powercommerce/{tenant}/fo/portal"
        self._external_session = session is not None
        self._session = session
        self._logged_in = False
        self._debug_dir = Path(debug_dir) if debug_dir else None
        self._debug_seq = 0
        if self._debug_dir:
            self._debug_dir.mkdir(parents=True, exist_ok=True)

    # -- context management -------------------------------------------------

    async def __aenter__(self) -> "ITCPowerCommerceClient":
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT}
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session is not None and not self._external_session:
            await self._session.close()

    # -- low-level helpers --------------------------------------------------

    def _url(self, view: str) -> str:
        return f"{self._base}/{view}"

    def _dump(self, name: str, text: str) -> None:
        if not self._debug_dir:
            return
        self._debug_seq += 1
        path = self._debug_dir / f"{self._debug_seq:02d}_{name}.html"
        path.write_text(text, encoding="utf-8")
        _LOGGER.debug("wrote debug response %s", path)

    async def _get_text(self, view: str) -> str:
        assert self._session is not None
        url = self._url(view)
        async with self._session.get(url) as resp:
            text = await resp.text()
        self._dump(view.split("?")[0].replace("/", "_"), text)
        return text

    async def _get_json(self, view: str) -> dict:
        assert self._session is not None
        url = self._url(view)
        async with self._session.get(
            url, headers={"X-Requested-With": "XMLHttpRequest"}
        ) as resp:
            text = await resp.text()
        self._dump(view.split("?")[0].replace("/", "_") + "_json", text)
        if is_login_page(text):
            raise SessionExpired(f"{view} returned the login page")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ParseError(f"{view} did not return JSON") from exc

    # -- authentication -----------------------------------------------------

    async def login(self) -> None:
        """Authenticate. Raises :class:`AuthError` on failure."""
        assert self._session is not None, "use within 'async with'"

        # 1. GET /start to obtain a JSESSIONID and the login form.
        start_html = await self._get_text("start")
        hidden = self._collect_hidden_fields(start_html, form_id="loginProcessForm")

        # 2. POST credentials. Hidden fields are echoed back generically.
        payload = {
            **hidden,
            "login": self._username,
            "password": self._password,
            "twoFactorAuthenticationCode": "",
        }
        async with self._session.post(
            self._url("loginProcess"), data=payload
        ) as resp:
            landing = await resp.text()
        self._dump("loginProcess", landing)

        # On success the portal 302-redirects to /home (aiohttp follows it).
        # On failure it re-renders the login page.
        if is_login_page(landing):
            raise AuthError("login failed: check credentials or 2FA requirement")
        self._logged_in = True

    def _collect_hidden_fields(self, html: str, *, form_id: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", id=form_id) or soup
        fields: dict[str, str] = {}
        for inp in form.find_all("input", attrs={"type": "hidden"}):
            name = inp.get("name")
            if name:
                fields[name] = inp.get("value", "")
        return fields

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
        payload = await self._ensure_session(
            lambda: self._get_json("meterWidget.json?responsiveDesign=true")
        )
        return parse_meter_widget(payload)

    async def get_latest_readings(self) -> list[Reading]:
        """Return the most recent reading per meter/tariff (JSON, fast path)."""
        self._require_login()
        payload = await self._ensure_session(
            lambda: self._get_json("meterWidget.json?responsiveDesign=true")
        )
        return parse_latest_readings(payload)

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
