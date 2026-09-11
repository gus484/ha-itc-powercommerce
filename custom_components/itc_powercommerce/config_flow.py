"""Config flow for ITC PowerCommerce."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ITCPowerCommerceClient
from .api.exceptions import AuthError, PortalError
from .const import (
    CONF_HISTORY_INTERVAL,
    CONF_METER_INTERVAL,
    CONF_TENANT,
    CONF_UTILITY,
    DEFAULT_HISTORY_INTERVAL_HOURS,
    DEFAULT_METER_INTERVAL_HOURS,
    DOMAIN,
    KNOWN_UTILITIES,
    MAX_INTERVAL_HOURS,
    MIN_METER_INTERVAL_HOURS,
    UTILITY_MANUAL,
)
from .coordinator import is_power_meter

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_TENANT): str,
    }
)


class NoElectricityMeter(Exception):
    """The account authenticated but has no electricity meter on it."""


async def async_validate_login(
    hass: HomeAssistant, host: str, tenant: str, username: str, password: str
) -> str:
    """Log in for real and return the electricity meter number.

    A config flow that only checked the form fields would happily store
    credentials the portal rejects.
    """
    # A throwaway session, so the validation login's cookie ends up neither in
    # HA's shared jar nor in the entry's session.
    session = async_create_clientsession(hass, auto_cleanup=False)
    try:
        client = ITCPowerCommerceClient(
            username, password, session, host=host, tenant=tenant
        )
        await client.login()
        meters = [m for m in await client.get_meters() if is_power_meter(m)]
    finally:
        session.detach()
    if not meters:
        raise NoElectricityMeter
    return meters[0].meter_no


def _interval_selector(minimum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=MAX_INTERVAL_HOURS,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="h",
        )
    )


class ITCPowerCommerceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Utility selection, then credentials validated by a real login."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._tenant: str | None = None
        self._utility: str | None = None

    # -- initial setup -----------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the utility, or choose to type host and tenant by hand."""
        if user_input is not None:
            self._utility = user_input[CONF_UTILITY]
            if self._utility == UTILITY_MANUAL:
                return await self.async_step_manual()
            known = KNOWN_UTILITIES[self._utility]
            self._host = known["host"]
            self._tenant = known["tenant"]
            return await self.async_step_credentials()

        options = [
            {"value": key, "label": entry["name"]}
            for key, entry in KNOWN_UTILITIES.items()
        ]
        options.append({"value": UTILITY_MANUAL, "label": "Andere / Other"})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UTILITY): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Host and tenant for a utility that is not in the list yet."""
        if user_input is not None:
            self._host = user_input[CONF_HOST].rstrip("/")
            self._tenant = user_input[CONF_TENANT].strip()
            return await self.async_step_credentials()
        # hassfest rejects URLs in translation strings, so the example address
        # comes in as placeholders — taken from a real tenant rather than a
        # "<tenant>" pattern, which the frontend's Markdown would swallow.
        example = KNOWN_UTILITIES["swet"]
        return self.async_show_form(
            step_id="manual",
            data_schema=MANUAL_SCHEMA,
            description_placeholders={
                "example_name": example["name"],
                "example_url": (
                    f"{example['host']}/powercommerce/{example['tenant']}/fo/portal/"
                ),
                "example_host": example["host"],
                "example_tenant": example["tenant"],
            },
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Credentials, validated against the portal."""
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._host is not None and self._tenant is not None
            try:
                meter_no = await async_validate_login(
                    self.hass,
                    self._host,
                    self._tenant,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except NoElectricityMeter:
                errors["base"] = "no_meter"
            except (PortalError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error validating portal login")
                errors["base"] = "unknown"
            else:
                # Without this the same installation can be added twice, and
                # the two entries then fight over one set of statistics.
                await self.async_set_unique_id(meter_no)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{self._utility_label()} ({meter_no})",
                    data={
                        CONF_UTILITY: self._utility,
                        CONF_HOST: self._host,
                        CONF_TENANT: self._tenant,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="credentials",
            data_schema=CREDENTIALS_SCHEMA,
            errors=errors,
            description_placeholders={"host": self._host or ""},
        )

    def _utility_label(self) -> str:
        if self._utility in KNOWN_UTILITIES:
            return KNOWN_UTILITIES[self._utility]["name"]
        return self._tenant or DOMAIN

    # -- reauth ------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Triggered by ConfigEntryAuthFailed from either coordinator."""
        self._host = entry_data[CONF_HOST]
        self._tenant = entry_data[CONF_TENANT]
        self._utility = entry_data.get(CONF_UTILITY)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and verify it before storing it."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        username = entry.data[CONF_USERNAME]

        if user_input is not None:
            username = user_input.get(CONF_USERNAME, username)
            try:
                await async_validate_login(
                    self.hass,
                    entry.data[CONF_HOST],
                    entry.data[CONF_TENANT],
                    username,
                    user_input[CONF_PASSWORD],
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except NoElectricityMeter:
                errors["base"] = "no_meter"
            except (PortalError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error validating portal login")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=username): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.EMAIL, autocomplete="username"
                        )
                    ),
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
            description_placeholders={"username": username},
        )

    # -- options -----------------------------------------------------------

    @staticmethod
    def async_get_options_flow(config_entry) -> ITCOptionsFlow:
        return ITCOptionsFlow()


class ITCOptionsFlow(OptionsFlow):
    """Lets the user slow down or speed up the two polls independently."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_METER_INTERVAL: int(user_input[CONF_METER_INTERVAL]),
                    CONF_HISTORY_INTERVAL: int(user_input[CONF_HISTORY_INTERVAL]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_METER_INTERVAL,
                        default=options.get(
                            CONF_METER_INTERVAL, DEFAULT_METER_INTERVAL_HOURS
                        ),
                    ): _interval_selector(MIN_METER_INTERVAL_HOURS),
                    vol.Required(
                        CONF_HISTORY_INTERVAL,
                        default=options.get(
                            CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL_HOURS
                        ),
                    ): _interval_selector(DEFAULT_HISTORY_INTERVAL_HOURS),
                }
            ),
        )
