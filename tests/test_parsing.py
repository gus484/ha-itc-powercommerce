"""Parser tests — the early-warning system for portal markup changes.

If the portal changes its number format, date format, or the meterDetails
table structure, these tests fail first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.exceptions import ParseError
from api.parsing import (
    is_login_page,
    parse_german_date,
    parse_german_decimal,
    parse_latest_readings,
    parse_meter_widget,
    parse_reading_history,
)


class TestGermanDecimal:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("13.364 kWh", Decimal("13364")),
            ("13.114,7 kWh", Decimal("13114.7")),
            ("11.827 kWh", Decimal("11827")),
            ("000013364,00", Decimal("13364.00")),
            ("10.911,1", Decimal("10911.1")),
            ("0", Decimal("0")),
        ],
    )
    def test_parses(self, text: str, expected: Decimal) -> None:
        assert parse_german_decimal(text) == expected

    def test_no_float_error_on_cumulative(self) -> None:
        # Decimal keeps exact values that float would corrupt.
        assert parse_german_decimal("13.114,7 kWh") == Decimal("13114.7")

    def test_invalid_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_german_decimal("kWh")


class TestGermanDate:
    def test_parses(self) -> None:
        assert parse_german_date("31.01.2026") == date(2026, 1, 31)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_german_date("2026-01-31")


class TestLoginPageDetection:
    def test_login_page_detected(self, login_page: str) -> None:
        assert is_login_page(login_page) is True

    def test_authed_page_not_login(self, meter_details: str, home_page: str) -> None:
        assert is_login_page(meter_details) is False
        assert is_login_page(home_page) is False


class TestMeterWidget:
    def test_parse_meters(self, meter_widget: dict) -> None:
        meters = parse_meter_widget(meter_widget)
        assert len(meters) == 1
        m = meters[0]
        assert m.meter_no == "1LAB0000000001"
        assert m.media_type == "POWER"
        assert m.media_type_label == "Strom"
        assert m.unit == "kWh"

    def test_parse_latest_readings(self, meter_widget: dict) -> None:
        readings = parse_latest_readings(meter_widget)
        assert len(readings) == 1
        r = readings[0]
        assert r.meter_no == "1LAB0000000001"
        assert r.reading_date == date(2026, 1, 31)
        assert r.value == Decimal("13364.00")
        assert r.unit == "kWh"

    def test_missing_structure_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_meter_widget({"unexpected": True})


class TestReadingHistory:
    def test_parses_all_rows(self, meter_details: str) -> None:
        readings = parse_reading_history(meter_details, "1LAB0000000001")
        assert len(readings) == 12

    def test_values_and_dates(self, meter_details: str) -> None:
        readings = parse_reading_history(meter_details, "1LAB0000000001")
        by_date = {r.reading_date: r for r in readings}
        assert by_date[date(2026, 1, 31)].value == Decimal("13364")
        assert by_date[date(2025, 12, 31)].value == Decimal("13114.7")
        assert by_date[date(2025, 6, 20)].value == Decimal("11827")
        assert all(r.unit == "kWh" for r in readings)
        assert all(r.meter_no == "1LAB0000000001" for r in readings)

    def test_login_page_raises(self, login_page: str) -> None:
        with pytest.raises(ParseError):
            parse_reading_history(login_page, "1LAB0000000001")

    def test_no_table_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_reading_history("<html><body>nothing</body></html>", "X")
