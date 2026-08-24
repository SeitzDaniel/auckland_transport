"""Data structures and the schedule/realtime merge for Auckland Transport."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta
from typing import Any

from .const import (
    GTFS_SCHEDULE_CANCELED,
    GTFS_STOP_SKIPPED,
    MODE_BUS,
    MODE_FERRY,
    MODE_SCHOOL_BUS,
    MODE_TRAIN,
    MODE_UNKNOWN,
    OCCUPANCY_LABELS,
    ON_TIME_TOLERANCE,
    ROUTE_TYPE_TO_MODE,
    SCHOOL_BUS_ROUTE_TYPES,
    STATUS_ARRIVING,
    STATUS_CANCELLED,
    STATUS_DEPARTING,
    STATUS_EARLY,
    STATUS_LATE,
    STATUS_ON_TIME,
    STATUS_SCHEDULED,
    STATUS_SKIPPED,
)

_TRAIN_NAME_RE = re.compile(r"train station", re.IGNORECASE)
_FERRY_NAME_RE = re.compile(r"ferry terminal|wharf", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Static feed
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Stop:
    """A stop, platform or parent station from the static feed."""

    stop_id: str
    stop_code: str
    stop_name: str
    latitude: float | None = None
    longitude: float | None = None
    location_type: int = 0
    wheelchair_boarding: int | None = None
    parent_station: str | None = None
    platform_code: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Stop | None:
        """Build a stop from a ``/gtfs/v3/stops`` entry, or ``None`` if unusable."""
        attrs = raw.get("attributes") or {}
        stop_id = raw.get("id") or attrs.get("stop_id")
        if not stop_id:
            return None
        return cls(
            stop_id=str(stop_id),
            stop_code=str(attrs.get("stop_code") or ""),
            stop_name=str(attrs.get("stop_name") or ""),
            latitude=attrs.get("stop_lat"),
            longitude=attrs.get("stop_lon"),
            location_type=int(attrs.get("location_type") or 0),
            wheelchair_boarding=attrs.get("wheelchair_boarding"),
            parent_station=attrs.get("parent_station"),
            platform_code=attrs.get("platform_code"),
        )

    @property
    def is_station(self) -> bool:
        """Return True when this stop groups platforms rather than being one."""
        return self.location_type == 1

    def guess_mode(self) -> str:
        """Guess the mode from the stop name.

        Only used to pre-filter the config-flow picker; the authoritative mode
        comes from the ``route_type`` of the routes actually calling here.
        """
        if _TRAIN_NAME_RE.search(self.stop_name):
            return MODE_TRAIN
        if _FERRY_NAME_RE.search(self.stop_name) and self.location_type == 1:
            return MODE_FERRY
        return MODE_BUS

    def label(self) -> str:
        """Return a human readable label for pickers."""
        parts = [self.stop_name or self.stop_id]
        if self.stop_code:
            parts.append(f"({self.stop_code})")
        if self.platform_code:
            parts.append(f"– platform {self.platform_code}")
        elif self.is_station:
            parts.append("– all platforms")
        return " ".join(parts)


@dataclass(slots=True)
class Route:
    """A route from the static feed, including its official line colours."""

    route_id: str
    short_name: str
    long_name: str
    route_type: int
    agency_id: str | None = None
    color: str | None = None
    text_color: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Route | None:
        """Build a route from a ``/gtfs/v3/routes`` entry."""
        attrs = raw.get("attributes") or {}
        route_id = raw.get("id") or attrs.get("route_id")
        if not route_id:
            return None
        return cls(
            route_id=str(route_id),
            short_name=str(attrs.get("route_short_name") or ""),
            long_name=str(attrs.get("route_long_name") or ""),
            route_type=int(attrs.get("route_type") or -1),
            agency_id=attrs.get("agency_id"),
            color=attrs.get("route_color"),
            text_color=attrs.get("route_text_color"),
        )

    @property
    def mode(self) -> str:
        """Return the transport mode implied by ``route_type``."""
        return ROUTE_TYPE_TO_MODE.get(self.route_type, MODE_UNKNOWN)

    @property
    def display_name(self) -> str:
        """Return the shortest sensible public-facing route name."""
        return self.short_name or self.long_name or self.route_id


@dataclass(slots=True)
class StaticData:
    """The cached static feed, refreshed only when the feed version changes."""

    feed_version: str | None = None
    stops: dict[str, Stop] = field(default_factory=dict)
    routes: dict[str, Route] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)

    def rebuild_children(self) -> None:
        """Index platform stops by their parent station."""
        children: dict[str, list[str]] = {}
        for stop in self.stops.values():
            if stop.parent_station:
                children.setdefault(stop.parent_station, []).append(stop.stop_id)
        self.children = children

    def family(self, stop_id: str) -> set[str]:
        """Return ``stop_id`` together with every platform underneath it."""
        return {stop_id, *self.children.get(stop_id, [])}


# ---------------------------------------------------------------------------
# Realtime feed
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Vehicle:
    """A realtime vehicle position."""

    vehicle_id: str | None = None
    label: str | None = None
    license_plate: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    bearing: float | None = None
    speed: float | None = None
    occupancy: int | None = None
    timestamp: int | None = None
    trip_id: str | None = None
    route_id: str | None = None

    @property
    def has_position(self) -> bool:
        """Return True when usable coordinates are present."""
        return self.latitude is not None and self.longitude is not None

    @property
    def occupancy_label(self) -> str | None:
        """Return the GTFS occupancy enum as a readable string."""
        if self.occupancy is None:
            return None
        return OCCUPANCY_LABELS.get(self.occupancy)

    @property
    def display_name(self) -> str | None:
        """Return the best available human identifier for the vehicle.

        Train labels arrive padded, e.g. ``"AMP        701"``, so collapse runs of
        whitespace before showing them.
        """
        for candidate in (self.license_plate, self.label, self.vehicle_id):
            if candidate and candidate.strip():
                return " ".join(str(candidate).split())
        return None


@dataclass(slots=True)
class TripUpdate:
    """A realtime trip update."""

    trip_id: str
    route_id: str | None = None
    delay: int | None = None
    cancelled: bool = False
    timestamp: int | None = None
    vehicle_id: str | None = None
    license_plate: str | None = None
    label: str | None = None
    next_stop_id: str | None = None
    next_stop_sequence: int | None = None
    stop_delay: int | None = None
    stop_time: int | None = None
    stop_skipped: bool = False


@dataclass(slots=True)
class Alert:
    """A service alert, flattened to the fields the UI needs."""

    alert_id: str
    header: str = ""
    description: str = ""
    cause: str | None = None
    effect: str | None = None
    effect_detail: str | None = None
    severity: str | None = None
    stop_ids: set[str] = field(default_factory=set)
    route_ids: set[str] = field(default_factory=set)
    trip_ids: set[str] = field(default_factory=set)
    periods: list[tuple[int | None, int | None]] = field(default_factory=list)
    timestamp: int | None = None

    def is_active(self, now_ts: int, look_ahead: int = 0) -> bool:
        """Return True when an active period covers now (or the next window)."""
        if not self.periods:
            return True
        for start, end in self.periods:
            if (start or 0) <= now_ts + look_ahead and (end or 2**62) >= now_ts:
                return True
        return False

    def active_until(self, now_ts: int) -> int | None:
        """Return the end of the currently running period, if any."""
        for start, end in self.periods:
            if (start or 0) <= now_ts <= (end or 2**62):
                return end
        return None

    @property
    def is_cancellation(self) -> bool:
        """Return True for the per-trip 'Service Cancellation' alerts."""
        return bool(self.trip_ids) and self.effect == "NO_SERVICE"


@dataclass(slots=True)
class RealtimeData:
    """Parsed realtime feed, shared by every configured stop."""

    fetched_at: datetime | None = None
    feed_timestamp: float | None = None
    trip_updates: dict[str, TripUpdate] = field(default_factory=dict)
    vehicles_by_trip: dict[str, Vehicle] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)
    alerts_by_stop: dict[str, list[int]] = field(default_factory=dict)
    alerts_by_route: dict[str, list[int]] = field(default_factory=dict)
    alerts_by_trip: dict[str, list[int]] = field(default_factory=dict)
    # Alerts that name a route but no stop, so they apply along the whole line.
    route_wide_alerts: dict[str, list[int]] = field(default_factory=dict)


def _as_float(value: Any) -> float | None:
    """Coerce a feed value to float.

    The AT feed is loosely typed: ``position.bearing`` arrives as a string for
    almost every vehicle while ``speed`` mixes ints and floats, so every numeric
    read from the feed is normalised here rather than at each use site.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce a feed value to int, tolerating numeric strings."""
    number = _as_float(value)
    return None if number is None else int(number)


def _translated(node: Any) -> str:
    """Pull the English (or first) translation out of a GTFS-RT text node."""
    if not isinstance(node, dict):
        return ""
    translations = node.get("translation") or []
    if isinstance(translations, dict):
        translations = [translations]
    for item in translations:
        if isinstance(item, dict) and item.get("language", "en").startswith("en"):
            return str(item.get("text") or "")
    for item in translations:
        if isinstance(item, dict) and item.get("text"):
            return str(item["text"])
    return ""


def parse_realtime(entities: list[dict[str, Any]], now: datetime) -> RealtimeData:
    """Turn the combined realtime feed into indexed lookups."""
    data = RealtimeData(fetched_at=now)

    for entity in entities:
        if raw := entity.get("trip_update"):
            _parse_trip_update(entity, raw, data)
        if raw := entity.get("vehicle"):
            _parse_vehicle(raw, data)
        if raw := entity.get("alert"):
            _parse_alert(entity, raw, data)

    return data


def _parse_trip_update(entity: dict[str, Any], raw: dict[str, Any], data: RealtimeData) -> None:
    """Index one ``trip_update`` entity."""
    trip = raw.get("trip") or {}
    trip_id = trip.get("trip_id") or entity.get("id")
    if not trip_id:
        return

    vehicle = raw.get("vehicle") or {}
    update = TripUpdate(
        trip_id=str(trip_id),
        route_id=trip.get("route_id"),
        delay=_as_int(raw.get("delay")),
        cancelled=_as_int(trip.get("schedule_relationship")) == GTFS_SCHEDULE_CANCELED,
        timestamp=_as_int(raw.get("timestamp")),
        vehicle_id=vehicle.get("id") or None,
        license_plate=vehicle.get("license_plate") or None,
        label=vehicle.get("label") or None,
    )

    # AT sends a single stop_time_update object describing the vehicle's next
    # stop, but tolerate a list in case the feed ever follows the spec exactly.
    stop_updates = raw.get("stop_time_update")
    if isinstance(stop_updates, dict):
        stop_updates = [stop_updates]
    if stop_updates:
        first = stop_updates[0] or {}
        update.next_stop_id = first.get("stop_id")
        update.next_stop_sequence = _as_int(first.get("stop_sequence"))
        update.stop_skipped = (
            _as_int(first.get("schedule_relationship")) == GTFS_STOP_SKIPPED
        )
        timing = first.get("departure") or first.get("arrival") or {}
        update.stop_delay = _as_int(timing.get("delay"))
        update.stop_time = _as_int(timing.get("time"))

    data.trip_updates[update.trip_id] = update


def _parse_vehicle(raw: dict[str, Any], data: RealtimeData) -> None:
    """Index one ``vehicle`` entity, keyed by the trip it is serving."""
    trip = raw.get("trip") or {}
    trip_id = trip.get("trip_id")
    if not trip_id:
        # Out of service / deadheading vehicles are not useful to us.
        return

    position = raw.get("position") or {}
    descriptor = raw.get("vehicle") or {}
    data.vehicles_by_trip[str(trip_id)] = Vehicle(
        vehicle_id=descriptor.get("id") or None,
        label=descriptor.get("label") or None,
        license_plate=descriptor.get("license_plate") or None,
        latitude=_as_float(position.get("latitude")),
        longitude=_as_float(position.get("longitude")),
        bearing=_as_float(position.get("bearing")),
        speed=_as_float(position.get("speed")),
        occupancy=_as_int(raw.get("occupancy_status")),
        timestamp=_as_int(raw.get("timestamp")),
        trip_id=str(trip_id),
        route_id=trip.get("route_id"),
    )


def _parse_alert(entity: dict[str, Any], raw: dict[str, Any], data: RealtimeData) -> None:
    """Index one ``alert`` entity by stop, route and trip."""
    periods: list[tuple[int | None, int | None]] = []
    for period in raw.get("active_period") or []:
        if isinstance(period, dict):
            periods.append((_as_int(period.get("start")), _as_int(period.get("end"))))

    alert = Alert(
        alert_id=str(entity.get("id") or len(data.alerts)),
        header=_translated(raw.get("header_text")),
        description=_translated(raw.get("description_text")),
        cause=raw.get("cause"),
        effect=raw.get("effect"),
        effect_detail=_translated(raw.get("effect_detail")) or None,
        severity=raw.get("severity_level"),
        periods=periods,
    )

    alert.timestamp = _as_int(entity.get("timestamp"))

    informed = raw.get("informed_entity") or []
    if isinstance(informed, dict):
        informed = [informed]
    for item in informed:
        if not isinstance(item, dict):
            continue
        if stop_id := item.get("stop_id"):
            alert.stop_ids.add(str(stop_id))
        if route_id := item.get("route_id"):
            alert.route_ids.add(str(route_id))
        if trip_id := ((item.get("trip") or {}).get("trip_id")):
            alert.trip_ids.add(str(trip_id))

    index = len(data.alerts)
    data.alerts.append(alert)
    for stop_id in alert.stop_ids:
        data.alerts_by_stop.setdefault(stop_id, []).append(index)
    for route_id in alert.route_ids:
        data.alerts_by_route.setdefault(route_id, []).append(index)
        if not alert.stop_ids:
            data.route_wide_alerts.setdefault(route_id, []).append(index)
    for trip_id in alert.trip_ids:
        data.alerts_by_trip.setdefault(trip_id, []).append(index)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ScheduledTrip:
    """One scheduled call at a stop, straight from ``stoptrips``."""

    trip_id: str
    route_id: str
    stop_id: str
    scheduled: datetime
    arrival: datetime | None
    stop_sequence: int
    pickup_type: int
    drop_off_type: int
    stop_headsign: str
    trip_headsign: str
    direction_id: int | None
    service_date: str


@dataclass(slots=True)
class StopSchedule:
    """The cached timetable for one stop and service day."""

    stop_id: str
    service_date: date_cls
    trips: list[ScheduledTrip] = field(default_factory=list)
    fetched_at: datetime | None = None


def naive_local(value: datetime) -> datetime:
    """Return ``value`` as a naive local wall-clock time.

    GTFS times are local wall-clock values, so the whole model layer works in
    naive local time and only the entities convert to an aware UTC instant. This
    accepts an aware datetime too so callers cannot accidentally mix the two and
    hit "can't subtract offset-naive and offset-aware datetimes".
    """
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def epoch_of(value: datetime) -> int:
    """Return the POSIX timestamp of ``value``, assuming local time if naive."""
    return int(value.timestamp())


def _parse_gtfs_time(value: str | None, base: date_cls) -> datetime | None:
    """Parse a GTFS ``HH:MM:SS`` time, honouring hours past 24."""
    if not value:
        return None
    parts = value.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return datetime(base.year, base.month, base.day) + timedelta(
        hours=hour, minutes=minute, seconds=second
    )


def parse_schedule(
    stop_id: str, service_date: date_cls, raw_trips: list[dict[str, Any]], now: datetime
) -> StopSchedule:
    """Turn a ``stoptrips`` response into a sorted schedule of local times."""
    now = naive_local(now)
    trips: list[ScheduledTrip] = []
    for raw in raw_trips:
        attrs = raw.get("attributes") or {}
        trip_id = attrs.get("trip_id")
        if not trip_id:
            continue
        # ``service_date`` anchors extended times (e.g. 24:30) to the right day.
        base = service_date
        if raw_date := attrs.get("service_date"):
            try:
                base = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                base = service_date
        departure = _parse_gtfs_time(attrs.get("departure_time"), base)
        if departure is None:
            continue
        trips.append(
            ScheduledTrip(
                trip_id=str(trip_id),
                route_id=str(attrs.get("route_id") or ""),
                stop_id=str(attrs.get("stop_id") or stop_id),
                scheduled=departure,
                arrival=_parse_gtfs_time(attrs.get("arrival_time"), base),
                stop_sequence=int(attrs.get("stop_sequence") or 0),
                pickup_type=int(attrs.get("pickup_type") or 0),
                drop_off_type=int(attrs.get("drop_off_type") or 0),
                stop_headsign=str(attrs.get("stop_headsign") or ""),
                trip_headsign=str(attrs.get("trip_headsign") or ""),
                direction_id=attrs.get("direction_id"),
                service_date=str(attrs.get("service_date") or service_date.isoformat()),
            )
        )

    trips.sort(key=lambda trip: trip.scheduled)
    return StopSchedule(
        stop_id=stop_id, service_date=service_date, trips=trips, fetched_at=now
    )


# ---------------------------------------------------------------------------
# Merged view
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Departure:
    """A scheduled call enriched with everything realtime knows about it."""

    trip_id: str
    route_id: str
    route_name: str
    route_color: str | None
    route_text_color: str | None
    mode: str
    destination: str
    scheduled: datetime
    expected: datetime
    delay: int | None
    status: str
    realtime: bool
    platform: str | None
    stop_id: str
    stop_name: str
    boarding: bool
    cancelled: bool
    skipped: bool
    direction_id: int | None
    vehicle: Vehicle | None
    alerts: list[Alert]
    reason: str | None
    alternative: str | None
    alert_effect: str | None
    alert_severity: str | None

    def minutes_until(self, now: datetime) -> int:
        """Return whole minutes until departure, rounded to nearest."""
        return int(round((self.expected - naive_local(now)).total_seconds() / 60))

    @property
    def occupancy(self) -> int | None:
        """Return the vehicle's occupancy enum, if reported."""
        return self.vehicle.occupancy if self.vehicle else None


