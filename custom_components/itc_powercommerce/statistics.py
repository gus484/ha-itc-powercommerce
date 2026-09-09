"""Backfill historical readings as external statistics.

A normal sensor can only ever record the present, so the portal's ~14 months
of monthly readings are written straight into the recorder's statistics
tables via :func:`async_add_external_statistics`.

Two rules govern everything here:

* ``Decimal`` all the way in, ``float`` only on the last line before handing a
  value to the recorder.
* ``start`` must be timezone-aware and hour-aligned or the row is rejected.
  The portal dates are bare ``DD.MM.YYYY``, so they are anchored at midnight
  in the *portal's* timezone, never in Home Assistant's.
"""

from __future__ import annotations

import logging
from datetime import datetime, time

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify

from .api.models import Reading
from .const import DOMAIN, PORTAL_TIME_ZONE

_LOGGER = logging.getLogger(__name__)

# The recorder replaced ``has_mean`` with ``mean_type`` mid-2025 and older
# cores do not know the new key. Set whichever this core actually declares
# rather than guessing at the installed version.
_METADATA_KEYS = frozenset(getattr(StatisticMetaData, "__annotations__", {}))


def build_statistic_id(tenant: str, meter_no: str) -> str:
    """Return the external statistic id for a meter.

    External ids carry the domain and a colon — ``itc_powercommerce:swet_…`` —
    which is what marks them as not belonging to any entity.
    """
    return f"{DOMAIN}:{slugify(tenant)}_{slugify(meter_no)}_energy"


def _reading_start(reading: Reading, tzinfo) -> datetime:
    """Midnight of the reading date in the portal timezone, as UTC."""
    local = datetime.combine(reading.reading_date, time.min, tzinfo=tzinfo)
    return dt_util.as_utc(local)


def _statistic_rows(
    readings: list[Reading], tzinfo, statistic_id: str
) -> list[StatisticData]:
    """Turn readings into hour-aligned, never-decreasing statistic rows."""
    # One row per timestamp; a later reading for the same day wins.
    by_start: dict[datetime, Reading] = {}
    for reading in readings:
        by_start[_reading_start(reading, tzinfo)] = reading

    rows: list[StatisticData] = []
    running_max = None
    for start in sorted(by_start):
        if start.minute or start.second or start.microsecond:
            # Should be unreachable — every European offset is whole hours —
            # but a misaligned row is silently dropped by the recorder, so say
            # so instead of wondering later why a month is missing.
            _LOGGER.warning(
                "%s: dropping reading at %s, not hour-aligned", statistic_id, start
            )
            continue

        value = by_start[start].value
        if running_max is not None and value < running_max:
            # `sum` must never decrease or the Energy Dashboard reads the drop
            # as a meter reset and invents a huge consumption spike.
            _LOGGER.warning(
                "%s: reading at %s is %s, below the previous %s — holding the "
                "previous value so the sum stays monotonic",
                statistic_id,
                start,
                value,
                running_max,
            )
            value = running_max
        running_max = value

        # Decimal -> float happens here and nowhere earlier.
        rows.append(StatisticData(start=start, state=float(value), sum=float(value)))
    return rows


async def async_import_readings(
    hass: HomeAssistant,
    tenant: str,
    meter_no: str,
    readings: list[Reading],
    *,
    name: str | None = None,
) -> int:
    """Write ``readings`` for one meter as external statistics.

    Returns the number of rows written. Imports are keyed on
    ``(statistic_id, start)``, so re-running this with the full history is
    idempotent and needs no diffing against what is already stored.
    """
    statistic_id = build_statistic_id(tenant, meter_no)

    usable = [r for r in readings if r.unit == UnitOfEnergy.KILO_WATT_HOUR]
    if len(usable) != len(readings):
        _LOGGER.debug(
            "%s: ignoring %d reading(s) not in kWh",
            statistic_id,
            len(readings) - len(usable),
        )
    if not usable:
        _LOGGER.debug("%s: nothing to import", statistic_id)
        return 0

    tzinfo = await dt_util.async_get_time_zone(PORTAL_TIME_ZONE)
    rows = _statistic_rows(usable, tzinfo, statistic_id)
    if not rows:
        return 0

    metadata = StatisticMetaData(
        has_sum=True,
        name=name or f"{meter_no} Energie",
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )
    if "mean_type" in _METADATA_KEYS:
        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticMeanType,
        )

        metadata["mean_type"] = StatisticMeanType.NONE
    if "has_mean" in _METADATA_KEYS:
        metadata["has_mean"] = False

    async_add_external_statistics(hass, metadata, rows)
    _LOGGER.debug(
        "%s: imported %d row(s), %s to %s",
        statistic_id,
        len(rows),
        rows[0]["start"],
        rows[-1]["start"],
    )
    return len(rows)
