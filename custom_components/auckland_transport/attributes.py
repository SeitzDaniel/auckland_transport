"""Attribute payload builders with a hard size budget.

Home Assistant's recorder refuses to store a state whose serialised attributes
exceed 16384 bytes, which is what the previous version hit once a busy stop had
a few hundred remaining departures. Two things fix that here:

* the bulk of the data lives on dedicated entities instead of one attribute blob,
  and the summary list on the main sensor is capped and uses short keys;
* :func:`fit_attributes` measures the real serialised size and trims the payload
  until it fits, so the error cannot come back regardless of how busy a stop is.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .const import (
    ATTR_ALERTS,
    ATTR_BYTE_BUDGET,
    ATTR_DEPARTURES,
    ATTR_LOCATION_TYPE,
    ATTR_PLATFORM_CODE,
    ATTR_SCHEMA_VERSION,
    ATTR_STOP_CODE,
    ATTR_STOP_ID,
    ATTR_STOP_LAT,
    ATTR_STOP_LON,
    ATTR_STOP_NAME,
    ATTR_TRANSPORT_TYPE,
    ATTR_WHEELCHAIR_BOARDING,
    BOARD_DEPARTURE_LIMIT,
    LEGACY_ATTR_ALERTS,
    LEGACY_ATTR_DEPARTURES,
    MAX_ALERT_TEXT,
    SCHEMA_VERSION,
)
from .model import Alert, Departure, StopSnapshot

# Short keys keep the summary list compact. The card maps them back to
# readable names; see the KEY_MAP table in auckland-transport-card.js.
KEY_ROUTE = "r"
KEY_ROUTE_ID = "ri"
KEY_DESTINATION = "d"
KEY_SCHEDULED = "s"
KEY_EXPECTED = "e"
KEY_DELAY = "dl"
KEY_MINUTES = "m"
KEY_PLATFORM = "p"
KEY_STATUS = "st"
KEY_REALTIME = "rt"
KEY_COLOR = "c"
KEY_TEXT_COLOR = "tc"
KEY_MODE = "mo"
KEY_OCCUPANCY = "oc"
KEY_VEHICLE = "v"
KEY_TRIP = "t"
KEY_BOARDING = "b"
KEY_REASON = "why"
KEY_ALTERNATIVE = "alt"
KEY_ALERT_EFFECT = "ae"
KEY_ALERT_SEVERITY = "as"
KEY_LAT = "lat"
KEY_LON = "lon"
KEY_BEARING = "brg"


def measure(payload: Any) -> int:
    """Return the serialised size of ``payload`` in bytes."""
    return len(json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8"))


def fit_attributes(
    attrs: dict[str, Any],
    *,
    budget: int = ATTR_BYTE_BUDGET,
    shrink_order: tuple[str, ...] = (ATTR_ALERTS, ATTR_DEPARTURES),
    minimum: int = 1,
) -> dict[str, Any]:
    """Trim list attributes until the payload fits inside ``budget``.

    Lists named in ``shrink_order`` lose their last element in turn until the
    payload fits or every list is down to ``minimum`` entries.
    """
    if measure(attrs) <= budget:
        return attrs

    while measure(attrs) > budget:
        trimmed = False
        for key in shrink_order:
            value = attrs.get(key)
            if isinstance(value, list) and len(value) > minimum:
                value.pop()
                trimmed = True
                break
        if not trimmed:
            break

    # Still too big (a single huge alert description, say): drop the softest data.
    if measure(attrs) > budget:
        for key in shrink_order:
            if key in attrs and measure(attrs) > budget:
                attrs[key] = []
        attrs["attributes_truncated"] = True

    return attrs


def compact_departure(departure: Departure, now: datetime, *, verbose: bool = True) -> dict[str, Any]:
    """Serialise a departure using short keys.

    Everything the Lovelace card reads is always included. ``verbose`` only adds the
    vehicle's coordinates, which the card takes from the vehicle-location entity
    instead, so repeating them per row on the board sensor would be wasted space.
    """
    payload: dict[str, Any] = {
        KEY_ROUTE: departure.route_name,
        # The full route id: the card matches filters against it and uses it as the
        # badge tooltip, so it cannot be treated as a detail-only field.
        KEY_ROUTE_ID: departure.route_id,
        KEY_DESTINATION: departure.destination,
        KEY_SCHEDULED: departure.scheduled.strftime("%H:%M"),
        KEY_EXPECTED: departure.expected.strftime("%H:%M"),
        KEY_MINUTES: departure.minutes_until(now),
        KEY_STATUS: departure.status,
        KEY_MODE: departure.mode,
        # The card compares this with the vehicle-location entity's trip to decide
        # whether the tracked vehicle really is the next departure.
        KEY_TRIP: departure.trip_id,
    }
    if departure.delay is not None:
        payload[KEY_DELAY] = departure.delay
    if departure.platform:
        payload[KEY_PLATFORM] = departure.platform
    if departure.realtime:
        payload[KEY_REALTIME] = True
    if departure.route_color:
        payload[KEY_COLOR] = departure.route_color
        payload[KEY_TEXT_COLOR] = departure.route_text_color or "FFFFFF"
    if not departure.boarding:
        payload[KEY_BOARDING] = False
    if departure.occupancy is not None:
        payload[KEY_OCCUPANCY] = departure.occupancy
    if departure.vehicle and departure.vehicle.display_name:
        # Number plate for buses and ferries, set number for trains.
        payload[KEY_VEHICLE] = departure.vehicle.display_name
    if departure.reason:
        payload[KEY_REASON] = departure.reason
    if departure.alternative:
        payload[KEY_ALTERNATIVE] = departure.alternative
    if departure.alert_effect:
        # Lets the card badge the row without carrying the whole alert text.
        payload[KEY_ALERT_EFFECT] = departure.alert_effect
        if departure.alert_severity:
            payload[KEY_ALERT_SEVERITY] = departure.alert_severity

    if verbose:
        if departure.vehicle and departure.vehicle.has_position:
            payload[KEY_LAT] = round(departure.vehicle.latitude, 5)
            payload[KEY_LON] = round(departure.vehicle.longitude, 5)
            if departure.vehicle.bearing is not None:
                payload[KEY_BEARING] = round(departure.vehicle.bearing)

    return payload


def compact_alert(alert: Alert, *, full_text: bool = False) -> dict[str, Any]:
    """Serialise an alert for attribute use, truncating long descriptions."""
    limit = MAX_ALERT_TEXT if full_text else 160
    description = alert.description
    if len(description) > limit:
        description = description[: limit - 1].rstrip() + "…"

    payload: dict[str, Any] = {
        "id": alert.alert_id,
        "header": alert.header,
        "effect": alert.effect,
        "severity": alert.severity or "INFO",
    }
    if alert.cause:
        payload["cause"] = alert.cause
    if alert.effect_detail:
        payload["detail"] = alert.effect_detail
    if description:
        payload["description"] = description
    if alert.route_ids:
        payload["routes"] = sorted(alert.route_ids)[:8]
    if full_text and alert.periods:
        payload["periods"] = [
            {"start": start, "end": end} for start, end in alert.periods[:4]
        ]
    return payload


# ---------------------------------------------------------------------------
# Backwards compatibility with the v0.2 card
# ---------------------------------------------------------------------------
def legacy_departure_attributes(
    departures: list[Departure], limit: int = LEGACY_ATTR_DEPARTURES
) -> dict[str, Any]:
    """Emit the flat ``departure_N_*`` attributes the v0.2 card reads.

    Deliberately bounded: an unbounded version of exactly these keys is what blew
    the recorder's attribute limit. The card walks the numbered keys until it
    finds a gap, so a bounded set works unchanged.
    """
    attrs: dict[str, Any] = {}
    for index, departure in enumerate(departures[:limit], start=1):
        prefix = f"departure_{index}"
        attrs[f"{prefix}_scheduled_time"] = departure.scheduled.strftime("%H:%M:%S")
        attrs[f"{prefix}_actual_time"] = departure.expected.strftime("%H:%M:%S")
        if departure.delay is not None:
            attrs[f"{prefix}_delay_in_seconds"] = departure.delay
        attrs[f"{prefix}_headsign"] = departure.destination
        attrs[f"{prefix}_route"] = departure.route_id
        attrs[f"{prefix}_trip_id"] = departure.trip_id
        if departure.vehicle and departure.vehicle.license_plate:
            attrs[f"{prefix}_license_plate"] = departure.vehicle.license_plate
    return attrs


def legacy_alert_attributes(
    alerts: list[Alert], limit: int = LEGACY_ATTR_ALERTS
) -> dict[str, Any]:
    """Emit the flat ``alert_N_*`` attributes the v0.2 card reads."""
    attrs: dict[str, Any] = {"service_alerts_count": min(len(alerts), limit)}
    for index, alert in enumerate(alerts[:limit], start=1):
        prefix = f"alert_{index}"
        attrs[f"{prefix}_header"] = alert.header
        description = alert.description
        if len(description) > MAX_ALERT_TEXT:
            description = description[: MAX_ALERT_TEXT - 1].rstrip() + "…"
        attrs[f"{prefix}_description"] = description
        attrs[f"{prefix}_cause"] = alert.cause or ""
        attrs[f"{prefix}_effect"] = alert.effect or ""
        attrs[f"{prefix}_status"] = alert.severity or ""
        if alert.route_ids:
            attrs[f"{prefix}_affected_routes"] = ", ".join(sorted(alert.route_ids)[:6])
        if alert.effect_detail:
            attrs[f"{prefix}_resolved_by"] = alert.effect_detail
    return attrs


# ---------------------------------------------------------------------------
# The main sensor's attribute payload
# ---------------------------------------------------------------------------
MAX_ALERTS_IN_ATTRS = 5


def stop_attributes(snapshot: StopSnapshot) -> dict[str, Any]:
    """Return the static description of a stop."""
    stop = snapshot.stop
    attrs: dict[str, Any] = {
        ATTR_STOP_NAME: stop.stop_name,
        ATTR_STOP_CODE: stop.stop_code,
        ATTR_STOP_ID: stop.stop_id,
        ATTR_TRANSPORT_TYPE: snapshot.mode,
        ATTR_LOCATION_TYPE: stop.location_type,
    }
    if stop.latitude is not None:
        attrs[ATTR_STOP_LAT] = stop.latitude
        attrs[ATTR_STOP_LON] = stop.longitude
    if stop.wheelchair_boarding is not None:
        attrs[ATTR_WHEELCHAIR_BOARDING] = stop.wheelchair_boarding
    if stop.platform_code:
        attrs[ATTR_PLATFORM_CODE] = stop.platform_code
    return attrs


def build_board_attributes(
    snapshot: StopSnapshot,
    *,
    legacy: bool = True,
) -> dict[str, Any]:
    """Build the main sensor's attributes.

    Emits both layouts so either version of the Lovelace card works:

    * ``departures`` / ``alerts`` with ``schema_version: 2`` for the current card;
    * the flat ``departure_N_*`` and ``alert_N_*`` keys the v0.2 card reads.

    The summary list is fixed at :data:`BOARD_DEPARTURE_LIMIT` entries. The result
    is always passed through :func:`fit_attributes`, so the payload cannot exceed
    the recorder's limit no matter how busy the stop is.
    """
    now = snapshot.generated_at
    attrs = stop_attributes(snapshot)
    attrs[ATTR_SCHEMA_VERSION] = SCHEMA_VERSION
    attrs["remaining_departures_for_today"] = len(snapshot.departures)
    attrs["scheduled_trips_today"] = snapshot.scheduled_today
    attrs["cancelled_departures"] = snapshot.cancelled_count
    attrs["service_alerts"] = len(snapshot.alerts)
    attrs["realtime_available"] = not snapshot.realtime_degraded
    attrs["last_updated"] = now.isoformat(timespec="seconds")
    attrs["gtfs_feed_version"] = snapshot.feed_version
    attrs["routes"] = sorted(snapshot.route_ids)[:25]

    if departure := snapshot.next_departure:
        # The value the v0.2 sensor exposed as its state.
        attrs["next_departure_time"] = departure.expected.strftime("%H:%M:%S")
        attrs["next_departure_in_minutes"] = departure.minutes_until(now)
        attrs["next_departure_route"] = departure.route_name
        attrs["next_departure_destination"] = departure.destination
        attrs["next_departure_platform"] = departure.platform
        attrs["next_departure_status"] = departure.status

    attrs[ATTR_DEPARTURES] = [
        compact_departure(item, now, verbose=False)
        for item in snapshot.departures[:BOARD_DEPARTURE_LIMIT]
    ]
    attrs[ATTR_ALERTS] = [
        compact_alert(item) for item in snapshot.alerts[:MAX_ALERTS_IN_ATTRS]
    ]

    if legacy:
        attrs.update(legacy_departure_attributes(snapshot.departures))
        attrs.update(legacy_alert_attributes(snapshot.alerts))

    return fit_attributes(attrs)
