"""Support for Auckland Transport sensors."""
import logging
from datetime import datetime, timedelta
import pytz
from typing import Any, Dict, Optional, List

import aiohttp
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    API_BASE_URL,
    ATTR_LOCATION_TYPE,
    ATTR_STOP_CODE,
    ATTR_STOP_LAT,
    ATTR_STOP_LON,
    ATTR_STOP_NAME,
    ATTR_WHEELCHAIR_BOARDING,
    CONF_STOP_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Auckland Transport sensor based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    api_key = entry.data[CONF_API_KEY]
    stop_id = entry.data[CONF_STOP_ID]
    
    # Find stop details in coordinator data
    stop_data = None
    
    for stop in coordinator.data:
        if stop.get("id") == stop_id:
            stop_data = stop
            break
    
    if not stop_data:
        _LOGGER.error("Could not find stop data for stop_id: %s", stop_id)
        return
    
    # Create real-time data coordinator
    realtime_coordinator = RealtimeDataCoordinator(
        hass, 
        api_key, 
        stop_id
    )
    
    # Initial data fetch - force immediate refresh
    await realtime_coordinator.async_refresh()
    
    # Create the sensor entity
    async_add_entities([AucklandTransportSensor(coordinator, realtime_coordinator, api_key, stop_data)])


class RealtimeDataCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Auckland Transport real-time data."""

    def __init__(self, hass: HomeAssistant, api_key: str, stop_id: str):
        """Initialize the data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{stop_id}_realtime",
            update_interval=timedelta(seconds=30),  # Update every 30 seconds
        )
        self._api_key = api_key
        self._stop_id = stop_id
        self.data = {"arrivals": [], "next_departure": None}

    async def _async_update_data(self):
        """Fetch data from Auckland Transport API."""
        # Get current date in YYYY-MM-DD format
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Get current hour in 24-hour format
        current_hour = datetime.now().hour
        
        # Create API endpoint
        api_endpoint = f"{API_BASE_URL}/stops/{self._stop_id}/stoptrips"
        
        # Set up query parameters
        params = {
            "filter[date]": current_date,
            "filter[start_hour]": current_hour,
            "filter[hour_range]": 24
        }
        
        # Set up headers
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_endpoint, params=params, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        return self._process_response(result)
                    else:
                        _LOGGER.error(
                            "Error fetching real-time data: %s (%s)",
                            response.status,
                            await response.text(),
                        )
                        return {"arrivals": [], "next_departure": None}
        except Exception as err:
            _LOGGER.error("Error fetching real-time data: %s", err)
            return {"arrivals": [], "next_departure": None}
    
    def _process_response(self, response_data):
        """Process the response data and filter out past trips."""
        arrivals = []
        next_departure = None
        
        if "data" not in response_data:
            return {"arrivals": [], "next_departure": None}
        
        # Get current time for filtering
        now = datetime.now()
        current_time_str = now.strftime("%H:%M:%S")
        
        # Filter and process trips
        for idx, trip in enumerate(response_data["data"]):
            attributes = trip.get("attributes", {})
            
            # Extract departure time
            departure_time = attributes.get("departure_time")
            
            # Skip trips in the past
            if departure_time and departure_time < current_time_str:
                continue
            
            trip_data = {
                "arrival_time": attributes.get("arrival_time"),
                "departure_time": departure_time,
                "trip_headsign": attributes.get("trip_headsign"),
                "stop_headsign": attributes.get("stop_headsign"),
                "route_id": attributes.get("route_id"),
            }
            
            arrivals.append(trip_data)
            
            # Set the first valid trip as next_departure
            if next_departure is None:
                next_departure = trip_data
        
        # Sort arrivals by departure time
        arrivals.sort(key=lambda x: x["departure_time"] if x["departure_time"] else "")
        
        return {
            "arrivals": arrivals,
            "next_departure": next_departure
        }


class AucklandTransportSensor(CoordinatorEntity, SensorEntity):
    """Auckland Transport sensor."""

    def __init__(self, stop_coordinator, realtime_coordinator, api_key, stop_data):
        """Initialize the sensor."""
        # Initialize with the realtime coordinator
        super().__init__(realtime_coordinator)
        
        self._stop_coordinator = stop_coordinator
        self._realtime_coordinator = realtime_coordinator
        self._api_key = api_key
        self._stop_data = stop_data
        self._stop_id = stop_data.get("id")
        self._attributes = stop_data.get("attributes", {})
        self._stop_name = self._attributes.get("stop_name", "Unknown Stop")
        self._stop_code = self._attributes.get("stop_code", "")
        
        # Set initial value from coordinator data if available
        data = self._realtime_coordinator.data if self._realtime_coordinator.data else {}
        next_departure = data.get("next_departure")
        if next_departure:
            self._attr_native_value = next_departure.get("departure_time", "Unknown")
        else:
            self._attr_native_value = "No upcoming departures"
        
        # Determine transport type based on stop_code
        self._transport_type = "unknown"
        if self._stop_code:
            code_length = len(self._stop_code)
            if code_length == 3:
                self._transport_type = "train"
            elif code_length == 4:
                self._transport_type = "bus"
            elif code_length == 5:
                self._transport_type = "ferry"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return f"Auckland Transport {self._stop_name}"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"auckland_transport_{self._stop_id}"

    @property
    def icon(self) -> str:
        """Return the icon based on transport type."""
        if self._transport_type == "train":
            return "mdi:train"
        elif self._transport_type == "bus":
            return "mdi:bus"
        elif self._transport_type == "ferry":
            return "mdi:ferry"
        return "mdi:transit-connection"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self._realtime_coordinator.data
        
        next_departure = data.get("next_departure")
        if next_departure:
            self._attr_native_value = next_departure.get("departure_time", "Unknown")
        else:
            self._attr_native_value = "No upcoming departures"
        
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes."""
        attrs = {
            ATTR_STOP_NAME: self._stop_name,
            ATTR_STOP_CODE: self._stop_code,
            "transport_type": self._transport_type,
        }
        
        # Add all attributes from the stop data
        if self._attributes:
            for key, value in self._attributes.items():
                if key not in [ATTR_STOP_NAME, ATTR_STOP_CODE]:
                    attrs[key] = value
        
        # Add realtime data attributes
        data = self._realtime_coordinator.data
        arrivals = data.get("arrivals", [])
        
        if arrivals:
            attrs["next_departures_count"] = len(arrivals)
            
            # Add numbered departures as attributes
            for idx, arrival in enumerate(arrivals, 1):
                prefix = f"departure_{idx}"
                attrs[f"{prefix}_time"] = arrival.get("departure_time")
                attrs[f"{prefix}_headsign"] = arrival.get("trip_headsign")
                attrs[f"{prefix}_route"] = arrival.get("route_id")
                
                # Only include the first 4 departures to avoid overloading
                # Notes: might add this as a variable at a later stage
                if idx >= 4:
                    break
        else:
            attrs["next_departures_count"] = 0
        
        return attrs