def _status_for(delay: int | None, cancelled: bool, skipped: bool, minutes: int) -> str:
    """Classify a departure for display."""
    if cancelled:
        return STATUS_CANCELLED
    if skipped:
        return STATUS_SKIPPED
    if minutes <= 0:
        return STATUS_DEPARTING
    if minutes <= 1:
        return STATUS_ARRIVING
    if delay is None:
        return STATUS_SCHEDULED
    if delay > ON_TIME_TOLERANCE:
        return STATUS_LATE
    if delay < -ON_TIME_TOLERANCE:
        return STATUS_EARLY
    return STATUS_ON_TIME


def build_departures(
    *,
    schedule: StopSchedule,
    realtime: RealtimeData | None,
    static: StaticData,
    stop_family: set[str],
    now: datetime,
    include_school_buses: bool = True,
    hide_arrivals: bool = False,
    past_grace: timedelta = timedelta(minutes=2),
    now_ts: int | None = None,
) -> list[Departure]:
    """Merge the timetable with realtime data into a sorted departure board.

    ``now`` may be naive or aware; it is normalised to naive local time to match
    the GTFS wall-clock values. ``now_ts`` is the POSIX timestamp used for alert
    activity windows and is derived from ``now`` when not supplied.
    """
    if now_ts is None:
        now_ts = epoch_of(now)
    now = naive_local(now)
    departures: list[Departure] = []

    for trip in schedule.trips:
        route = static.routes.get(trip.route_id)
        route_type = route.route_type if route else -1
        if not include_school_buses and route_type in SCHOOL_BUS_ROUTE_TYPES:
            continue
        if hide_arrivals and trip.pickup_type == 1:
            continue

        update = realtime.trip_updates.get(trip.trip_id) if realtime else None
        delay = None
        cancelled = False
        skipped = False
        if update is not None:
            cancelled = update.cancelled
            delay = update.delay
            # The stop_time_update is more precise, but only for the stop it names.
            if update.next_stop_id and update.next_stop_id == trip.stop_id:
                if update.stop_delay is not None:
                    delay = update.stop_delay
                skipped = update.stop_skipped

        expected = trip.scheduled + timedelta(seconds=delay or 0)
        if expected < now - past_grace:
            continue

        minutes = int(round((expected - now).total_seconds() / 60))
        alerts = _alerts_for(realtime, trip, stop_family, now_ts)
        reason, alternative = _explain(alerts, cancelled, skipped)
        vehicle = realtime.vehicles_by_trip.get(trip.trip_id) if realtime else None
        platform_stop = static.stops.get(trip.stop_id)
        worst_alert = alerts[0] if alerts else None

        departures.append(
            Departure(
                trip_id=trip.trip_id,
                route_id=trip.route_id,
                route_name=route.display_name if route else trip.route_id,
                route_color=route.color if route else None,
                route_text_color=route.text_color if route else None,
                mode=route.mode if route else MODE_UNKNOWN,
                destination=trip.stop_headsign or trip.trip_headsign,
                scheduled=trip.scheduled,
                expected=expected,
                delay=delay,
                status=_status_for(delay, cancelled, skipped, minutes),
                # A vehicle position without a trip update still proves the
                # service is being tracked live, which matters for ferries.
                realtime=update is not None or vehicle is not None,
                platform=platform_stop.platform_code if platform_stop else None,
                stop_id=trip.stop_id,
                stop_name=platform_stop.stop_name if platform_stop else "",
                boarding=trip.pickup_type != 1,
                cancelled=cancelled,
                skipped=skipped,
                direction_id=trip.direction_id,
                vehicle=vehicle,
                alerts=alerts,
                reason=reason,
                alternative=alternative,
                alert_effect=worst_alert.effect if worst_alert else None,
                alert_severity=worst_alert.severity if worst_alert else None,
            )
        )

    departures.sort(key=lambda item: (item.expected, item.route_name))
    return departures


