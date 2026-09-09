"""Parsers for the portal's German-formatted values and HTML.

Kept separate from the client so it can be unit-tested against fixtures
without any network mocking. These parsers are the early-warning system for
portal markup changes.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .exceptions import ParseError
from .models import Meter, Reading, ReadingKind

# Marker that identifies the (unauthenticated) login page. Present on the login
# page, absent once authenticated — so its presence means the session expired.
LOGIN_PAGE_MARKER = "loginProcessForm"


def is_login_page(html: str) -> bool:
    """True if ``html`` looks like the login page rather than an authed view."""
    return LOGIN_PAGE_MARKER in html


def parse_german_decimal(text: str) -> Decimal:
    """Parse a German-formatted number like ``13.114,7`` into ``Decimal('13114.7')``.

    Also accepts the zero-padded JSON form ``000013364,00``. A trailing unit
    (``kWh``) and surrounding whitespace are stripped.
    """
    cleaned = text.strip()
    # drop a trailing unit token if present ("13.364 kWh" -> "13.364")
    cleaned = re.sub(r"[^\d.,-].*$", "", cleaned).strip()
    # German grouping: '.' thousands, ',' decimal -> remove dots, comma to dot
    cleaned = cleaned.replace(".", "").replace(",", ".")
    if not cleaned:
        raise ParseError(f"could not parse number from {text!r}")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ParseError(f"could not parse number from {text!r}") from exc


def parse_german_date(text: str) -> date:
    """Parse a ``DD.MM.YYYY`` date."""
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise ParseError(f"could not parse date from {text!r}") from exc


def parse_meter_widget(payload: dict) -> list[Meter]:
    """Extract meters from the ``meterWidget.json`` payload."""
    try:
        sub_elements = payload["model"]["subElements"]
    except (KeyError, TypeError) as exc:
        raise ParseError("meterWidget.json missing model.subElements") from exc

    meters: list[Meter] = []
    for el in sub_elements:
        try:
            last = next(iter(el["lastMeterReadings"].values()))
            unit = last.get("unit", "")
        except (KeyError, StopIteration, AttributeError):
            unit = ""
        meters.append(
            Meter(
                meter_no=el["meterNo"],
                media_type=el.get("mediaType", ""),
                media_type_label=el.get("mediaTypeStr", ""),
                unit=unit,
            )
        )
    if not meters:
        raise ParseError("meterWidget.json contained no meters")
    return meters


def parse_latest_readings(payload: dict) -> list[Reading]:
    """Extract the latest reading per tariff from ``meterWidget.json``."""
    sub_elements = payload.get("model", {}).get("subElements", [])
    readings: list[Reading] = []
    for el in sub_elements:
        meter_no = el.get("meterNo", "")
        for tariff in el.get("lastMeterReadings", {}).values():
            readings.append(
                Reading(
                    meter_no=meter_no,
                    reading_date=parse_german_date(tariff["readingDate"]),
                    value=parse_german_decimal(tariff["meterValue"]),
                    unit=tariff.get("unit", ""),
                    kind=ReadingKind.METER_READING,
                )
            )
    return readings


def parse_reading_history(html: str, meter_no: str) -> list[Reading]:
    """Parse the reading-history table on the ``meterDetails`` page.

    Rows are ``<td data-title="Datum">DD.MM.YYYY</td>`` followed by a value
    cell (``data-title="Zählerstand"``) holding ``13.114,7 kWh``.
    """
    if is_login_page(html):
        raise ParseError("expected meterDetails, got login page")

    soup = BeautifulSoup(html, "html.parser")
    date_cells = soup.find_all("td", attrs={"data-title": "Datum"})
    if not date_cells:
        raise ParseError("no reading-history rows found on meterDetails")

    readings: list[Reading] = []
    for date_cell in date_cells:
        value_cell = date_cell.find_next_sibling("td")
        if value_cell is None:
            continue
        raw_value = value_cell.get_text(strip=True)
        unit = "kWh" if "kWh" in raw_value else ""
        readings.append(
            Reading(
                meter_no=meter_no,
                reading_date=parse_german_date(date_cell.get_text(strip=True)),
                value=parse_german_decimal(raw_value),
                unit=unit,
                kind=ReadingKind.METER_READING,
            )
        )
    if not readings:
        raise ParseError("reading-history table had rows but none parsed")
    return readings
