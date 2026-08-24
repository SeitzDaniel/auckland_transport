"""Binary sensor platform for Auckland Transport."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .attributes import compact_alert, fit_attributes
from .const import ATTR_ALERTS, DOMAIN
from .coordinator import StopCoordinator
from .entity import ATEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Auckland Transport binary sensors."""
    coordinator: StopCoordinator = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities([DisruptionBinarySensor(coordinator)])


class DisruptionBinarySensor(ATEntity, BinarySensorEntity):
    """On while something is disrupting service at this stop.

    Useful as an automation trigger: it turns on for a cancelled or skipped
    upcoming departure, or for an active alert that stops or diverts service.
    """

    _attr_name = "Disruption"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    DISRUPTIVE_EFFECTS = frozenset(
        {"NO_SERVICE", "DETOUR", "MODIFIED_SERVICE", "SIGNIFICANT_DELAYS"}
    )

    @property
    def unique_id(self) -> str:
        """Return a stable unique id."""
        return f"auckland_transport_{self._stop_id}_disruption"

    @property
    def _disruptive_alerts(self) -> list:
        """Return the alerts that actually interrupt service."""
        snapshot = self.snapshot
        if not snapshot:
            return []
        return [a for a in snapshot.alerts if a.effect in self.DISRUPTIVE_EFFECTS]

    @property
    def is_on(self) -> bool:
        """Return True when a cancellation or disruptive alert applies."""
        snapshot = self.snapshot
        if not snapshot:
            return False
        return bool(snapshot.cancelled_count) or bool(self._disruptive_alerts)

    @property
    def icon(self) -> str:
        """Return an icon reflecting the current state."""
        return "mdi:alert-circle" if self.is_on else "mdi:check-circle-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what is wrong, why, and any published alternative."""
        snapshot = self.snapshot
        if not snapshot:
            return {}

        affected = [d for d in snapshot.departures if d.cancelled or d.skipped]
        attrs: dict[str, Any] = {
            "cancelled_departures": len(affected),
            "alert_count": len(self._disruptive_alerts),
        }

        reasons = [d.reason for d in affected if d.reason]
        if not reasons:
            reasons = [
                a.effect_detail or a.header for a in self._disruptive_alerts if a.header
            ]
        if reasons:
            attrs["reason"] = reasons[0]
            attrs["reasons"] = list(dict.fromkeys(reasons))[:5]

        alternatives = [d.alternative for d in affected if d.alternative]
        if alternatives:
            attrs["alternative"] = alternatives[0]

        if affected:
            attrs["affected"] = [
                {
                    "route": d.route_name,
                    "destination": d.destination,
                    "scheduled": d.scheduled.strftime("%H:%M"),
                    "status": d.status,
                    "reason": d.reason,
                    "alternative": d.alternative,
                }
                for d in affected[:10]
            ]

        if self._disruptive_alerts:
            attrs[ATTR_ALERTS] = [
                compact_alert(a, full_text=True) for a in self._disruptive_alerts[:5]
            ]

        return fit_attributes(attrs, shrink_order=(ATTR_ALERTS, "affected"), minimum=0)
