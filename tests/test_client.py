"""Client flow tests using aioresponses against the fixtures."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import aiohttp
import pytest
from aioresponses import aioresponses
from yarl import URL

from api import AuthError, ITCPowerCommerceClient
from api.client import METER_WIDGET_VIEW

BASE = "https://onlineservice.stadtwerke-elbtal.de/powercommerce/swet/fo/portal"

# meterWidget.json carries a query string, so match by prefix.
WIDGET_RE = re.compile(re.escape(f"{BASE}/meterWidget.json") + r".*")


@pytest.fixture
async def client() -> ITCPowerCommerceClient:
    """A client on a caller-owned session, the way the integration builds it."""
    async with aiohttp.ClientSession() as session:
        yield ITCPowerCommerceClient("user", "secret", session)


async def test_login_success(client, login_page, home_page) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)  # 302 followed -> home
        await client.login()
    assert client.logged_in is True


async def test_login_posts_only_the_three_observed_fields(
    client, login_page, home_page
) -> None:
    """The captured POST carries exactly these fields — no CSRF, no hidden inputs."""
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        await client.login()
        request = m.requests[("POST", URL(f"{BASE}/loginProcess"))][-1]
    body = request.kwargs["data"]
    assert set(body) == {"login", "password", "twoFactorAuthenticationCode"}
    assert body["login"] == "user"
    assert body["password"] == "secret"
    assert body["twoFactorAuthenticationCode"] == ""


async def test_login_starts_from_an_empty_portal_cookie_jar(
    login_page, home_page
) -> None:
    """A stale JSESSIONID must not survive into a new login.

    Logging in on top of one made the portal serve a meterWidget.json without
    meters and then reject the next login as bad credentials.
    """
    async with aiohttp.ClientSession() as session:
        session.cookie_jar.update_cookies({"JSESSIONID": "stale"}, URL(BASE))
        session.cookie_jar.update_cookies({"other": "keep"}, URL("https://example.org/"))
        client = ITCPowerCommerceClient("user", "secret", session)
        with aioresponses() as m:
            m.get(f"{BASE}/start", body=login_page)
            m.post(f"{BASE}/loginProcess", body=home_page)
            await client.login()

        assert "JSESSIONID" not in session.cookie_jar.filter_cookies(URL(BASE))
        # Only the portal's cookies go; the jar is not the client's to wipe.
        assert "other" in session.cookie_jar.filter_cookies(URL("https://example.org/"))
    assert client.logged_in is True


async def test_login_failure_raises(client, login_page) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=login_page)  # re-rendered login = fail
        with pytest.raises(AuthError):
            await client.login()


async def test_get_meters(client, login_page, home_page, meter_widget_text) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        m.get(WIDGET_RE, body=meter_widget_text)
        await client.login()
        meters = await client.get_meters()
    assert [x.meter_no for x in meters] == ["1LAB0000000001"]


async def test_get_meters_and_readings_hits_the_portal_once(
    client, login_page, home_page, meter_widget_text
) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        m.get(WIDGET_RE, body=meter_widget_text)
        await client.login()
        meters, readings = await client.get_meters_and_readings()
        widget_calls = m.requests[("GET", URL(f"{BASE}/{METER_WIDGET_VIEW}"))]
    assert len(widget_calls) == 1
    assert [x.meter_no for x in meters] == ["1LAB0000000001"]
    assert readings[0].value == Decimal("13364.00")


async def test_get_readings(client, login_page, home_page, meter_details) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        m.get(f"{BASE}/meterDetails", body=meter_details)
        await client.login()
        readings = await client.get_readings("1LAB0000000001")
    assert len(readings) == 12
    assert readings[0].value == Decimal("13364")


async def test_get_readings_date_filter(
    client, login_page, home_page, meter_details
) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        m.get(f"{BASE}/meterDetails", body=meter_details)
        await client.login()
        readings = await client.get_readings(
            "1LAB0000000001", start=date(2025, 7, 1), end=date(2025, 12, 31)
        )
    dates = {r.reading_date for r in readings}
    assert dates == {date(2025, 12, 31), date(2025, 9, 30), date(2025, 8, 31),
                     date(2025, 7, 31)}


async def test_session_expiry_triggers_single_relogin(
    client, login_page, home_page, meter_details
) -> None:
    with aioresponses() as m:
        # initial login
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        # first meterDetails request: session expired -> login page
        m.get(f"{BASE}/meterDetails", body=login_page)
        # re-login
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        # retry succeeds
        m.get(f"{BASE}/meterDetails", body=meter_details)
        await client.login()
        readings = await client.get_readings("1LAB0000000001")
    assert len(readings) == 12


async def test_session_expiry_gives_up_after_one_retry(
    client, login_page, home_page
) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        # meterDetails always returns login page (session never recovers)
        m.get(f"{BASE}/meterDetails", body=login_page)
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        m.get(f"{BASE}/meterDetails", body=login_page)
        await client.login()
        with pytest.raises(AuthError):
            await client.get_readings("1LAB0000000001")
