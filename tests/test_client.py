"""Client flow tests using aioresponses against the fixtures."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest
from aioresponses import aioresponses
from yarl import URL

from custom_components.itc_powercommerce.api import AuthError, ITCPowerCommerceClient

BASE = "https://onlineservice.stadtwerke-elbtal.de/powercommerce/swet/fo/portal"

# meterWidget.json carries a cache-busting query param, so match by prefix.
WIDGET_RE = re.compile(re.escape(f"{BASE}/meterWidget.json") + r".*")


@pytest.fixture
def client() -> ITCPowerCommerceClient:
    return ITCPowerCommerceClient("user", "secret")


async def test_login_success(client, login_page, home_page) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)  # 302 followed -> home
        async with client:
            await client.login()
    assert client._logged_in is True


async def test_login_sends_hidden_fields(client, login_page, home_page) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        async with client:
            await client.login()
        request = m.requests[("POST", URL(f"{BASE}/loginProcess"))][-1]
    body = request.kwargs["data"]
    assert body["_csrf"] == "test-csrf-token"  # hidden field echoed back
    assert body["login"] == "user"
    assert body["password"] == "secret"


async def test_login_failure_raises(client, login_page) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=login_page)  # re-rendered login = fail
        async with client:
            with pytest.raises(AuthError):
                await client.login()


async def test_get_meters(client, login_page, home_page, meter_widget_text) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        m.get(WIDGET_RE, body=meter_widget_text)
        async with client:
            await client.login()
            meters = await client.get_meters()
    assert [x.meter_no for x in meters] == ["1LAB0000000001"]


async def test_get_readings(client, login_page, home_page, meter_details) -> None:
    with aioresponses() as m:
        m.get(f"{BASE}/start", body=login_page)
        m.post(f"{BASE}/loginProcess", body=home_page)
        m.get(f"{BASE}/meterDetails", body=meter_details)
        async with client:
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
        async with client:
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
        async with client:
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
        async with client:
            await client.login()
            with pytest.raises(AuthError):
                await client.get_readings("1LAB0000000001")
