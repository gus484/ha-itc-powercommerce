"""Coordinators — two rhythms, not one.

``meterWidget.json`` is a small JSON payload holding the current reading, so
it is polled on a regular interval. ``meterDetails`` is a full HTML page that
only feeds the statistics backfill, so it runs once at setup and daily after
that. Putting both on one interval would either hammer the portal for the
history or starve the current reading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ITCPowerCommerceClient
from .api.exceptions import AuthError, PortalError
from .api.models import Meter, Reading
from .const import (
    CONF_HISTORY_INTERVAL,
    CONF_METER_INTERVAL,
    CONF_TENANT,
    DEFAULT_HISTORY_INTERVAL_HOURS,
    DEFAULT_METER_INTERVAL_HOURS,
    DOMAIN,
    POWER_MEDIA_TYPES,
)
from .statistics import async_import_readings

_LOGGER = logging.getLogger(__name__)

# Transport-level failures that mean "try again later", not "credentials wrong".
TRANSIENT_ERRORS = (PortalError, aiohttp.ClientError, TimeoutError)


def is_power_meter(meter: Meter) -> bool:
    """True for electricity meters.

    Phase 2 is electricity only. Tenants that do not report ``mediaType`` are
    judged by their unit instead, so the check stays about what a meter *is*
    rather than about which media types we happen to want to exclude.
    """
    if meter.media_type:
        return meter.media_type in POWER_MEDIA_TYPES
    return meter.unit == UnitOfEnergy.KILO_WATT_HOUR


@dataclass(slots=True)
class MeterSnapshot:
    """What one ``meterWidget.json`` poll yields."""

    meters: list[Meter]
    latest: dict[str, Reading]

    @property
    def power_meters(self) -> list[Meter]:
        return [m for m in self.meters if is_power_meter(m)]


@dataclass(slots=True)
class ITCRuntimeData:
    """Everything a config entry owns at runtime."""

    client: ITCPowerCommerceClient
    meter: "ITCMeterCoordinator"
    history: "ITCHistoryCoordinator"


type ITCConfigEntry = ConfigEntry[ITCRuntimeData]


class ITCBaseCoordinator[_DataT](DataUpdateCoordinator[_DataT]):
    """Shared login handling and error mapping."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ITCConfigEntry,
        client: ITCPowerCommerceClient,
        *,
        name: str,
        interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=name,
            update_interval=interval,
        )
        self.client = client

    async def _async_update_data(self) -> _DataT:
        try:
            await self.client.async_ensure_login()
            return await self._async_fetch()
        except AuthError as err:
            # Bubbles up as the reauth dialog rather than a silent retry loop;
            # repeated failed logins can lock the portal account.
            raise ConfigEntryAuthFailed(str(err)) from err
        except TRANSIENT_ERRORS as err:
            raise UpdateFailed(f"{self.name}: {err}") from err

    async def _async_fetch(self) -> _DataT:
        raise NotImplementedError


class ITCMeterCoordinator(ITCBaseCoordinator[MeterSnapshot]):
    """Polls ``meterWidget.json`` for meters and their latest reading."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ITCConfigEntry,
        client: ITCPowerCommerceClient,
    ) -> None:
        hours = entry.options.get(CONF_METER_INTERVAL, DEFAULT_METER_INTERVAL_HOURS)
        super().__init__(
            hass,
            entry,
            client,
            name=f"{DOMAIN} meter",
            interval=timedelta(hours=hours),
        )

    async def _async_fetch(self) -> MeterSnapshot:
        meters, readings = await self.client.get_meters_and_readings()
        latest: dict[str, Reading] = {}
        for reading in readings:
            current = latest.get(reading.meter_no)
            if current is None or reading.reading_date >= current.reading_date:
                latest[reading.meter_no] = reading
        return MeterSnapshot(meters=meters, latest=latest)


class ITCHistoryCoordinator(ITCBaseCoordinator[list[Reading]]):
    """Fetches the ``meterDetails`` history and backfills statistics.

    The import runs here rather than in a separate task because "fetch the
    history" and "write the history" share exactly one schedule; splitting
    them would mean keeping two timers in step for no gain.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ITCConfigEntry,
        client: ITCPowerCommerceClient,
        meter_coordinator: ITCMeterCoordinator,
    ) -> None:
        hours = entry.options.get(CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL_HOURS)
        super().__init__(
            hass,
            entry,
            client,
            name=f"{DOMAIN} history",
            interval=timedelta(hours=hours),
        )
        self._meter_coordinator = meter_coordinator
        self._tenant = entry.data[CONF_TENANT]
        self._warned_multiple = False

    async def _async_fetch(self) -> list[Reading]:
        meters = (
            self._meter_coordinator.data.power_meters
            if self._meter_coordinator.data
            else []
        )
        if not meters:
            _LOGGER.debug("no electricity meter known yet, skipping history")
            return []

        # The captured meterDetails page has no meter selector, so it can only
        # ever describe one meter. Attribute it to the first electricity meter
        # and say so instead of silently duplicating a history onto every meter.
        meter = meters[0]
        if len(meters) > 1 and not self._warned_multiple:
            self._warned_multiple = True
            _LOGGER.warning(
                "%d electricity meters found; the portal's meterDetails page "
                "covers only one, so history is imported for %s only",
                len(meters),
                meter.meter_no,
            )

        readings = await self.client.get_readings(meter.meter_no)
        await async_import_readings(
            self.hass,
            self._tenant,
            meter.meter_no,
            readings,
            name=f"{meter.media_type_label or 'Strom'} {meter.meter_no}",
        )
        return readings