def _alerts_for(
    realtime: RealtimeData | None,
    trip: ScheduledTrip,
    stop_family: set[str],
    now_ts: int,
) -> list[Alert]:
    """Return the alerts that apply to one scheduled call."""
    if realtime is None:
        return []

    indexes: list[int] = []
    indexes.extend(realtime.alerts_by_trip.get(trip.trip_id, ()))
    # A stop-scoped alert only counts when it also names our route, or names no
    # route at all; otherwise a closure of route 74 would show against route 27.
    for stop_id in {trip.stop_id, *stop_family}:
        for index in realtime.alerts_by_stop.get(stop_id, ()):
            alert = realtime.alerts[index]
            if not alert.route_ids or trip.route_id in alert.route_ids:
                indexes.append(index)
    indexes.extend(realtime.route_wide_alerts.get(trip.route_id, ()))

    seen: set[str] = set()
    result: list[Alert] = []
    for index in indexes:
        alert = realtime.alerts[index]
        if alert.alert_id in seen or not alert.is_active(now_ts):
            continue
        seen.add(alert.alert_id)
        result.append(alert)

    result.sort(key=lambda item: (item.severity != "SEVERE", item.header))
    return result


# Sentences that name a replacement arrangement. Anchored at the start of a
# sentence or bullet so a passing mention mid-paragraph is not mistaken for the
# actual advice.
_ALTERNATIVE_RE = re.compile(
    r"(?:^|[.:\n•]\s*)("
    # "Journey Planner" is generic filler AT appends to almost every alert, so it
    # is excluded in favour of advice that names a real replacement.
    r"(?:use|catch|board|take)\s+(?!journey planner)[^.\n•]{4,140}"
    r"|rail replacement[^.\n•]{0,140}"
    r"|(?:temporary|replacement)\s+(?:bus\s+)?stop[^.\n•]{0,140}"
    r")",
    re.IGNORECASE,
)
MAX_ALTERNATIVE_LEN = 160

