"""Coordinators for the Auckland Transport integration.

The API quota is the binding constraint, so all HTTP work is funnelled through a
single :class:`ATRuntime` shared by every config entry that uses the same
subscription key:

* ``/realtime/legacy`` is fetched **once per polling interval in total** and
  reused by every stop. The combined feed carries trip updates, vehicle
  positions and service alerts together.
* ``stoptrips`` is static timetable data, so it is fetched once per stop per
  service day.
* ``/stops`` and ``/routes`` are only re-read when the published GTFS
  ``feed_version`` changes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as date_cls, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ATApiError, ATAuthError, ATRateLimitError, AucklandTransportApi
from .const import (
    API_MAX_HOUR_RANGE,
    API_MIN_START_HOUR,
    CONF_API_KEY,
    CONF_HIDE_ARRIVALS,
    CONF_INCLUDE_SCHOOL_BUSES,
    CONF_ONLY_ACTIVE_ALERTS,
    CONF_STOP_ID,
    DEFAULT_HIDE_ARRIVALS,
    DEFAULT_INCLUDE_SCHOOL_BUSES,
    DEFAULT_ONLY_ACTIVE_ALERTS,
    DOMAIN,
    MODE_UNKNOWN,
    REALTIME_CACHE_MARGIN,
    SCHEDULE_LOOKBACK_HOUR,
    SCHEDULE_RETRY_COOLDOWN,
    STATIC_RECHECK_INTERVAL,
    UPDATE_INTERVAL,
)
from .model import (
    RealtimeData,
    Route,
    StaticData,
    Stop,
    StopSchedule,
    StopSnapshot,
    build_departures,
    parse_realtime,
    parse_schedule,
    resolve_mode,
    stop_alerts,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared runtime
# ---------------------------------------------------------------------------
class ATRuntime:
    """Deduplicates API traffic across every entry using one subscription key."""

    def __init__(self, api: AucklandTransportApi) -> None:
        """Set up empty caches and the locks that serialise refreshes."""
        self.api = api
        self.static = StaticData()

        self._static_lock = asyncio.Lock()
        self._static_checked: datetime | None = None
        self._realtime_lock = asyncio.Lock()
        self._realtime: RealtimeData | None = None
        self._realtime_error: str | None = None
        self._schedule_locks: dict[str, asyncio.Lock] = {}
        # Keyed by (stop_id, service_date) so the previous day survives midnight.
        self._schedules: dict[tuple[str, date_cls], StopSchedule] = {}
        self._schedule_failures: dict[tuple[str, date_cls], datetime] = {}
        self.api_calls = 0

    # -- static ------------------------------------------------------------
    async def async_ensure_static(self, force: bool = False) -> StaticData:
        """Load stops and routes, refreshing only when the feed version changes."""
        async with self._static_lock:
            now = dt_util.utcnow()
            fresh_enough = (
                self._static_checked is not None
                and (now - self._static_checked).total_seconds() < STATIC_RECHECK_INTERVAL
            )
            if self.static.stops and fresh_enough and not force:
                return self.static

            version = None
            try:
                version = await self._counted(self.api.async_get_feed_version())
            except ATApiError as err:
                if self.static.stops:
                    _LOGGER.debug("Could not check GTFS feed version: %s", err)
                    self._static_checked = now
                    return self.static
                raise

            if self.static.stops and version == self.static.feed_version and not force:
                self._static_checked = now
                return self.static

            _LOGGER.debug(
                "Loading GTFS static data (feed version %s -> %s)",
                self.static.feed_version,
                version,
            )
            raw_stops = await self._counted(self.api.async_get_stops())
            raw_routes = await self._counted(self.api.async_get_routes())

            static = StaticData(feed_version=version)
            for raw in raw_stops:
                if stop := Stop.from_api(raw):
                    static.stops[stop.stop_id] = stop
            for raw in raw_routes:
                if route := Route.from_api(raw):
                    static.routes[route.route_id] = route
            static.rebuild_children()

            if not static.stops:
                raise ATApiError("GTFS stops endpoint returned no data")

            self.static = static
            self._static_checked = now
            # A new feed version invalidates every cached timetable.
            self._schedules.clear()
            self._schedule_failures.clear()
            return self.static

    # -- realtime ----------------------------------------------------------
    async def async_get_realtime(self, max_age: float) -> RealtimeData:
        """Return realtime data, reusing a response younger than ``max_age``.

        This is the single recurring API call. The lock means concurrent stops
        share one request rather than each issuing their own.
        """
        async with self._realtime_lock:
            now = dt_util.utcnow()
            if (
                self._realtime is not None
                and self._realtime.fetched_at is not None
                and (now - self._realtime.fetched_at).total_seconds() < max_age
            ):
                return self._realtime

            try:
                entities = await self._counted(self.api.async_get_realtime())
            except ATApiError as err:
                self._realtime_error = str(err)
                if self._realtime is not None:
                    _LOGGER.debug("Reusing cached realtime data: %s", err)
                    return self._realtime
                raise

            self._realtime_error = None
            self._realtime = parse_realtime(entities, now)
            return self._realtime

    @property
    def realtime_error(self) -> str | None:
        """Return the last realtime error message, if the feed is degraded."""
        return self._realtime_error

    @property
    def cached_realtime(self) -> RealtimeData | None:
        """Return the last parsed realtime payload without fetching."""
        return self._realtime

    # -- schedules ---------------------------------------------------------
    @staticmethod
    def relevant_service_dates(now: datetime) -> list[date_cls]:
        """Return the service days whose trips can still be running at ``now``.

        In the small hours the previous day's timetable is still live, because a
        service running past midnight is published as an extended hour on the day
        it departed rather than as an early hour on the new day.
        """
        today = now.date()
        if now.hour < SCHEDULE_LOOKBACK_HOUR:
            return [today - timedelta(days=1), today]
        return [today]

    async def async_get_schedule(self, stop_id: str, now: datetime) -> StopSchedule:
        """Return the timetable covering ``now``, fetching once per service day.

        One request with ``start_hour=1`` and the maximum hour range returns an
        entire service day, verified against an hour-by-hour sweep of the API, so
        there is never a reason to request the same day twice. A stop with no
        services left, or none at all today, is therefore not re-requested either.
        """
        lock = self._schedule_locks.setdefault(stop_id, asyncio.Lock())
        async with lock:
            dates = self.relevant_service_dates(now)
            today = now.date()

            for service_date in dates:
                key = (stop_id, service_date)
                if key in self._schedules or not self._may_fetch_schedule(key, now):
                    continue
                try:
                    raw = await self._counted(
                        self.api.async_get_stop_trips(
                            stop_id,
                            service_date.isoformat(),
                            start_hour=API_MIN_START_HOUR,
                            hour_range=API_MAX_HOUR_RANGE,
                        )
                    )
                except ATApiError as err:
                    self._schedule_failures[key] = now
                    if service_date != today:
                        # Losing the previous day only costs after-midnight trips.
                        _LOGGER.debug(
                            "Could not load the %s timetable for %s: %s",
                            service_date,
                            stop_id,
                            err,
                        )
                        continue
                    raise
                self._schedule_failures.pop(key, None)
                self._schedules[key] = parse_schedule(stop_id, service_date, raw, now)

            if (stop_id, today) not in self._schedules:
                raise ATApiError(
                    f"No timetable available for {stop_id} on {today}"
                )

            self._prune_schedules(stop_id, dates)
            return self._merged_schedule(stop_id, dates, now)

    def cached_service_dates(self, stop_id: str) -> set[date_cls]:
        """Return the service days currently held in the timetable cache."""
        return {key[1] for key in self._schedules if key[0] == stop_id}

    def _may_fetch_schedule(self, key: tuple[str, date_cls], now: datetime) -> bool:
        """Return False while a recently failed day is still in its cooldown."""
        failed_at = self._schedule_failures.get(key)
        if failed_at is None:
            return True
        return (now - failed_at).total_seconds() >= SCHEDULE_RETRY_COOLDOWN

    def _merged_schedule(
        self, stop_id: str, dates: list[date_cls], now: datetime
    ) -> StopSchedule:
        """Combine the cached service days into one timetable."""
        trips = []
        fetched_at: datetime | None = None
        for service_date in dates:
            cached = self._schedules.get((stop_id, service_date))
            if cached is None:
                continue
            trips.extend(cached.trips)
            if cached.fetched_at is not None and (
                fetched_at is None or cached.fetched_at > fetched_at
            ):
                fetched_at = cached.fetched_at

        trips.sort(key=lambda trip: trip.scheduled)
        return StopSchedule(
            stop_id=stop_id,
            service_date=now.date(),
            trips=trips,
            fetched_at=fetched_at or now,
        )

    def _prune_schedules(self, stop_id: str, keep: list[date_cls]) -> None:
        """Drop cached days that can no longer contain a running service."""
        wanted = set(keep)
        for cache in (self._schedules, self._schedule_failures):
            for key in [
                key for key in cache if key[0] == stop_id and key[1] not in wanted
            ]:
                del cache[key]

    def invalidate_schedule(self, stop_id: str) -> None:
        """Drop every cached timetable for a stop so the next update refetches."""
        for cache in (self._schedules, self._schedule_failures):
            for key in [key for key in cache if key[0] == stop_id]:
                del cache[key]

    async def _counted(self, awaitable):
        """Await an API call while keeping a diagnostics counter."""
        self.api_calls += 1
        return await awaitable


def get_runtime(hass: HomeAssistant, api_key: str) -> ATRuntime:
    """Return the shared runtime for ``api_key``, creating it on first use.

    Sharing by key is what keeps the API budget flat: every stop configured with
    the same subscription key reuses one realtime response per interval.
    """
    store: dict[str, ATRuntime] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "runtimes", {}
    )
    if api_key not in store:
        store[api_key] = ATRuntime(
            AucklandTransportApi(async_get_clientsession(hass), api_key)
        )
    return store[api_key]


def release_runtime(hass: HomeAssistant, api_key: str, exclude_entry_id: str) -> None:
    """Drop the shared runtime once no remaining entry uses ``api_key``.

    ``exclude_entry_id`` is the entry being unloaded, which is still registered at
    this point and must not keep the runtime alive.
    """
    still_used = any(
        entry.data.get(CONF_API_KEY) == api_key and entry.entry_id != exclude_entry_id
        for entry in hass.config_entries.async_entries(DOMAIN)
    )
    if not still_used:
        hass.data.get(DOMAIN, {}).get("runtimes", {}).pop(api_key, None)


# ---------------------------------------------------------------------------
# Per-entry coordinator
# ---------------------------------------------------------------------------
class StopCoordinator(DataUpdateCoordinator[StopSnapshot]):
    """Builds the departure board for one configured stop."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, runtime: ATRuntime) -> None:
        """Set up the coordinator on the fixed poll interval."""
        self.entry = entry
        self.runtime = runtime
        self.stop_id: str = entry.data[CONF_STOP_ID]

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.stop_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> StopSnapshot:
        """Refresh the board, making at most one shared realtime request."""
        # GTFS times are local wall-clock values, so the model layer works in
        # naive local time; the epoch is kept separately for alert windows.
        now_aware = dt_util.now()
        now = now_aware.replace(tzinfo=None)
        now_ts = int(now_aware.timestamp())

        try:
            static = await self.runtime.async_ensure_static()
        except ATAuthError as err:
            # Prompts Home Assistant to open the reauth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except ATApiError as err:
            raise UpdateFailed(str(err)) from err

        stop = static.stops.get(self.stop_id)
        if stop is None:
            raise UpdateFailed(
                f"Stop {self.stop_id} is not in the current GTFS feed "
                f"(version {static.feed_version})"
            )

        schedule: StopSchedule
        try:
            schedule = await self.runtime.async_get_schedule(self.stop_id, now)
        except ATAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ATRateLimitError as err:
            raise UpdateFailed(str(err)) from err
        except ATApiError as err:
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            _LOGGER.debug("Keeping cached timetable for %s: %s", self.stop_id, err)
            schedule = StopSchedule(self.stop_id, now.date())

        # One shared request per interval. Stops polling out of phase reuse the
        # response cached by whichever stop fetched it first.
        realtime: RealtimeData | None = self.runtime.cached_realtime
        degraded = False
        max_age = UPDATE_INTERVAL * REALTIME_CACHE_MARGIN
        try:
            realtime = await self.runtime.async_get_realtime(max_age)
        except ATAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ATApiError as err:
            degraded = True
            _LOGGER.debug("Realtime feed unavailable, showing timetable only: %s", err)

        options = self.entry.options
        family = static.family(self.stop_id)
        departures = build_departures(
            schedule=schedule,
            realtime=realtime,
            static=static,
            stop_family=family,
            now=now,
            include_school_buses=bool(
                options.get(CONF_INCLUDE_SCHOOL_BUSES, DEFAULT_INCLUDE_SCHOOL_BUSES)
            ),
            hide_arrivals=bool(options.get(CONF_HIDE_ARRIVALS, DEFAULT_HIDE_ARRIVALS)),
            now_ts=now_ts,
        )

        route_ids = {trip.route_id for trip in schedule.trips}
        alerts = stop_alerts(
            realtime,
            family,
            route_ids,
            now_ts,
            only_active=bool(options.get(CONF_ONLY_ACTIVE_ALERTS, DEFAULT_ONLY_ACTIVE_ALERTS)),
        )

        return StopSnapshot(
            stop=stop,
            mode=resolve_mode(route_ids, static) if route_ids else MODE_UNKNOWN,
            departures=departures,
            alerts=alerts,
            route_ids=route_ids,
            scheduled_today=len(schedule.trips),
            generated_at=now,
            realtime_at=realtime.fetched_at if realtime else None,
            realtime_degraded=degraded,
            feed_version=static.feed_version,
        )
