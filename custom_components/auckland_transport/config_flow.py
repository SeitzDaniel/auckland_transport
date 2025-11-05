import logging
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .const import (
    API_STOPS_ENDPOINT,
    CONF_DISABLE_UPDATES_END,
    CONF_DISABLE_UPDATES_START,
    CONF_STOP_ID,
    CONF_STOP_TYPE,
    DEFAULT_DISABLE_UPDATES_END,
    DEFAULT_DISABLE_UPDATES_START,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    STOP_TYPES,
    STOP_TYPE_ALL,
    DEPARTURE_QTY,
)

_LOGGER = logging.getLogger(__name__)


async def validate_api_key(hass: HomeAssistant, api_key: str) -> bool:
    session = async_get_clientsession(hass)
    headers = {
        "Cache-Control": "no-cache",
        "Ocp-Apim-Subscription-Key": api_key,
    }

    try:
        async with session.get(API_STOPS_ENDPOINT, headers=headers) as response:
            return response.status == 200
    except aiohttp.ClientError:
        return False


class AucklandTransportConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._api_key = None
        self._stops_by_type = {}
        self._stop_type = STOP_TYPE_ALL
        self._data = None

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Handle the initial step - choose between existing or new API key."""
        existing_api_keys = self._get_existing_api_keys()

        if not existing_api_keys:
            # No existing API keys, go straight to entering a new one
            return await self.async_step_new_api_key()

        if user_input is not None:
            choice = user_input.get("api_key_choice")
            
            if choice == "new":
                return await self.async_step_new_api_key()
            else:
                # User selected an existing API key
                self._api_key = choice
                self._data = {CONF_API_KEY: choice}
                return await self.async_step_stop_type_selection()

        # Build options for the selection
        api_key_options = {key: label for key, label in existing_api_keys.items()}
        api_key_options["new"] = "Enter new API key"

        schema = vol.Schema({
            vol.Required("api_key_choice"): vol.In(api_key_options)
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={
                "existing_count": str(len(existing_api_keys))
            }
        )

    def _get_existing_api_keys(self) -> Dict[str, str]:
        """Get all existing API keys from configured entries."""
        api_key_counts = {}
        
        # Count how many stops use each API key
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            api_key = entry.data.get(CONF_API_KEY)
            if api_key:
                api_key_counts[api_key] = api_key_counts.get(api_key, 0) + 1
        
        # Build the display labels
        existing_keys = {}
        for api_key, count in api_key_counts.items():
            masked_key = f"...{api_key[-8:]}" if len(api_key) > 8 else "***"
            stop_text = "stop" if count == 1 else "stops"
            existing_keys[api_key] = f"{masked_key} (used by {count} {stop_text})"
        
        return existing_keys

    async def async_step_new_api_key(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Handle entering a new API key."""
        errors = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            valid = await validate_api_key(self.hass, api_key)

            if valid:
                self._api_key = api_key
                self._data = {CONF_API_KEY: api_key}
                return await self.async_step_stop_type_selection()
            else:
                errors["base"] = "invalid_auth"

        schema = vol.Schema({vol.Required(CONF_API_KEY): str})

        return self.async_show_form(step_id="new_api_key", data_schema=schema, errors=errors)

    async def async_step_stop_type_selection(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            self._stop_type = user_input[CONF_STOP_TYPE]
            return await self.async_step_stop_selection()

        schema = vol.Schema({
            vol.Required(CONF_STOP_TYPE, default=self._stop_type): vol.In(
                {k: k.capitalize() for k in STOP_TYPES}
            )
        })

        return self.async_show_form(step_id="stop_type_selection", data_schema=schema)

    async def async_step_stop_selection(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors = {}

        # Always fetch stops to ensure filtering works
        if not self._stops_by_type:
            try:
                self._stops_by_type = await self._fetch_stops()
            except Exception:
                errors["base"] = "cannot_connect"
                self._stops_by_type = {}

        current_stop_options = self._stops_by_type.get(self._stop_type, [])
        stop_options = {stop_id: name for stop_id, name in current_stop_options}

        if user_input is not None:
            stop_id = user_input[CONF_STOP_ID]

            # Check if stop is already configured
            if await self.async_set_unique_id(stop_id, raise_on_progress=False):
                errors["base"] = "already_configured"
            else:
                self._data.update({
                    CONF_STOP_TYPE: self._stop_type,
                    CONF_STOP_ID: stop_id,
                })

                combined_stop_name = stop_options[stop_id]
                return self.async_create_entry(
                    title=f"AT Stop - {combined_stop_name}",
                    data=self._data,
                )

        schema = vol.Schema({
            vol.Required(CONF_STOP_ID, default=next(iter(stop_options), "")):
                vol.In(stop_options) if stop_options else str
        })

        return self.async_show_form(
            step_id="stop_selection",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "stop_count": str(len(stop_options)),
                "stop_type": self._stop_type.capitalize(),
            },
            last_step=True,
        )

    async def _fetch_stops(self):
        session = async_get_clientsession(self.hass)
        headers = {
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self._api_key,
        }

        async with session.get(API_STOPS_ENDPOINT, headers=headers) as response:
            if response.status != 200:
                return {}

            data = await response.json()
            stops = data.get("data", [])
            stops_by_type = {t: [] for t in STOP_TYPES}

            for stop in stops:
                attributes = stop.get("attributes", {})
                stop_code = attributes.get("stop_code", "")
                stop_name = attributes.get("stop_name", "")
                stop_id = stop.get("id", "")

                if not stop_code or not stop_name or not stop_id:
                    continue

                stop_option = f"{stop_name} ({stop_code})"
                code_length = len(stop_code)

                stops_by_type[STOP_TYPE_ALL].append((stop_id, stop_option))
                if code_length == 3:
                    stops_by_type["train"].append((stop_id, stop_option))
                elif code_length == 4:
                    stops_by_type["bus"].append((stop_id, stop_option))
                elif code_length == 5:
                    stops_by_type["ferry"].append((stop_id, stop_option))

            return stops_by_type

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return AucklandTransportOptionsFlow(config_entry)


class AucklandTransportOptionsFlow(config_entries.OptionsFlow):
    """Handle Auckland Transport options."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        super().__init__()
        self._entry = entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._entry.options

        schema = vol.Schema({
            vol.Optional(
                "update_interval",
                default=options.get("update_interval", DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Optional(
                CONF_DISABLE_UPDATES_START,
                default=options.get(CONF_DISABLE_UPDATES_START, DEFAULT_DISABLE_UPDATES_START),
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_DISABLE_UPDATES_END,
                default=options.get(CONF_DISABLE_UPDATES_END, DEFAULT_DISABLE_UPDATES_END),
            ): selector.TimeSelector(),
            vol.Optional(
                "departure_qty",
                default=options.get("departure_qty", DEPARTURE_QTY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, 
                    max=10,
                    mode="box",
                    step=1
                )
            ),        
        })

        return self.async_show_form(step_id="init", data_schema=schema)
