"""Shared test helpers: load fixtures from tests/fixtures/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def login_page() -> str:
    return load("login_page.html")


@pytest.fixture
def home_page() -> str:
    return load("home.html")


@pytest.fixture
def meter_details() -> str:
    return load("meter_details.html")


@pytest.fixture
def meter_widget() -> dict:
    return json.loads(load("meter_widget.json"))


@pytest.fixture
def meter_widget_text() -> str:
    return load("meter_widget.json")
