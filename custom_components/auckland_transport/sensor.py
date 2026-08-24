"""Sensor platform for Auckland Transport.

Entity layout, per configured stop:

===========================================  ==========================================
Entity                                       State
===========================================  ==========================================
``sensor.auckland_transport_<stop>``          next boardable departure (timestamp)
``sensor.…_<stop>_departures``                remaining departures today
``sensor.…_<stop>_minutes_to_departure``      minutes until the next departure
``sensor.…_<stop>_departure_1..N``            one entity per upcoming departure
``sensor.…_<stop>_service_alerts``            number of active alerts
``sensor.…_<stop>_vehicle_location``          "lat, lon" of the next vehicle
===========================================  ==========================================

Splitting the board across entities is what keeps every state under the
recorder's 16384 byte attribute ceiling; :mod:`.attributes` enforces the rest.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .attributes import (
    build_board_attributes,
    compact_alert,
    fit_attributes,
    legacy_alert_attributes,
)
from .const import (
    ATTR_ALERTS,
    ATTR_SCHEMA_VERSION,
    ATTR_STOP_CODE,
    ATTR_STOP_NAME,
    ATTR_TRANSPORT_TYPE,
    CONF_DEPARTURE_SENSORS,
    CONF_LEGACY_ATTRIBUTES,
    DEFAULT_DEPARTURE_SENSORS,
    DEFAULT_LEGACY_ATTRIBUTES,
    DOMAIN,
    MAX_DEPARTURE_SENSORS,
    MODE_ICONS,
    MODE_UNKNOWN,
    SCHEMA_VERSION,
)
from .coordinator import StopCoordinator
from .entity import ATEntity
from .model import Departure

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Auckland Transport sensors for a config entry."""
    coordinator: StopCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]

    entities: list[SensorEntity] = [
        StopBoardSensor(coordinator),
        DepartureCountSensor(coordinator),
        MinutesToDepartureSensor(coordinator),
        ServiceAlertsSensor(coordinator),
        VehicleLocationSensor(coordinator),
    ]

    count = entry.options.get(CONF_DEPARTURE_SENSORS, DEFAULT_DEPARTURE_SENSORS)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = DEFAULT_DEPARTURE_SENSORS
    for index in range(1, max(0, min(MAX_DEPARTURE_SENSORS, count)) + 1):
        entities.append(SingleDepartureSensor(coordinator, index))

    async_add_entities(entities)


