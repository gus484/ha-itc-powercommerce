"""Data models for meters and readings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class ReadingKind(str, Enum):
    """Whether a value is a cumulative meter reading or a consumption delta."""

    METER_READING = "meter_reading"  # Zählerstand, cumulative
    CONSUMPTION = "consumption"  # Verbrauch, difference


class ReadingSource(str, Enum):
    """How the reading was obtained. The portal does not always disclose this."""

    READ = "read"  # abgelesen
    ESTIMATED = "estimated"  # geschätzt
    SELF_REPORTED = "self_reported"  # selbst gemeldet
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Meter:
    """A single meter (Zähler)."""

    meter_no: str
    media_type: str  # e.g. "POWER"
    media_type_label: str  # e.g. "Strom"
    unit: str  # e.g. "kWh"


@dataclass(frozen=True)
class Reading:
    """One dated value for a meter.

    ``value`` is a Decimal — cumulative meter readings must not accumulate
    float rounding error.
    """

    meter_no: str
    reading_date: date
    value: Decimal
    unit: str
    kind: ReadingKind = ReadingKind.METER_READING
    source: ReadingSource = ReadingSource.UNKNOWN
