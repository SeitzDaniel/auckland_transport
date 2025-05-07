"""Auckland Transport integration."""
import asyncio
import logging
from datetime import timedelta

import aiohttp
import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_STOPS_ENDPOINT,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Auckland Transport component."""
    # Home Assistant handles translations automatically
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auckland Transport from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    
    session = async_get_clientsession(hass)
    coordinator = AucklandTransportDataUpdateCoordinator(hass, session, api_key)
    
    await coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Set up options update listener
    entry.async_on_unload(entry.add_update_listener(update_listener))
    
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
    return unload_ok


class AucklandTransportDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Auckland Transport data."""

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession, api_key: str):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.session = session
        self.api_key = api_key
        self._stops_data = None

    async def _async_update_data(self):
        """Fetch data from Auckland Transport API."""
        try:
            async with async_timeout.timeout(10):
                # For now, we just return existing stops data if we've already fetched it
                if self._stops_data:
                    return self._stops_data
                
                # Fetch stops data
                self._stops_data = await self._fetch_stops_data()
                return self._stops_data
                
        except (asyncio.TimeoutError, aiohttp.ClientError) as error:
            raise UpdateFailed(f"Error communicating with API: {error}") from error

    async def _fetch_stops_data(self):
        """Fetch stops data from Auckland Transport API."""
        headers = {
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
        }
        
        async with self.session.get(API_STOPS_ENDPOINT, headers=headers) as response:
            if response.status != 200:
                _LOGGER.error("Error fetching stops data: %s", response.status)
                return []
                
            data = await response.json()
            return data.get("data", [])

    async def get_stops(self, stop_type=None):
        """Get filtered stops based on type."""
        if self._stops_data is None:
            await self.async_refresh()
            
        if not self._stops_data:
            return []
            
        if stop_type is None or stop_type == "all":
            return self._stops_data
            
        filtered_stops = []
        
        for stop in self._stops_data:
            attributes = stop.get("attributes", {})
            stop_code = attributes.get("stop_code", "")
            
            if not stop_code:
                continue
                
            # Filter based on stop code pattern
            code_length = len(stop_code)
            
            if stop_type == "train" and code_length == 3:
                filtered_stops.append(stop)
            elif stop_type == "bus" and code_length == 4:
                filtered_stops.append(stop)
            elif stop_type == "ferry" and code_length == 5:
                filtered_stops.append(stop)
                
        return filtered_stops