class StopBoardSensor(ATEntity, SensorEntity):
    """The main departure board sensor.

    State is the expected departure time of the next boardable service as a
    timestamp, which Home Assistant renders as a live relative time.
    """

    _attr_name = None
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self) -> str:
        """Return the v0.2 unique id so existing entities are reused."""
        return f"auckland_transport_{self._stop_id}"

    @property
    def native_value(self) -> datetime | None:
        """Return when the next boardable service is expected."""
        snapshot = self.snapshot
        if not snapshot or not (departure := snapshot.next_departure):
            return None
        return dt_util.as_utc(departure.expected)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the board plus the legacy flat attributes, size limited."""
        snapshot = self.snapshot
        if not snapshot:
            return {}
        options = self.coordinator.entry.options
        return build_board_attributes(
            snapshot,
            legacy=bool(options.get(CONF_LEGACY_ATTRIBUTES, DEFAULT_LEGACY_ATTRIBUTES)),
        )


class MinutesToDepartureSensor(ATEntity, SensorEntity):
    """Minutes until the next boardable departure."""

    _attr_name = "Minutes to departure"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-outline"

    @property
    def unique_id(self) -> str:
        """Return a stable unique id."""
        return f"auckland_transport_{self._stop_id}_minutes"

    @property
    def native_value(self) -> int | None:
        """Return whole minutes until the next boardable departure."""
        snapshot = self.snapshot
        if not snapshot or not (departure := snapshot.next_departure):
            return None
        return max(0, departure.minutes_until(snapshot.generated_at))


class DepartureCountSensor(ATEntity, SensorEntity):
    """Number of departures still to come today."""

    _attr_name = "Departures"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:format-list-numbered"

    @property
    def unique_id(self) -> str:
        """Return a stable unique id."""
        return f"auckland_transport_{self._stop_id}_departure_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of remaining departures."""
        return len(self.snapshot.departures) if self.snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return a breakdown of the remaining departures."""
        snapshot = self.snapshot
        if not snapshot:
            return {}
        by_route: dict[str, int] = {}
        for departure in snapshot.departures:
            by_route[departure.route_name] = by_route.get(departure.route_name, 0) + 1
        attrs = {
            "scheduled_trips_today": snapshot.scheduled_today,
            "cancelled_departures": snapshot.cancelled_count,
            "realtime_departures": sum(1 for d in snapshot.departures if d.realtime),
            "by_route": dict(sorted(by_route.items(), key=lambda kv: -kv[1])[:25]),
        }
        return fit_attributes(attrs, shrink_order=("by_route",))


class SingleDepartureSensor(ATEntity, SensorEntity):
    """One entity per upcoming departure, so no single state carries the board."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: StopCoordinator, index: int) -> None:
        """Store the 1-based position this entity tracks."""
        super().__init__(coordinator)
        self._index = index
        self._attr_name = f"Departure {index}"

    @property
    def unique_id(self) -> str:
        """Return a stable unique id."""
        return f"auckland_transport_{self._stop_id}_departure_{self._index}"

    @property
    def _departure(self) -> Departure | None:
        """Return the departure at this position, if it exists."""
        snapshot = self.snapshot
        if not snapshot or len(snapshot.departures) < self._index:
            return None
        return snapshot.departures[self._index - 1]

    @property
    def icon(self) -> str:
        """Return an icon matching this departure's own mode."""
        departure = self._departure
        mode = departure.mode if departure else self._mode
        return MODE_ICONS.get(mode, MODE_ICONS[MODE_UNKNOWN])

    @property
    def native_value(self) -> datetime | None:
        """Return the expected departure time."""
        departure = self._departure
        return dt_util.as_utc(departure.expected) if departure else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full detail of this one departure."""
        departure = self._departure
        snapshot = self.snapshot
        if not departure or not snapshot:
            return {}

        attrs: dict[str, Any] = {
            "position": self._index,
            "route": departure.route_name,
            "route_id": departure.route_id,
            "destination": departure.destination,
            "mode": departure.mode,
            "scheduled_time": departure.scheduled.strftime("%H:%M:%S"),
            "expected_time": departure.expected.strftime("%H:%M:%S"),
            "scheduled": dt_util.as_utc(departure.scheduled).isoformat(),
            "minutes": departure.minutes_until(snapshot.generated_at),
            "status": departure.status,
            "realtime": departure.realtime,
            "boarding": departure.boarding,
            "cancelled": departure.cancelled,
            "trip_id": departure.trip_id,
            ATTR_STOP_NAME: snapshot.stop.stop_name,
            ATTR_STOP_CODE: snapshot.stop.stop_code,
        }
        if departure.delay is not None:
            attrs["delay_seconds"] = departure.delay
            attrs["delay_minutes"] = round(departure.delay / 60, 1)
        if departure.platform:
            attrs["platform"] = departure.platform
            attrs["platform_stop_name"] = departure.stop_name
        if departure.route_color:
            attrs["route_color"] = f"#{departure.route_color}"
            attrs["route_text_color"] = f"#{departure.route_text_color or 'FFFFFF'}"
        if departure.direction_id is not None:
            attrs["direction_id"] = departure.direction_id
        if departure.alert_effect:
            attrs["alert_effect"] = departure.alert_effect
            attrs["alert_severity"] = departure.alert_severity
        if departure.reason:
            attrs["reason"] = departure.reason
        if departure.alternative:
            attrs["alternative"] = departure.alternative
        if departure.skipped:
            attrs["stop_skipped"] = True

        if vehicle := departure.vehicle:
            attrs["vehicle_id"] = vehicle.vehicle_id
            if vehicle.license_plate:
                attrs["license_plate"] = vehicle.license_plate
            if vehicle.occupancy_label:
                attrs["occupancy"] = vehicle.occupancy_label
                attrs["occupancy_status"] = vehicle.occupancy
            if vehicle.has_position:
                # Named so the entity can be dropped straight onto a map card.
                attrs["latitude"] = vehicle.latitude
                attrs["longitude"] = vehicle.longitude
                attrs["gps_accuracy"] = 0
            if vehicle.bearing is not None:
                attrs["bearing"] = vehicle.bearing
            if vehicle.speed is not None:
                attrs["speed"] = round(vehicle.speed * 3.6, 1)

        if departure.alerts:
            attrs[ATTR_ALERTS] = [compact_alert(item) for item in departure.alerts[:3]]

        return fit_attributes(attrs)


class ServiceAlertsSensor(ATEntity, SensorEntity):
    """Active service alerts affecting this stop."""

    _attr_name = "Service alerts"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        """Return a stable unique id."""
        return f"auckland_transport_{self._stop_id}_alerts"

    @property
    def icon(self) -> str:
        """Return an icon reflecting whether anything is wrong."""
        snapshot = self.snapshot
        if snapshot and snapshot.alerts:
            severe = any(alert.severity == "SEVERE" for alert in snapshot.alerts)
            return "mdi:alert-octagon" if severe else "mdi:alert"
        return "mdi:check-circle-outline"

    @property
    def native_value(self) -> int | None:
        """Return the number of active alerts."""
        return len(self.snapshot.alerts) if self.snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the alerts in full, trimmed to the attribute budget."""
        snapshot = self.snapshot
        if not snapshot:
            return {}
        attrs: dict[str, Any] = {
            ATTR_SCHEMA_VERSION: SCHEMA_VERSION,
            ATTR_STOP_NAME: snapshot.stop.stop_name,
            ATTR_STOP_CODE: snapshot.stop.stop_code,
            "severe_count": sum(1 for a in snapshot.alerts if a.severity == "SEVERE"),
            "effects": sorted({a.effect for a in snapshot.alerts if a.effect}),
            ATTR_ALERTS: [compact_alert(a, full_text=True) for a in snapshot.alerts],
        }
        attrs.update(legacy_alert_attributes(snapshot.alerts))
        return fit_attributes(attrs, shrink_order=(ATTR_ALERTS,), minimum=0)