# Effects that describe an actual interruption rather than an advisory.
_DISRUPTIVE_EFFECTS = frozenset({"NO_SERVICE", "DETOUR", "MODIFIED_SERVICE", "STOP_MOVED"})


def _explain(alerts: list[Alert], cancelled: bool, skipped: bool) -> tuple[str | None, str | None]:
    """Derive why a departure is disrupted and what to do instead.

    Only populated for departures that are genuinely affected, so an advisory such
    as a broken escalator does not attach a "reason" to every train of the day.
    """
    if not (cancelled or skipped):
        return None, None

    reason: str | None = None
    alternative: str | None = None

    for alert in alerts:
        # The auto-generated per-trip cancellation alerts only restate the
        # cancellation ("Service Cancellation for Route X [Schedule Start: …]"),
        # so they never make a useful reason.
        if alert.is_cancellation:
            continue
        if reason is None and alert.effect in _DISRUPTIVE_EFFECTS:
            reason = alert.effect_detail or alert.header or None
        if alternative is None and alert.description:
            if match := _ALTERNATIVE_RE.search(alert.description):
                text = " ".join(match.group(1).split()).strip(" ,;:")
                if len(text) > MAX_ALTERNATIVE_LEN:
                    text = text[: MAX_ALTERNATIVE_LEN - 1].rstrip() + "…"
                alternative = text

    if reason is None:
        reason = "Trip cancelled" if cancelled else "This stop is being skipped"
    return reason, alternative


