"""The ITC PowerCommerce integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ITCPowerCommerceClient
from .const import CONF_TENANT
from .coordinator import (
    ITCConfigEntry,
    ITCHistoryCoordinator,
    ITCMeterCoordinator,
    ITCRuntimeData,
)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ITCConfigEntry) -> bool:
    """Set up ITC PowerCommerce from a config entry."""
    client = ITCPowerCommerceClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        # Its own cookie jar: the portal session is a cookie, and the shared
        # session would keep it across reloads and hand it to the next client.
        # Created during setup, so HA detaches it again on unload.
        async_create_clientsession(hass),
        host=entry.data[CONF_HOST],
        tenant=entry.data[CONF_TENANT],
    )

    meter = ITCMeterCoordinator(hass, entry, client)
    await meter.async_config_entry_first_refresh()

    history = ITCHistoryCoordinator(hass, entry, client, meter)
    # Deliberately not a first_refresh: the history only feeds the statistics
    # backfill, so a portal markup change there must not take the current
    # reading down with it. A failure is logged and retried on the next tick.
    await history.async_refresh()
    # A coordinator only reschedules itself while something listens, and no
    # entity listens to the history. Without this it would run once at setup
    # and never again until the next restart.
    entry.async_on_unload(history.async_add_listener(lambda: None))

    entry.runtime_data = ITCRuntimeData(client=client, meter=meter, history=history)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ITCConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: ITCConfigEntry) -> None:
    """Reload so both coordinators pick up their new intervals."""
    await hass.config_entries.async_reload(entry.entry_id)
