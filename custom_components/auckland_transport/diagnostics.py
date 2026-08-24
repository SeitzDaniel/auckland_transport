"""Diagnostics support for Auckland Transport."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .attributes import compact_alert, compact_departure, measure
from .const import CONF_API_KEY, DOMAIN
from .coordinator import StopCoordinator

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: StopCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    runtime = coordinator.runtime
    snapshot = coordinator.data

    diagnostics: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "version": entry.version,
        },
        "runtime": {
            "api_calls_since_start": runtime.api_calls,
            "gtfs_feed_version": runtime.static.feed_version,
            "stops_cached": len(runtime.static.stops),
            "routes_cached": len(runtime.static.routes),
            "realtime_error": runtime.realtime_error,
            "realtime_fetched_at": (
                runtime.cached_realtime.fetched_at.isoformat()
                if runtime.cached_realtime and runtime.cached_realtime.fetched_at
                else None
            ),
            "realtime_trip_updates": (
                len(runtime.cached_realtime.trip_updates) if runtime.cached_realtime else 0
            ),
            "realtime_vehicles": (
                len(runtime.cached_realtime.vehicles_by_trip) if runtime.cached_realtime else 0
            ),
            "realtime_alerts": (
                len(runtime.cached_realtime.alerts) if runtime.cached_realtime else 0
            ),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
        },
    }

    if snapshot is not None:
        departures = [
            compact_departure(item, snapshot.generated_at)
            for item in snapshot.departures[:20]
        ]
        diagnostics["snapshot"] = {
            "stop": {
                "stop_id": snapshot.stop.stop_id,
                "stop_code": snapshot.stop.stop_code,
                "stop_name": snapshot.stop.stop_name,
                "location_type": snapshot.stop.location_type,
                "platform_code": snapshot.stop.platform_code,
            },
            "mode": snapshot.mode,
            "routes": sorted(snapshot.route_ids),
            "scheduled_today": snapshot.scheduled_today,
            "upcoming_departures": len(snapshot.departures),
            "cancelled_departures": snapshot.cancelled_count,
            "realtime_degraded": snapshot.realtime_degraded,
            "departures": departures,
            "alerts": [compact_alert(a, full_text=True) for a in snapshot.alerts],
        }
        # Handy when investigating recorder attribute-size complaints.
        diagnostics["attribute_sizes"] = {
            entity_id: measure(state.attributes)
            for entity_id, state in (
                (eid, hass.states.get(eid))
                for eid in hass.states.async_entity_ids()
                if eid.startswith("sensor.auckland_transport")
            )
            if state is not None
        }

    return diagnostics