class VehicleLocationSensor(ATEntity, SensorEntity):
    """Position of the vehicle serving the next departure.

    Kept with the v0.2 name and unique id so the old card's map keeps working.
    """

    _attr_name = "Vehicle location"

    @property
    def unique_id(self) -> str:
        """Return the v0.2 unique id so the existing entity is reused."""
        return f"auckland_transport_{self._stop_id}_vehicle_location"

    @property
    def _tracked(self) -> Departure | None:
        """Return the first upcoming departure that has a live vehicle."""
        snapshot = self.snapshot
        if not snapshot:
            return None
        for departure in snapshot.departures:
            if departure.vehicle and departure.vehicle.has_position:
                return departure
        return None

    @property
    def icon(self) -> str:
        """Return an icon matching the tracked vehicle's mode."""
        departure = self._tracked
        mode = departure.mode if departure else self._mode
        return MODE_ICONS.get(mode, "mdi:map-marker")

    @property
    def native_value(self) -> str:
        """Return the tracked vehicle's coordinates as ``lat, lon``."""
        departure = self._tracked
        if not departure or not departure.vehicle:
            return "No vehicle location"
        vehicle = departure.vehicle
        return f"{vehicle.latitude}, {vehicle.longitude}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return map friendly attributes for the tracked vehicle."""
        snapshot = self.snapshot
        if not snapshot:
            return {}

        attrs: dict[str, Any] = {
            ATTR_STOP_NAME: snapshot.stop.stop_name,
            ATTR_STOP_CODE: snapshot.stop.stop_code,
            ATTR_TRANSPORT_TYPE: snapshot.mode,
        }

        departure = self._tracked
        if not departure or not departure.vehicle:
            attrs["tracking"] = False
            return attrs

        vehicle = departure.vehicle
        attrs.update(
            {
                "tracking": True,
                "latitude": vehicle.latitude,
                "longitude": vehicle.longitude,
                "gps_accuracy": 0,
                "trip_id": departure.trip_id,
                "route_id": departure.route_id,
                "route": departure.route_name,
                # The v0.2 card reads ``headsign``.
                "headsign": departure.destination,
                "destination": departure.destination,
                "mode": departure.mode,
                "expected_time": departure.expected.strftime("%H:%M:%S"),
                "minutes": departure.minutes_until(snapshot.generated_at),
            }
        )
        if departure.platform:
            attrs["platform"] = departure.platform
        if vehicle.bearing is not None:
            attrs["bearing"] = vehicle.bearing
        if vehicle.speed is not None:
            attrs["speed"] = round(vehicle.speed * 3.6, 1)
        if vehicle.timestamp:
            attrs["last_update"] = vehicle.timestamp
        if vehicle.vehicle_id:
            attrs["vehicle_id"] = vehicle.vehicle_id
        if vehicle.license_plate:
            attrs["license_plate"] = vehicle.license_plate
        if vehicle.occupancy_label:
            attrs["occupancy"] = vehicle.occupancy_label
        return attrs
