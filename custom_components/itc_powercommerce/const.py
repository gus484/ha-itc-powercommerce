"""Constants for the ITC PowerCommerce integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "itc_powercommerce"

# -- config entry / options keys ------------------------------------------

CONF_UTILITY: Final = "utility"
CONF_TENANT: Final = "tenant"
CONF_METER_INTERVAL: Final = "meter_interval_hours"
CONF_HISTORY_INTERVAL: Final = "history_interval_hours"

# Sentinel for "my utility is not in the list, let me type host and tenant".
UTILITY_MANUAL: Final = "manual"

# -- polling ---------------------------------------------------------------

# meterWidget.json is cheap; the portal updates it monthly at best, so twice a
# day is already generous. meterDetails is a full HTML page and only feeds the
# history backfill — daily is plenty.
DEFAULT_METER_INTERVAL_HOURS: Final = 12
DEFAULT_HISTORY_INTERVAL_HOURS: Final = 24
MIN_METER_INTERVAL_HOURS: Final = 1
MAX_INTERVAL_HOURS: Final = 168

# -- portal ----------------------------------------------------------------

# Known utilities on the ITC PowerCommerce platform. v1 ships one; the manual
# option keeps every other tenant reachable without a code change.
KNOWN_UTILITIES: Final[dict[str, dict[str, str]]] = {
    "swet": {
        "name": "Stadtwerke Elbtal",
        "host": "https://onlineservice.stadtwerke-elbtal.de",
        "tenant": "swet",
    },
}

# Media types we produce entities and statistics for. Phase 2 is electricity
# only; other media types are ignored rather than mis-modelled.
POWER_MEDIA_TYPES: Final[frozenset[str]] = frozenset({"POWER"})

# Reading dates come from a German portal as bare DD.MM.YYYY with no time, so
# they are anchored in the portal's timezone, not in Home Assistant's.
PORTAL_TIME_ZONE: Final = "Europe/Berlin"

# -- diagnostics / fixtures ------------------------------------------------

# The single field list used by both diagnostics redaction and the fixture
# anonymisation, so the two cannot drift apart.
REDACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "username",
        "login",
        "cookie",
        "cookies",
        "authorization",
        "customerNo",
        "customer_no",
        "meterNo",
        "meter_no",
        "meteringPointId",
        "metering_point_id",
        "name",
        "firstName",
        "lastName",
        "address",
        "street",
        "city",
        "zipCode",
        "email",
    }
)
