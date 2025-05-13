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
    
    # Get update interval from options or use default
    update_interval = entry.options.get("update_interval", DEFAULT_SCAN_INTERVAL)
    
    # Find stop details in coordinator data
    stop_data = None
    
    for stop in coordinator.data:
        if stop.get("id") == stop_id:
            stop_data = stop
            break
    
    if not stop_data:
        _LOGGER.error("Could not find stop data for stop_id: %s", stop_id)
        return
    
    # Create real-time data coordinator with configured update interval
    realtime_coordinator = RealtimeDataCoordinator(
        hass, 
        api_key, 
        stop_id,
        update_interval
    )
    
    # Initial data fetch - force immediate refresh
    await realtime_coordinator.async_refresh()
    
    # Create the sensor entity
    async_add_entities([AucklandTransportSensor(coordinator, realtime_coordinator, api_key, stop_data)])


class RealtimeDataCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Auckland Transport real-time data."""

    def __init__(self, hass: HomeAssistant, api_key: str, stop_id: str, update_interval: int = 60):
        """Initialize the data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{stop_id}_realtime",
            update_interval=timedelta(seconds=update_interval),
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
                        processed_data = self._process_response(result)

                        # If we have arrivals, fetch additional details for the first one
                        if processed_data["arrivals"] and processed_data["next_departure"]:
                            trip_id = processed_data["next_departure"].get("trip_id")
                            if trip_id:
                                realtime_details = await self._fetch_realtime_trip_details(session, trip_id)
                                if realtime_details:
                                    processed_data["next_departure"].update(realtime_details)
                        
                        return processed_data
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
    
    async def _fetch_realtime_trip_details(self, session, trip_id):
        """Fetch additional real-time details for a specific trip."""
        api_endpoint = "https://api.at.govt.nz/realtime/legacy/tripupdates"
        params = {"tripid": trip_id}
        headers = {"Cache-Control": "no-cache", "Ocp-Apim-Subscription-Key": self._api_key}
        
        try:
            async with session.get(api_endpoint, params=params, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    if result.get("status") == "OK" and "response" in result:
                        entities = result["response"].get("entity", [])
                        for entity in entities:
                            if entity.get("id") == trip_id and "trip_update" in entity:
                                trip_update = entity["trip_update"]
                                
                                # Extract license plate
                                license_plate = None
                                if "vehicle" in trip_update and "license_plate" in trip_update["vehicle"]:
                                    license_plate = trip_update["vehicle"]["license_plate"]
                                
                                # Extract delay in seconds
                                delay_seconds = None
                                if "delay" in trip_update:
                                    delay_seconds = trip_update["delay"]
                                elif "stop_time_update" in trip_update and "arrival" in trip_update["stop_time_update"]:
                                    delay_seconds = trip_update["stop_time_update"]["arrival"].get("delay")
                                
                                return {
                                    "license_plate": license_plate,
                                    "delay_seconds": delay_seconds
                                }
                else:
                    _LOGGER.error(
                        "Error fetching real-time trip details: %s (%s)",
                        response.status,
                        await response.text(),
                    )
        except Exception as err:
            _LOGGER.error("Error fetching real-time trip details: %s", err)
        
        return None
    
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
                "scheduled_departure_time": departure_time,  # Renamed from departure_time to scheduled_departure_time
                "trip_headsign": attributes.get("trip_headsign"),
                "stop_headsign": attributes.get("stop_headsign"),
                "route_id": attributes.get("route_id"),
                "trip_id": attributes.get("trip_id"),
            }
            
            arrivals.append(trip_data)
            
            # Set the first valid trip as next_departure
            if next_departure is None:
                next_departure = trip_data
        
        # Sort arrivals by departure time
        arrivals.sort(key=lambda x: x["scheduled_departure_time"] if x["scheduled_departure_time"] else "")
        
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
            self._attr_native_value = next_departure.get("scheduled_departure_time", "Unknown")
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
            # Calculate actual departure time if we have delay information
            scheduled_time = next_departure.get("scheduled_departure_time", "Unknown")
            delay_seconds = next_departure.get("delay_seconds")
            
            if scheduled_time != "Unknown" and delay_seconds is not None:
                # Use the scheduled time as the native value if no delay
                if delay_seconds == 0:
                    self._attr_native_value = scheduled_time
                else:
                    # Try to calculate actual departure time with delay
                    try:
                        # Parse the scheduled time
                        hour, minute, second = map(int, scheduled_time.split(':'))
                        
                        # Create a datetime object for today with this time
                        now = datetime.now()
                        scheduled_dt = datetime(
                            now.year, now.month, now.day, 
                            hour, minute, second
                        )
                        
                        # Add the delay
                        actual_dt = scheduled_dt + timedelta(seconds=delay_seconds)
                        
                        # Format the actual departure time
                        self._attr_native_value = actual_dt.strftime("%H:%M:%S")
                    except Exception as e:
                        _LOGGER.error("Error calculating actual departure time: %s", e)
                        self._attr_native_value = scheduled_time
            else:
                # If no delay info available, use scheduled time
                self._attr_native_value = scheduled_time
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
            attrs["total_departures_for_today"] = len(arrivals)
            
            # Add numbered departures as attributes
            for idx, arrival in enumerate(arrivals, 1):
                prefix = f"departure_{idx}"
                
                # For the first departure, include additional information
                if idx == 1:
                    attrs[f"{prefix}_scheduled_time"] = arrival.get("scheduled_departure_time")
                    
                    # Add delay information if available
                    delay_seconds = arrival.get("delay_seconds")
                    if delay_seconds is not None:
                        attrs[f"{prefix}_delay_in_seconds"] = delay_seconds
                        
                        # Calculate and add actual departure time with delay
                        scheduled_time = arrival.get("scheduled_departure_time")
                        if scheduled_time:
                            try:
                                # Parse the scheduled time
                                hour, minute, second = map(int, scheduled_time.split(':'))
                                
                                # Create a datetime object for today with this time
                                now = datetime.now()
                                scheduled_dt = datetime(
                                    now.year, now.month, now.day, 
                                    hour, minute, second
                                )
                                
                                # Add the delay
                                actual_dt = scheduled_dt + timedelta(seconds=delay_seconds)
                                
                                # Format the actual departure time
                                attrs[f"{prefix}_actual_time"] = actual_dt.strftime("%H:%M:%S")
                            except Exception as e:
                                _LOGGER.error("Error calculating actual departure time: %s", e)
                    
                    # Add license plate if available
                    license_plate = arrival.get("license_plate")
                    if license_plate:
                        attrs[f"{prefix}_license_plate"] = license_plate

                else:
                    # For other departures, just include basic information
                    attrs[f"{prefix}_scheduled_time"] = arrival.get("scheduled_departure_time")
                
                attrs[f"{prefix}_headsign"] = arrival.get("trip_headsign")
                attrs[f"{prefix}_route"] = arrival.get("route_id")
                attrs[f"{prefix}_trip_id"] = arrival.get("trip_id")
                
                # Only include the first 4 departures to avoid overloading
                # Notes: might add this as a variable at a later stage
                if idx >= 4:
                    break
        else:
            attrs["total_departures_for_today"] = 0
        
        return attrs