def stop_alerts(
    realtime: RealtimeData | None,
    stop_family: set[str],
    route_ids: set[str],
    now_ts: int,
    *,
    only_active: bool = True,
    look_ahead: int = 0,
) -> list[Alert]:
    """Return the de-duplicated alerts relevant to a stop."""
    if realtime is None:
        return []

    indexes: list[int] = []
    for stop_id in stop_family:
        indexes.extend(realtime.alerts_by_stop.get(stop_id, ()))
    for route_id in route_ids:
        indexes.extend(realtime.route_wide_alerts.get(route_id, ()))

    seen: set[str] = set()
    # Collapse the many single-night duplicates AT publishes for the same works.
    seen_text: set[tuple[str, str | None]] = set()
    result: list[Alert] = []
    for index in indexes:
        alert = realtime.alerts[index]
        if alert.alert_id in seen:
            continue
        if only_active and not alert.is_active(now_ts, look_ahead):
            continue
        # Per-trip cancellations are shown on the departure row instead.
        if alert.is_cancellation:
            continue
        key = (alert.header, alert.effect)
        if key in seen_text:
            continue
        seen.add(alert.alert_id)
        seen_text.add(key)
        result.append(alert)

    result.sort(key=lambda item: (item.severity != "SEVERE", item.header))
    return result


