"""Config and options flow for Auckland Transport."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ATApiError, ATAuthError, AucklandTransportApi
from .const import (
    CONF_API_KEY,
    CONF_DEPARTURE_SENSORS,
    CONF_HIDE_ARRIVALS,
    CONF_INCLUDE_SCHOOL_BUSES,
    CONF_LEGACY_ATTRIBUTES,
    CONF_ONLY_ACTIVE_ALERTS,
    CONF_STOP_CODE,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOP_TYPE,
    DEFAULT_DEPARTURE_SENSORS,
    DEFAULT_HIDE_ARRIVALS,
    DEFAULT_INCLUDE_SCHOOL_BUSES,
    DEFAULT_LEGACY_ATTRIBUTES,
    DEFAULT_ONLY_ACTIVE_ALERTS,
    DOMAIN,
    MAX_DEPARTURE_SENSORS,
    MODE_BUS,
    MODE_FERRY,
    MODE_TRAIN,
    STOP_TYPE_ALL,
    STOP_TYPES,
)
from .model import Stop

_LOGGER = logging.getLogger(__name__)

# Cap how many stops are offered at once; a 7000 entry dropdown is unusable.
MAX_PICKER_RESULTS = 80

CONF_SEARCH = "search"
CONF_INCLUDE_PLATFORMS = "include_platforms"


async def _async_fetch_stops(hass, api_key: str) -> list[Stop]:
    """Fetch and parse the stop list for the picker."""
    api = AucklandTransportApi(async_get_clientsession(hass), api_key)
    raw = await api.async_get_stops()
    stops = [stop for item in raw if (stop := Stop.from_api(item))]
    stops.sort(key=lambda stop: (stop.stop_name, stop.stop_code))
    return stops


def _filter_stops(
    stops: list[Stop], mode: str, search: str, include_platforms: bool
) -> list[Stop]:
    """Narrow the stop list by mode and search text.

    Ordering puts an exact stop-code match first, then parent stations, then
    everything else alphabetically.
    """
    needle = (search or "").strip().lower()
    ranked: list[tuple[bool, bool, str, str, Stop]] = []

    for stop in stops:
        code = stop.stop_code.lower()
        exact_code = bool(needle) and code == needle

        if needle and not exact_code:
            if needle not in stop.stop_name.lower() and needle not in code:
                continue

        # Platforms are normally hidden because the parent station already covers
        # them all. An exact stop-code match is the number printed on the sign,
        # though, so it is always offered.
        if not include_platforms and stop.parent_station and not exact_code:
            continue

        if mode != STOP_TYPE_ALL and stop.guess_mode() != mode:
            continue

        ranked.append((not exact_code, not stop.is_station, stop.stop_name, stop.stop_code, stop))

    ranked.sort(key=lambda item: item[:4])
    return [item[4] for item in ranked]


class AucklandTransportConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Auckland Transport config flow."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._api_key: str | None = None
        self._stops: list[Stop] = []
        self._mode: str = STOP_TYPE_ALL
        self._search: str = ""
        self._include_platforms: bool = False

    # -- API key -----------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reuse an already configured API key or ask for a new one."""
        existing = self._existing_api_keys()
        if not existing:
            return await self.async_step_new_api_key()

        if user_input is not None:
            choice = user_input["api_key_choice"]
            if choice == "new":
                return await self.async_step_new_api_key()
            self._api_key = choice
            return await self.async_step_stop_filter()

        options = [
            selector.SelectOptionDict(value=key, label=label)
            for key, label in existing.items()
        ]
        options.append(
            selector.SelectOptionDict(value="new", label="Enter a new API key")
        )
        schema = vol.Schema(
            {
                vol.Required("api_key_choice", default=next(iter(existing))): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    def _existing_api_keys(self) -> dict[str, str]:
        """Return configured API keys, masked, with a usage count."""
        counts: dict[str, int] = {}
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if key := entry.data.get(CONF_API_KEY):
                counts[key] = counts.get(key, 0) + 1

        labels: dict[str, str] = {}
        for key, count in counts.items():
            masked = f"…{key[-8:]}" if len(key) > 8 else "***"
            labels[key] = f"{masked} ({count} stop{'s' if count != 1 else ''})"
        return labels

    async def async_step_new_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and store a newly entered API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            api = AucklandTransportApi(async_get_clientsession(self.hass), api_key)
            try:
                await api.async_validate_key()
            except ATAuthError:
                errors["base"] = "invalid_auth"
            except ATApiError:
                errors["base"] = "cannot_connect"
            else:
                self._api_key = api_key
                return await self.async_step_stop_filter()

        schema = vol.Schema(
            {vol.Required(CONF_API_KEY): selector.TextSelector()}
        )
        return self.async_show_form(
            step_id="new_api_key", data_schema=schema, errors=errors
        )

    # -- reauth ------------------------------------------------------------
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start the reauthentication flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a replacement API key and update every entry that used it."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            api = AucklandTransportApi(async_get_clientsession(self.hass), api_key)
            try:
                await api.async_validate_key()
            except ATAuthError:
                errors["base"] = "invalid_auth"
            except ATApiError:
                errors["base"] = "cannot_connect"
            else:
                old_key = entry.data.get(CONF_API_KEY)
                for other in self.hass.config_entries.async_entries(DOMAIN):
                    if other.data.get(CONF_API_KEY) == old_key:
                        self.hass.config_entries.async_update_entry(
                            other, data={**other.data, CONF_API_KEY: api_key}
                        )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): selector.TextSelector()}),
            errors=errors,
        )

    # -- stop picker -------------------------------------------------------
    async def async_step_stop_filter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the mode filter and a search term for the stop picker."""
        errors: dict[str, str] = {}

        if not self._stops:
            try:
                self._stops = await _async_fetch_stops(self.hass, self._api_key)
            except ATAuthError:
                errors["base"] = "invalid_auth"
            except ATApiError:
                errors["base"] = "cannot_connect"

        if user_input is not None and not errors:
            self._mode = user_input.get(CONF_STOP_TYPE, STOP_TYPE_ALL)
            self._search = user_input.get(CONF_SEARCH, "") or ""
            self._include_platforms = bool(user_input.get(CONF_INCLUDE_PLATFORMS, False))
            return await self.async_step_stop_selection()

        schema = vol.Schema(
            {
                vol.Required(CONF_STOP_TYPE, default=self._mode): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=value, label=_MODE_LABELS[value]
                            )
                            for value in STOP_TYPES
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_SEARCH, default=self._search): selector.TextSelector(),
                vol.Optional(
                    CONF_INCLUDE_PLATFORMS, default=self._include_platforms
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="stop_filter",
            data_schema=schema,
            errors=errors,
            description_placeholders={"total": str(len(self._stops))},
        )

    async def async_step_stop_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the stop to monitor."""
        matches = _filter_stops(
            self._stops, self._mode, self._search, self._include_platforms
        )
        truncated = len(matches) > MAX_PICKER_RESULTS
        shown = matches[:MAX_PICKER_RESULTS]
        by_id = {stop.stop_id: stop for stop in shown}

        if not shown:
            return self.async_show_form(
                step_id="stop_selection",
                data_schema=vol.Schema({}),
                errors={"base": "no_stops_found"},
                description_placeholders={"count": "0", "total": "0", "hint": ""},
            )

        if user_input is not None:
            stop_id = user_input[CONF_STOP_ID]
            stop = by_id.get(stop_id)
            if stop is None:
                return self.async_abort(reason="unknown")

            await self.async_set_unique_id(stop_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"AT Stop - {stop.stop_name}"
                + (f" ({stop.stop_code})" if stop.stop_code else ""),
                data={
                    CONF_API_KEY: self._api_key,
                    CONF_STOP_ID: stop_id,
                    CONF_STOP_TYPE: self._mode,
                    CONF_STOP_NAME: stop.stop_name,
                    CONF_STOP_CODE: stop.stop_code,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_STOP_ID, default=shown[0].stop_id): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=stop.stop_id, label=stop.label()
                            )
                            for stop in shown
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        hint = (
            f"Only the first {MAX_PICKER_RESULTS} matches are shown - go back and "
            "refine the search to see the rest."
            if truncated
            else ""
        )
        return self.async_show_form(
            step_id="stop_selection",
            data_schema=schema,
            description_placeholders={
                "count": str(len(shown)),
                "total": str(len(matches)),
                "hint": hint,
            },
            last_step=True,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return AucklandTransportOptionsFlow()


_MODE_LABELS = {
    STOP_TYPE_ALL: "All stops",
    MODE_TRAIN: "Train stations",
    MODE_BUS: "Bus stops",
    MODE_FERRY: "Ferry terminals",
}


class AucklandTransportOptionsFlow(OptionsFlow):
    """Handle the Auckland Transport options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Present and save the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DEPARTURE_SENSORS,
                    default=options.get(
                        CONF_DEPARTURE_SENSORS, DEFAULT_DEPARTURE_SENSORS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=MAX_DEPARTURE_SENSORS,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_HIDE_ARRIVALS,
                    default=options.get(CONF_HIDE_ARRIVALS, DEFAULT_HIDE_ARRIVALS),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_INCLUDE_SCHOOL_BUSES,
                    default=options.get(
                        CONF_INCLUDE_SCHOOL_BUSES, DEFAULT_INCLUDE_SCHOOL_BUSES
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_ONLY_ACTIVE_ALERTS,
                    default=options.get(
                        CONF_ONLY_ACTIVE_ALERTS, DEFAULT_ONLY_ACTIVE_ALERTS
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_LEGACY_ATTRIBUTES,
                    default=options.get(
                        CONF_LEGACY_ATTRIBUTES, DEFAULT_LEGACY_ATTRIBUTES
                    ),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
