"""The Auckland Transport integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .api import ATApiError
from .const import (
    CONF_API_KEY,
    CONF_STOP_CODE,
    CONF_STOP_NAME,
    CONF_STOP_TYPE,
    DOMAIN,
    STOP_TYPE_ALL,
)
from .coordinator import ATRuntime, StopCoordinator, get_runtime, release_runtime

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

SERVICE_REFRESH = "refresh"
SERVICE_RELOAD_STATIC = "reload_static_data"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration and its services."""
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auckland Transport from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    runtime = get_runtime(hass, api_key)

    coordinator = StopCoordinator(hass, entry, runtime)
    # Raises ConfigEntryNotReady itself on failure, and ConfigEntryAuthFailed when
    # the coordinator reports a rejected key, which starts the reauth flow.
    await coordinator.async_config_entry_first_refresh()

    # Cache the resolved stop name so the entry title and picker stay meaningful
    # even when the API is unreachable at startup.
    stop = coordinator.data.stop
    if entry.data.get(CONF_STOP_NAME) != stop.stop_name:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_STOP_NAME: stop.stop_name,
                CONF_STOP_CODE: stop.stop_code,
            },
        )

    hass.data.setdefault(DOMAIN, {}).setdefault("entries", {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).get("entries", {}).pop(entry.entry_id, None)
        release_runtime(hass, entry.data[CONF_API_KEY], entry.entry_id)
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entries forward."""
    if entry.version >= 2:
        return True

    # v1 entries only stored api_key, stop_type and stop_id. The stop id format is
    # unchanged, so all that is needed is filling in the newer optional keys.
    data = {**entry.data}
    data.setdefault(CONF_STOP_TYPE, STOP_TYPE_ALL)
    data.setdefault(CONF_STOP_NAME, "")
    data.setdefault(CONF_STOP_CODE, "")
    hass.config_entries.async_update_entry(entry, data=data, version=2)
    _LOGGER.debug("Migrated %s to config entry version 2", entry.title)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so option changes take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _handle_refresh(call: ServiceCall) -> None:
        """Force an immediate refresh of every configured stop."""
        for coordinator in _coordinators(hass):
            await coordinator.async_request_refresh()

    async def _handle_reload_static(call: ServiceCall) -> None:
        """Re-download the GTFS stop and route lists."""
        seen: set[int] = set()
        for coordinator in _coordinators(hass):
            runtime: ATRuntime = coordinator.runtime
            if id(runtime) in seen:
                continue
            seen.add(id(runtime))
            try:
                await runtime.async_ensure_static(force=True)
            except ATApiError as err:
                _LOGGER.error("Could not reload GTFS static data: %s", err)
        for coordinator in _coordinators(hass):
            coordinator.runtime.invalidate_schedule(coordinator.stop_id)
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=vol.Schema({}))
    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD_STATIC, _handle_reload_static, schema=vol.Schema({})
    )


def _coordinators(hass: HomeAssistant) -> list[StopCoordinator]:
    """Return every loaded stop coordinator."""
    return list(hass.data.get(DOMAIN, {}).get("entries", {}).values())