@dataclass(slots=True)
class StopSnapshot:
    """Everything the entities of one stop need for a single update."""

    stop: Stop
    mode: str
    departures: list[Departure] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    route_ids: set[str] = field(default_factory=set)
    scheduled_today: int = 0
    generated_at: datetime = field(default_factory=datetime.now)
    realtime_at: datetime | None = None
    realtime_degraded: bool = False
    feed_version: str | None = None

    @property
    def next_departure(self) -> Departure | None:
        """Return the first departure that can actually be boarded."""
        for departure in self.departures:
            if departure.boarding and not departure.cancelled:
                return departure
        return self.departures[0] if self.departures else None

    @property
    def cancelled_count(self) -> int:
        """Return how many upcoming departures are cancelled or skipped."""
        return sum(1 for item in self.departures if item.cancelled or item.skipped)


def resolve_mode(route_ids: set[str], static: StaticData) -> str:
    """Pick the dominant mode of the routes serving a stop."""
    counts: dict[str, int] = {}
    for route_id in route_ids:
        route = static.routes.get(route_id)
        if route is None:
            continue
        counts[route.mode] = counts.get(route.mode, 0) + 1
    if not counts:
        return MODE_UNKNOWN
    # Prefer a scheduled mode over school buses when a stop serves both.
    if len(counts) > 1 and MODE_SCHOOL_BUS in counts:
        counts.pop(MODE_SCHOOL_BUS)
    for preferred in (MODE_TRAIN, MODE_FERRY):
        if preferred in counts:
            return preferred
    return max(counts.items(), key=lambda item: item[1])[0]
