"""Shared entity base for Auckland Transport."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .attributes import stop_attributes
from .const import DOMAIN, MODE_ICONS, MODE_UNKNOWN
from .coordinator import StopCoordinator
from .model import StopSnapshot


class ATEntity(CoordinatorEntity[StopCoordinator]):
    """Common plumbing for every Auckland Transport entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StopCoordinator) -> None:
        """Bind the entity to the stop's device."""
        super().__init__(coordinator)
        self._stop_id = coordinator.stop_id
        stop = coordinator.data.stop if coordinator.data else None
        stop_name = stop.stop_name if stop else self._stop_id
        stop_code = stop.stop_code if stop else ""
        # The device name drives the entity_id prefix. Keeping it as
        # "Auckland Transport <stop>" reproduces the entity ids the v0.2 release
        # created, so existing dashboards and the v0.2 card keep working.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._stop_id)},
            name=f"Auckland Transport {stop_name}",
            manufacturer="Auckland Transport",
            model=f"Stop {stop_code}" if stop_code else "Stop",
            configuration_url="https://at.govt.nz/bus-train-ferry/",
        )

    @property
    def snapshot(self) -> StopSnapshot | None:
        """Return the latest merged board for this stop."""
        return self.coordinator.data

    @property
    def _mode(self) -> str:
        """Return the stop's dominant transport mode."""
        return self.snapshot.mode if self.snapshot else MODE_UNKNOWN

    @property
    def icon(self) -> str:
        """Return a mode specific icon."""
        return MODE_ICONS.get(self._mode, MODE_ICONS[MODE_UNKNOWN])

    def _stop_attributes(self) -> dict[str, Any]:
        """Return the static description of the stop."""
        return stop_attributes(self.snapshot) if self.snapshot else {}
